"""Sending a letter through the owner's SMTP server (SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, SMTP_FROM in .env).
Without SMTP_HOST the site sends no mail and the features that need it say so."""
import logging
import smtplib
import ssl
from email.message import EmailMessage

from app.config import settings

log = logging.getLogger("mailer")


def enabled() -> bool:
    return bool(settings.smtp_host and (settings.smtp_from or settings.smtp_user))


def send(to: str, subject: str, text: str) -> bool:
    """True when the server accepted the letter. Never raises: a broken mail server must not break the site."""
    if not enabled():
        return False
    msg = EmailMessage()
    msg["From"] = settings.smtp_from or settings.smtp_user
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(text)
    try:
        if settings.smtp_port == 465:
            with smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port, timeout=15, context=ssl.create_default_context()) as s:
                if settings.smtp_user:
                    s.login(settings.smtp_user, settings.smtp_password)
                s.send_message(msg)
        else:
            with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=15) as s:
                s.starttls(context=ssl.create_default_context())
                if settings.smtp_user:
                    s.login(settings.smtp_user, settings.smtp_password)
                s.send_message(msg)
        return True
    except Exception as exc:            # noqa: BLE001
        log.warning("mail to %s failed: %s", to[:3] + "***", exc.__class__.__name__)
        return False
