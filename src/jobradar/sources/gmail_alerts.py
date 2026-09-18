"""Tier 2: read Handshake and LinkedIn job-alert emails from Gmail (read-only) and extract postings.

Handshake has no public API and sits behind school SSO, so its alert emails are the practical way in.
"""

from __future__ import annotations

import asyncio
import base64
import os
import re
from email.utils import parseaddr

from selectolax.parser import HTMLParser, Node

from ..models import RawPosting, SourceResult
from .base import Context, Source, SourceFailed, parse_dt

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]

NOISE_LINES = re.compile(
    r"^(view job|view all jobs|see all jobs|apply|easy apply|apply now|save|promoted|actively recruiting|"
    r"be an early applicant|new|\d+ (?:connections?|alumni|applicants?).*|.*school alum.*|"
    r"\d+ (?:day|days|hour|hours|week|weeks) ago|just now|posted .*|recommended for you|"
    r"your job alert.*|jobs? (?:you|we) (?:might|think).*|see more jobs.*|unsubscribe|"
    r"\$[\d,.kK]+.*|.*/(?:yr|hr|year|hour)\b.*)$",
    re.I,
)


def _lines(node: Node) -> list[str]:
    text = node.text(separator="\n")
    out = []
    for line in text.split("\n"):
        line = re.sub(r"\s+", " ", line).strip()
        if line and not NOISE_LINES.match(line) and line not in out:
            out.append(line)
    return out


def _card(anchor: Node, title: str, max_up: int = 6) -> list[str]:
    """Walk up from the title link to the smallest block that also holds company/location lines."""
    node = anchor
    for _ in range(max_up):
        if node.parent is None:
            break
        node = node.parent
        lines = _lines(node)
        if title in lines and len(lines) >= 2:
            i = lines.index(title)
            return lines[i + 1 : i + 4]
    return []


def _split_company_location(lines: list[str]) -> tuple[str | None, str | None]:
    if not lines:
        return None, None
    first = lines[0]
    for sep in (" · ", " • ", " - ", " | "):
        if sep in first:
            company, loc = first.split(sep, 1)
            return company.strip(), loc.strip()
    return first, (lines[1] if len(lines) > 1 else None)


def parse_linkedin(html: str) -> list[RawPosting]:
    tree = HTMLParser(html)
    best: dict[str, tuple[str, Node]] = {}
    for a in tree.css("a[href]"):
        m = re.search(r"linkedin\.com/(?:comm/)?jobs/view/(?:[^/?]*-)?(\d+)", a.attributes.get("href") or "")
        if not m:
            continue
        text = re.sub(r"\s+", " ", a.text()).strip()
        if not text or NOISE_LINES.match(text):
            continue
        job_id = m.group(1)
        if job_id not in best:
            best[job_id] = (text, a)
    out = []
    for job_id, (title, a) in best.items():
        company, location = _split_company_location(_card(a, title))
        if not company:
            continue
        out.append(
            RawPosting(
                title=title,
                company=company,
                location=location,
                url=f"https://www.linkedin.com/jobs/view/{job_id}/",
                source="gmail:linkedin",
            )
        )
    return out


HANDSHAKE_JOB = re.compile(r"joinhandshake\.com/(?:stu/)?(?:jobs|job-search)/(\d+)")


def parse_handshake(html: str) -> list[RawPosting]:
    """Handshake alert cards: job title link, then employer, then location / job type lines."""
    tree = HTMLParser(html)
    seen: dict[str, tuple[str, Node, str]] = {}
    for a in tree.css("a[href]"):
        href = a.attributes.get("href") or ""
        text = re.sub(r"\s+", " ", a.text()).strip()
        m = HANDSHAKE_JOB.search(href)
        # Links may be wrapped in click trackers; fall back to anything that looks like a job link.
        key = m.group(1) if m else None
        if not key:
            continue
        if not text or NOISE_LINES.match(text):
            continue
        if key not in seen:
            seen[key] = (text, a, href)
    out = []
    for key, (title, a, href) in seen.items():
        lines = _card(a, title)
        company, location = _split_company_location(lines)
        emp = next((l for l in lines if re.search(r"intern|part[- ]time|full[- ]time|co-?op|on[- ]campus", l, re.I)), None)
        if location and emp and location == emp:
            location = None
        if not company:
            continue
        out.append(
            RawPosting(
                title=title,
                company=company,
                location=location,
                url=f"https://app.joinhandshake.com/jobs/{key}",
                source="gmail:handshake",
                employment_type=emp,
            )
        )
    return out


def _html_body(payload: dict) -> str:
    stack = [payload]
    plain = ""
    while stack:
        part = stack.pop()
        stack.extend(part.get("parts") or [])
        data = (part.get("body") or {}).get("data")
        if not data:
            continue
        text = base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode("utf-8", "replace")
        if part.get("mimeType") == "text/html":
            return text
        if part.get("mimeType") == "text/plain":
            plain = text
    return plain


def parse_message(sender: str, html: str) -> list[RawPosting]:
    addr = parseaddr(sender)[1].lower()
    if "linkedin.com" in addr:
        return parse_linkedin(html)
    if "handshake" in addr:
        return parse_handshake(html)
    return parse_linkedin(html) or parse_handshake(html)


class GmailAlerts(Source):
    name = "gmail_alerts"

    def enabled(self, cfg) -> bool:
        return cfg.sources.gmail_alerts.enabled

    def _fetch_sync(self, query: str) -> tuple[list[RawPosting], int]:
        from google.auth.exceptions import RefreshError
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build

        env = {k: os.environ.get(k) for k in ("GMAIL_OAUTH_CLIENT_ID", "GMAIL_OAUTH_CLIENT_SECRET", "GMAIL_REFRESH_TOKEN")}
        missing = [k for k, v in env.items() if not v]
        if missing:
            raise SourceFailed(f"missing secrets: {', '.join(missing)}")
        creds = Credentials(
            None,
            refresh_token=env["GMAIL_REFRESH_TOKEN"],
            client_id=env["GMAIL_OAUTH_CLIENT_ID"],
            client_secret=env["GMAIL_OAUTH_CLIENT_SECRET"],
            token_uri="https://oauth2.googleapis.com/token",
            scopes=SCOPES,
        )
        try:
            svc = build("gmail", "v1", credentials=creds, cache_discovery=False)
            ids: list[str] = []
            req = svc.users().messages().list(userId="me", q=query, maxResults=100)
            while req is not None and len(ids) < 300:
                resp = req.execute()
                ids += [m["id"] for m in resp.get("messages", [])]
                req = svc.users().messages().list_next(req, resp)
            out: list[RawPosting] = []
            for mid in ids:
                msg = svc.users().messages().get(userId="me", id=mid, format="full").execute()
                headers = {h["name"].lower(): h["value"] for h in msg["payload"].get("headers", [])}
                received = parse_dt(int(msg.get("internalDate", 0)))
                for p in parse_message(headers.get("from", ""), _html_body(msg["payload"])):
                    p.posted_at = p.posted_at or received
                    out.append(p)
            return out, len(ids)
        except RefreshError as e:
            raise SourceFailed(
                "Gmail refresh token expired or revoked. Re-run scripts/gmail_oauth_setup.py, and set the "
                "OAuth app's publishing status to 'In production' so tokens stop expiring after 7 days."
            ) from e

    async def fetch(self, ctx: Context, result: SourceResult) -> list[RawPosting]:
        try:
            import googleapiclient  # noqa: F401
        except ImportError as e:
            raise SourceFailed("Gmail support not installed (uv sync --extra gmail)") from e
        query = f"({ctx.cfg.sources.gmail_alerts.query}) after:{int(ctx.since.timestamp())}"
        postings, n_messages = await asyncio.to_thread(self._fetch_sync, query)
        result.checked = n_messages
        return postings
