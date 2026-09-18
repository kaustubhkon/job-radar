"""Deciding when two postings are the same job, within a run and against seen.json."""

from __future__ import annotations

from difflib import SequenceMatcher

from .models import Location, Posting
from .normalize import normalize_company, normalize_title, url_key

ATS_SOURCES = {"greenhouse", "lever", "ashby", "workday", "smartrecruiters", "workable", "oracle", "eightfold"}


def source_rank(source: str) -> int:
    """Lower is better. Company ATS links beat curated lists, which beat alert emails and job boards."""
    base = source.split(":")[0]
    if base in ATS_SOURCES:
        return 0
    if base == "github_lists":
        return 1
    if base == "gmail":
        return 2
    return 3


def loc_keys(locs: list[Location]) -> set[str]:
    keys = set()
    for loc in locs:
        if loc.city:
            keys.add(loc.city)
        elif loc.state:
            keys.add(f"state:{loc.state}")
        elif loc.remote:
            keys.add("remote")
        elif loc.country:
            keys.add(f"country:{loc.country}")
    return keys


def locations_compatible(a: set[str], b: set[str]) -> bool:
    if not a or not b or a & b:
        return True
    vague = {"remote", "country:US"}
    # "Remote" or "United States" on one side is compatible with anything US on the other.
    if a <= vague or b <= vague:
        return True
    return False


def companies_match(a: str, b: str) -> bool:
    if a == b:
        return True
    if not a or not b:
        return False
    wa, wb = a.split(), b.split()
    short, long_ = (wa, wb) if len(wa) <= len(wb) else (wb, wa)
    return long_[: len(short)] == short and len("".join(short)) >= 4


def titles_similar(a: str, b: str) -> bool:
    if a == b:
        return True
    ta, tb = a.split(), b.split()
    if SequenceMatcher(None, " ".join(sorted(ta)), " ".join(sorted(tb))).ratio() >= 0.9:
        return True
    sa, sb = set(ta), set(tb)
    small, big = (sa, sb) if len(sa) <= len(sb) else (sb, sa)
    return len(small) >= 2 and small <= big and len(big - small) <= 2


def seasons_compatible(a: str | None, b: str | None) -> bool:
    return a is None or b is None or a == b


class Fingerprint:
    """The fields used to compare postings, precomputed once."""

    __slots__ = ("company", "title", "season", "locs", "urls")

    def __init__(self, company: str, title: str, season: str | None, locs: set[str], urls: set[str]):
        self.company = company
        self.title = title
        self.season = season
        self.locs = locs
        self.urls = urls

    @classmethod
    def of(cls, p: Posting, extra_urls: list[str] | None = None) -> Fingerprint:
        urls = {url_key(p.url)} | {url_key(u) for u in (extra_urls or [])}
        return cls(normalize_company(p.company), normalize_title(p.title), p.season, loc_keys(p.locations_parsed), urls)

    def to_json(self) -> dict:
        return {"c": self.company, "t": self.title, "s": self.season, "l": sorted(self.locs), "u": sorted(self.urls)}

    @classmethod
    def from_json(cls, d: dict) -> Fingerprint:
        return cls(d["c"], d["t"], d.get("s"), set(d.get("l", [])), set(d.get("u", [])))

    def same_job(self, other: Fingerprint) -> bool:
        if self.urls & other.urls:
            return True
        return (
            companies_match(self.company, other.company)
            and seasons_compatible(self.season, other.season)
            and titles_similar(self.title, other.title)
            and locations_compatible(self.locs, other.locs)
        )


def _merge_into(best: Posting, other: Posting) -> None:
    for src in other.sources:
        if src not in best.sources:
            best.sources.append(src)
    if not best.description and other.description:
        best.description = other.description
    if other.posted_at and (best.posted_at is None or other.posted_at < best.posted_at):
        best.posted_at = other.posted_at
    if not best.compensation and other.compensation:
        best.compensation = other.compensation
    if not best.season and other.season:
        best.season = other.season
    if not best.locations_parsed and other.locations_parsed:
        best.locations_parsed = other.locations_parsed
        best.location = other.location
    if not best.employment_type and other.employment_type:
        best.employment_type = other.employment_type


def merge_postings(postings: list[Posting]) -> list[Posting]:
    """Collapse postings that are the same job, keeping the company-ATS link when there is one."""
    ordered = sorted(postings, key=lambda p: (source_rank(p.source), p.source))
    kept: list[tuple[Posting, Fingerprint]] = []
    by_company: dict[str, list[int]] = {}
    by_url: dict[str, int] = {}
    for p in ordered:
        fp = Fingerprint.of(p)
        hit: int | None = None
        for u in fp.urls:
            if u in by_url:
                hit = by_url[u]
                break
        if hit is None:
            first = fp.company.split()[0] if fp.company else ""
            for idx in by_company.get(first, []):
                if kept[idx][1].same_job(fp):
                    hit = idx
                    break
        if hit is None:
            kept.append((p, fp))
            idx = len(kept) - 1
            by_company.setdefault(fp.company.split()[0] if fp.company else "", []).append(idx)
            for u in fp.urls:
                by_url[u] = idx
        else:
            best, best_fp = kept[hit]
            _merge_into(best, p)
            best_fp.urls |= fp.urls
            best_fp.locs |= fp.locs
            for u in fp.urls:
                by_url[u] = hit
    return [p for p, _ in kept]
