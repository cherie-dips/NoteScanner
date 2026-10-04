"""Sending password-reset emails over SMTP. Enabled only when SMTP_HOST and SMTP_FROM are set."""
import logging
import os
import smtplib
from email.message import EmailMessage

logger = logging.getLogger(__name__)


def _env(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip()


def is_configured() -> bool:
    return bool(_env("SMTP_HOST") and _env("SMTP_FROM"))


def send_email(to: str, subject: str, body: str) -> None:
    """Send a plain-text email. Raises on failure."""
    host = _env("SMTP_HOST")
    port = int(_env("SMTP_PORT", "587") or "587")
    username = _env("SMTP_USERNAME")
    password = _env("SMTP_PASSWORD")
    starttls = _env("SMTP_STARTTLS", "true").lower() in ("1", "true", "yes")

    msg = EmailMessage()
    msg["From"] = _env("SMTP_FROM")
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)

    smtp_cls = smtplib.SMTP_SSL if port == 465 else smtplib.SMTP
    with smtp_cls(host, port, timeout=20) as smtp:
        if starttls and port != 465:
            smtp.starttls()
        if username:
            smtp.login(username, password)
        smtp.send_message(msg)


def send_password_reset(to: str, reset_link: str) -> None:
    body = (
        "Someone asked to reset the password for your NoteScanner account.\n\n"
        f"Open this link to choose a new password (it works once and expires in 1 hour):\n{reset_link}\n\n"
        "If you didn't ask for this, you can ignore this email; your password stays the same."
    )
    send_email(to, "Reset your NoteScanner password", body)
