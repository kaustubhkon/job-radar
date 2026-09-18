"""seen.json and per-run logs. The workflow keeps these on an orphan `state` branch checked out at ./state."""

from __future__ import annotations

import csv
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .dedupe import Fingerprint
from .models import Posting, RunReport

SEEN_STATUSES = {"emailed", "overflow", "seeded", "llm_rejected"}


class SeenStore:
    def __init__(self, path: Path):
        self.path = path
        self.entries: dict[str, dict] = {}
        self.last_successful_run_at: datetime | None = None
        self.source_ok: dict[str, str] = {}  # source name -> ISO time it last succeeded
        self.existed = path.exists()
        if self.existed:
            data = json.loads(path.read_text() or "{}")
            self.entries = data.get("entries", {})
            ts = data.get("last_successful_run_at")
            self.last_successful_run_at = datetime.fromisoformat(ts) if ts else None
            self.source_ok = data.get("source_ok", {})
        self._fps = {k: Fingerprint.from_json(v) for k, v in self.entries.items()}
        self._by_url: dict[str, str] = {}
        self._by_company: dict[str, list[str]] = {}
        for k, fp in self._fps.items():
            self._index(k, fp)

    def _index(self, key: str, fp: Fingerprint) -> None:
        for u in fp.urls:
            self._by_url[u] = key
        first = fp.company.split()[0] if fp.company else ""
        self._by_company.setdefault(first, []).append(key)

    def find(self, p: Posting) -> str | None:
        fp = Fingerprint.of(p)
        for u in fp.urls:
            if self._by_url.get(u) in self._fps:
                return self._by_url[u]
        if p.id in self._fps:
            return p.id
        first = fp.company.split()[0] if fp.company else ""
        for key in self._by_company.get(first, []):
            if key in self._fps and self._fps[key].same_job(fp):
                return key
        return None

    def is_seen(self, p: Posting, filter_hash: str) -> bool:
        key = self.find(p)
        if key is None:
            return False
        entry = self.entries[key]
        if entry.get("st") == "llm_rejected" and entry.get("fh") != filter_hash:
            return False  # config changed since the model rejected it; look again
        return entry.get("st") in SEEN_STATUSES

    def touch(self, p: Posting, now: datetime) -> None:
        """Record that a known posting is still live, so it isn't pruned while it stays up."""
        key = self.find(p)
        if key:
            self.entries[key]["ls"] = now.date().isoformat()

    def add(self, p: Posting, status: str, now: datetime, filter_hash: str, reason: str | None = None) -> None:
        key = self.find(p) or p.id
        fp = Fingerprint.of(p)
        old = self.entries.get(key, {})
        entry = fp.to_json() | {
            "title": p.title,
            "company": p.company,
            "fs": old.get("fs", now.date().isoformat()),
            "ls": now.date().isoformat(),
            "st": status,
            "fh": filter_hash,
        }
        if reason:
            entry["r"] = reason
        self.entries[key] = entry
        self._fps[key] = fp
        self._index(key, fp)

    def prune(self, now: datetime, days: int) -> int:
        """Drop entries not seen live for `days` days (by last-seen, not first-seen)."""
        cutoff = (now - timedelta(days=days)).date().isoformat()
        stale = [k for k, v in self.entries.items() if v.get("ls", v.get("fs", "")) < cutoff]
        for k in stale:
            del self.entries[k]
            self._fps.pop(k, None)
        return len(stale)

    def save(self, now: datetime) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "version": 1,
            "last_successful_run_at": now.isoformat(),
            "source_ok": dict(sorted(self.source_ok.items())),
            "entries": dict(sorted(self.entries.items())),
        }
        self.path.write_text(json.dumps(data, indent=0, sort_keys=False, ensure_ascii=False) + "\n")
        self.last_successful_run_at = now


def lookback_since(
    store: SeenStore, now: datetime, default_hours: int, buffer_hours: int, max_days: int, source: str | None = None
) -> datetime:
    """How far back time-windowed sources (job boards, alert emails) should look.

    Covers everything since that source last succeeded plus a buffer, so weekends, skipped
    runs, failed sources and GitHub's delayed crons never leave a gap.
    """
    last = store.source_ok.get(source) if source else None
    last_dt = datetime.fromisoformat(last) if last else store.last_successful_run_at
    if last_dt is None:
        since = now - timedelta(hours=default_hours)
    else:
        since = last_dt - timedelta(hours=buffer_hours)
        since = min(since, now - timedelta(hours=default_hours))
    return max(since, now - timedelta(days=max_days))


CSV_FIELDS = ["score", "category", "company", "title", "location", "season", "posted_at", "url", "sources", "reason", "status"]


def write_run_files(state_dir: Path, now: datetime, report: RunReport, rows: list[tuple[Posting, str]]) -> tuple[Path, Path]:
    runs = state_dir / "runs"
    runs.mkdir(parents=True, exist_ok=True)
    stamp = now.strftime("%Y-%m-%d")
    json_path = runs / f"{stamp}.json"
    csv_path = runs / f"{stamp}.csv"
    report_data = report.model_dump(mode="json")
    report_data["drops"] = report_data["drops"][:3000]
    json_path.write_text(json.dumps(report_data, indent=1, ensure_ascii=False) + "\n")
    with csv_path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        w.writeheader()
        for p, status in rows:
            w.writerow(
                {
                    "score": round(p.score),
                    "category": p.category,
                    "company": p.company,
                    "title": p.title,
                    "location": p.location_label(),
                    "season": p.season or "",
                    "posted_at": p.posted_at.date().isoformat() if p.posted_at else "",
                    "url": p.url,
                    "sources": " ".join(p.sources),
                    "reason": p.reason or "",
                    "status": status,
                }
            )
    return json_path, csv_path
