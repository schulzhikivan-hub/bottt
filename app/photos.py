"""Listing photos: up to ten per listing, re-encoded on the server.

Every upload is opened, checked, rotated by its EXIF orientation and saved again as a clean JPEG
(the original metadata, including GPS position, is thrown away), plus a small preview.
Files are named by a random key. A photo added to an already published listing waits for a
moderator (status "pending") while the old photos stay visible; the same goes for removals.
"""
import hashlib
import io
import re
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from PIL import Image, ImageOps
from pydantic import BaseModel
from sqlalchemy import select, func
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.admin_auth import MOD, staff
from app.audit import log_action
from app.models import BannedPhoto, Listing, ListingPhoto
from app.users import writer

router = APIRouter()
MAX_UPLOAD_BYTES = 8 * 1024 * 1024
MAX_PIXELS = 40_000_000
FULL_SIDE, THUMB_SIDE = 1600, 480
NAME_RE = re.compile(r"^[0-9a-f]{32}(_t)?\.jpg$")
Image.MAX_IMAGE_PIXELS = MAX_PIXELS  # Pillow refuses decompression bombs above this


def photo_root() -> Path:
    root = Path(settings.upload_dir) / "listings"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _jpeg(img: Image.Image, side: int) -> bytes:
    copy = img.copy()
    copy.thumbnail((side, side))
    out = io.BytesIO()
    copy.save(out, "JPEG", quality=85, optimize=True)
    return out.getvalue()


def process_image(data: bytes) -> tuple[bytes, bytes]:
    """Return (full, thumb) JPEG bytes or raise 400. Metadata is not carried over."""
    try:
        probe = Image.open(io.BytesIO(data))
        probe.verify()
        img = Image.open(io.BytesIO(data))
        if img.format not in ("JPEG", "PNG", "WEBP"):
            raise ValueError("format")
        if img.width * img.height > MAX_PIXELS:
            raise ValueError("size")
        img = ImageOps.exif_transpose(img).convert("RGB")
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(400, "Нужна фотография в формате JPEG, PNG или WebP")
    return _jpeg(img, FULL_SIDE), _jpeg(img, THUMB_SIDE)


MIN_SIDE, MAX_ASPECT = 80, 5


def check_shape(full_jpeg: bytes) -> None:
    """A listing photo must be a real picture: not a thumbnail-sized dot and not a thin strip."""
    img = Image.open(io.BytesIO(full_jpeg))
    short, long = sorted(img.size)
    if short < MIN_SIDE:
        raise HTTPException(400, f"Фото слишком маленькое: меньшая сторона не меньше {MIN_SIDE} пикселей")
    if long / short > MAX_ASPECT:
        raise HTTPException(400, "Странные пропорции фото: загрузите обычный снимок, не узкую полосу")


def dhash(jpeg: bytes) -> str:
    """64-bit difference hash: survives re-compression and resizing, so a banned picture is recognised again."""
    img = Image.open(io.BytesIO(jpeg)).convert("L").resize((9, 8), Image.LANCZOS)
    px = list(img.getdata())
    bits = 0
    for row in range(8):
        for col in range(8):
            bits = (bits << 1) | (1 if px[row * 9 + col] > px[row * 9 + col + 1] else 0)
    return f"{bits:016x}"


def is_banned(db: Session, jpeg: bytes, max_distance: int = 5) -> bool:
    h = int(dhash(jpeg), 16)
    for (other,) in db.execute(select(BannedPhoto.dhash)).all():
        if bin(h ^ int(other, 16)).count("1") <= max_distance:
            return True
    return False


def url(key: str, thumb: bool = False) -> str:
    return f"/api/photos/{key}{'_t' if thumb else ''}.jpg"


def photos_map(db: Session, listing_ids: list[int], owner: bool = False) -> dict[int, list[dict]]:
    """Photos per listing in display order. Public view: shown photos only; owner view: all, with status."""
    if not listing_ids:
        return {}
    stmt = select(ListingPhoto).where(ListingPhoto.listing_id.in_(listing_ids))
    if not owner:
        stmt = stmt.where(ListingPhoto.status.in_(["live", "removal"]))
    out: dict[int, list[dict]] = {}
    for p in db.scalars(stmt.order_by(ListingPhoto.position, ListingPhoto.id)).all():
        out.setdefault(p.listing_id, []).append(
            {"id": p.id, "url": url(p.key), "thumb": url(p.key, True), "status": p.status})
    return out


def delete_files(key: str) -> None:
    for name in (f"{key}.jpg", f"{key}_t.jpg"):
        path = photo_root() / name
        if path.exists():
            path.unlink()


def count_photos(db: Session, listing_id: int, shown_only: bool = False) -> int:
    """Photos that count towards the limit and the "has a photo" rule (removals do not count)."""
    statuses = ["live"] if shown_only else ["live", "pending"]
    return db.scalar(select(func.count()).select_from(ListingPhoto).where(
        ListingPhoto.listing_id == listing_id, ListingPhoto.status.in_(statuses))) or 0


def refresh_edit_flag(db: Session, item: Listing) -> None:
    """A published listing has a waiting edit if changes or photo changes are pending."""
    waiting_photos = db.scalar(select(func.count()).select_from(ListingPhoto).where(
        ListingPhoto.listing_id == item.id, ListingPhoto.status.in_(["pending", "removal"]))) or 0
    item.has_edit = item.status == "approved" and (bool(item.pending_changes) or waiting_photos > 0)


def _own_listing(db: Session, listing_id: int, user_id: str) -> Listing:
    item = db.get(Listing, listing_id)
    if item is None or item.author_telegram_id != user_id or item.status == "archived":
        raise HTTPException(404, "Объявление не найдено")
    return item


@router.post("/api/listings/{listing_id}/photos", status_code=201)
async def add_photo(listing_id: int, file: UploadFile = File(...), user_id: str = Depends(writer),
                    db: Session = Depends(get_db)):
    item = _own_listing(db, listing_id, user_id)
    if count_photos(db, listing_id) >= settings.max_photos:
        raise HTTPException(409, f"Не больше {settings.max_photos} фото")
    data = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "Фото больше 8 МБ")
    full, thumb = process_image(data)
    check_shape(full)
    if is_banned(db, full):
        raise HTTPException(400, "Это изображение запрещено правилами сервиса. Выберите другое фото")
    sha = hashlib.sha256(full).hexdigest()
    key = uuid.uuid4().hex
    (photo_root() / f"{key}.jpg").write_bytes(full)
    (photo_root() / f"{key}_t.jpg").write_bytes(thumb)
    last = db.scalar(select(func.max(ListingPhoto.position)).where(ListingPhoto.listing_id == listing_id))
    # On a published listing a new photo waits for a moderator; elsewhere it is shown once the listing is
    status = "pending" if item.status == "approved" else "live"
    photo = ListingPhoto(listing_id=listing_id, key=key, position=(last if last is not None else -1) + 1, status=status, sha=sha)
    db.add(photo)
    db.flush()
    same = db.scalar(select(Listing.id).join(ListingPhoto, ListingPhoto.listing_id == Listing.id).where(
        ListingPhoto.sha == sha, Listing.author_telegram_id != user_id).limit(1))
    if same is not None:   # the same picture under another account: a moderator should look (the author is not told)
        from app.audit import log_action
        log_action(db, "system", "photo.duplicate_suspected", "listing", listing_id, f"то же фото, что в #{same}")
    refresh_edit_flag(db, item)
    db.commit()
    return {"id": photo.id, "url": url(key), "thumb": url(key, True), "status": status}


@router.delete("/api/listings/{listing_id}/photos/{photo_id}")
def remove_photo(listing_id: int, photo_id: int, user_id: str = Depends(writer), db: Session = Depends(get_db)):
    item = _own_listing(db, listing_id, user_id)
    photo = db.get(ListingPhoto, photo_id)
    if photo is None or photo.listing_id != listing_id:
        raise HTTPException(404, "Фото не найдено")
    if item.status == "approved" and photo.status == "live":
        photo.status = "removal"          # stays visible until a moderator approves the edit
    else:
        delete_files(photo.key)
        db.delete(photo)
    db.flush()
    refresh_edit_flag(db, item)
    db.commit()
    return {"ok": True}


class OrderIn(BaseModel):
    ids: list[int]


@router.put("/api/listings/{listing_id}/photos/order")
def reorder_photos(listing_id: int, payload: OrderIn, user_id: str = Depends(writer), db: Session = Depends(get_db)):
    """The first photo is the cover. Order changes apply at once: they do not change what is shown."""
    _own_listing(db, listing_id, user_id)
    photos = {p.id: p for p in db.scalars(select(ListingPhoto).where(ListingPhoto.listing_id == listing_id)).all()}
    if set(payload.ids) != set(photos):
        raise HTTPException(400, "Передайте все фото объявления")
    for position, photo_id in enumerate(payload.ids):
        photos[photo_id].position = position
    db.commit()
    return {"ok": True}


@router.get("/api/photos/{name}")
def get_photo(name: str):
    if not NAME_RE.match(name):
        raise HTTPException(404, "Фото не найдено")
    path = photo_root() / name
    if not path.exists():
        raise HTTPException(404, "Фото не найдено")
    return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "public, max-age=31536000, immutable"})


@router.post("/api/admin/listings/{listing_id}/ban-photos")
def ban_listing_photos(listing_id: int, actor: str = Depends(staff(*MOD)), db: Session = Depends(get_db)):
    """The pictures of this listing are banned: they are deleted and the same picture cannot be uploaded again."""
    item = db.get(Listing, listing_id)
    if item is None:
        raise HTTPException(404, "Объявление не найдено")
    rows = db.scalars(select(ListingPhoto).where(ListingPhoto.listing_id == listing_id)).all()
    banned = 0
    for row in rows:
        path = photo_root() / f"{row.key}.jpg"
        if path.exists():
            db.add(BannedPhoto(dhash=dhash(path.read_bytes()), note=f"объявление #{listing_id}", created_by=actor))
            banned += 1
        delete_files(row.key)
        db.delete(row)
    log_action(db, actor, "photo.ban", "listing", listing_id, f"запрещено фото: {banned}")
    db.commit()
    return {"banned": banned}
