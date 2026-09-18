"""Career sites of banks and large companies: Workday, Oracle Cloud HCM, Eightfold.

These return thousands of jobs, so we search for student-ish terms, page by relevance until a page
has nothing relevant, and only fetch full descriptions for titles that could plausibly match.
"""

from __future__ import annotations

import asyncio
import re
from datetime import timedelta
from urllib.parse import quote

from ..config import WatchlistEntry
from ..filters import looks_relevant, worth_details
from ..models import RawPosting
from .base import Context, WatchlistSource, parse_dt

SEARCH_TERMS = ("intern", "summer analyst", "part-time student", "co-op")
MAX_PAGES = 5


def _relative_posted(text: str | None, ctx: Context):
    """Workday's 'Posted 3 Days Ago' / 'Posted Today' / 'Posted 30+ Days Ago'."""
    if not text:
        return None
    t = text.lower()
    if "today" in t:
        return ctx.now
    if "yesterday" in t:
        return ctx.now - timedelta(days=1)
    m = re.search(r"(\d+)\+?\s*day", t)
    return ctx.now - timedelta(days=int(m.group(1))) if m else None


class Workday(WatchlistSource):
    name = ats = "workday"
    timeout = 420.0  # dozens of big career sites, each paged by relevance

    async def fetch_board(self, ctx: Context, e: WatchlistEntry) -> list[RawPosting]:
        host = f"https://{e.tenant}.{e.wd}.myworkdayjobs.com"
        api = f"{host}/wday/cxs/{e.tenant}/{e.site}"
        listings: dict[str, dict] = {}
        for term in SEARCH_TERMS:
            total = None
            for page in range(MAX_PAGES):
                body = {"appliedFacets": {}, "limit": 20, "offset": page * 20, "searchText": term}
                data = await ctx.http.post_json(f"{api}/jobs", body)
                if total is None:
                    total = data.get("total", 0)  # only reported on the first page
                jobs = data.get("jobPostings") or []
                relevant = [j for j in jobs if j.get("title") and looks_relevant(j["title"])]
                for j in relevant:
                    listings[j["externalPath"]] = j
                if not relevant or (page + 1) * 20 >= (total or 0):
                    break

        async def detail(path: str, j: dict) -> RawPosting:
            info: dict = {}
            if worth_details(j["title"], ctx.cfg):
                try:
                    info = (await ctx.http.get_json(f"{api}{path}")).get("jobPostingInfo") or {}
                except Exception:  # noqa: BLE001
                    info = {}
            locs = [info.get("location") or j.get("locationsText")]
            locs += info.get("additionalLocations") or []
            return RawPosting(
                title=j["title"],
                company=e.name,
                url=info.get("externalUrl") or f"{host}/{e.site}{path}",
                source="workday",
                locations=[l for l in locs if l and not re.match(r"^\d+ Locations?$", l)],
                posted_at=parse_dt(info.get("startDate")) or _relative_posted(j.get("postedOn"), ctx),
                description=info.get("jobDescription"),
                employment_type=info.get("timeType"),
            )

        return list(await asyncio.gather(*(detail(p, j) for p, j in listings.items())))


class OracleHCM(WatchlistSource):
    """Oracle Cloud HCM candidate experience sites (e.g. JPMorgan: host jpmc.fa.oraclecloud.com, site CX_1001)."""

    name = ats = "oracle"
    timeout = 300.0

    async def fetch_board(self, ctx: Context, e: WatchlistEntry) -> list[RawPosting]:
        base = f"https://{e.host}/hcmRestApi/resources/latest"
        listings: dict[str, dict] = {}
        for term in ("intern", "summer analyst", "part-time"):
            for page in range(MAX_PAGES):
                finder = (
                    f"findReqs;siteNumber={e.site},limit=25,offset={page * 25},"
                    f"keyword=%22{quote(term)}%22,sortBy=RELEVANCY"
                )
                data = await ctx.http.get_json(
                    f"{base}/recruitingCEJobRequisitions?onlyData=true&expand=requisitionList.secondaryLocations&finder={finder}"
                )
                items = data.get("items") or [{}]
                reqs = items[0].get("requisitionList") or []
                relevant = [r for r in reqs if looks_relevant(r.get("Title", ""))]
                for r in relevant:
                    listings[str(r["Id"])] = r
                if not relevant or len(reqs) < 25:
                    break

        async def detail(rid: str, r: dict) -> RawPosting:
            desc = None
            if worth_details(r["Title"], ctx.cfg):
                try:
                    d = await ctx.http.get_json(
                        f"{base}/recruitingCEJobRequisitionDetails?expand=all&onlyData=true"
                        f"&finder=ById;Id=%22{rid}%22,siteNumber={e.site}"
                    )
                    it = (d.get("items") or [{}])[0]
                    desc = "\n".join(
                        it.get(k) or ""
                        for k in ("ExternalDescriptionStr", "ExternalResponsibilitiesStr", "ExternalQualificationsStr")
                    )
                except Exception:  # noqa: BLE001
                    pass
            secondary = [s.get("Name") for s in r.get("secondaryLocations") or [] if s.get("Name")]
            return RawPosting(
                title=r["Title"],
                company=e.name,
                url=f"https://{e.host}/hcmUI/CandidateExperience/en/sites/{e.site}/job/{rid}",
                source="oracle",
                location=r.get("PrimaryLocation"),
                locations=secondary,
                is_remote=(r.get("WorkplaceTypeCode") or "").upper().startswith("ORA_REMOTE") or None,
                posted_at=parse_dt(r.get("PostedDate")),
                description=desc or r.get("ShortDescriptionStr"),
                employment_type=r.get("JobSchedule") or r.get("WorkerType"),
            )

        return list(await asyncio.gather(*(detail(k, r) for k, r in listings.items())))


class Eightfold(WatchlistSource):
    """Eightfold career sites (e.g. Morgan Stanley). Tries the newer PCSX API, then the older v2 API."""

    name = ats = "eightfold"
    timeout = 300.0

    async def fetch_board(self, ctx: Context, e: WatchlistEntry) -> list[RawPosting]:
        try:
            return await self._pcsx(ctx, e)
        except Exception:  # noqa: BLE001
            return await self._v2(ctx, e)

    async def _pcsx(self, ctx: Context, e: WatchlistEntry) -> list[RawPosting]:
        base = f"https://{e.host}/api/pcsx"
        found: dict[int, dict] = {}
        for term in ("intern", "summer analyst", "part-time"):
            for page in range(MAX_PAGES):
                data = await ctx.http.get_json(
                    f"{base}/search", params={"domain": e.domain, "query": term, "start": page * 10}
                )
                if data.get("status") not in (None, 200) or "data" not in data:
                    raise RuntimeError("pcsx unavailable")
                positions = (data["data"] or {}).get("positions") or []
                relevant = [p for p in positions if looks_relevant(p.get("name", ""))]
                for p in relevant:
                    found[p["id"]] = p
                if not relevant or len(positions) < 10:
                    break

        async def detail(p: dict) -> RawPosting:
            desc = None
            if worth_details(p["name"], ctx.cfg):
                try:
                    d = await ctx.http.get_json(
                        f"{base}/position_details", params={"position_id": p["id"], "domain": e.domain, "hl": "en"}
                    )
                    desc = (d.get("data") or {}).get("jobDescription")
                except Exception:  # noqa: BLE001
                    pass
            return RawPosting(
                title=p["name"],
                company=e.name,
                url=f"https://{e.host}{p.get('positionUrl') or f'/careers/job/{p['id']}'}",
                source="eightfold",
                locations=p.get("standardizedLocations") or p.get("locations") or [],
                is_remote=(p.get("workLocationOption") == "remote") or None,
                posted_at=parse_dt(p.get("postedTs") or p.get("creationTs")),
                description=desc,
            )

        return list(await asyncio.gather(*(detail(p) for p in found.values())))

    async def _v2(self, ctx: Context, e: WatchlistEntry) -> list[RawPosting]:
        found: dict[int, dict] = {}
        for term in ("intern", "summer analyst"):
            for page in range(MAX_PAGES):
                data = await ctx.http.get_json(
                    f"https://{e.host}/api/apply/v2/jobs",
                    params={"domain": e.domain, "query": term, "start": page * 10, "num": 10},
                )
                positions = data.get("positions") or []
                relevant = [p for p in positions if looks_relevant(p.get("name", ""))]
                for p in relevant:
                    found[p["id"]] = p
                if not relevant or len(positions) < 10:
                    break
        return [
            RawPosting(
                title=p["name"],
                company=e.name,
                url=p.get("canonicalPositionUrl") or f"https://{e.host}/careers?pid={p['id']}",
                source="eightfold",
                locations=p.get("locations") or ([p["location"]] if p.get("location") else []),
                posted_at=parse_dt(p.get("t_create")),
                description=p.get("job_description"),
            )
            for p in found.values()
        ]
