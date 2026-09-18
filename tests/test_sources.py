"""Each source parser against recorded (trimmed) real API responses."""

import asyncio
from datetime import timedelta

import httpx
import respx

from jobradar.config import WatchlistEntry
from jobradar.models import SourceResult
from jobradar.normalize import normalize
from jobradar.sources import Context, Http
from jobradar.sources.ats import Ashby, Greenhouse, Lever, SmartRecruiters, Workable
from jobradar.sources.base import run_source
from jobradar.sources.enterprise import Eightfold, OracleHCM, Workday
from jobradar.sources.github_lists import GithubLists, parse_listings_json, parse_readme
from jobradar.sources.jobspy_source import rows_to_postings

from .conftest import NOW, load_fixture


def ctx(cfg, entries):
    return Context(cfg=cfg, http=Http(httpx.AsyncClient()), now=NOW, since=NOW - timedelta(hours=36), watchlist=entries)


def fetch(source, cfg, entries):
    async def go():
        c = ctx(cfg, entries)
        try:
            return await run_source(source, c)
        finally:
            await c.http.aclose()

    return asyncio.run(go())


@respx.mock
def test_greenhouse(cfg):
    respx.get("https://boards-api.greenhouse.io/v1/boards/mercury/jobs?content=true").respond(json=load_fixture("greenhouse_mercury.json"))
    result, posts = fetch(Greenhouse(), cfg, [WatchlistEntry(name="Mercury", ats="greenhouse", slug="mercury")])
    assert result.ok and result.count == len(posts) > 0
    p = normalize(posts[0], NOW)
    assert p.company == "Mercury" and p.url.startswith("https://job-boards.greenhouse.io/mercury/")
    assert p.description and "<" not in p.description
    assert p.posted_at is not None


@respx.mock
def test_lever(cfg):
    respx.get("https://api.lever.co/v0/postings/palantir?mode=json").respond(json=load_fixture("lever_palantir.json"))
    result, posts = fetch(Lever(), cfg, [WatchlistEntry(name="Palantir", ats="lever", slug="palantir")])
    assert result.ok and posts
    assert all(p.url.startswith("https://jobs.lever.co/palantir/") for p in posts)
    assert posts[0].employment_type  # commitment


@respx.mock
def test_ashby(cfg):
    respx.get("https://api.ashbyhq.com/posting-api/job-board/ramp?includeCompensation=true").respond(json=load_fixture("ashby_ramp.json"))
    result, posts = fetch(Ashby(), cfg, [WatchlistEntry(name="Ramp", ats="ashby", slug="ramp")])
    assert result.ok and posts
    assert any(p.compensation for p in posts)
    assert any(p.locations for p in posts)  # secondary locations


@respx.mock
def test_workable(cfg):
    respx.get("https://apply.workable.com/api/v1/widget/accounts/huggingface?details=true").respond(json=load_fixture("workable_huggingface.json"))
    result, posts = fetch(Workable(), cfg, [WatchlistEntry(name="Hugging Face", ats="workable", slug="huggingface")])
    assert result.ok and len(posts) == 3


@respx.mock
def test_smartrecruiters_searches_and_filters_titles(cfg):
    route = respx.get(url__startswith="https://api.smartrecruiters.com/v1/companies/BoschGroup/postings")
    route.side_effect = lambda req: httpx.Response(
        200, json=load_fixture("smartrecruiters_bosch_search.json") if "q=" in str(req.url) else {"jobAd": {"sections": {}}}
    )
    result, posts = fetch(SmartRecruiters(), cfg, [WatchlistEntry(name="Bosch", ats="smartrecruiters", slug="BoschGroup")])
    assert result.ok
    assert posts and all("intern" in p.title.lower() or "student" in p.title.lower() or "part" in p.title.lower() or "summer" in p.title.lower() for p in posts)


@respx.mock
def test_workday_pages_and_fetches_details(cfg):
    search = load_fixture("workday_nvidia_search.json")
    detail = load_fixture("workday_nvidia_detail.json")
    api = "https://nvidia.wd5.myworkdayjobs.com/wday/cxs/nvidia/NVIDIAExternalCareerSite"
    respx.post(f"{api}/jobs").respond(json=search)
    respx.get(url__startswith=f"{api}/job/").respond(json=detail)
    entry = WatchlistEntry(name="NVIDIA", ats="workday", tenant="nvidia", wd="wd5", site="NVIDIAExternalCareerSite")
    result, posts = fetch(Workday(), cfg, [entry])
    assert result.ok and posts
    assert all(p.url.startswith("https://nvidia.wd5.myworkdayjobs.com/") for p in posts)
    assert all(p.posted_at for p in posts)


@respx.mock
def test_oracle_hcm(cfg):
    respx.get(url__startswith="https://jpmc.fa.oraclecloud.com/hcmRestApi/resources/latest/recruitingCEJobRequisitions").respond(
        json=load_fixture("oracle_jpmc_search.json"))
    respx.get(url__startswith="https://jpmc.fa.oraclecloud.com/hcmRestApi/resources/latest/recruitingCEJobRequisitionDetails").respond(
        json={"items": [{"ExternalDescriptionStr": "<p>Summer analyst program</p>"}]})
    entry = WatchlistEntry(name="JPMorgan Chase", ats="oracle", host="jpmc.fa.oraclecloud.com", site="CX_1001")
    result, posts = fetch(OracleHCM(), cfg, [entry])
    assert result.ok and posts
    assert posts[0].url.startswith("https://jpmc.fa.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1001/job/")


@respx.mock
def test_eightfold_pcsx(cfg):
    respx.get(url__startswith="https://morganstanley.eightfold.ai/api/pcsx/search").respond(json=load_fixture("eightfold_ms_search.json"))
    respx.get(url__startswith="https://morganstanley.eightfold.ai/api/pcsx/position_details").respond(
        json={"status": 200, "data": {"jobDescription": "<p>Program details</p>"}})
    entry = WatchlistEntry(name="Morgan Stanley", ats="eightfold", host="morganstanley.eightfold.ai", domain="morganstanley.com")
    result, posts = fetch(Eightfold(), cfg, [entry])
    assert result.ok and posts
    assert posts[0].url.startswith("https://morganstanley.eightfold.ai/careers/job/")


@respx.mock
def test_one_dead_board_is_a_warning_not_a_failure(cfg):
    respx.get("https://boards-api.greenhouse.io/v1/boards/mercury/jobs?content=true").respond(json=load_fixture("greenhouse_mercury.json"))
    respx.get("https://boards-api.greenhouse.io/v1/boards/gone/jobs?content=true").respond(404)
    entries = [WatchlistEntry(name="Mercury", ats="greenhouse", slug="mercury"), WatchlistEntry(name="Gone", ats="greenhouse", slug="gone")]
    result, posts = fetch(Greenhouse(), cfg, entries)
    assert result.ok and posts
    assert result.warnings == ["Gone: 404 not found"]


@respx.mock
def test_all_boards_dead_fails_the_source(cfg):
    respx.get(url__startswith="https://boards-api.greenhouse.io/").respond(404)
    result, posts = fetch(Greenhouse(), cfg, [WatchlistEntry(name="Gone", ats="greenhouse", slug="gone")])
    assert not result.ok and posts == []


def test_github_listings_json():
    posts = parse_listings_json(load_fixture("simplify_listings.json"), "SimplifyJobs/Summer2027-Internships")
    assert posts and all(p.employment_type == "internship" for p in posts)
    assert len(posts) == 6  # inactive entries skipped
    assert any(p.season and p.season.startswith("summer-") for p in posts)


def test_github_readme_fallback():
    html = """<table><tbody>
    <tr><td><strong><a href="https://simplify.jobs/c/Acme">Acme</a></strong></td><td>Finance Intern</td><td>Austin, TX</td>
    <td><a href="https://jobs.lever.co/acme/1?utm_source=Simplify"><img alt="Apply"></a> <a href="https://simplify.jobs/p/x">S</a></td><td>0d</td></tr>
    <tr><td>↳</td><td>Strategy Intern</td><td>NYC</td><td><a href="https://jobs.lever.co/acme/2">Apply</a></td><td>1d</td></tr>
    <tr><td>Closed Co</td><td>Old Intern</td><td>SF</td><td>🔒</td><td>9d</td></tr>
    </tbody></table>"""
    posts = parse_readme(html, "x/y")
    assert [(p.company, p.title) for p in posts] == [("Acme", "Finance Intern"), ("Acme", "Strategy Intern")]


@respx.mock
def test_github_lists_falls_back_across_branches(cfg):
    cfg.github_lists = ["SimplifyJobs/Summer2027-Internships"]
    respx.get("https://raw.githubusercontent.com/SimplifyJobs/Summer2027-Internships/dev/.github/scripts/listings.json").respond(404)
    respx.get("https://raw.githubusercontent.com/SimplifyJobs/Summer2027-Internships/main/.github/scripts/listings.json").respond(
        json=load_fixture("simplify_listings.json"))
    result, posts = fetch(GithubLists(), cfg, [])
    assert result.ok and len(posts) == 6


def test_jobspy_rows():
    rows = [
        {"title": "Finance Intern", "company": "Acme", "job_url": "https://www.linkedin.com/jobs/view/123", "job_url_direct": float("nan"),
         "location": "Austin, TX", "date_posted": "2026-09-17", "min_amount": 25.0, "max_amount": 30.0, "interval": "hourly",
         "currency": "USD", "is_remote": False, "description": "desc", "job_type": "internship"},
        {"title": float("nan"), "company": "Broken", "job_url": "x"},
    ]
    posts = rows_to_postings(rows, "linkedin")
    assert len(posts) == 1
    assert posts[0].source == "jobspy:linkedin"
    assert posts[0].compensation == "USD 25–30 hourly"


def test_failing_source_is_contained(cfg):
    class Boom(Greenhouse):
        name = "boom"

        async def fetch(self, ctx, result: SourceResult):
            raise RuntimeError("kaboom")

    result, posts = fetch(Boom(), cfg, [])
    assert not result.ok and "kaboom" in result.error and posts == []
