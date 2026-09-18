"""Core data types shared across the pipeline."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class Location(BaseModel):
    city: str | None = None  # lowercase, e.g. "austin"
    state: str | None = None  # two-letter US code, e.g. "TX"
    country: str | None = None  # "US" when known to be US, otherwise a best-effort code/name
    remote: bool = False
    raw: str = ""

    @property
    def is_us(self) -> bool:
        return self.country == "US" or self.state is not None

    def label(self) -> str:
        if self.remote and not self.city:
            return "Remote" + (" (US)" if self.country == "US" else "")
        parts = [p for p in (self.city.title() if self.city else None, self.state) if p]
        return ", ".join(parts) or self.raw


class RawPosting(BaseModel):
    """What a source returns before normalization. Only title/company/url are required."""

    title: str
    company: str
    url: str
    source: str
    location: str | None = None
    locations: list[str] = Field(default_factory=list)
    is_remote: bool | None = None
    posted_at: datetime | None = None
    description: str | None = None  # plain text or HTML; normalize() handles both
    employment_type: str | None = None
    season: str | None = None  # a hint from the source, e.g. Simplify "terms"
    compensation: str | None = None
    extra: dict[str, Any] = Field(default_factory=dict)


class Posting(BaseModel):
    id: str
    title: str
    company: str
    location: str | None
    locations_parsed: list[Location] = Field(default_factory=list)
    is_remote: bool | None = None
    url: str
    source: str
    sources: list[str] = Field(default_factory=list)
    posted_at: datetime | None = None
    first_seen_at: datetime
    description: str | None = None
    employment_type: str | None = None
    season: str | None = None
    compensation: str | None = None

    # Filled in by filters/scoring.
    posting_type: str | None = None
    family: str | None = None
    family_match: str | None = None  # "title" | "description"
    location_tier: str | None = None  # "preferred" | "accepted" | "unclear"
    on_watchlist: bool = False
    class_year_adj: int = 0
    class_year_note: str | None = None
    rule_score: float = 0.0
    llm_fit: float | None = None
    category: str | None = None
    reason: str | None = None
    score: float = 0.0

    def location_label(self) -> str:
        if self.locations_parsed:
            labels = []
            for loc in self.locations_parsed:
                label = loc.label()
                if label and label not in labels:
                    labels.append(label)
            if len(labels) > 3:
                return ", ".join(labels[:3]) + f" +{len(labels) - 3}"
            if labels:
                return " · ".join(labels)
        return self.location or "Location not listed"


class SourceResult(BaseModel):
    name: str
    ok: bool = True
    count: int = 0  # postings returned
    checked: int = 0  # boards/queries attempted
    error: str | None = None
    warnings: list[str] = Field(default_factory=list)
    seconds: float = 0.0


class Drop(BaseModel):
    id: str
    title: str
    company: str
    reason: str
    detail: str | None = None


class RunReport(BaseModel):
    started_at: datetime
    finished_at: datetime | None = None
    mode: str = "run"  # run | backfill | dry-run
    lookback_since: datetime | None = None
    sources: list[SourceResult] = Field(default_factory=list)
    collected: int = 0
    unique: int = 0
    new: int = 0
    matched: int = 0
    emailed: int = 0
    overflow: int = 0
    drop_counts: dict[str, int] = Field(default_factory=dict)
    drops: list[Drop] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    email_sent: bool = False
    email_kind: str | None = None  # digest | backfill | empty | failure | none

    def drop(self, posting: Posting, reason: str, detail: str | None = None) -> None:
        self.drop_counts[reason] = self.drop_counts.get(reason, 0) + 1
        self.drops.append(
            Drop(id=posting.id, title=posting.title, company=posting.company, reason=reason, detail=detail)
        )

    @property
    def all_sources_failed(self) -> bool:
        return bool(self.sources) and not any(s.ok for s in self.sources)
