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
        # full-width commas / digits from a Chinese IME
        ("1700 W Washington St，Phoenix，AZ 85007", 1700, "", "W Washington St", "", "Phoenix", "AZ", "85007"),
        ("１７００ W Washington St, Phoenix, AZ 85007", 1700, "", "W Washington St", "", "Phoenix", "AZ", "85007"),
        # every designator leaves the street, the last one is the unit
        ("100 Main St Bldg 2 Apt 5, Springfield, IL 62701", 100, "", "Main St", "5", "Springfield", "IL", "62701"),
        ("123 Main St PMB 456, Phoenix, AZ 85007", 123, "", "Main St", "456", "Phoenix", "AZ", "85007"),
        # unit words that are street names
        ("100 Pier Ave, Hermosa Beach, CA 90254", 100, "", "Pier Ave", "", "Hermosa Beach", "CA", "90254"),
        ("2100 Space Park Dr, Houston, TX 77058", 2100, "", "Space Park Dr", "", "Houston", "TX", "77058"),
        # full state names, with and without commas
        ("1700 W Washington St, Phoenix, Arizona 85007", 1700, "", "W Washington St", "", "Phoenix", "AZ", "85007"),
        ("350 5th Ave New York New York 10118", 350, "", "5th Ave", "", "New York", "NY", "10118"),
        ("1 Main St, Charleston, West Virginia 25301", 1, "", "Main St", "", "Charleston", "WV", "25301"),
        # ZIP that lost its leading zero in a spreadsheet; a typo'd ZIP keeps the state
        ("1 Beacon St, Boston, MA 2108", 1, "", "Beacon St", "", "Boston", "MA", "02108"),
        ("12 Elm St, Phoenix, AZ 8500", 12, "", "Elm St", "", "Phoenix", "AZ", ""),
        ("100½ Oak Ave, Austin TX 78701", 100, "1/2", "Oak Ave", "", "Austin", "TX", "78701"),
        # floors written as ordinals, alone or in the street line
        ("100 Main St, 2nd Floor, Springfield, IL 62701", 100, "", "Main St", "2", "Springfield", "IL", "62701"),
        ("100 Main St 2nd Fl, Springfield, IL 62701", 100, "", "Main St", "2", "Springfield", "IL", "62701"),
        # a country at the end (order exports), with and without commas
        ("1700 W Washington St, Phoenix, AZ 85007, USA", 1700, "", "W Washington St", "", "Phoenix", "AZ", "85007"),
        ("1700 W Washington St Phoenix AZ 85007 US", 1700, "", "W Washington St", "", "Phoenix", "AZ", "85007"),
        # Census' own format puts the ZIP in a segment of its own
        ("100 MAIN ST, SPRINGFIELD, MA, 01151", 100, "", "MAIN ST", "", "SPRINGFIELD", "MA", "01151"),
        ("123-A Main St, Phoenix, AZ 85007", 123, "A", "Main St", "", "Phoenix", "AZ", "85007"),
        # line breaks inside a spreadsheet cell
        ("123 Main St\nApt 5\nPhoenix, AZ 85007", 123, "", "Main St", "5", "Phoenix", "AZ", "85007"),
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


def test_lone_full_state_name_stays_a_city():
    pa = parse_oneline("123 Main St, Washington")
    assert (pa.city, pa.state) == ("Washington", "")


def test_parse_oneline_without_number():
    pa = parse_oneline("asdkjh qwe zzz")
    assert pa.house is None
    assert pa.warnings


def test_parse_structured():
    pa = parse_structured("11 W Washington St", "Phoenix", "az", "85003-1234", "Suite 240")
    assert pa.house.number == 11
    assert pa.unit == "240"
    assert (pa.state, pa.zip5, pa.zip4) == ("AZ", "85003", "1234")


def test_parse_structured_spreadsheet_values():
    pa = parse_structured("1 Beacon St", "Boston", "Massachusetts", "2108")
    assert (pa.state, pa.zip5) == ("MA", "02108")
    assert "02108" in pa.warnings[0]
    # only padded when the result is a ZIP of that state
    pa = parse_structured("1 Main St", "Phoenix", "AZ", "8500")
    assert pa.zip5 == "" and "unrecognized" in pa.warnings[0]


def test_po_box_gets_a_specific_warning():
    pa = parse_oneline("P.O. Box 123, Phoenix, AZ 85001")
    assert pa.house is None
    assert "PO Box" in pa.warnings[0]


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
        (("", "", "5TH", "AVE", ""), ("", "", "3", "Avenue", "")),  # 0.8 as text
        (("W", "", "34TH", "ST", ""), ("West", "", "35", "Street", "")),
        (("", "US HWY", "101", "", ""), ("", "US Highway", "10", "", "")),
        (("NW", "", "5TH", "ST", ""), ("Northwest", "", "5", "Avenue", "")),  # grid: these cross each other
    ],
)
def test_street_score_rejects(query, cand):
    assert street_score(StreetKey.from_parts(*query), StreetKey.from_parts(*cand)) < 0.8


def test_street_key_from_text():
    k = StreetKey.from_text("North Main Street SW")
    assert (k.predir, k.core, k.posttype, k.postdir) == ("N", "MAIN", "ST", "SW")


@pytest.mark.parametrize("raw, expected", [("Apt. #4-B", "4B"), ("Unit 12", "12"), ("STE 1750", "1750"), ("#3F", "3F"),
                                           ("Unit 0012", "12"), ("002A", "2A"), ("0", "0")])
def test_norm_unit(raw, expected):
    assert norm_unit(raw) == expected
