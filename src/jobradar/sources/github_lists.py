"""Crowd-maintained internship lists on GitHub (SimplifyJobs format)."""

from __future__ import annotations

import re

from selectolax.parser import HTMLParser

from ..models import RawPosting, SourceResult
from .base import Context, HttpError, Source, SourceFailed, parse_dt

BRANCHES = ("dev", "main", "master")


def parse_listings_json(data: list[dict], repo: str) -> list[RawPosting]:
    year = re.search(r"(20\d\d)", repo)
    out = []
    for item in data:
        if not item.get("active", True) or not item.get("is_visible", True):
            continue
        terms = item.get("terms") or []
        season = terms[0] if terms and terms[0] not in ("N/A", "") else None
        if not season and item.get("season") and year and str(item["season"]).lower() == "summer":
            season = f"Summer {year.group(1)}"
        out.append(
            RawPosting(
                title=item["title"],
                company=item["company_name"],
                url=item["url"],
                source="github_lists",
                locations=item.get("locations") or [],
                posted_at=parse_dt(item.get("date_posted")),
                employment_type="internship",
                season=season.replace(" ", "-").lower() if season else None,
                extra={"repo": repo},
            )
        )
    return out


def parse_readme(text: str, repo: str) -> list[RawPosting]:
    """Fallback: the HTML tables in the README (company | role | location | links | age)."""
    out = []
    tree = HTMLParser(text)
    company = ""
    for row in tree.css("tbody tr"):
        cells = row.css("td")
        if len(cells) < 4:
            continue
        name = cells[0].text(strip=True).replace("🔥", "").strip()
        if name and name != "↳":
            company = name
        if "🔒" in cells[3].text() or not company:
            continue  # closed
        link = next((a.attributes.get("href") for a in cells[3].css("a") if "simplify.jobs/p/" not in (a.attributes.get("href") or "")), None)
        if not link:
            continue
        locs = [s.strip() for s in re.split(r"<br\s*/?>|</br>", cells[2].html or "") if s.strip()]
        locs = [HTMLParser(l).text(strip=True) for l in locs] or [cells[2].text(strip=True)]
        out.append(
            RawPosting(
                title=cells[1].text(strip=True),
                company=company,
                url=link,
                source="github_lists",
                locations=[l for l in locs if l and not re.fullmatch(r"\d+ locations?", l, re.I)],
                employment_type="internship",
                extra={"repo": repo},
            )
        )
    return out


class GithubLists(Source):
    name = "github_lists"

    def enabled(self, cfg) -> bool:
        return cfg.sources.github_lists and bool(cfg.github_lists)

    async def _fetch_repo(self, ctx: Context, repo: str) -> list[RawPosting]:
        for branch in BRANCHES:
            url = f"https://raw.githubusercontent.com/{repo}/{branch}/.github/scripts/listings.json"
            try:
                return parse_listings_json(await ctx.http.get_json(url), repo)
            except HttpError as e:
                if e.status != 404:
                    raise
        for branch in BRANCHES:
            try:
                resp = await ctx.http.request("GET", f"https://raw.githubusercontent.com/{repo}/{branch}/README.md")
                return parse_readme(resp.text, repo)
            except HttpError as e:
                if e.status != 404:
                    raise
        raise HttpError(404, repo, "repo or listings not found (renamed for the new cycle?)")

    async def fetch(self, ctx: Context, result: SourceResult) -> list[RawPosting]:
        out: list[RawPosting] = []
        result.checked = len(ctx.cfg.github_lists)
        for repo in ctx.cfg.github_lists:
            try:
                out += await self._fetch_repo(ctx, repo)
            except Exception as e:  # noqa: BLE001
                result.warnings.append(f"{repo}: {e}")
        if result.warnings and len(result.warnings) == len(ctx.cfg.github_lists):
            raise SourceFailed(result.warnings[0])
        return out
