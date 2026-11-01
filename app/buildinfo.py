"""A short number of the files the phone downloads. It changes with every update of the app, so the page can ask
for «app.js?v=…» and a stale copy kept by Telegram or the browser is never used. It is shown in the app and in /health,
so anybody can see which version is really running."""
import hashlib
from pathlib import Path

STATIC = Path(__file__).parent / "static"
_FILES = ("index.html", "app.js", "app.css", "admin.html", "admin.js", "sw.js")
_cache: dict = {}


def build_id() -> str:
    if "id" not in _cache:
        h = hashlib.sha1()
        for name in _FILES:
            p = STATIC / name
            if p.exists():
                h.update(p.read_bytes())
        _cache["id"] = h.hexdigest()[:8]
    return _cache["id"]


def page(name: str) -> str:
    """index.html or admin.html with versioned links to scripts and styles."""
    text = (STATIC / name).read_text(encoding="utf-8")
    v = build_id()
    for asset in ("app.js", "app.css", "admin.js"):
        text = text.replace(f'/static/{asset}"', f'/static/{asset}?v={v}"')
    return text.replace("</head>", f'<meta name="build" content="{v}"></head>', 1)


def app_url(path: str = "") -> str:
    """The Mini App address for a Telegram button: the same page, but with the version in it. When the app is updated the
    address changes, so Telegram cannot show an old copy of the page it remembered under the old address."""
    from app.config import settings
    url = settings.webapp_url.rstrip("/") + ("/" + path.lstrip("/") if path else "")
    return url + ("&" if "?" in url else "?") + "b=" + build_id()
