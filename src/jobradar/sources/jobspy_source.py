"""Tier 2: LinkedIn / Indeed / Google / Glassdoor / ZipRecruiter via python-jobspy.

These sites' terms prohibit automated access and they block datacenter IPs. Off by default.
"""

from __future__ import annotations

import asyncio
import math
import os
from typing import Any

from ..models import RawPosting, SourceResult
from .base import Context, Source, SourceFailed, parse_dt


def _proxies() -> list[str] | None:
    raw = os.environ.get("PROXY_URLS", "").strip()
    return [p.strip() for p in raw.replace("\n", ",").split(",") if p.strip()] or None


def _val(row: dict, key: str) -> Any:
    v = row.get(key)
    if v is None:
        return None
    try:
        if isinstance(v, float) and math.isnan(v):
            return None
    except TypeError:
        pass
    s = str(v)
    return None if s in ("nan", "NaT", "None", "") else v


def _compensation(row: dict) -> str | None:
    lo, hi, interval = _val(row, "min_amount"), _val(row, "max_amount"), _val(row, "interval")
    if not lo and not hi:
        return None
    cur = _val(row, "currency") or "USD"
    rng = f"{float(lo):,.0f}–{float(hi):,.0f}" if lo and hi else f"{float(lo or hi):,.0f}"
    return f"{cur} {rng} {interval or ''}".strip()


def rows_to_postings(rows: list[dict], site: str) -> list[RawPosting]:
    out = []
    for r in rows:
        title, company = _val(r, "title"), _val(r, "company")
        url = _val(r, "job_url_direct") or _val(r, "job_url")
        if not (title and company and url):
            continue
        jt = _val(r, "job_type")
        out.append(
            RawPosting(
                title=str(title),
                company=str(company),
                url=str(url),
                source=f"jobspy:{site}",
                location=_val(r, "location"),
                is_remote=bool(_val(r, "is_remote")) or None,
                posted_at=parse_dt(str(_val(r, "date_posted"))) if _val(r, "date_posted") else None,
                description=_val(r, "description"),
                employment_type=str(jt) if jt else None,
                compensation=_compensation(r),
                extra={"board_url": _val(r, "job_url")},
            )
        )
    return out


class JobSpySource(Source):
    def __init__(self, site: str):
        self.site = site
        self.name = f"jobspy:{site}"

    def enabled(self, cfg) -> bool:
        js = cfg.sources.jobspy
        return js.enabled and self.site in js.sites

    def _scrape(self, term: str, location: str, hours: int, results: int) -> list[dict]:
        from jobspy import scrape_jobs

        kwargs: dict[str, Any] = dict(
            site_name=[self.site],
            search_term=term,
            location=location,
            results_wanted=results,
            hours_old=hours,
            country_indeed="USA",
            proxies=_proxies(),
            description_format="markdown",
            verbose=0,
        )
        if self.site == "google":
            kwargs["google_search_term"] = f"{term} jobs near {location} since {'yesterday' if hours <= 36 else 'last week'}"
        if self.site not in ("indeed", "linkedin"):
            # Indeed and LinkedIn only honour one of hours_old / job_type; we keep hours_old and
            # let our own filters decide what counts as an internship.
            kwargs["job_type"] = "internship"
        df = scrape_jobs(**kwargs)
        return df.to_dict(orient="records") if df is not None else []

    async def fetch(self, ctx: Context, result: SourceResult) -> list[RawPosting]:
        try:
            import jobspy  # noqa: F401
        except ImportError as e:
            raise SourceFailed("python-jobspy not installed (uv sync --extra jobspy)") from e
        js = ctx.cfg.sources.jobspy
        hours = max(js.hours_old, math.ceil((ctx.now - ctx.since).total_seconds() / 3600))
        queries = [(t, loc) for t in js.search_terms for loc in js.locations]
        result.checked = len(queries)
        out: list[RawPosting] = []
        for term, loc in queries:  # sequential: these sites rate-limit hard
            try:
                rows = await asyncio.to_thread(self._scrape, term, loc, hours, js.results_wanted)
                out += rows_to_postings(rows, self.site)
            except Exception as e:  # noqa: BLE001
                msg = str(e).splitlines()[0][:100] if str(e) else type(e).__name__
                if "429" in msg:
                    msg = "429 rate-limited"
                result.warnings.append(f"'{term}' @ {loc}: {msg}")
        if queries and len(result.warnings) == len(queries):
            raise SourceFailed(result.warnings[0].split(": ", 1)[-1])
        return out
