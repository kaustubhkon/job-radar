"""Source protocol, shared HTTP client, and the runner that keeps one bad source from killing the run."""

from __future__ import annotations

import asyncio
import os
import random
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from urllib.parse import urlsplit

import httpx

from ..config import Config, WatchlistEntry
from ..models import RawPosting, SourceResult

REQUEST_TIMEOUT = 20.0
SOURCE_TIMEOUT = 180.0
MAX_RETRIES = 3
CONCURRENCY = 8
PER_HOST_DELAY = 0.25


def user_agent() -> str:
    repo = os.environ.get("GITHUB_REPOSITORY", "job-radar")
    return f"JobRadar/0.1 (personal internship digest; +https://github.com/{repo})"


class SourceFailed(Exception):
    """A source-level failure with a message meant for the email footer as-is."""


class HttpError(Exception):
    def __init__(self, status: int, url: str, detail: str = ""):
        self.status = status
        self.url = url
        super().__init__(f"HTTP {status}{' ' + detail if detail else ''}")


class Http:
    """httpx wrapper: global concurrency limit, polite per-host spacing, retries on 429/5xx."""

    def __init__(self, client: httpx.AsyncClient | None = None):
        self.client = client or httpx.AsyncClient(
            timeout=REQUEST_TIMEOUT,
            follow_redirects=True,
            headers={"User-Agent": user_agent(), "Accept": "application/json"},
        )
        self._sem = asyncio.Semaphore(CONCURRENCY)
        self._host_locks: dict[str, asyncio.Lock] = {}
        self._host_last: dict[str, float] = {}

    async def _space(self, host: str) -> None:
        lock = self._host_locks.setdefault(host, asyncio.Lock())
        async with lock:
            wait = self._host_last.get(host, 0) + PER_HOST_DELAY - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            self._host_last[host] = time.monotonic()

    async def request(self, method: str, url: str, **kw: Any) -> httpx.Response:
        host = urlsplit(url).netloc
        last: Exception | None = None
        for attempt in range(MAX_RETRIES + 1):
            await self._space(host)
            async with self._sem:
                try:
                    resp = await self.client.request(method, url, **kw)
                except (httpx.TransportError, httpx.TimeoutException) as e:
                    last = e
                    resp = None
            if resp is not None:
                if resp.status_code < 400:
                    return resp
                if resp.status_code != 429 and resp.status_code < 500:
                    raise HttpError(resp.status_code, url)
                last = HttpError(resp.status_code, url, "rate-limited" if resp.status_code == 429 else "")
                retry_after = resp.headers.get("retry-after", "")
                delay = float(retry_after) if retry_after.isdigit() else None
            else:
                delay = None
            if attempt < MAX_RETRIES:
                await asyncio.sleep(min(delay or (2**attempt + random.random()), 20))
        assert last is not None
        raise last

    async def get_json(self, url: str, **kw: Any) -> Any:
        return (await self.request("GET", url, **kw)).json()

    async def post_json(self, url: str, body: Any, **kw: Any) -> Any:
        return (await self.request("POST", url, json=body, **kw)).json()

    async def aclose(self) -> None:
        await self.client.aclose()


@dataclass
class Context:
    cfg: Config
    http: Http
    now: datetime
    since: datetime  # lookback start for time-windowed sources
    watchlist: list[WatchlistEntry] = field(default_factory=list)


class Source:
    name: str = "source"
    timeout: float = SOURCE_TIMEOUT

    def enabled(self, cfg: Config) -> bool:
        raise NotImplementedError

    async def fetch(self, ctx: Context, result: SourceResult) -> list[RawPosting]:
        raise NotImplementedError


class WatchlistSource(Source):
    """A source that checks each watchlist company on one ATS."""

    ats: str = ""

    def enabled(self, cfg: Config) -> bool:
        return bool(getattr(cfg.sources, self.ats)) and any(e.ats == self.ats for e in cfg.all_watchlist())

    async def fetch_board(self, ctx: Context, entry: WatchlistEntry) -> list[RawPosting]:
        raise NotImplementedError

    async def fetch(self, ctx: Context, result: SourceResult) -> list[RawPosting]:
        entries = [e for e in ctx.watchlist if e.ats == self.ats]
        result.checked = len(entries)
        if not entries:
            return []

        async def one(e: WatchlistEntry) -> list[RawPosting]:
            try:
                return await self.fetch_board(ctx, e)
            except Exception as ex:  # noqa: BLE001
                result.warnings.append(f"{e.name}: {describe_error(ex)}")
                return []

        chunks = await asyncio.gather(*(one(e) for e in entries))
        if len(result.warnings) == len(entries):
            reasons = {w.split(": ", 1)[-1] for w in result.warnings}
            if len(entries) == 1 or len(reasons) == 1:
                raise SourceFailed(reasons.pop())
            raise SourceFailed(f"all {len(entries)} boards failed ({result.warnings[0]})")
        return [p for chunk in chunks for p in chunk]


def describe_error(e: BaseException) -> str:
    if isinstance(e, SourceFailed):
        return str(e)
    if isinstance(e, HttpError):
        return {404: "404 not found", 429: "429 rate-limited", 403: "403 blocked"}.get(e.status, str(e))
    if isinstance(e, (asyncio.TimeoutError, httpx.TimeoutException)):
        return "timed out"
    msg = str(e).strip().splitlines()[0] if str(e).strip() else ""
    return f"{type(e).__name__}{': ' + msg[:120] if msg else ''}"


async def run_source(source: Source, ctx: Context) -> tuple[SourceResult, list[RawPosting]]:
    result = SourceResult(name=source.name)
    start = time.monotonic()
    try:
        postings = await asyncio.wait_for(source.fetch(ctx, result), timeout=source.timeout)
        result.count = len(postings)
    except Exception as e:  # noqa: BLE001 - fail soft, report in the email footer
        result.ok = False
        result.error = describe_error(e)
        postings = []
    result.seconds = round(time.monotonic() - start, 1)
    return result, postings


async def run_sources(sources: list[Source], ctx: Context) -> tuple[list[SourceResult], list[RawPosting]]:
    pairs = await asyncio.gather(*(run_source(s, ctx) for s in sources))
    results = [r for r, _ in pairs]
    postings = [p for _, ps in pairs for p in ps]
    return results, postings


def parse_dt(value: Any) -> datetime | None:
    """ISO strings, dates, or epoch seconds/milliseconds -> aware datetime."""
    from datetime import timezone

    if value in (None, ""):
        return None
    try:
        if isinstance(value, (int, float)) or (isinstance(value, str) and value.isdigit()):
            n = float(value)
            return datetime.fromtimestamp(n / 1000 if n > 1e11 else n, tz=timezone.utc)
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (ValueError, OSError, OverflowError):
        return None
