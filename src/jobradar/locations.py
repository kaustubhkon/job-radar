"""Parse free-form location strings ("New York, NY or San Francisco, CA", "Remote (Canada)") into Locations."""

from __future__ import annotations

import re

from .models import Location

US_STATES = {
    "AL": "alabama", "AK": "alaska", "AZ": "arizona", "AR": "arkansas", "CA": "california",
    "CO": "colorado", "CT": "connecticut", "DE": "delaware", "FL": "florida", "GA": "georgia",
    "HI": "hawaii", "ID": "idaho", "IL": "illinois", "IN": "indiana", "IA": "iowa",
    "KS": "kansas", "KY": "kentucky", "LA": "louisiana", "ME": "maine", "MD": "maryland",
    "MA": "massachusetts", "MI": "michigan", "MN": "minnesota", "MS": "mississippi", "MO": "missouri",
    "MT": "montana", "NE": "nebraska", "NV": "nevada", "NH": "new hampshire", "NJ": "new jersey",
    "NM": "new mexico", "NY": "new york", "NC": "north carolina", "ND": "north dakota", "OH": "ohio",
    "OK": "oklahoma", "OR": "oregon", "PA": "pennsylvania", "RI": "rhode island", "SC": "south carolina",
    "SD": "south dakota", "TN": "tennessee", "TX": "texas", "UT": "utah", "VT": "vermont",
    "VA": "virginia", "WA": "washington", "WV": "west virginia", "WI": "wisconsin", "WY": "wyoming",
    "DC": "district of columbia", "PR": "puerto rico",
}
STATE_BY_NAME = {v: k for k, v in US_STATES.items()}

# Cities that often appear without a state.
US_CITIES = {
    "new york": "NY", "san francisco": "CA", "austin": "TX", "chicago": "IL", "boston": "MA",
    "seattle": "WA", "dallas": "TX", "houston": "TX", "los angeles": "CA", "miami": "FL",
    "atlanta": "GA", "denver": "CO", "charlotte": "NC", "palo alto": "CA", "menlo park": "CA",
    "mountain view": "CA", "san jose": "CA", "san diego": "CA", "philadelphia": "PA",
    "washington": "DC", "pittsburgh": "PA", "nashville": "TN", "salt lake city": "UT",
    "minneapolis": "MN", "detroit": "MI", "phoenix": "AZ", "san antonio": "TX", "plano": "TX",
    "fort worth": "TX", "irving": "TX", "columbus": "OH", "jersey city": "NJ", "stamford": "CT",
    "greenwich": "CT", "cambridge": "MA", "brooklyn": "NY", "oakland": "CA", "sunnyvale": "CA",
    "redwood city": "CA", "santa clara": "CA", "boulder": "CO", "raleigh": "NC", "tampa": "FL",
    "portland": "OR", "baltimore": "MD", "st. louis": "MO", "st louis": "MO", "kansas city": "MO",
    "wilmington": "DE", "richmond": "VA", "mclean": "VA", "arlington": "VA", "princeton": "NJ",
}

CITY_ALIASES = {
    "nyc": "new york", "new york city": "new york", "manhattan": "new york", "ny": "new york",
    "sf": "san francisco", "san francisco bay": "san francisco", "bay": "san francisco",
    "sf bay": "san francisco", "dc": "washington", "washington dc": "washington",
    "washington d.c.": "washington", "la": "los angeles", "dfw": "dallas",
    "dallas-fort worth": "dallas", "dallas fort worth": "dallas", "silicon valley": "san jose",
}

NON_US_COUNTRIES = {
    "canada": "CA-country", "united kingdom": "GB", "uk": "GB", "england": "GB", "scotland": "GB",
    "ireland": "IE", "india": "IN", "germany": "DE", "france": "FR", "spain": "ES", "italy": "IT",
    "netherlands": "NL", "switzerland": "CH", "poland": "PL", "sweden": "SE", "denmark": "DK",
    "norway": "NO", "finland": "FI", "belgium": "BE", "austria": "AT", "portugal": "PT",
    "israel": "IL-country", "japan": "JP", "china": "CN", "hong kong": "HK", "singapore": "SG",
    "australia": "AU", "new zealand": "NZ", "brazil": "BR", "mexico": "MX", "argentina": "AR-country",
    "colombia": "CO-country", "chile": "CL", "south korea": "KR", "korea": "KR", "taiwan": "TW",
    "philippines": "PH", "vietnam": "VN", "thailand": "TH", "malaysia": "MY", "indonesia": "ID-country",
    "united arab emirates": "AE", "uae": "AE", "saudi arabia": "SA", "south africa": "ZA",
    "nigeria": "NG", "kenya": "KE", "egypt": "EG", "turkey": "TR", "romania": "RO",
    "czech republic": "CZ", "czechia": "CZ", "hungary": "HU", "greece": "GR", "luxembourg": "LU",
    "emea": "EMEA", "europe": "EU", "apac": "APAC", "latam": "LATAM", "costa rica": "CR",
    "estonia": "EE", "lithuania": "LT", "ukraine": "UA", "serbia": "RS", "bulgaria": "BG",
    "pakistan": "PK", "bangladesh": "BD", "peru": "PE", "uruguay": "UY", "puerto rico": None,
}
# Two-letter ISO codes that show up as a trailing token ("Tokyo,JP"). Excludes codes that
# collide with US state abbreviations, which are far more common in this data.
NON_US_CODES = {"GB", "UK", "JP", "CN", "HK", "SG", "AU", "NZ", "BR", "MX", "FR", "ES", "IT", "NL", "CH",
                "PL", "SE", "DK", "NO", "FI", "BE", "AT", "PT", "IE", "KR", "TW", "PH", "VN", "TH",
                "MY", "AE", "ZA", "CL", "CZ", "HU", "GR", "LU", "EE", "LT", "UA", "RS", "BG", "PK"}  # not IN/DE/CO/AR/ID: US states
NON_US_CITIES = {
    "london", "toronto", "vancouver", "montreal", "ottawa", "calgary", "waterloo", "paris", "berlin",
    "munich", "frankfurt", "hamburg", "dublin", "amsterdam", "zurich", "geneva", "madrid",
    "barcelona", "lisbon", "milan", "rome", "warsaw", "krakow", "prague", "stockholm", "copenhagen",
    "oslo", "helsinki", "brussels", "vienna", "tel aviv", "singapore", "hong kong", "tokyo",
    "osaka", "seoul", "taipei", "taipei city", "shanghai", "beijing", "shenzhen", "bengaluru",
    "bangalore", "mumbai", "hyderabad", "pune", "chennai", "gurgaon", "gurugram", "new delhi",
    "delhi", "noida", "sydney", "melbourne", "auckland", "sao paulo", "são paulo",
    "mexico city", "buenos aires", "bogota", "bogotá", "santiago", "lima", "manila",
    "metro manila", "dubai", "abu dhabi", "riyadh", "cape town", "johannesburg", "lagos",
    "nairobi", "edinburgh", "glasgow", "manchester", "belfast", "luxembourg", "kuala lumpur",
    "jakarta", "ho chi minh city", "bangkok", "budapest", "bucharest", "athens", "tallinn",
    "vilnius", "kyiv", "belgrade", "sofia", "cairo", "istanbul", "guadalajara", "monterrey",
    "rio de janeiro", "medellin", "medellín", "montevideo", "karachi", "lahore", "dhaka",
}
US_MARKERS = {"us", "usa", "u.s.", "u.s", "u.s.a.", "united states", "united states of america", "america"}
REMOTE_WORDS = re.compile(r"\b(remote|anywhere|work from home|wfh|virtual|distributed)\b", re.I)
UNKNOWN_WORDS = re.compile(
    r"^(multiple locations?|various locations?|various|tbd|flexible|n/?a|global|worldwide|nationwide|hybrid|on-?site|in[- ]office)$",
    re.I,
)
SPLIT_RE = re.compile(r"\s*(?:;|\||\n|\s/\s|\bor\b|\band\b|•|·)\s*", re.I)
PAREN_RE = re.compile(r"\(([^)]*)\)")


def _clean_token(tok: str) -> str:
    tok = tok.strip().strip(".-–—:").strip()
    tok = re.sub(r"\s+", " ", tok)
    return tok


def _clean_city(city: str) -> str:
    c = city.lower().strip()
    c = re.sub(r"^(greater|metro|downtown)\s+", "", c)
    c = re.sub(r"\s+(metropolitan area|metro area|metroplex|bay area|area|region|office|hq|headquarters|downtown|city center)$", "", c)
    c = re.sub(r"\s*\((hq|headquarters)\)$", "", c)
    c = c.replace("saint ", "st. ")
    return CITY_ALIASES.get(c, c)


def _state_code(tok: str) -> str | None:
    t = tok.strip().rstrip(".")
    if t.upper() in US_STATES and (t.isupper() or len(t) == 2):
        return t.upper()
    return STATE_BY_NAME.get(t.lower())


def _country(tok: str) -> str | None:
    t = tok.lower().strip().rstrip(".")
    if t in US_MARKERS:
        return "US"
    if t in NON_US_COUNTRIES:
        return NON_US_COUNTRIES[t] or "US"
    if tok.strip().upper() in NON_US_CODES and tok.strip().isupper():
        return tok.strip().upper()
    return None


def parse_segment(segment: str) -> list[Location]:
    raw = segment.strip()
    if not raw:
        return []
    text = raw
    remote = bool(REMOTE_WORDS.search(text))
    # Pull parentheticals out: "Remote (Canada)", "New York, NY (HQ)"
    parens = PAREN_RE.findall(text)
    text = PAREN_RE.sub(" ", text)
    text = REMOTE_WORDS.sub(" ", text)
    text = re.sub(r"\b(hybrid|on-?site|in[- ]office|flexible)\b", " ", text, flags=re.I)
    text = re.sub(r"^\s*[-–—:,]+|[-–—:,]+\s*$", "", text.strip())
    tokens = [_clean_token(t) for t in re.split(r",|\s[-–—]\s", text) if _clean_token(t)]
    for p in parens:
        # Keep parentheticals only when they name a place: "Remote (Canada)" yes, "(Job Requisitions Only)" no.
        for t in (_clean_token(t) for t in p.split(",")):
            if t and (_country(t) or _state_code(t) or _clean_city(t) in US_CITIES or t.lower() in NON_US_CITIES):
                tokens.append(t)

    if not tokens:
        if remote:
            return [Location(remote=True, raw=raw)]
        return []
    if len(tokens) == 1 and UNKNOWN_WORDS.match(tokens[0]):
        return [Location(remote=True, raw=raw)] if remote else []

    locs: list[Location] = []
    country: str | None = None
    pending_city: str | None = None
    pending_state: str | None = None  # Workday writes "PA - Pittsburgh"
    unknown: list[Location] = []
    for tok in tokens:
        if re.search(r"\d", tok) or len(tok) > 40:
            continue  # zip codes, street addresses, "Zone 1"
        c = _country(tok)
        st = _state_code(tok)
        low = tok.lower()
        if not c and not st:
            # "Sao Paulo Sao Paulo Brazil": a country name at the end of a comma-less string
            for name in sorted(NON_US_COUNTRIES, key=len, reverse=True):
                if low.endswith(" " + name) and NON_US_COUNTRIES[name]:
                    c = NON_US_COUNTRIES[name]
                    break
        if c:
            country = c
            if pending_city:
                locs.append(Location(city=_clean_city(pending_city), country=c, raw=raw))
                pending_city = None
        elif st and pending_city:
            locs.append(Location(city=_clean_city(pending_city), state=st, country="US", raw=raw))
            pending_city = None
        elif st and _clean_city(low) not in US_CITIES and low not in NON_US_CITIES:
            if pending_state:
                locs.append(Location(state=pending_state, country="US", raw=raw))
            pending_state = st
        else:
            if pending_city:
                _add_city(pending_city, raw, locs, unknown)
            if pending_state:
                locs.append(Location(city=_clean_city(tok), state=pending_state, country="US", raw=raw))
                pending_state = None
            else:
                pending_city = tok
    if pending_city:
        _add_city(pending_city, raw, locs, unknown)
    if pending_state:
        locs.append(Location(state=pending_state, country="US", raw=raw))
    # Unrecognised single words ("Embarcadero", "Job Requisitions Only") only count if nothing else parsed.
    if not locs:
        locs = unknown[:1]

    if country:
        for loc in locs:
            if loc.country is None:
                loc.country = country
            if loc.country != "US" and country == "US":
                loc.country = "US"
        if not locs:
            locs.append(Location(country=country, raw=raw))
    if remote:
        if locs:
            for loc in locs:
                loc.remote = True
        else:
            locs.append(Location(remote=True, country=country, raw=raw))
    return locs


def _add_city(city: str, raw: str, locs: list[Location], unknown: list[Location]) -> None:
    loc = _city_only(city, raw)
    if loc.state or loc.country:
        locs.append(loc)
    elif loc.city and len(loc.city.split()) <= 3:
        unknown.append(loc)


def _city_only(city: str, raw: str) -> Location:
    c = _clean_city(city)
    if UNKNOWN_WORDS.match(c):
        return Location(raw=raw)
    if c in US_CITIES:
        return Location(city=c, state=US_CITIES[c], country="US", raw=raw)
    if c in NON_US_CITIES:
        return Location(city=c, country="non-US", raw=raw)
    return Location(city=c, raw=raw)


def parse_locations(values: list[str] | str | None, is_remote: bool | None = None) -> list[Location]:
    if values is None:
        values = []
    if isinstance(values, str):
        values = [values]
    out: list[Location] = []
    for v in values:
        if not v:
            continue
        for seg in SPLIT_RE.split(v):
            if seg and seg.strip():
                out.extend(parse_segment(seg))
    # Drop empty placeholders when real locations exist.
    real = [loc for loc in out if loc.city or loc.state or loc.country or loc.remote]
    resolved = [loc for loc in real if loc.state or loc.country or loc.remote]
    if resolved:
        # Drop unrecognised fragments ("Job Requisitions Only") and bare states already covered by a city.
        city_states = {loc.state for loc in resolved if loc.city and loc.state}
        real = [loc for loc in resolved if loc.city or loc.remote or loc.state not in city_states]
    out = real
    if is_remote and not any(loc.remote for loc in out):
        us = any(loc.is_us for loc in out)
        out.append(Location(remote=True, country="US" if us else None, raw="remote"))
    # de-dupe
    uniq: list[Location] = []
    for loc in out:
        if not any(u.city == loc.city and u.state == loc.state and u.remote == loc.remote and u.country == loc.country for u in uniq):
            uniq.append(loc)
    return uniq


def is_non_us(loc: Location) -> bool:
    return loc.country is not None and loc.country != "US"


def parse_config_location(entry: str) -> Location | str:
    """Config entries: "remote_us", "anywhere_us", "Austin, TX", "TX", "Texas"."""
    e = entry.strip()
    if e.lower() in {"remote_us", "anywhere_us"}:
        return e.lower()
    locs = parse_segment(e)
    if not locs:
        raise ValueError(f"can't understand location '{entry}'")
    return locs[0]


def location_matches(want: Location | str, loc: Location) -> bool:
    if want == "remote_us":
        return loc.remote and loc.country in (None, "US")
    if want == "anywhere_us":
        return loc.is_us or (loc.remote and loc.country in (None, "US"))
    assert isinstance(want, Location)
    if want.city:
        return loc.city == want.city and (loc.state is None or want.state is None or loc.state == want.state)
    if want.state:
        return loc.state == want.state
    return False
