"""Watching the service: structured logs, request IDs, 5xx alerts to administrators, security headers, health checks.

Logs are one JSON object per line and never contain message texts, tokens or request bodies: only what is needed
to find a problem (time, request ID, method, masked path, status, duration)."""
import json
import logging
import re
import shutil
import socket
import ssl
import time
import traceback
import uuid
from datetime import datetime, timezone
from urllib.parse import urlparse

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text
from starlette.middleware.base import BaseHTTPMiddleware

from app.config import settings
from app.database import SessionLocal

log = logging.getLogger("koreahub")
TOKEN_LIKE = re.compile(r"^[A-Za-z0-9_\-]{20,}$")
ALERT_EVERY_SECONDS = 600
BOT_STALE_SECONDS = 300
_last_alert = {"at": 0.0}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        data = {"ts": datetime.fromtimestamp(record.created, timezone.utc).isoformat(timespec="seconds"),
                "level": record.levelname, "msg": record.getMessage()}
        data.update(getattr(record, "extra_fields", {}))
        if record.exc_info:
            # the type and the place, not the text of the error: it may quote user data
            data["error"] = record.exc_info[0].__name__ if record.exc_info[0] else ""
        return json.dumps(data, ensure_ascii=False)


def setup_logging() -> None:
    if log.handlers:
        return
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    log.addHandler(handler)
    log.setLevel(getattr(logging, settings.log_level.upper(), logging.INFO))
    log.propagate = False


def event(level: int, msg: str, **fields) -> None:
    log.log(level, msg, extra={"extra_fields": fields})


def mask_path(path: str) -> str:
    """Sign-in tokens and keys travel in the path: hide anything that looks like one."""
    return "/".join("*" if TOKEN_LIKE.match(part) and not part.isalpha() else part for part in path.split("/"))


def alert_admins(message: str, now: float | None = None) -> bool:
    """Tell administrators in Telegram, at most once in ten minutes. Returns whether a message was sent."""
    moment = time.monotonic() if now is None else now
    if moment - _last_alert["at"] < ALERT_EVERY_SECONDS and _last_alert["at"] != 0.0:
        return False
    _last_alert["at"] = moment
    if not settings.notifications_enabled:
        return False
    from app import guard
    for tg_id in [x.strip() for x in settings.admin_ids.split(",") if x.strip().isdigit()]:
        guard.send_telegram(tg_id, "⚠️ KOREA HUB: " + message)
    return True


SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=()",
    # The page is shown inside Telegram, so Telegram (and the site itself) may frame it; nobody else.
    # X-Frame-Options is deliberately NOT set: it would break the Mini App in Telegram.
    "Content-Security-Policy": "frame-ancestors 'self' https://web.telegram.org https://*.telegram.org https://telegram.org",
}


class ObserveMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        rid = uuid.uuid4().hex[:12]
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception as exc:  # noqa: BLE001 - last line of defence: log it, tell the admins, answer politely
            ms = int((time.perf_counter() - started) * 1000)
            event(logging.ERROR, "unhandled error", rid=rid, method=request.method, path=mask_path(request.url.path),
                  status=500, ms=ms, error=type(exc).__name__, where=_where(exc))
            alert_admins(f"ошибка сервера в {request.method} {mask_path(request.url.path)} (номер {rid}, {type(exc).__name__})")
            response = JSONResponse({"detail": f"Внутренняя ошибка. Номер для поддержки: {rid}"}, status_code=500)
        else:
            ms = int((time.perf_counter() - started) * 1000)
            if response.status_code >= 500:
                event(logging.ERROR, "server error", rid=rid, method=request.method, path=mask_path(request.url.path),
                      status=response.status_code, ms=ms)
                alert_admins(f"ответ {response.status_code} на {request.method} {mask_path(request.url.path)} (номер {rid})")
            elif request.url.path != "/health" and not request.url.path.startswith("/static/"):
                event(logging.INFO, "request", rid=rid, method=request.method, path=mask_path(request.url.path),
                      status=response.status_code, ms=ms)
        response.headers["X-Request-ID"] = rid
        for key, value in SECURITY_HEADERS.items():
            response.headers.setdefault(key, value)
        if request.headers.get("x-forwarded-proto") == "https":
            response.headers.setdefault("Strict-Transport-Security", "max-age=15552000")
        if request.url.path.startswith("/admin") or request.url.path.startswith("/api/admin"):
            response.headers["Cache-Control"] = "no-store"
        elif request.url.path.startswith("/static/") and request.url.path.rsplit(".", 1)[-1] in {"js", "css", "html"}:
            response.headers["Cache-Control"] = "no-cache"      # ask the server every time; an unchanged file costs only a short «304»
        return response


def _where(exc: BaseException) -> str:
    """File and line where the error happened (no message text)."""
    tb = traceback.extract_tb(exc.__traceback__)
    return f"{tb[-1].filename.split('/')[-1]}:{tb[-1].lineno}" if tb else ""


# ---------- health ----------

def beat(db, name: str = "bot") -> None:
    from app.models import Heartbeat
    from app.payments import now
    row = db.get(Heartbeat, name)
    if row is None:
        db.add(Heartbeat(name=name, at=now()))
    else:
        row.at = now()
    db.commit()


def health_report() -> tuple[dict, bool]:
    """(report, database_ok). The bot being silent is reported, but only a broken database makes the check fail."""
    from app.models import Heartbeat
    from app.payments import now, aware
    from app import buildinfo
    report: dict = {"status": "ok", "service": "korea-hub", "build": buildinfo.build_id()}
    db_ok = True
    try:
        with SessionLocal() as db:
            db.execute(text("select 1"))
            beat_row = db.get(Heartbeat, "bot")
            if beat_row is None:
                report["bot"] = "unknown"
            else:
                age = int((now() - aware(beat_row.at)).total_seconds())
                report["bot"] = "ok" if age <= BOT_STALE_SECONDS else "stale"
                report["bot_age_s"] = age
    except Exception:  # noqa: BLE001
        db_ok = False
        report.update(status="error", db=False)
    else:
        report["db"] = True
    try:
        report["disk_free_mb"] = shutil.disk_usage(settings.upload_dir if _exists(settings.upload_dir) else ".").free // (1024 * 1024)
    except Exception:  # noqa: BLE001
        report["disk_free_mb"] = None
    return report, db_ok


def _exists(path: str) -> bool:
    import os
    return os.path.isdir(path)


_cert_cache: dict = {"at": 0.0, "days": None}


def certificate_days(url: str | None = None, ttl: int = 3600) -> int | None:
    """Days until the HTTPS certificate of the site expires (None if it cannot be read: a local run, no network)."""
    host = urlparse(url or settings.webapp_url).hostname
    if not host or host in ("localhost", "127.0.0.1") or host.endswith("example.com"):
        return None
    if time.monotonic() - _cert_cache["at"] < ttl and _cert_cache["at"]:
        return _cert_cache["days"]
    days = None
    try:
        ctx = ssl.create_default_context()
        with socket.create_connection((host, 443), timeout=4) as sock, ctx.wrap_socket(sock, server_hostname=host) as tls:
            expires = ssl.cert_time_to_seconds(tls.getpeercert()["notAfter"])
            days = int((expires - time.time()) // 86400)
    except Exception:  # noqa: BLE001
        days = None
    _cert_cache.update(at=time.monotonic(), days=days)
    return days


def install(app: FastAPI) -> None:
    setup_logging()
    app.add_middleware(ObserveMiddleware)

    @app.get("/health")
    def health():
        report, ok = health_report()
        return JSONResponse(report, status_code=200 if ok else 503)

    @app.get("/health/extended")
    def health_extended():
        report, ok = health_report()
        days = certificate_days()
        report["cert_days"] = days
        if days is not None and days < 10:
            report["cert_warning"] = "Сертификат HTTPS скоро истекает"
        return JSONResponse(report, status_code=200 if ok else 503)
