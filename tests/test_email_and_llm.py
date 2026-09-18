import asyncio
import json
from types import SimpleNamespace

import pytest

from jobradar import llm
from jobradar.cli import detect_ats
from jobradar.emailer.render import render_digest
from jobradar.emailer.smtp import build_message
from jobradar.models import RunReport, SourceResult
from jobradar.scoring import apply_rule_scores

from .conftest import NOW, posting


def scored(cfg, *titles):
    ps = [posting(t, location="Austin, TX", posted_at=NOW, description="Summer 2027. Paid.") for t in titles]
    for p in ps:
        p.family, p.family_match, p.location_tier, p.posting_type = "finance", "title", "preferred", "summer_internship"
    apply_rule_scores(ps, cfg, NOW, set())
    return ps


def test_subject_and_sections(cfg):
    ps = scored(cfg, "Finance Intern", "Treasury Intern")
    report = RunReport(started_at=NOW, sources=[SourceResult(name="greenhouse", count=214),
                                                SourceResult(name="jobspy:linkedin", ok=False, error="429 rate-limited")])
    report.emailed = 2
    email = render_digest(ps, cfg, report, NOW)
    assert email.subject.startswith("Job Radar · Fri Sep 18 · 2 new")
    assert "greenhouse ✓ 214" in email.text and "jobspy:linkedin ✗ 429 rate-limited" in email.text
    assert "Apply" in email.html and 'style="' in email.html  # CSS inlined for Gmail
    assert "&amp;" not in email.text


def test_top_picks_respect_threshold(cfg):
    ps = scored(cfg, "Finance Intern")
    ps[0].score = 90
    email = render_digest(ps, cfg, RunReport(started_at=NOW, emailed=1), NOW)
    assert "(1 top pick)" in email.subject and "TOP PICKS" in email.text
    ps[0].score = 10
    email = render_digest(ps, cfg, RunReport(started_at=NOW, emailed=1), NOW)
    assert "top pick" not in email.subject


def test_empty_and_failure_emails(cfg):
    assert "nothing new" in render_digest([], cfg, RunReport(started_at=NOW), NOW, kind="empty").subject
    assert "every source failed" in render_digest([], cfg, RunReport(started_at=NOW), NOW, kind="failure").subject


def test_smtp_message_has_text_and_html(cfg):
    email = render_digest(scored(cfg, "Finance Intern"), cfg, RunReport(started_at=NOW, emailed=1), NOW)
    msg = build_message(email, "me@gmail.com", "me@gmail.com", "Job Radar")
    types = [p.get_content_type() for p in msg.iter_parts()]
    assert types == ["text/plain", "text/html"]


def test_rule_score_prefers_austin_and_recency(cfg):
    a, b = scored(cfg, "Finance Intern", "Finance Intern B")
    b.location_tier = "accepted"
    b.posted_at = NOW.replace(month=6)
    apply_rule_scores([a, b], cfg, NOW, set())
    assert a.rule_score > b.rule_score


class FakeMessages:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = 0

    async def create(self, **kw):
        self.calls += 1
        text = self.replies.pop(0) if self.replies else "[]"
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=text)])


def fake_anthropic(monkeypatch, replies):
    msgs = FakeMessages(replies)
    import sys
    import types

    mod = types.ModuleType("anthropic")
    mod.AsyncAnthropic = lambda: SimpleNamespace(messages=msgs)
    monkeypatch.setitem(sys.modules, "anthropic", mod)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    return msgs


def test_llm_blends_scores_and_drops_ineligible(cfg, monkeypatch):
    cfg.ranking.llm = True
    a, b = scored(cfg, "Finance Intern", "Treasury Intern")
    reply = json.dumps([
        {"id": a.id, "category": "Finance", "fit": 90, "eligible": True, "reason": "Fintech FP&A work in Austin, matches founder background"},
        {"id": b.id, "category": "Finance", "fit": 40, "eligible": False, "reason": "Requires 2027 graduation"},
    ])
    fake_anthropic(monkeypatch, [f"Here you go:\n{reply}"])
    report = RunReport(started_at=NOW)
    rejected = asyncio.run(llm.llm_rank([a, b], cfg, report))
    assert [p.id for p in rejected] == [b.id]
    assert a.llm_fit == 90 and a.score == pytest.approx(0.4 * a.rule_score + 0.6 * 90, abs=0.1)
    assert a.reason.startswith("Fintech")
    assert report.drop_counts == {"llm_ineligible": 1}


def test_llm_bad_output_falls_back_to_rule_score(cfg, monkeypatch):
    cfg.ranking.llm = True
    [a] = scored(cfg, "Finance Intern")
    fake_anthropic(monkeypatch, ["not json", "still not json"])
    report = RunReport(started_at=NOW)
    assert asyncio.run(llm.llm_rank([a], cfg, report)) == []
    assert a.llm_fit is None and a.score == a.rule_score
    assert any("rule score" in n for n in report.notes)


def test_llm_cap_ranks_only_top_n(cfg, monkeypatch):
    cfg.ranking.llm = True
    cfg.ranking.llm_max_postings = 1
    a, b = scored(cfg, "Finance Intern", "Treasury Intern")
    msgs = fake_anthropic(monkeypatch, ["[]"])
    report = RunReport(started_at=NOW)
    asyncio.run(llm.llm_rank([a, b], cfg, report))
    assert msgs.calls == 1
    assert any("top 1" in n for n in report.notes)


def test_llm_skipped_without_key(cfg, monkeypatch):
    cfg.ranking.llm = True
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    [a] = scored(cfg, "Finance Intern")
    assert asyncio.run(llm.llm_rank([a], cfg, RunReport(started_at=NOW))) == []


@pytest.mark.parametrize(
    "url, expected",
    [
        ("https://job-boards.greenhouse.io/mercury/jobs/123", ("greenhouse", "mercury")),
        ("https://boards.greenhouse.io/embed/job_board?for=stripe", ("greenhouse", "stripe")),
        ("https://jobs.lever.co/palantir/abc", ("lever", "palantir")),
        ("https://jobs.ashbyhq.com/ramp", ("ashby", "ramp")),
        ("https://nvidia.wd5.myworkdayjobs.com/en-US/NVIDIAExternalCareerSite/job/x", ("workday", "nvidia")),
        ("https://jpmc.fa.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1001/job/1", ("oracle", "jpmc.fa.oraclecloud.com")),
        ("https://morganstanley.eightfold.ai/careers?domain=morganstanley.com", ("eightfold", "morganstanley.eightfold.ai")),
    ],
)
def test_detect_ats(url, expected):
    e = detect_ats(url, "X")
    assert (e.ats, e.slug or e.tenant or e.host) == expected
