"""Gmail SMTP with an app password (no domain needed)."""

from __future__ import annotations

import os
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr, make_msgid

from ..config import Config
from .render import Email


def build_message(email: Email, sender: str, to: str, from_name: str) -> EmailMessage:
    msg = EmailMessage()
    msg["Subject"] = email.subject
    msg["From"] = formataddr((from_name, sender))
    msg["To"] = to
    msg["Message-ID"] = make_msgid(domain=sender.split("@")[-1])
    msg.set_content(email.text)
    msg.add_alternative(email.html, subtype="html")
    return msg


def send_smtp(email: Email, to: str, cfg: Config) -> None:
    from . import DeliveryError

    user = os.environ.get("SMTP_USER")
    password = os.environ.get("SMTP_APP_PASSWORD")
    if not user or not password:
        raise DeliveryError("SMTP_USER and SMTP_APP_PASSWORD secrets are required for the smtp provider.")
    host = os.environ.get("SMTP_HOST", "smtp.gmail.com")
    port = int(os.environ.get("SMTP_PORT", "465"))
    msg = build_message(email, user, to, cfg.email.from_name)
    ctx = ssl.create_default_context()
    try:
        if port == 465:
            with smtplib.SMTP_SSL(host, port, context=ctx, timeout=30) as s:
                s.login(user, password.replace(" ", ""))
                s.send_message(msg)
        else:
            with smtplib.SMTP(host, port, timeout=30) as s:
                s.starttls(context=ctx)
                s.login(user, password.replace(" ", ""))
                s.send_message(msg)
    except smtplib.SMTPAuthenticationError as e:
        raise DeliveryError(
            "Gmail rejected the login. Use a 16-character app password (needs 2-Step Verification), "
            "not your normal password."
        ) from e
