"""collect → normalize → dedupe → filter → score → email → persist."""

from __future__ import annotations

import asyncio
import dataclasses
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from . import emailer
from .config import Config
from .dedupe import merge_postings
from .emailer.render import Email, render_digest
from .filters import apply_filters
from .llm import llm_rank
from .models import Posting, RawPosting, RunReport, SourceResult
from .normalize import normalize, normalize_company
from .scoring import apply_rule_scores
from .sources import Context, Http, enabled_sources
from .sources.base import run_source
from .state import SeenStore, lookback_since, write_run_files


@dataclasses.dataclass
class RunResult:
    report: RunReport
    email: Email | None
    emailed: list[Posting]
    overflow: list[Posting]


async def collect(cfg: Config, store: SeenStore, now: datetime, http: Http | None = None) -> tuple[list[SourceResult], list[RawPosting], datetime]:
    own_http = http is None
    http = http or Http()
    lb = cfg.lookback
    base = Context(cfg=cfg, http=http, now=now, since=lookback_since(store, now, lb.default_hours, lb.buffer_hours, lb.max_days),
                   watchlist=cfg.all_watchlist())
    try:
        sources = enabled_sources(cfg)
        pairs = await asyncio.gather(
            *(
                run_source(s, dataclasses.replace(
                    base, since=lookback_since(store, now, lb.default_hours, lb.buffer_hours, lb.max_days, s.name)))
                for s in sources
            )
        )
    finally:
        if own_http:
            await http.aclose()
    return [r for r, _ in pairs], [p for _, ps in pairs for p in ps], base.since


def _should_send_empty(cfg: Config, now: datetime) -> bool:
    mode = cfg.email.send_when_empty
    if mode == "always":
        return True
    if mode == "never":
        return False
    try:
        local = now.astimezone(ZoneInfo(cfg.email.timezone))
    except Exception:  # noqa: BLE001
        local = now
    return local.weekday() == 0


async def process(
    raws: list[RawPosting],
    cfg: Config,
    store: SeenStore,
    report: RunReport,
    now: datetime,
    *,
    use_llm: bool = True,
) -> tuple[list[Posting], list[Posting], list[Posting]]:
    """Returns (ranked matches, llm-rejected, new-before-filtering)."""
    report.collected = len(raws)
    postings: list[Posting] = []
    for raw in raws:
        try:
            postings.append(normalize(raw, now))
        except Exception as e:  # noqa: BLE001 - one malformed posting shouldn't sink the run
            report.notes.append(f"Skipped a malformed posting from {raw.source}: {type(e).__name__}")
    merged = merge_postings(postings)
    report.unique = len(merged)

    fh = cfg.filter_hash()
    new = []
    for p in merged:
        if store.is_seen(p, fh):
            store.touch(p, now)
        else:
            new.append(p)
    report.new = len(new)

    matched = apply_filters(new, cfg, report)
    watch = {normalize_company(e.name) for e in cfg.all_watchlist()}
    apply_rule_scores(matched, cfg, now, watch)
    rejected: list[Posting] = []
    if use_llm:
        rejected = await llm_rank(matched, cfg, report)
    rejected_ids = {p.id for p in rejected}
    matched = [p for p in matched if p.id not in rejected_ids]
    matched.sort(key=lambda p: (p.score, p.posted_at or datetime.min.replace(tzinfo=timezone.utc)), reverse=True)
    report.matched = len(matched)
    return matched, rejected, new


async def run_pipeline(
    cfg: Config,
    state_dir: Path,
    *,
    mode: str = "run",  # run | backfill
    dry_run: bool = False,
    use_llm: bool = True,
    preview_path: Path | None = None,
    now: datetime | None = None,
    http: Http | None = None,
) -> RunResult:
    now = now or datetime.now(timezone.utc)
    store = SeenStore(state_dir / "seen.json")
    if mode == "run" and not store.existed:
        mode = "backfill"  # first run: seed state instead of emailing everything that's open
    report = RunReport(started_at=now, mode="dry-run" if dry_run else mode)

    results, raws, since = await collect(cfg, store, now, http)
    report.sources = results
    report.lookback_since = since
    matched, rejected, _ = await process(raws, cfg, store, report, now, use_llm=use_llm)

    cap = cfg.ranking.backfill_cap if mode == "backfill" else cfg.ranking.max_per_digest
    emailed, overflow = matched[:cap], matched[cap:]
    report.emailed, report.overflow = len(emailed), len(overflow)

    email: Email | None
    if report.all_sources_failed:
        kind = "failure"
    elif not emailed:
        kind = "empty" if _should_send_empty(cfg, now) else "none"
    else:
        kind = mode if mode == "backfill" else "digest"
    report.email_kind = kind
    email = None
    if kind != "none":
        email = render_digest(emailed, cfg, report, now, overflow=len(overflow), kind=kind, total_matched=len(matched))

    if dry_run:
        if email and preview_path:
            preview_path.write_text(email.html)
            preview_path.with_suffix(".txt").write_text(f"Subject: {email.subject}\n\n{email.text}")
        report.finished_at = datetime.now(timezone.utc)
        return RunResult(report, email, emailed, overflow)

    if email:
        emailer.send(email, cfg)  # raises on failure: state is not advanced, so nothing is lost
        report.email_sent = True

    fh = cfg.filter_hash()
    for p in emailed:
        store.add(p, "emailed", now, fh)
    for p in overflow:
        store.add(p, "seeded" if mode == "backfill" else "overflow", now, fh)
    for p in rejected:
        store.add(p, "llm_rejected", now, fh, reason=p.reason)
    for r in results:
        if r.ok:
            store.source_ok[r.name] = now.isoformat()
    store.prune(now, cfg.prune_days)
    report.finished_at = datetime.now(timezone.utc)
    rows = [(p, "emailed") for p in emailed] + [(p, "overflow") for p in overflow]
    write_run_files(state_dir, now, report, rows)
    if not report.all_sources_failed:
        store.save(now)
    return RunResult(report, email, emailed, overflow)
