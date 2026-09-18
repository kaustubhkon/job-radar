from datetime import timedelta

from jobradar.dedupe import merge_postings, titles_similar
from jobradar.state import SeenStore, lookback_since

from .conftest import NOW, posting


def test_same_job_on_greenhouse_and_linkedin_merges_to_greenhouse():
    li = posting("Finance & Strategy Intern - Summer 2027 (Austin)", company="Lumen Pay Inc.",
                 location="Austin, Texas Metropolitan Area", source="jobspy:linkedin",
                 url="https://www.linkedin.com/jobs/view/4100000001/?trk=x")
    gh = posting("Finance & Strategy Intern (Summer 2027)", company="Lumen Pay", location="Austin, TX",
                 url="https://job-boards.greenhouse.io/lumenpay/jobs/1", description="Full description")
    merged = merge_postings([li, gh])
    assert len(merged) == 1
    assert merged[0].url.startswith("https://job-boards.greenhouse.io")
    assert set(merged[0].sources) == {"greenhouse", "jobspy:linkedin"}


def test_multi_location_posting_matches_single_location_copy():
    gh = posting("Growth Intern", company="Mercury", location="New York, NY or San Francisco, CA",
                 url="https://job-boards.greenhouse.io/mercury/jobs/9")
    li = posting("Growth Intern", company="Mercury Technologies", location="San Francisco, CA",
                 source="jobspy:linkedin", url="https://www.linkedin.com/jobs/view/1/")
    assert len(merge_postings([gh, li])) == 1


def test_different_seasons_are_different_jobs():
    fall = posting("Finance Intern - Fall 2026", company="Acme", url="https://x.com/1")
    spring = posting("Finance Intern - Spring 2027", company="Acme", url="https://x.com/2")
    assert len(merge_postings([fall, spring])) == 2


def test_different_cities_are_different_jobs():
    a = posting("Summer Analyst", company="Crestmark", location="New York, NY", url="https://x.com/1")
    b = posting("Summer Analyst", company="Crestmark", location="Chicago, IL", url="https://x.com/2")
    assert len(merge_postings([a, b])) == 2


def test_same_url_always_merges():
    a = posting("Finance Intern", company="Acme", url="https://jobs.lever.co/acme/1?lever-source=linkedin")
    b = posting("Totally Different Title", company="Acme Corp", url="https://jobs.lever.co/acme/1")
    assert len(merge_postings([a, b])) == 1


def test_title_similarity_needs_more_than_one_shared_word():
    assert titles_similar("finance intern", "finance intern new york")
    assert not titles_similar("intern", "finance intern")
    assert not titles_similar("finance intern", "marketing intern")


def test_seen_store_roundtrip_and_prune_by_last_seen(tmp_path):
    store = SeenStore(tmp_path / "seen.json")
    p = posting("Finance Intern - Summer 2027", company="Acme")
    store.add(p, "emailed", NOW - timedelta(days=200), "h1")
    store.touch(p, NOW - timedelta(days=5))  # still live recently
    old = posting("Growth Intern", company="Other")
    store.add(old, "emailed", NOW - timedelta(days=200), "h1")
    store.save(NOW)

    store = SeenStore(tmp_path / "seen.json")
    assert store.prune(NOW, 120) == 1
    assert store.is_seen(posting("Finance Internship (Summer 2027)", company="Acme Inc"), "h1")
    assert not store.is_seen(posting("Growth Intern", company="Other"), "h1")


def test_llm_rejects_are_rechecked_after_config_change(tmp_path):
    store = SeenStore(tmp_path / "seen.json")
    p = posting("Finance Intern", company="Acme")
    store.add(p, "llm_rejected", NOW, "hash-a")
    assert store.is_seen(p, "hash-a")
    assert not store.is_seen(p, "hash-b")


def test_lookback_covers_weekend_and_missed_runs(tmp_path):
    store = SeenStore(tmp_path / "seen.json")
    assert lookback_since(store, NOW, 36, 6, 7) == NOW - timedelta(hours=36)  # first run
    friday = NOW - timedelta(days=3)
    store.last_successful_run_at = friday
    monday = lookback_since(store, NOW, 36, 6, 7)
    assert monday == friday - timedelta(hours=6)  # Friday's run minus buffer, not a fixed 36h
    store.source_ok["jobspy:linkedin"] = (NOW - timedelta(days=5)).isoformat()
    assert lookback_since(store, NOW, 36, 6, 7, "jobspy:linkedin") == NOW - timedelta(days=5, hours=6)
    store.source_ok["jobspy:linkedin"] = (NOW - timedelta(days=30)).isoformat()
    assert lookback_since(store, NOW, 36, 6, 7, "jobspy:linkedin") == NOW - timedelta(days=7)  # capped
