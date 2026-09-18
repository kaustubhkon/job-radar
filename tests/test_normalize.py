from jobradar.normalize import (
    clean_url,
    detect_employment_type,
    detect_season,
    has_word,
    html_to_text,
    normalize_company,
    normalize_title,
    url_key,
)

from .conftest import posting


def test_html_to_text_handles_greenhouse_double_escaping():
    text = html_to_text("&lt;p&gt;Hello &amp;amp; welcome&lt;/p&gt;&lt;ul&gt;&lt;li&gt;One&lt;/li&gt;&lt;li&gt;Two&lt;/li&gt;&lt;/ul&gt;")
    assert "Hello & welcome" in text
    assert "One" in text and "Two" in text
    assert "<" not in text


def test_html_to_text_truncates():
    assert len(html_to_text("word " * 5000)) <= 6000


def test_clean_url_strips_tracking_but_keeps_job_ids():
    u = clean_url("https://Boards.Greenhouse.io/acme/jobs/123?gh_src=abc&utm_source=li&gh_jid=123#apply")
    assert u == "https://boards.greenhouse.io/acme/jobs/123?gh_jid=123"


def test_linkedin_urls_canonicalize():
    a = clean_url("https://www.linkedin.com/comm/jobs/view/4100000001/?trackingId=abc&refId=x")
    b = clean_url("https://linkedin.com/jobs/view/finance-intern-at-lumen-4100000001?trk=foo")
    assert a == b == "https://www.linkedin.com/jobs/view/4100000001/"


def test_url_key_ignores_scheme_www_and_apply_suffix():
    assert url_key("http://www.jobs.lever.co/acme/1/apply") == url_key("https://jobs.lever.co/acme/1")


def test_company_normalization():
    assert normalize_company("Ramp Business Corporation") == normalize_company("Ramp")
    assert normalize_company("JPMorgan Chase & Co.") == normalize_company("J.P. Morgan")
    assert normalize_company("Acme, Inc.") == "acme"
    assert normalize_company("The Goldman Sachs Group, Inc.") == normalize_company("Goldman Sachs")


def test_title_normalization_unifies_intern_wording_and_drops_season():
    assert normalize_title("Finance Internship - Summer 2027 (Remote)") == normalize_title("Finance Intern")
    assert normalize_title("Co-Op, Strategy & Ops") == "coop strategy and ops"


def test_word_matching_is_whole_word():
    assert not has_word("Internal Audit Analyst", "intern")
    assert not has_word("International Business Development", "intern")
    assert has_word("Finance Intern", "intern")
    assert not has_word("Answer Engine Analyst", "SWE")
    assert has_word("SWE Intern", "SWE")
    assert has_word("Business-Operations Intern", "business operations")
    assert not has_word("vc funded startup", "VC")  # short all-caps keywords are case-sensitive


def test_season_detection():
    assert detect_season("Summer 2027 Finance Intern") == "summer-2027"
    assert detect_season("Finance Intern - Summer '27") == "summer-2027"
    assert detect_season("2027 Markets Summer Analyst Program") == "summer-2027"
    assert detect_season("Fall 2026 Co-op") == "fall-2026"
    assert detect_season("Autumn 2026 Intern") == "fall-2026"
    assert detect_season("Strategy Intern", "Join us for Spring 2027 in Austin.") == "spring-2027"
    assert detect_season("Strategy Intern", None, "summer-2027") == "summer-2027"
    assert detect_season("Part-time Analyst", "This is a year-round role during the school year.") == "year-round"
    assert detect_season("Strategy Intern") is None


def test_employment_type_detection():
    assert detect_employment_type("Finance Intern", None, None) == "internship"
    assert detect_employment_type("Strategy Co-op", None, None) == "co-op"
    assert detect_employment_type("Summer Analyst", None, None) == "internship"
    assert detect_employment_type("Analyst", None, "Intern") == "internship"
    assert detect_employment_type("Part-Time Marketing Assistant", None, None) == "part-time"
    assert detect_employment_type("Business Analyst", None, "Full-time") == "full-time"
    # Lever commitment "International EOR" once matched "intern" as a substring.
    assert detect_employment_type("Business Development Lead", None, "International EOR") == "contract"
    assert detect_employment_type("Internal Audit Associate", None, None) is None


def test_posting_id_is_stable_across_sources():
    a = posting("Finance Intern (Summer 2027)", company="Ramp", location="New York, NY")
    b = posting("Finance Internship - Summer 2027", company="Ramp Business Corporation", location="New York, NY", source="jobspy:linkedin")
    assert a.id == b.id
