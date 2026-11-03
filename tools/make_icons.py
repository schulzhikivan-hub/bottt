"""Draws the site icons and the link preview picture. Run once: python tools/make_icons.py
Output goes to app/static/. The files are committed, so the server does not need Pillow fonts."""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

OUT = Path(__file__).resolve().parent.parent / "app" / "static"
NAVY, BLUE, RED, WHITE = (11, 45, 115), (31, 111, 229), (205, 46, 58), (255, 255, 255)
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"


def flag(size: int) -> Image.Image:
    """A simplified flag of Korea on a rounded white card (circle in red and blue, four groups of bars)."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((0, 0, size - 1, size - 1), radius=size // 5, fill=WHITE)
    c, r = size / 2, size * 0.2
    d.pieslice((c - r, c - r, c + r, c + r), 180, 360, fill=RED)
    d.pieslice((c - r, c - r, c + r, c + r), 0, 180, fill=BLUE)
    d.ellipse((c - r / 2, c - r, c + r / 2, c), fill=RED)
    d.ellipse((c - r / 2, c, c + r / 2, c + r), fill=BLUE)
    # Four groups of three bars in the corners: each bar is a thin rectangle across the line from the centre
    th, half, gap = size * 0.04, size * 0.085, size * 0.07
    for sx, sy in ((-1, -1), (1, -1), (-1, 1), (1, 1)):
        corner = (c + sx * size * 0.29, c + sy * size * 0.29)
        nx, ny = sx * 0.7071, sy * 0.7071      # from the centre outwards
        tx, ty = -ny, nx                       # along the bar
        for k in (-1, 0, 1):
            mx, my = corner[0] + nx * gap * k, corner[1] + ny * gap * k
            pts = [(mx + tx * half + nx * th / 2, my + ty * half + ny * th / 2), (mx - tx * half + nx * th / 2, my - ty * half + ny * th / 2),
                   (mx - tx * half - nx * th / 2, my - ty * half - ny * th / 2), (mx + tx * half - nx * th / 2, my + ty * half - ny * th / 2)]
            d.polygon(pts, fill=(20, 20, 20))
    return img


def square_icon(size: int) -> Image.Image:
    base = Image.new("RGBA", (size, size), NAVY)
    mark = flag(int(size * 0.72))
    base.paste(mark, ((size - mark.width) // 2, (size - mark.height) // 2), mark)
    return base


def preview() -> Image.Image:
    w, h = 1200, 630
    img = Image.new("RGB", (w, h), NAVY)
    d = ImageDraw.Draw(img)
    for y in range(h):  # vertical gradient navy -> blue
        t = y / h
        d.line((0, y, w, y), fill=tuple(int(NAVY[i] + (BLUE[i] - NAVY[i]) * t * 0.9) for i in range(3)))
    mark = flag(260)
    img.paste(mark, (90, 185), mark)
    d.text((410, 205), "KOREA HUB", font=ImageFont.truetype(FONT, 110), fill=WHITE)
    d.text((414, 345), "Jobs, housing, goods and useful info", font=ImageFont.truetype(FONT, 38), fill=(214, 228, 255))
    d.text((414, 400), "for foreigners in South Korea", font=ImageFont.truetype(FONT, 38), fill=(214, 228, 255))
    return img


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    square_icon(512).convert("RGB").save(OUT / "icon-512.png", optimize=True)
    square_icon(192).convert("RGB").save(OUT / "icon-192.png", optimize=True)
    square_icon(180).convert("RGB").save(OUT / "apple-touch-icon.png", optimize=True)
    square_icon(48).convert("RGB").save(OUT / "favicon.png", optimize=True)
    preview().save(OUT / "og.png", optimize=True)
    print("icons written to", OUT)
