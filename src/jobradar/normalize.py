"""Turn RawPostings into Postings: clean text and URLs, detect season and employment type."""

from __future__ import annotations

import hashlib
import html
import re
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import yaml
from selectolax.parser import HTMLParser

from .locations import parse_locations
from .models import Posting, RawPosting

DESCRIPTION_LIMIT = 6000
ALIASES_FILE = Path(__file__).resolve().parents[2] / "data" / "company_aliases.yaml"

# ---------------------------------------------------------------- text


def html_to_text(value: str | None) -> str | None:
    if not value:
        return None
    s = value
    if "&lt;" in s[:500]:  # Greenhouse double-escapes its HTML
        s = html.unescape(s)
    if "<" in s and ">" in s:
        tree = HTMLParser(s)
        for tag in tree.css("script, style"):
            tag.decompose()
        for tag in tree.css("br, p, li, div, h1, h2, h3, h4, tr"):
            tag.insert_after("\n")
        s = tree.text(separator=" ") if tree.body is None else tree.body.text(separator=" ")
    s = html.unescape(s).replace("\xa0", " ")
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r"\s*\n\s*", "\n", s)
    s = re.sub(r"\n{3,}", "\n\n", s).strip()
    return s[:DESCRIPTION_LIMIT] if s else None


def word_re(keyword: str) -> re.Pattern[str]:
    """Whole-word, case-insensitive pattern. Short all-caps acronyms (VC, SWE, GTM) are case-sensitive."""
    kw = keyword.strip()
    escaped = re.escape(kw).replace(r"\ ", r"[\s\-]+")
    flags = 0 if (kw.isupper() and len(kw) <= 5) else re.I
    return re.compile(rf"(?<![A-Za-z0-9]){escaped}(?![A-Za-z0-9])", flags)


@lru_cache(maxsize=4096)
def _cached_word_re(keyword: str) -> re.Pattern[str]:
    return word_re(keyword)


def has_word(text: str | None, keyword: str) -> bool:
    return bool(text) and bool(_cached_word_re(keyword).search(text))


def find_words(text: str | None, keywords: list[str]) -> list[str]:
    return [k for k in keywords if has_word(text, k)]


# ---------------------------------------------------------------- urls

TRACKING_PARAMS = {
    "gh_src", "trk", "trkinfo", "trackingid", "refid", "lipi", "midtoken", "midsig", "ebp", "source",
    "src", "ref", "referrer", "lever-source", "lever-origin", "utm", "fbclid", "gclid", "mc_cid",
    "mc_eid", "_hsenc", "_hsmi", "sid", "ccid", "codes", "iis", "iisn", "jobpipeline", "sourcetype",
    "from", "position", "pagenum", "originalsubdomain", "campaign", "rx_source", "rx_medium",
}


def clean_url(url: str) -> str:
    url = url.strip()
    parts = urlsplit(url)
    query = [
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=False)
        if not k.lower().startswith("utm_") and k.lower() not in TRACKING_PARAMS
    ]
    path = parts.path
    if "linkedin.com" in parts.netloc:
        m = re.search(r"/jobs/view/(?:[^/]*?-)?(\d+)", path)
        if m:
            return f"https://www.linkedin.com/jobs/view/{m.group(1)}/"
    return urlunsplit((parts.scheme or "https", parts.netloc.lower(), path, urlencode(query), ""))


def url_key(url: str) -> str:
    u = clean_url(url).lower()
    u = re.sub(r"^https?://(www\.)?", "", u)
    u = re.sub(r"/(apply|application)/?$", "", u)
    return u.rstrip("/")


# ---------------------------------------------------------------- companies & titles

LEGAL_SUFFIXES = {
    "inc", "incorporated", "llc", "ltd", "limited", "corp", "corporation", "co", "company", "plc", "lp",
    "llp", "pllc", "gmbh", "ag", "sa", "nv", "bv", "holdings", "holding", "group", "the", "pbc",
}


@lru_cache(maxsize=1)
def _aliases() -> dict[str, str]:
    if not ALIASES_FILE.exists():
        return {}
    data = yaml.safe_load(ALIASES_FILE.read_text()) or {}
    out: dict[str, str] = {}
    for canonical, variants in data.items():
        base = _company_base(canonical)
        out[base] = base
        for v in variants or []:
            out[_company_base(v)] = base
    return out


def _company_base(name: str) -> str:
    s = name.lower().replace("&", " and ").replace("’", "'")
    s = re.sub(r"\.com\b", "", s)
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    words = [w for w in s.split() if w]
    while words and words[-1] in LEGAL_SUFFIXES:
        words.pop()
    while words and words[0] == "the":
        words.pop(0)
    return " ".join(words)


def normalize_company(name: str) -> str:
    base = _company_base(name)
    return _aliases().get(base, base)


SEASON_WORDS = r"(summer|fall|autumn|spring|winter)"
TITLE_NOISE = re.compile(
    r"\b(20\d\d|summer|fall|autumn|spring|winter|remote|hybrid|onsite|on site|usa|us|united states|"
    r"north america|program|programme|opportunity|position|role|paid|\d{1,2}\s*months?|\d+\s*weeks?)\b"
)


def normalize_title(title: str) -> str:
    s = title.lower().replace("&", " and ")
    s = re.sub(r"\binternships?\b|\binterns\b", "intern", s)
    s = re.sub(r"\bco[\s-]?ops?\b", "coop", s)
    s = re.sub(r"\boff[\s-]?cycle\b", "offcycle", s)
    s = re.sub(r"\bpart[\s-]?time\b", "parttime", s)
    s = re.sub(r"\bsr\b\.?", "senior", s)
    s = re.sub(r"\bmgmt\b", "management", s)
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    s = TITLE_NOISE.sub(" ", s)
    return " ".join(s.split())


# ---------------------------------------------------------------- season & type

_SEASON_YEAR = re.compile(rf"\b{SEASON_WORDS}\s*[-/'’]?\s*(20\d\d|\d\d)\b", re.I)
_YEAR_SEASON = re.compile(rf"\b(20\d\d)\s*[-/]?\s*{SEASON_WORDS}\b", re.I)
_SEASON_ONLY = re.compile(rf"\b{SEASON_WORDS}\b", re.I)
_YEAR = re.compile(r"\b(20[2-3]\d)\b")
_YEAR_ROUND = re.compile(r"\b(year[\s-]round|academic[\s-]year|ongoing|during the school year|during the semester)\b", re.I)


def _season_name(word: str) -> str:
    w = word.lower()
    return "fall" if w == "autumn" else w


def _year(y: str) -> int:
    n = int(y)
    return 2000 + n if n < 100 else n


def detect_season(title: str, description: str | None = None, hint: str | None = None) -> str | None:
    """Return 'summer-2027', 'fall-2026', 'year-round', or None."""
    for text in (title,):
        if m := _SEASON_YEAR.search(text):
            return f"{_season_name(m.group(1))}-{_year(m.group(2))}"
        if m := _YEAR_SEASON.search(text):
            return f"{_season_name(m.group(2))}-{_year(m.group(1))}"
        # "2027 Markets Summer Analyst Program"
        season, year = _SEASON_ONLY.search(text), _YEAR.search(text)
        if season and year:
            return f"{_season_name(season.group(1))}-{year.group(1)}"
    if hint:
        h = hint.lower().replace(" ", "-")
        if re.fullmatch(r"(summer|fall|winter|spring|autumn)-20\d\d", h):
            return h.replace("autumn", "fall")
        if "year" in h:
            return "year-round"
    title_season = _SEASON_ONLY.search(title)
    if description:
        head = description[:3000]
        matches = [f"{_season_name(m.group(1))}-{_year(m.group(2))}" for m in _SEASON_YEAR.finditer(head)]
        matches += [f"{_season_name(m.group(2))}-{_year(m.group(1))}" for m in _YEAR_SEASON.finditer(head)]
        if title_season:
            want = _season_name(title_season.group(1))
            matches = [m for m in matches if m.startswith(want)] or matches
        if matches:
            return max(set(matches), key=matches.count)
    if _YEAR_ROUND.search(title) or (description and _YEAR_ROUND.search(description[:3000])):
        return "year-round"
    return None


INTERN_RE = re.compile(
    r"\b(intern|interns|internship|internships|co[\s-]?op|summer (?:analyst|associate|scholar|fellow)|"
    r"off[\s-]?cycle|placement|apprentice(?:ship)?|working student|student (?:analyst|associate|worker|assistant)|"
    r"early insights?|discovery (?:program|day)|insight (?:program|week))\b",
    re.I,
)
PART_TIME_RE = re.compile(r"\bpart[\s-]?time\b", re.I)
STUDENT_RE = re.compile(r"\b(student|students|undergraduate|currently enrolled|enrolled in)\b", re.I)
NEW_GRAD_RE = re.compile(
    r"\b(new grad(?:uate)?s?|graduating student|recent grad(?:uate)?s?|graduate program|entry[\s-]level|"
    r"early careers?|full[\s-]time analyst|analyst program|associate program|rotational program|"
    r"campus hire|class of 20\d\d)\b",
    re.I,
)


HINT_INTERN = re.compile(r"\b(intern|interns|internship|internships|co[\s-]?op|student)\b", re.I)
HINT_FULL = re.compile(r"\b(full[\s-]?time|permanent|regular|experienced)\b", re.I)
HINT_CONTRACT = re.compile(r"\b(contract|contractor|temporary|temp|eor|freelance)\b", re.I)


def detect_employment_type(title: str, description: str | None, hint: str | None) -> str | None:
    # Word-boundary matching throughout: Lever's "International EOR" is not an internship.
    h = hint or ""
    if INTERN_RE.search(title) or HINT_INTERN.search(h) or re.fullmatch(r"\s*intern\s*", h, re.I):
        return "co-op" if re.search(r"\bco[\s-]?op\b", title, re.I) else "internship"
    if PART_TIME_RE.search(title) or PART_TIME_RE.search(h):
        return "part-time"
    if NEW_GRAD_RE.search(title):
        return "new-grad"
    if HINT_FULL.search(h) or re.search(r"FullTime", h):
        return "full-time"
    if HINT_CONTRACT.search(h):
        return "contract"
    return None


# ---------------------------------------------------------------- posting


def primary_location_key(p: Posting) -> str:
    if not p.locations_parsed:
        return ""
    first = p.locations_parsed[0]
    if first.remote and not first.city:
        return "remote"
    return first.city or first.state or first.country or ""


def canonical_key(company: str, title: str, season: str | None, loc: str) -> str:
    return f"{normalize_company(company)}|{normalize_title(title)}|{season or ''}|{loc}"


def posting_id(key: str) -> str:
    return hashlib.sha1(key.encode()).hexdigest()[:16]


def normalize(raw: RawPosting, now: datetime | None = None) -> Posting:
    now = now or datetime.now(timezone.utc)
    title = re.sub(r"\s+", " ", raw.title).strip()
    description = html_to_text(raw.description)
    loc_strings = [s for s in ([raw.location] if raw.location else []) + raw.locations if s]
    locations = parse_locations(loc_strings, raw.is_remote)
    is_remote = raw.is_remote if raw.is_remote is not None else (any(l.remote for l in locations) or None)
    posted = raw.posted_at
    if posted and posted.tzinfo is None:
        posted = posted.replace(tzinfo=timezone.utc)
    p = Posting(
        id="",
        title=title,
        company=raw.company.strip(),
        location=" | ".join(dict.fromkeys(loc_strings)) or None,
        locations_parsed=locations,
        is_remote=is_remote,
        url=clean_url(raw.url),
        source=raw.source,
        sources=[raw.source],
        posted_at=posted,
        first_seen_at=now,
        description=description,
        employment_type=detect_employment_type(title, description, raw.employment_type),
        season=detect_season(title, description, raw.season),
        compensation=raw.compensation,
    )
    p.id = posting_id(canonical_key(p.company, p.title, p.season, primary_location_key(p)))
    return p
