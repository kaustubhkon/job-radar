"""Render the digest (HTML with inlined CSS for Gmail, plus a plain-text alternative)."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from jinja2 import Environment, FileSystemLoader

from ..config import Config
from ..models import Posting, RunReport

TEMPLATES = Path(__file__).parent / "templates"
_env = Environment(
    loader=FileSystemLoader(TEMPLATES),
    autoescape=lambda name: bool(name and name.endswith(".html.j2")),
    trim_blocks=True,
    lstrip_blocks=True,
)


@dataclass
class Email:
    subject: str
    html: str
    text: str


def _links(now: datetime) -> dict[str, str | None]:
    repo = os.environ.get("GITHUB_REPOSITORY")
    server = os.environ.get("GITHUB_SERVER_URL", "https://github.com")
    branch = os.environ.get("GITHUB_REF_NAME", "main")
    if not repo:
        return {"csv": None, "config": None, "runs": None}
    return {
        "csv": f"{server}/{repo}/blob/state/runs/{now:%Y-%m-%d}.csv",
        "config": f"{server}/{repo}/edit/{branch}/config.yaml",
        "runs": f"{server}/{repo}/actions",
    }


def _source_line(r) -> str:
    if r.ok:
        extra = f" · {len(r.warnings)} failed" if r.warnings else ""
        return f"{r.name} ✓ {r.count}{extra}"
    return f"{r.name} ✗ {r.error or 'failed'}"


def _posted(p: Posting, now: datetime) -> str:
    if not p.posted_at:
        return ""
    days = (now - p.posted_at).days
    if days <= 0:
        return "today"
    if days == 1:
        return "yesterday"
    if days < 30:
        return f"{days}d ago"
    return p.posted_at.strftime("%b %-d")


def _inline(html: str) -> str:
    try:
        from premailer import Premailer

        logging.getLogger("cssutils").setLevel(logging.CRITICAL)
        return Premailer(html, remove_classes=False, keep_style_tags=True, strip_important=False,
                         disable_validation=True, cssutils_logging_level=logging.CRITICAL).transform()
    except Exception:  # noqa: BLE001 - un-inlined HTML still renders in most clients
        return html


def _local(now: datetime, cfg: Config) -> datetime:
    try:
        return now.astimezone(ZoneInfo(cfg.email.timezone))
    except Exception:  # noqa: BLE001
        return now


def render_digest(
    postings: list[Posting],
    cfg: Config,
    report: RunReport,
    now: datetime,
    *,
    overflow: int = 0,
    kind: str = "digest",
    total_matched: int | None = None,
) -> Email:
    """postings: already sorted by score and capped. kind: digest | backfill | empty | failure."""
    local = _local(now, cfg)
    day = f"{local:%a %b} {local.day}"
    threshold = cfg.ranking.top_pick_threshold
    top = [p for p in postings if p.score >= threshold][: cfg.ranking.top_picks_max]
    top_ids = {p.id for p in top}
    sections = []
    for cat in cfg.categories():
        rows = [p for p in postings if (p.category or "Other") == cat and p.id not in top_ids]
        if rows:
            sections.append((cat, rows))
    known = set(cfg.categories())
    stray = [p for p in postings if (p.category or "Other") not in known and p.id not in top_ids]
    if stray:
        sections.append(("Other", stray))

    n = len(postings) + overflow
    if kind == "backfill":
        subject = f"Job Radar · backfill · top {len(postings)} of {total_matched or n} open roles"
    elif kind == "empty":
        subject = f"Job Radar · {day} · nothing new"
    elif kind == "failure":
        subject = f"Job Radar · {day} · every source failed"
    else:
        picks = f" ({len(top)} top pick{'s' if len(top) != 1 else ''})" if top else ""
        subject = f"Job Radar · {day} · {n} new{picks}"

    ctx = dict(
        cfg=cfg,
        report=report,
        kind=kind,
        day=day,
        top=top,
        sections=sections,
        overflow=overflow,
        total_matched=total_matched,
        links=_links(now),
        source_lines=[_source_line(r) for r in report.sources],
        failed_sources=[r for r in report.sources if not r.ok],
        posted=lambda p: _posted(p, now),
        threshold=threshold,
    )
    html = _inline(_env.get_template("digest.html.j2").render(**ctx))
    text = _env.get_template("digest.txt.j2").render(**ctx)
    return Email(subject=subject, html=html, text=text)
