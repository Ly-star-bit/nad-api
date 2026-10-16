"""The verification pipeline against a fake upstream (no network)."""

import asyncio

from app.address import parse_oneline
from app.upstream import UpstreamError, nad_record
from app.verify import verify_address

ORIGIN = (33.4484, -112.0957)


def nad(number: int, name: str = "Washington", typ: str = "Street", lat: float = ORIGIN[0]) -> dict:
    return nad_record({
        "Add_Number": number, "St_PreDir": "West", "St_Name": name, "St_PosTyp": typ,
        "StNam_Full": f"West {name} {typ}", "Post_City": "Phoenix", "State": "AZ", "Zip_Code": "85007",
        "Latitude": lat, "Longitude": ORIGIN[1],
    })


class FakeUpstream:
    def __init__(self, street=(), geocode_error=None, alternatives=None):
        self.street = list(street)  # every NAD point around the origin
        self.geocode_error = geocode_error
        self.alternatives = alternatives
        self.searches = []  # radii of the house-number searches

    async def census_geocode_oneline(self, text):
        if self.geocode_error:
            raise UpstreamError("census_geocoder", self.geocode_error)
        g = {"matched_address": "1716 W WASHINGTON ST, PHOENIX, AZ, 85007", "lat": ORIGIN[0], "lon": ORIGIN[1],
             "components": {}, "county": "Maricopa", "state": "AZ", "zip": "85007", "match_count": 1}
        if self.alternatives:
            g.update(match_count=1 + len(self.alternatives), alternatives=self.alternatives)
        return g

    async def resolve_coverage(self, lat, lon, county=None, state=None):
        return {"method": "point", "county": "Maricopa", "state": "AZ", "status_code": 10, "status": "Data in the NAD", "in_nad": True}

    async def nad_near(self, lat, lon, radius, where="1=1", limit=2000):
        if where == "1=1":
            return list(self.street), False
        self.searches.append(radius)
        number = int(where.split("=")[1])
        return [r for r in self.street if r["address_number_int"] == number], False


def run(up, text):
    return asyncio.run(verify_address(up, parse_oneline(text), overture=None))


def test_unreachable_geocoder_is_an_upstream_error_not_a_bad_address():
    r = run(FakeUpstream(geocode_error="connection failed: ConnectError (SSLEOFError: EOF)"), "1700 W Washington St, Phoenix, AZ 85007")
    assert r["status"] == "upstream_error"
    assert any("census geocoder unavailable" in n for n in r["notes"])
    assert not any("could not be geocoded" in n for n in r["notes"])


def test_bracketed_number_skips_the_wide_search():
    up = FakeUpstream(street=[nad(1700), nad(1702), nad(1818)])
    r = run(up, "1716 W Washington St, Phoenix, AZ 85007")
    assert r["status"] == "street_only"
    assert up.searches == [300]  # 1700 and 1818 are within reach, so 1500 m was never searched
    assert [s["address_number_int"] for s in r["suggestions"]][:2] == [1702, 1700]


def test_unbracketed_number_still_searches_wider():
    up = FakeUpstream(street=[nad(1700), nad(1702)])
    r = run(up, "1900 W Washington St, Phoenix, AZ 85007")
    assert up.searches == [300, 1500]
    assert r["status"] == "street_only"


def test_other_numbered_street_is_not_a_match():
    up = FakeUpstream(street=[nad(351, name="3", typ="Avenue", lat=ORIGIN[0] + 0.008)])
    r = run(up, "351 5th Ave, Phoenix, AZ 85007")
    assert r["status"] != "verified"


def test_ambiguous_geocode_is_reported():
    alts = [{"matched_address": "100 MAIN ST, SPRINGFIELD, MA, 01105", "lat": 42.1, "lon": -72.6}]
    r = run(FakeUpstream(street=[nad(1716)], alternatives=alts), "1716 W Washington St, Springfield")
    assert any("Census found 2 addresses" in n and "SPRINGFIELD, MA" in n for n in r["notes"])


def test_rejected_input_is_not_an_outage():
    class Rejecting(FakeUpstream):
        async def census_geocode_oneline(self, text):
            from app.upstream import UpstreamRejected
            raise UpstreamRejected("census_geocoder", "HTTP 400: Specify House number and Street name")

    r = run(Rejecting(), "100 Main St")
    assert r["status"] == "geocode_failed"
    assert any("rejected" in n for n in r["notes"])


def test_bracketing_with_house_number_suffixes():
    # 13 sits between 12 and 14: not found, and no wide search
    up = FakeUpstream(street=[nad(12), nad(14)])
    assert run(up, "13 W Washington St, Phoenix, AZ 85007")["status"] == "street_only"
    assert up.searches == [300]
    # 12A: the 12 record matches the street, so it is verified (with a note) before bracketing comes into play
    up = FakeUpstream(street=[nad(12), nad(14)])
    r = run(up, "12A W Washington St, Phoenix, AZ 85007")
    assert r["status"] == "verified" and up.searches == [300]
    assert any("suffix differs" in n for n in r["notes"])
