"""Offline tests for parsing / normalization (no network)."""

import pytest

from app.address import StreetKey, norm_unit, parse_oneline, parse_structured, street_score


@pytest.mark.parametrize(
    "text, number, suffix, street, unit, city, state, zip5",
    [
        ("1700 W Washington St Apt 2, Phoenix, AZ 85007", 1700, "", "W Washington St", "2", "Phoenix", "AZ", "85007"),
        ("350 5th Ave, New York, NY 10118", 350, "", "5th Ave", "", "New York", "NY", "10118"),
        ("123 Main St Miami FL 33101", 123, "", "Main St", "", "Miami", "FL", "33101"),
        ("123 Main St Apt 4 Miami FL 33101", 123, "", "Main St", "4", "Miami", "FL", "33101"),
        ("123 Main St, Apt 4, Springfield, IL 62701", 123, "", "Main St", "4", "Springfield", "IL", "62701"),
        ("45-12 Queens Blvd #3F, Sunnyside NY 11104", 45, "-12", "Queens Blvd", "3F", "Sunnyside", "NY", "11104"),
        ("12A Elm Street Rear, Boston, MA", 12, "A", "Elm Street", "REAR", "Boston", "MA", ""),
        ("100 1/2 Oak Ave, Austin TX 78701", 100, "1/2", "Oak Ave", "", "Austin", "TX", "78701"),
        ("1 Park Ave New York NY 10016", 1, "", "Park Ave", "", "New York", "NY", "10016"),
    ],
)
def test_parse_oneline(text, number, suffix, street, unit, city, state, zip5):
    pa = parse_oneline(text)
    assert pa.house is not None
    assert (pa.house.number, pa.house.suffix) == (number, suffix)
    assert pa.street_text == street
    assert pa.unit == unit
    assert (pa.city, pa.state, pa.zip5) == (city, state, zip5)
    assert "apt" not in pa.geocode_text.lower()


def test_parse_oneline_without_number():
    pa = parse_oneline("asdkjh qwe zzz")
    assert pa.house is None
    assert pa.warnings


def test_parse_structured():
    pa = parse_structured("11 W Washington St", "Phoenix", "az", "85003-1234", "Suite 240")
    assert pa.house.number == 11
    assert pa.unit == "240"
    assert (pa.state, pa.zip5, pa.zip4) == ("AZ", "85003", "1234")


@pytest.mark.parametrize(
    "query, cand, at_least",
    [
        (("W", "", "WASHINGTON", "ST", ""), ("West", "", "Washington", "Street", ""), 1.0),
        (("", "", "5TH", "AVE", ""), ("", "", "5", "Avenue", ""), 1.0),
        (("", "", "FIFTH", "AVE", ""), ("", "", "5th", "Avenue", ""), 1.0),
        (("W", "", "LAKEVIEW", "RD", ""), ("West", "", "LAKE VIEW", "Road", ""), 0.9),
        (("", "", "AMPHITHEATRE", "PKWY", ""), ("", "", "Amphitheatre", "Parkway", ""), 1.0),
    ],
)
def test_street_score_matches(query, cand, at_least):
    assert street_score(StreetKey.from_parts(*query), StreetKey.from_parts(*cand)) >= at_least


@pytest.mark.parametrize(
    "query, cand",
    [
        (("E", "", "WASHINGTON", "ST", ""), ("West", "", "Washington", "Street", "")),  # opposite direction
        (("W", "", "WASHINGTON", "ST", ""), ("West", "", "Linden", "Street", "")),  # different street
    ],
)
def test_street_score_rejects(query, cand):
    assert street_score(StreetKey.from_parts(*query), StreetKey.from_parts(*cand)) < 0.8


def test_street_key_from_text():
    k = StreetKey.from_text("North Main Street SW")
    assert (k.predir, k.core, k.posttype, k.postdir) == ("N", "MAIN", "ST", "SW")


@pytest.mark.parametrize("raw, expected", [("Apt. #4-B", "4B"), ("Unit 12", "12"), ("STE 1750", "1750"), ("#3F", "3F")])
def test_norm_unit(raw, expected):
    assert norm_unit(raw) == expected
