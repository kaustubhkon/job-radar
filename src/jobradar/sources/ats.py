"""Public job-board APIs of the ATSes startups and mid-size companies use."""

from __future__ import annotations

import asyncio

from ..config import WatchlistEntry
from ..filters import looks_relevant, worth_details
from ..models import RawPosting
from .base import Context, WatchlistSource, parse_dt


class Greenhouse(WatchlistSource):
    name = ats = "greenhouse"

    async def fetch_board(self, ctx: Context, e: WatchlistEntry) -> list[RawPosting]:
        data = await ctx.http.get_json(f"https://boards-api.greenhouse.io/v1/boards/{e.slug}/jobs?content=true")
        out = []
        for j in data.get("jobs", []):
            emp = None
            for m in j.get("metadata") or []:
                if "employment" in (m.get("name") or "").lower() and isinstance(m.get("value"), str):
                    emp = m["value"]
            location = (j.get("location") or {}).get("name")
            # Offices are sometimes pay-zone labels ("US - Remote Zone 1"); only fall back to them.
            offices = [] if location else [o.get("name") for o in j.get("offices") or [] if o.get("name")]
            out.append(
                RawPosting(
                    title=j["title"],
                    company=e.name,
                    url=j["absolute_url"],
                    source="greenhouse",
                    location=location,
                    locations=offices,
                    posted_at=parse_dt(j.get("first_published") or j.get("updated_at")),
                    description=j.get("content"),
                    employment_type=emp,
                )
            )
        return out


class Lever(WatchlistSource):
    name = ats = "lever"

    async def fetch_board(self, ctx: Context, e: WatchlistEntry) -> list[RawPosting]:
        data = await ctx.http.get_json(f"https://api.lever.co/v0/postings/{e.slug}?mode=json")
        out = []
        for j in data:
            cats = j.get("categories") or {}
            lists = "\n".join(
                f"{l.get('text', '')}\n{l.get('content', '')}" for l in j.get("lists") or []
            )
            desc = "\n".join(filter(None, [j.get("descriptionPlain"), lists, j.get("additionalPlain")]))
            sal = j.get("salaryRange") or {}
            comp = None
            if sal.get("min"):
                comp = f"{sal.get('currency', '')} {sal['min']:,}–{sal.get('max', sal['min']):,} {sal.get('interval', '')}".strip()
            out.append(
                RawPosting(
                    title=j["text"],
                    company=e.name,
                    url=j["hostedUrl"],
                    source="lever",
                    location=cats.get("location"),
                    locations=cats.get("allLocations") or [],
                    is_remote=(j.get("workplaceType") == "remote") or None,
                    posted_at=parse_dt(j.get("createdAt")),
                    description=desc,
                    employment_type=cats.get("commitment"),
                    compensation=comp,
                )
            )
        return out


class Ashby(WatchlistSource):
    name = ats = "ashby"

    async def fetch_board(self, ctx: Context, e: WatchlistEntry) -> list[RawPosting]:
        data = await ctx.http.get_json(
            f"https://api.ashbyhq.com/posting-api/job-board/{e.slug}?includeCompensation=true"
        )
        out = []
        for j in data.get("jobs", []):
            if j.get("isListed") is False:
                continue
            comp = (j.get("compensation") or {}).get("compensationTierSummary")
            out.append(
                RawPosting(
                    title=j["title"],
                    company=e.name,
                    url=j.get("jobUrl") or j["applyUrl"],
                    source="ashby",
                    location=j.get("location"),
                    locations=[s.get("location") for s in j.get("secondaryLocations") or [] if s.get("location")],
                    is_remote=j.get("isRemote"),
                    posted_at=parse_dt(j.get("publishedAt")),
                    description=j.get("descriptionPlain") or j.get("descriptionHtml"),
                    employment_type=j.get("employmentType"),
                    compensation=comp,
                )
            )
        return out


class SmartRecruiters(WatchlistSource):
    name = ats = "smartrecruiters"
    queries = ("intern", "student", "part-time", "summer")
    max_pages = 3

    async def fetch_board(self, ctx: Context, e: WatchlistEntry) -> list[RawPosting]:
        base = f"https://api.smartrecruiters.com/v1/companies/{e.slug}/postings"
        found: dict[str, dict] = {}
        for q in self.queries:
            for page in range(self.max_pages):
                data = await ctx.http.get_json(base, params={"q": q, "limit": 100, "offset": page * 100})
                content = data.get("content", [])
                relevant = [
                    j for j in content
                    if looks_relevant(j["name"]) or "intern" in ((j.get("typeOfEmployment") or {}).get("label") or "").lower()
                ]
                for j in relevant:
                    found[j["id"]] = j
                if not relevant or (page + 1) * 100 >= data.get("totalFound", 0):
                    break

        async def detail(j: dict) -> RawPosting:
            desc = None
            url = f"https://jobs.smartrecruiters.com/{e.slug}/{j['id']}"
            try:
                if not worth_details(j["name"], ctx.cfg):
                    raise LookupError
                d = await ctx.http.get_json(f"{base}/{j['id']}")
                sections = (d.get("jobAd") or {}).get("sections") or {}
                desc = "\n".join(
                    (sections.get(k) or {}).get("text") or "" for k in ("jobDescription", "qualifications", "additionalInformation")
                )
                url = d.get("postingUrl") or url
            except Exception:  # noqa: BLE001 - listing data is enough to go on
                pass
            loc = j.get("location") or {}
            return RawPosting(
                title=j["name"],
                company=e.name,
                url=url,
                source="smartrecruiters",
                location=loc.get("fullLocation") or ", ".join(filter(None, [loc.get("city"), loc.get("region"), loc.get("country")])),
                is_remote=loc.get("remote"),
                posted_at=parse_dt(j.get("releasedDate")),
                description=desc,
                employment_type=(j.get("typeOfEmployment") or {}).get("label"),
            )

        return list(await asyncio.gather(*(detail(j) for j in found.values())))


class Workable(WatchlistSource):
    name = ats = "workable"

    async def fetch_board(self, ctx: Context, e: WatchlistEntry) -> list[RawPosting]:
        data = await ctx.http.get_json(f"https://apply.workable.com/api/v1/widget/accounts/{e.slug}?details=true")
        out = []
        for j in data.get("jobs", []):
            locs = [
                ", ".join(filter(None, [l.get("city"), l.get("region"), l.get("country")]))
                for l in j.get("locations") or []
            ]
            out.append(
                RawPosting(
                    title=j["title"],
                    company=e.name,
                    url=j.get("url") or j.get("shortlink"),
                    source="workable",
                    location=", ".join(filter(None, [j.get("city"), j.get("state"), j.get("country")])) or None,
                    locations=locs,
                    is_remote=j.get("telecommuting"),
                    posted_at=parse_dt(j.get("published_on") or j.get("created_at")),
                    description=j.get("description"),
                    employment_type=j.get("employment_type"),
                )
            )
        return out
