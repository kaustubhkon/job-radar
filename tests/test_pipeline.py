"""End-to-end runs with mocked HTTP and a captured mailbox (acceptance criteria from the PRD)."""

import asyncio
from datetime import timedelta

import httpx
import pytest
import respx

from jobradar import emailer
from jobradar.config import WatchlistEntry
from jobradar.pipeline import run_pipeline
from jobradar.sources import Http

from .conftest import NOW

BOARD = {
    "jobs": [
        {"title": "Finance Intern - Summer 2027", "absolute_url": "https://job-boards.greenhouse.io/acme/jobs/1",
         "location": {"name": "Austin, TX"}, "first_published": "2026-09-17T10:00:00Z", "content": "&lt;p&gt;Paid.&lt;/p&gt;"},
        {"title": "Business Operations Intern", "absolute_url": "https://job-boards.greenhouse.io/acme/jobs/2",
         "location": {"name": "Remote - US"}, "first_published": "2026-09-16T10:00:00Z", "content": "Summer 2027"},
        {"title": "Software Engineer Intern", "absolute_url": "https://job-boards.greenhouse.io/acme/jobs/3",
         "location": {"name": "Austin, TX"}, "content": "Summer 2027"},
        {"title": "Senior Accountant", "absolute_url": "https://job-boards.greenhouse.io/acme/jobs/4",
         "location": {"name": "Austin, TX"}},
    ]
}


@pytest.fixture
def mailbox(monkeypatch):
    sent = []
    monkeypatch.setattr(emailer, "send", lambda email, cfg: sent.append(email))
    return sent


@pytest.fixture
def pcfg(cfg):
    cfg.github_lists = []
    cfg.watchlist = [
        WatchlistEntry(name="Acme", ats="greenhouse", slug="acme"),
        WatchlistEntry(name="Beta", ats="lever", slug="beta"),
    ]
    cfg.email.send_when_empty = "always"
    return cfg


def run(cfg, state, now=NOW, **kw):
    async def go():
        http = Http(httpx.AsyncClient())
        try:
            return await run_pipeline(cfg, state, now=now, http=http, **kw)
        finally:
            await http.aclose()

    return asyncio.run(go())


def mock_sources(lever_status=200, lever_json=None):
    respx.get("https://boards-api.greenhouse.io/v1/boards/acme/jobs?content=true").respond(json=BOARD)
    respx.get("https://api.lever.co/v0/postings/beta?mode=json").respond(lever_status, json=lever_json or [])


@respx.mock
def test_first_run_is_a_backfill_then_no_repeats(pcfg, tmp_path, mailbox):
    mock_sources()
    r1 = run(pcfg, tmp_path)
    assert r1.report.mode == "backfill" and r1.report.email_kind == "backfill"
    assert {p.title for p in r1.emailed} == {"Finance Intern - Summer 2027", "Business Operations Intern"}
    assert (tmp_path / "seen.json").exists()
    assert (tmp_path / "runs" / "2026-09-18.csv").exists()

    r2 = run(pcfg, tmp_path, now=NOW + timedelta(days=1))
    assert r2.emailed == []  # running twice sends zero repeated postings
    assert r2.report.email_kind == "empty"
    assert "nothing new" in mailbox[-1].subject


@respx.mock
def test_new_posting_shows_up_next_day(pcfg, tmp_path, mailbox):
    mock_sources()
    run(pcfg, tmp_path)
    board = {"jobs": BOARD["jobs"] + [{"title": "Growth Intern", "absolute_url": "https://job-boards.greenhouse.io/acme/jobs/5",
                                       "location": {"name": "New York, NY"}, "content": "Summer 2027 growth"}]}
    respx.get("https://boards-api.greenhouse.io/v1/boards/acme/jobs?content=true").respond(json=board)
    r = run(pcfg, tmp_path, now=NOW + timedelta(days=1))
    assert [p.title for p in r.emailed] == ["Growth Intern"]
    assert r.report.email_kind == "digest"
    assert mailbox[-1].subject.startswith("Job Radar · Sat Sep 19 · 1 new")


@respx.mock
def test_rate_limited_source_still_produces_digest_with_footer(pcfg, tmp_path, mailbox, monkeypatch):
    monkeypatch.setattr("jobradar.sources.base.MAX_RETRIES", 0)
    mock_sources(lever_status=429)
    r = run(pcfg, tmp_path)
    lever = next(s for s in r.report.sources if s.name == "lever")
    assert not lever.ok and "429" in lever.error
    assert r.emailed
    assert "lever ✗ 429 rate-limited" in mailbox[-1].text


@respx.mock
def test_every_source_failing_sends_failure_email_and_keeps_state(pcfg, tmp_path, mailbox, monkeypatch):
    monkeypatch.setattr("jobradar.sources.base.MAX_RETRIES", 0)
    respx.get(url__startswith="https://boards-api.greenhouse.io/").respond(500)
    respx.get(url__startswith="https://api.lever.co/").respond(500)
    r = run(pcfg, tmp_path)
    assert r.report.email_kind == "failure"
    assert "every source failed" in mailbox[-1].subject
    assert not (tmp_path / "seen.json").exists()  # nothing advanced; next run retries the whole window


@respx.mock
def test_email_failure_does_not_advance_state(pcfg, tmp_path, monkeypatch):
    mock_sources()

    def boom(email, cfg):
        raise emailer.DeliveryError("smtp down")

    monkeypatch.setattr(emailer, "send", boom)
    with pytest.raises(emailer.DeliveryError):
        run(pcfg, tmp_path)
    assert not (tmp_path / "seen.json").exists()


@respx.mock
def test_dry_run_writes_preview_and_no_state(pcfg, tmp_path, mailbox):
    mock_sources()
    preview = tmp_path / "preview.html"
    r = run(pcfg, tmp_path / "state", dry_run=True, preview_path=preview)
    assert preview.exists() and "Finance Intern" in preview.read_text()
    assert mailbox == []
    assert not (tmp_path / "state" / "seen.json").exists()


@respx.mock
def test_send_when_empty_mondays(pcfg, tmp_path, mailbox):
    pcfg.email.send_when_empty = "mondays"
    mock_sources()
    run(pcfg, tmp_path)
    n = len(mailbox)
    saturday = NOW + timedelta(days=1)
    assert run(pcfg, tmp_path, now=saturday).report.email_kind == "none"
    assert len(mailbox) == n
    monday = NOW + timedelta(days=3)
    assert run(pcfg, tmp_path, now=monday).report.email_kind == "empty"


@respx.mock
def test_overflow_goes_to_csv(pcfg, tmp_path, mailbox):
    jobs = [{"title": f"Finance Intern {i}", "absolute_url": f"https://job-boards.greenhouse.io/acme/jobs/{100 + i}",
             "location": {"name": f"{city}"}, "content": "Summer 2027"}
            for i, city in enumerate(["Austin, TX", "Dallas, TX", "Houston, TX", "New York, NY", "Chicago, IL"])]
    respx.get("https://boards-api.greenhouse.io/v1/boards/acme/jobs?content=true").respond(json={"jobs": jobs})
    respx.get("https://api.lever.co/v0/postings/beta?mode=json").respond(json=[])
    run(pcfg, tmp_path, mode="run")  # backfill seeds
    pcfg.ranking.max_per_digest = 2
    more = [dict(j, title=j["title"].replace("Finance", "Growth"), absolute_url=j["absolute_url"] + "0") for j in jobs]
    respx.get("https://boards-api.greenhouse.io/v1/boards/acme/jobs?content=true").respond(json={"jobs": jobs + more})
    r = run(pcfg, tmp_path, now=NOW + timedelta(days=1))
    assert len(r.emailed) == 2 and len(r.overflow) == 3
    assert "+3 more" in mailbox[-1].text
    csv = (tmp_path / "runs" / "2026-09-19.csv").read_text()
    assert csv.count("overflow") == 3
