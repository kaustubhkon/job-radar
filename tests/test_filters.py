from jobradar.filters import (
    LocationRules,
    allowed_grad_years,
    apply_filters,
    check_class_year,
    check_location,
    check_posting_type,
    check_role_family,
    worth_details,
)
from jobradar.models import RunReport

from .conftest import NOW, posting


def run(p, cfg):
    report = RunReport(started_at=NOW)
    kept = apply_filters([p], cfg, report)
    reason = report.drops[0].reason if report.drops else None
    return bool(kept), reason


def test_good_posting_passes(cfg):
    ok, _ = run(posting("Finance Intern - Summer 2027", location="Austin, TX"), cfg)
    assert ok


def test_software_engineer_intern_never_appears(cfg):
    for title in ("Software Engineer Intern", "Software Engineering Intern, Payments", "SWE Intern - Fintech"):
        ok, reason = run(posting(title, description="fintech treasury payments growth partnerships"), cfg)
        assert not ok, title


def test_posting_type_rejects_full_time(cfg):
    assert check_posting_type(posting("Senior Financial Analyst", employment_type="Full-time"), cfg) == "posting_type"
    assert check_posting_type(posting("Internal Audit Analyst"), cfg) == "posting_type"


def test_season_filter(cfg):
    assert check_posting_type(posting("Finance Intern - Summer 2026"), cfg) == "season"
    assert check_posting_type(posting("Finance Intern - Summer 2028"), cfg) is None  # junior-summer recruiting
    assert check_posting_type(posting("Finance Intern"), cfg) is None  # no season stated: keep


def test_part_time_student_role(cfg):
    p = posting("Part-Time Finance Assistant", description="Open to current undergraduate students.")
    assert check_posting_type(p, cfg) is None
    assert p.posting_type == "part_time"


def test_role_family_title_beats_description(cfg):
    p = posting("Business Operations Intern", description="You'll support our finance and treasury teams.")
    assert check_role_family(p, cfg) is None
    assert p.family == "startup_ops" and p.family_match == "title"


def test_description_match_needs_two_keywords_and_a_generic_title(cfg):
    generic = posting("Summer Intern", description="Work on partnerships and business development.")
    assert check_role_family(generic, cfg) is None and generic.family_match == "description"
    one_hit = posting("Summer Intern", description="Some growth work.")
    assert check_role_family(one_hit, cfg) == "role_family"
    other_function = posting("Data Science Intern", description="partnerships and business development")
    assert check_role_family(other_function, cfg) == "role_family"


def test_excludes_use_whole_words(cfg):
    ok, reason = run(posting("Senior Finance Intern"), cfg)
    assert not ok and reason == "exclude"
    ok, _ = run(posting("Staff Accountant Intern - Finance"), cfg)  # "staff engineer" is excluded, not "staff"
    assert ok


def test_location_rules(cfg):
    rules = LocationRules.from_config(cfg)
    p = posting("Finance Intern", location="Austin, TX")
    assert check_location(p, cfg, rules) is None and p.location_tier == "preferred"
    p = posting("Finance Intern", location="New York, NY")
    assert check_location(p, cfg, rules) is None and p.location_tier == "accepted"
    p = posting("Finance Intern", location="Remote - US")
    assert check_location(p, cfg, rules) is None and p.location_tier == "accepted"
    assert check_location(posting("Finance Intern", location="Boston, MA"), cfg, rules) == "location"
    assert check_location(posting("Finance Intern", location="London, UK"), cfg, rules) == "non_us"
    assert check_location(posting("Finance Intern", location="Remote (Canada)"), cfg, rules) == "non_us"
    p = posting("Finance Intern", location=None)
    assert check_location(p, cfg, rules) is None and p.location_tier == "unclear"


def test_foreign_office_plus_remote_is_not_us_remote(cfg):
    rules = LocationRules.from_config(cfg)
    p = posting("Operations Intern", location="Bangalore", locations=["Remote"])
    assert check_location(p, cfg, rules) == "non_us"


def test_part_time_needs_part_time_location(cfg):
    rules = LocationRules.from_config(cfg)
    p = posting("Part-Time Finance Intern", location="New York, NY")
    check_posting_type(p, cfg)
    assert check_location(p, cfg, rules) == "part_time_location"
    p = posting("Part-Time Finance Intern", location="Austin, TX")
    check_posting_type(p, cfg)
    assert check_location(p, cfg, rules) is None


def test_grad_year_window_parsing():
    assert allowed_grad_years("Open to students graduating between December 2026 and June 2027.") == (2026, 2027)
    assert allowed_grad_years("Candidates must be Class of 2028.") == (2028, 2028)
    assert allowed_grad_years("2027 graduates only.") == (2027, 2027)
    assert allowed_grad_years("Expected graduation in 2028 or later.") == (2028, 9999)
    assert allowed_grad_years("Our Summer 2027 program is great.") is None
    assert allowed_grad_years("Join our graduate program in 2027.") is None


def test_restricted_to_2027_grads_never_appears_for_2029(cfg):
    p = posting("Finance Intern - Summer 2027", description="Must be graduating between Dec 2026 and June 2027.")
    ok, reason = run(p, cfg)
    assert not ok and reason == "grad_year"


def test_class_year_adjustments(cfg):
    boosted = posting("Sophomore Discovery Program - Summer 2027", description="For sophomores interested in finance.")
    assert check_class_year(boosted, cfg) is None and boosted.class_year_adj > 0
    senior = posting("Consulting Intern - Summer 2027", description="Rising seniors preferred.")
    assert check_class_year(senior, cfg) is None and senior.class_year_adj < 0
    bank = posting("2027 Investment Banking Summer Analyst")
    assert check_class_year(bank, cfg) is None and bank.class_year_adj < 0
    assert "class of 2028" in bank.class_year_note
    junior_summer = posting("2028 Investment Banking Summer Analyst")  # user is a rising senior in 2028
    check_class_year(junior_summer, cfg)
    assert junior_summer.class_year_adj == 0


def test_degree_level(cfg):
    assert check_class_year(posting("MBA Summer Associate"), cfg) == "degree_level"
    assert check_class_year(posting("PhD Quant Research Intern"), cfg) == "degree_level"


def test_worth_details(cfg):
    assert worth_details("Summer Analyst", cfg)
    assert worth_details("Finance Intern", cfg)
    assert not worth_details("Software Engineer Intern", cfg)
    assert not worth_details("Director of Finance", cfg)
