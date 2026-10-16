"""Offline tests for the Overture record mapping and the shared decision logic (no network)."""

import pytest

from app.address import StreetKey, parse_oneline
from app.overture import _parse_number, overture_record
from app.verify import STATUS_RANK, bbox_around, decide, score_candidate

ROW = {
    "id": "2d965367-7fd0-4dd1-9760-f4d0b76eb81b", "number": "1600", "street": "AMPHITHEATRE PKWY", "unit": None,
    "postcode": "94043", "postal_city": None, "address_levels": [{"value": "CA"}, {"value": "MOUNTAIN VIEW"}],
    "lon": -122.0841, "lat": 37.4223, "dataset": "us/ca/santa_clara",
    "license": "LicenseRef-Public-Domain-US-CA-Law", "update_time": "2026-08-01T00:00:00Z",
}


@pytest.mark.parametrize("raw, expected", [
    ("1700", (1700, "")), ("12A", (12, "A")), ("350 B", (350, "B")), ("100 1/2", (100, "1/2")),
    ("45-12", (45, "-12")), ("N95W18000", (None, "")), (None, (None, "")),
])
def test_parse_number(raw, expected):
    assert _parse_number(raw) == expected


def test_overture_record_mapping():
    rec = overture_record(ROW)
    assert rec["address_number_int"] == 1600
    assert (rec["state"], rec["city"], rec["zip"]) == ("CA", "MOUNTAIN VIEW", "94043")
    assert rec["source"] == "overture:us/ca/santa_clara"
    assert rec["license"] == "LicenseRef-Public-Domain-US-CA-Law"
    assert rec["updated"] == "2026-08-01"
    assert rec["formatted"] == "1600 AMPHITHEATRE PKWY, MOUNTAIN VIEW, CA 94043"


def test_overture_city_prefers_municipality_over_postal_city():
    rec = overture_record({**ROW, "postal_city": "Capitol", "address_levels": [{"value": "AZ"}, {"value": "Phoenix"}]})
    assert rec["city"] == "Phoenix"


def test_zip4_split():
    rec = overture_record({**ROW, "postcode": "94043-1351"})
    assert (rec["zip"], rec["zip4"]) == ("94043", "1351")


def test_decide_on_overture_records():
    pa = parse_oneline("1600 Amphitheatre Pkwy, Mountain View, CA 94043")
    keys = [StreetKey.from_text(pa.street_text)]
    scored = [score_candidate(overture_record(ROW), pa, keys, (37.4220, -122.0840), "94043")]
    d = decide(pa, scored, [], dataset="Overture", ref_zip="94043", max_candidates=5)
    assert d.status == "verified" and d.match_level == "address"
    assert d.best_match["match"]["number_exact"] and d.best_match["match"]["zip_match"]


def test_decide_unit_not_found_lists_units_naturally():
    pa = parse_oneline("108 Bryant St Apt 99, Mountain View, CA 94041")
    keys = [StreetKey.from_text(pa.street_text)]
    rows = [overture_record({**ROW, "number": "108", "street": "BRYANT ST", "unit": f"#{n}", "postcode": "94041"})
            for n in (10, 2, 1)]
    scored = [score_candidate(r, pa, keys, None, "94041") for r in rows]
    d = decide(pa, scored, [], dataset="Overture", ref_zip="94041", max_candidates=5)
    assert d.status == "unit_not_found"
    assert "#1, #2, #10" in d.notes[-1]


def test_status_rank_prefers_answers():
    assert STATUS_RANK["verified"] > STATUS_RANK["street_only"] > STATUS_RANK["not_found"] > STATUS_RANK["not_covered"]
    assert STATUS_RANK["not_covered"] > STATUS_RANK["upstream_error"]


def test_bbox_around_is_roughly_square_in_meters():
    b = bbox_around(37.0, -122.0, 1000)
    assert abs((b["ymax"] - b["ymin"]) * 111_320 - 2000) < 1
    assert b["xmax"] - b["xmin"] > b["ymax"] - b["ymin"]  # longitude degrees are shorter away from the equator


def test_user_directional_beats_census_correction():
    """Census geocoded '200 N Spring St' as '200 S SPRING ST'; the user's N must still win."""
    pa = parse_oneline("200 N Spring St, Los Angeles, CA 90012")
    census = StreetKey.from_parts("S", "", "SPRING", "ST", "")
    keys = [census, StreetKey.from_text(pa.street_text)]
    south = overture_record({**ROW, "number": "200", "street": "South Spring Street", "postcode": "90012"})
    north = overture_record({**ROW, "number": "200", "street": "North Spring Street", "postcode": "90012"})
    s = score_candidate(south, pa, keys, None, "90012")["match"]["street_score"]
    n = score_candidate(north, pa, keys, None, "90012")["match"]["street_score"]
    assert n == 1.0 and s < 0.8
