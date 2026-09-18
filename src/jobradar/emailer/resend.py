"""Resend API. Sending to anyone but the account owner needs a verified domain."""

from __future__ import annotations

import os

import httpx

from ..config import Config
from .render import Email


def send_resend(email: Email, to: str, cfg: Config) -> None:
    from . import DeliveryError

    key = os.environ.get("RESEND_API_KEY")
    if not key:
        raise DeliveryError("RESEND_API_KEY secret is required for the resend provider.")
    sender = cfg.email.from_address or os.environ.get("RESEND_FROM") or "onboarding@resend.dev"
    resp = httpx.post(
        "https://api.resend.com/emails",
        headers={"Authorization": f"Bearer {key}"},
        json={
            "from": f"{cfg.email.from_name} <{sender}>",
            "to": [to],
            "subject": email.subject,
            "html": email.html,
            "text": email.text,
        },
        timeout=30,
    )
    if resp.status_code >= 300:
        raise DeliveryError(f"Resend returned {resp.status_code}: {resp.text[:200]}")
