from fastapi import FastAPI, Depends, HTTPException, Header, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session
from sqlalchemy import select
from pathlib import Path
from app.database import Base, engine, get_db
from app.models import Listing
from app.schemas import ListingCreate, ListingOut, CATEGORIES
from app.config import settings

Base.metadata.create_all(bind=engine)
app = FastAPI(title="KOREA HUB API", version="0.1.0")
STATIC = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=STATIC), name="static")

@app.get("/")
def home():
    return FileResponse(STATIC / "index.html")

@app.get("/health")
def health():
    return {"status": "ok", "service": "korea-hub"}

@app.get("/api/listings", response_model=list[ListingOut])
def get_listings(category: str | None = None, city: str | None = None,
                 q: str | None = Query(default=None, max_length=100),
                 db: Session = Depends(get_db)):
    stmt = select(Listing).where(Listing.status == "approved")
    if category:
        if category not in CATEGORIES:
            raise HTTPException(400, "Unsupported category")
        stmt = stmt.where(Listing.category == category)
    if city:
        stmt = stmt.where(Listing.city.ilike(f"%{city}%"))
    if q:
        stmt = stmt.where(Listing.title.ilike(f"%{q}%"))
    return db.scalars(stmt.order_by(Listing.id.desc()).limit(100)).all()

@app.post("/api/listings", response_model=ListingOut, status_code=201)
def create_listing(payload: ListingCreate, db: Session = Depends(get_db)):
    if payload.category not in CATEGORIES:
        raise HTTPException(400, "Unsupported category")
    item = Listing(**payload.model_dump(), status="pending")
    db.add(item)
    db.commit()
    db.refresh(item)
    return item

def require_admin(x_admin_key: str | None):
    if not x_admin_key or x_admin_key != settings.admin_api_key:
        raise HTTPException(401, "Admin authentication required")

@app.get("/api/admin/listings", response_model=list[ListingOut])
def pending_listings(x_admin_key: str | None = Header(default=None), db: Session = Depends(get_db)):
    require_admin(x_admin_key)
    return db.scalars(select(Listing).where(Listing.status == "pending").order_by(Listing.id)).all()

@app.post("/api/admin/listings/{listing_id}/{decision}")
def moderate_listing(listing_id: int, decision: str, x_admin_key: str | None = Header(default=None),
                     db: Session = Depends(get_db)):
    require_admin(x_admin_key)
    if decision not in {"approve", "reject"}:
        raise HTTPException(400, "Decision must be approve or reject")
    item = db.get(Listing, listing_id)
    if not item:
        raise HTTPException(404, "Listing not found")
    item.status = "approved" if decision == "approve" else "rejected"
    db.commit()
    return {"id": item.id, "status": item.status}
