import pytest

from jobradar.locations import location_matches, parse_config_location, parse_locations


def parsed(value):
    return [(l.city, l.state, l.country, l.remote) for l in parse_locations(value)]


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("Austin, TX", [("austin", "TX", "US", False)]),
        ("Austin, Texas Metropolitan Area", [("austin", "TX", "US", False)]),
        ("New York, NY or San Francisco, CA", [("new york", "NY", "US", False), ("san francisco", "CA", "US", False)]),
        ("NYC", [("new york", "NY", "US", False)]),
        ("PA - Pittsburgh", [("pittsburgh", "PA", "US", False)]),  # Workday ordering
        ("PA, Pittsburgh, PA, 15222", [("pittsburgh", "PA", "US", False)]),
        ("New York, NY | Zone 1 | Job Requisitions Only", [("new york", "NY", "US", False)]),
        ("Chicago, IL - 155 Wacker", [("chicago", "IL", "US", False)]),
        ("Remote - US", [(None, None, "US", True)]),
        ("Remote (Canada)", [(None, None, "CA-country", True)]),
        ("United States", [(None, None, "US", False)]),
        ("Multiple Locations", []),
        ("London, England, United Kingdom", [("london", None, "GB", False)]),
        ("Tokyo,JP", [("tokyo", None, "JP", False)]),
        ("Indianapolis, IN", [("indianapolis", "IN", "US", False)]),  # IN is Indiana, not India
        ("Seattle, Washington", [("seattle", "WA", "US", False)]),
        ("Washington, DC", [("washington", "DC", "US", False)]),
        ("Santa Clara, CALIFORNIA, United States", [("santa clara", "CA", "US", False)]),
    ],
)
def test_parse(raw, expected):
    assert parsed(raw) == expected


def test_is_remote_flag_adds_remote_location():
    locs = parse_locations(["New York, NY"], is_remote=True)
    assert any(l.remote for l in locs)


def test_config_matching():
    austin = parse_config_location("Austin, TX")
    [loc] = parse_locations("Austin, Texas")
    assert location_matches(austin, loc)
    [remote] = parse_locations("Remote - US")
    assert location_matches("remote_us", remote)
    [canada] = parse_locations("Remote (Canada)")
    assert not location_matches("remote_us", canada)
    [bare_remote] = parse_locations("Remote")
    assert location_matches("remote_us", bare_remote)
    tx = parse_config_location("TX")
    [houston] = parse_locations("Houston, TX")
    assert location_matches(tx, houston)
