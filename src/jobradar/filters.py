"""Hard rules. Each returns a drop reason (logged for tuning) or annotates the posting and lets it through."""

from __future__ import annotations

import re
from dataclasses import dataclass

from .config import Config
from .locations import is_non_us, location_matches, parse_config_location
from .models import Location, Posting, RunReport
from .normalize import INTERN_RE, PART_TIME_RE, STUDENT_RE, find_words, has_word

# --------------------------------------------------------------------------- posting type


def classify_types(p: Posting) -> set[str]:
    emp = p.employment_type
    title = p.title
    part = bool(PART_TIME_RE.search(title)) or emp == "part-time"
    types: set[str] = set()
    if emp in ("internship", "co-op"):
        s = p.season
        off = emp == "co-op" or re.search(r"\boff[\s-]?cycle\b|\bco[\s-]?op\b", title, re.I)
        if off or (s and not s.startswith("summer") and s != "year-round") or (part and not s):
            types.add("offcycle_internship")
        else:
            types.add("summer_internship")
        if part or s == "year-round":
            types.add("part_time")
    elif part and (STUDENT_RE.search(title) or STUDENT_RE.search((p.description or "")[:2500])):
        types.add("part_time")
    elif emp == "new-grad":
        types.add("new_grad")
    return types


def check_posting_type(p: Posting, cfg: Config) -> str | None:
    types = classify_types(p)
    enabled = [t for t in cfg.posting_types if t in types]
    if not enabled:
        return "posting_type"
    p.posting_type = enabled[0]
    if cfg.seasons and p.season and p.season not in cfg.seasons:
        return "season"
    return None


# --------------------------------------------------------------------------- role family

# Titles that clearly belong to a function the user isn't targeting. A description-only
# keyword match can't rescue these; only a keyword in the title can.
OTHER_FUNCTION_TITLE = re.compile(
    r"\b(engineer(ing)?|developer|scientist|data science|machine learning|ml|ai research|research(er)?|"
    r"design(er)?|ux|ui|hardware|electrical|mechanical|chemical|civil|nurse|nursing|clinical|pharmac\w*|"
    r"legal|paralegal|law|recruit(ing|er)|talent acquisition|human resources|hr|it support|"
    r"help ?desk|technician|manufacturing|warehouse|security|devops|qa|test|software)\b",
    re.I,
)


def check_role_family(p: Posting, cfg: Config) -> str | None:
    best: tuple[float, str, str] | None = None  # (weight, family, match)
    for name, fam in cfg.role_families.items():
        if find_words(p.title, fam.keywords):
            cand = (fam.weight + 1.0, name, "title")  # title matches outrank description matches
            if best is None or cand[0] > best[0]:
                best = cand
    if best is None and not OTHER_FUNCTION_TITLE.search(p.title):
        desc = (p.description or "")[:4000]
        for name, fam in cfg.role_families.items():
            hits = find_words(desc, fam.keywords)
            if len(hits) >= 2:
                cand = (fam.weight, name, "description")
                if best is None or cand[0] > best[0]:
                    best = cand
    if best is None:
        return "role_family"
    p.family, p.family_match = best[1], best[2]
    return None


# --------------------------------------------------------------------------- excludes


def check_excludes(p: Posting, cfg: Config) -> str | None:
    hit = find_words(p.title, cfg.exclude_title_keywords)
    return f"exclude:{hit[0]}" if hit else None


# --------------------------------------------------------------------------- location


@dataclass
class LocationRules:
    preferred: list[Location | str]
    accepted: list[Location | str]
    part_time: list[Location | str]

    @classmethod
    def from_config(cls, cfg: Config) -> LocationRules:
        return cls(
            [parse_config_location(x) for x in cfg.locations.preferred],
            [parse_config_location(x) for x in cfg.locations.accepted],
            [parse_config_location(x) for x in cfg.locations.part_time_locations],
        )


def _any_match(wants: list[Location | str], locs: list[Location]) -> bool:
    return any(location_matches(w, loc) for w in wants for loc in locs)


def check_location(p: Posting, cfg: Config, rules: LocationRules) -> str | None:
    locs = p.locations_parsed
    concrete = [l for l in locs if l.city or l.state or l.remote or (l.country and l.country != "US")]
    if concrete and all(is_non_us(l) for l in concrete):
        return "non_us"
    us_locs = [l for l in locs if not is_non_us(l)]
    if any(is_non_us(l) for l in locs) and not any(l.is_us for l in locs):
        # "Bangalore · Remote": a remote option next to only foreign offices isn't US-remote.
        us_locs = []
        if concrete:
            return "non_us"
    part_time = p.posting_type == "part_time" or "part_time" in classify_types(p)

    if not concrete:
        # Nothing parseable, or just "United States".
        if cfg.locations.unknown == "drop":
            return "location_unknown"
        if locs and "anywhere_us" in rules.accepted + rules.preferred:
            p.location_tier = "accepted"
        else:
            p.location_tier = "unclear"
        return None

    if part_time and rules.part_time and not _any_match(rules.part_time, us_locs):
        return "part_time_location"
    if _any_match(rules.preferred, us_locs):
        p.location_tier = "preferred"
    elif _any_match(rules.accepted, us_locs):
        p.location_tier = "accepted"
    else:
        return "location"
    return None


# --------------------------------------------------------------------------- class year

DEGREE_TITLE = re.compile(r"\b(mba|ph\.?d|doctoral|postdoc|master'?s|ms|jd|law student|graduate student)\b", re.I)
GRAD_CONTEXT = re.compile(
    r"\b(graduating|graduation|graduates? (?:in|between|by|from|during|no later)|expected to graduate|"
    r"to graduate|grad date|class of|degree completion|complet\w+ (?:your |their |a |an )?(?:degree|studies))\b",
    re.I,
)
SEASON_YEAR = re.compile(r"\b(summer|fall|autumn|spring|winter)\s*[-']?\s*20\d\d\b|\b20\d\d\s+(summer|fall|spring|winter)\b", re.I)
YEAR = re.compile(r"\b(20[2-3]\d)\b")
GRADS_OF_YEAR = re.compile(r"\b(20[2-3]\d)\s+(?:grads?|graduates?|graduating class)\b", re.I)
BOOST_WORDS = [
    "sophomore", "sophomores", "freshman", "freshmen", "first-year", "first year", "early insight",
    "early insights", "discovery program", "insight program", "insight week", "exploration program",
]
SENIOR_ONLY = re.compile(r"\b(rising seniors?|penultimate[\s-]year|final[\s-]year students?)\b", re.I)


def allowed_grad_years(text: str) -> tuple[int, int] | None:
    """Find an explicit graduation-year window in a posting, e.g. 'graduating between Dec 2026 and June 2027'."""
    lows, highs = [], []
    for sentence in re.split(r"(?<=[.!?;])\s+|\n", text):
        if not GRAD_CONTEXT.search(sentence) and not GRADS_OF_YEAR.search(sentence):
            continue
        cleaned = SEASON_YEAR.sub(" ", sentence)
        years = [int(y) for y in YEAR.findall(cleaned)]
        if not years:
            continue
        low, high = min(years), max(years)
        if re.search(r"\b(or later|or after|and later|onwards?|and beyond)\b", cleaned, re.I):
            high = 9999
        if re.search(r"\b(or earlier|or before|no later than|by)\b", cleaned, re.I) and len(years) == 1:
            low = 0
        lows.append(low)
        highs.append(high)
    if not lows:
        return None
    return min(lows), max(highs)


def rising_class(grad_year: int, summer_year: int) -> str:
    left = grad_year - summer_year
    return {1: "senior", 2: "junior", 3: "sophomore"}.get(left, "freshman" if left >= 4 else "graduated")


def check_class_year(p: Posting, cfg: Config) -> str | None:
    prof = cfg.profile
    if prof.degree == "bachelors" and DEGREE_TITLE.search(p.title):
        return "degree_level"
    text = f"{p.title}\n{p.description or ''}"[:6000]
    window = allowed_grad_years(text)
    if window and not (window[0] <= prof.grad_year <= window[1]):
        lo, hi = window
        span = f"{lo}" if lo == hi else f"{lo if lo else '…'}–{hi if hi < 9999 else '…'}"
        return f"grad_year:{span}"

    adj, notes = 0, []
    summer_year = None
    if p.season and p.season.startswith("summer-"):
        summer_year = int(p.season.split("-")[1])
    user_rising = rising_class(prof.grad_year, summer_year) if summer_year else None
    boosted = bool(find_words(text, BOOST_WORDS)) or (
        user_rising is not None and has_word(text, f"rising {user_rising}")
    )
    if boosted and prof.class_standing in ("freshman", "sophomore"):
        adj += 12
        notes.append("early-year program")
    elif SENIOR_ONLY.search(text) and user_rising not in (None, "senior"):
        adj -= 15
        notes.append("aimed at rising seniors")
    elif (
        summer_year
        and re.search(r"\bsummer (analyst|associate)\b", p.title, re.I)
        and prof.grad_year != summer_year + 1
        and not boosted
    ):
        adj -= 20
        notes.append(f"Summer Analyst {summer_year} programs usually hire the class of {summer_year + 1}")
    p.class_year_adj = adj
    p.class_year_note = "; ".join(notes) or None
    return None


# --------------------------------------------------------------------------- driver


def apply_filters(postings: list[Posting], cfg: Config, report: RunReport) -> list[Posting]:
    rules = LocationRules.from_config(cfg)
    kept = []
    for p in postings:
        reason = (
            check_posting_type(p, cfg)
            or check_role_family(p, cfg)
            or check_excludes(p, cfg)
            or check_location(p, cfg, rules)
            or check_class_year(p, cfg)
        )
        if reason:
            base, _, detail = reason.partition(":")
            report.drop(p, base, detail or None)
        else:
            kept.append(p)
    return kept


def looks_relevant(title: str) -> bool:
    """Cheap pre-check sources use to avoid fetching details for obviously irrelevant jobs."""
    return bool(INTERN_RE.search(title) or PART_TIME_RE.search(title) or STUDENT_RE.search(title)
                or re.search(r"\b(new grad|graduate|campus|analyst program|summer)\b", title, re.I))


def worth_details(title: str, cfg: Config) -> bool:
    """Whether a listing deserves a second request for its full description."""
    if not looks_relevant(title) or find_words(title, cfg.exclude_title_keywords):
        return False
    if any(find_words(title, f.keywords) for f in cfg.role_families.values()):
        return True
    return not OTHER_FUNCTION_TITLE.search(title)
