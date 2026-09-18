"""Email rendering and delivery."""

from __future__ import annotations

import os

from ..config import Config
from .render import Email


class DeliveryError(Exception):
    pass


def recipient(cfg: Config) -> str:
    to = cfg.profile.email_to or os.environ.get("EMAIL_TO") or os.environ.get("SMTP_USER")
    if not to:
        raise DeliveryError("No recipient: set profile.email_to in config.yaml or the SMTP_USER secret.")
    return to


def send(email: Email, cfg: Config) -> None:
    to = recipient(cfg)
    if cfg.email.provider == "resend":
        from .resend import send_resend

        send_resend(email, to, cfg)
    else:
        from .smtp import send_smtp

        send_smtp(email, to, cfg)


__all__ = ["DeliveryError", "Email", "recipient", "send"]
