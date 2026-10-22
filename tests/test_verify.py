"""The verification pipeline against a fake upstream (no network)."""

import asyncio
import re

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


class NotInTiger(FakeUpstream):
    """Census has no match (a new street); NAD has it in a ZIP area of the named city."""

    def __init__(self, records=(), area=True, city_error=None, in_nad=True):
        super().__init__()
        self.records = list(records)
        self.area = area
        self.city_error = city_error
        self.in_nad = in_nad
        self.wheres = []

    async def census_geocode_oneline(self, text):
        return None

    async def city_area(self, city, state):
        if self.city_error:
            raise UpstreamError("tigerweb", self.city_error)
        return {"zips": ["35756", "35757"], "extent": {"xmin": -86.9, "ymin": 34.5, "xmax": -86.6, "ymax": 34.9}} if self.area else None

    async def zcta_extent(self, zip5):
        return {"xmin": -86.9, "ymin": 34.5, "xmax": -86.7, "ymax": 34.7}

    async def nad_in_envelope(self, extent, where, limit=200):
        self.wheres.append(where)
        number = re.match(r"Add_Number = (\d+) AND ", where)
        return [r for r in self.records if not number or r["address_number_int"] == int(number.group(1))]

    async def resolve_coverage(self, lat, lon, county=None, state=None):
        return {"method": "point", "county": "Limestone", "state": "AL", "status_code": 10 if self.in_nad else 0,
                "status": "Data in the NAD" if self.in_nad else "No data", "in_nad": self.in_nad}


FANNING_ATTRS = {
    "Add_Number": 28869, "AddNo_Full": "28869 <Null>", "St_Name": "Fanning", "St_PosTyp": "Drive", "St_PosDir": "Northwest",
    "StNam_Full": "Fanning Drive Northwest", "Inc_Muni": "Huntsville", "Post_City": "MADISON", "State": "AL",
    "Zip_Code": "35756", "Latitude": 34.6576, "Longitude": -86.8094,
}
FANNING = nad_record(FANNING_ATTRS)


def test_city_search_finds_a_street_missing_from_tiger():
    up = NotInTiger([FANNING])
    r = run(up, "28869 FANNING DR NW,MADISON,AL US")
    assert r["status"] == "verified" and r["data_source"] == "nad"
    assert r["geocode"]["source"] == "city" and "35756" in r["geocode"]["zips"]
    assert up.wheres == ["Add_Number = 28869 AND St_Name LIKE '%FANNING%'"]


def test_street_missing_everywhere_is_unknown_not_absent():
    # 1410 United Dr, Shippensburg, PA: a new building on a new street that no address dataset has yet
    up = NotInTiger([])
    r = run(up, "28869 Nosuch Dr NW, Madison, AL")
    assert r["status"] == "geocode_failed" and r["nad_status"] == "not_found"
    assert any("newer than the data" in n and "ZIP areas of Madison, AL" in n for n in r["notes"])
    assert up.wheres[-1] == "St_Name LIKE '%NOSUCH%'"  # asked whether the street itself is there


def _place(freeform, name="Walmart", **row):
    from app.overture import place_record
    return place_record({"id": "p1", "name": name, "freeform": freeform, "locality": "Shippensburg",
                         "postcode": "17257-9552", "region": "PA", "category": "shopping", "confidence": 0.92,
                         "operating_status": "open", "lon": -77.5274, "lat": 40.0150, "dataset": "meta", **row})


def test_business_listed_at_the_address_verifies_it():
    # 1410 United Dr, Shippensburg, PA: on a new street no address dataset has, but Overture lists a business there
    for text, score in (("1410 United Dr, Shippensburg, PA 17257", 0.95), ("1410 United Dr, Shippensburg, PA", 0.9)):
        r = asyncio.run(verify_address(NotInTiger([]), parse_oneline(text), overture=EmptyOverture([_place("1410 United Dr")])))
        assert r["status"] == "verified" and r["data_source"] == "overture_places" and r["nad_status"] == "not_found"
        assert r["confidence"] == round(score * 0.8, 3)
        assert r["best_match"]["place"]["name"] == "Walmart" and r["best_match"]["source"] == "overture_places:meta"
        assert not r["fallback"]["adopted"] and r["fallback"]["places"]["triggered_by"] == "not_found"
        assert r["fallback"]["places"]["adopted"] and r["fallback"]["places"]["status"] == "verified"
        assert r["parsed"]["source"] == "record" and r["parsed"]["city"] == "Shippensburg"
        assert any("'Walmart' (meta) is listed at it" in n for n in r["notes"])
        # what the earlier steps said before the listing turned up no longer holds
        assert any("United Dr' in " in n and n.endswith("likely because the street is newer than the data")
                   for n in r["notes"])
        assert not any("misspelled" in n or "kept the NAD result" in n for n in r["notes"])
        assert "Overture fallback (2026-09-23.1) returned 'not_found'" in r["notes"]


def test_business_on_another_street_or_number_proves_nothing():
    for place in ("1410 Walnut Bottom Rd", "1400 United Dr"):
        ov = EmptyOverture([_place(place)])
        r = asyncio.run(verify_address(NotInTiger([]), parse_oneline("1410 United Dr, Shippensburg, PA 17257"), overture=ov))
        assert r["status"] == "geocode_failed" and r["data_source"] == "nad" and r["best_match"] is None, place
        assert r["fallback"]["places"]["status"] == "not_found" and not r["fallback"]["places"]["adopted"]


def test_other_numbers_on_the_street_make_it_street_only():
    neighbour = nad_record({**{k: v for k, v in FANNING_ATTRS.items() if k != "AddNo_Full"}, "Add_Number": 28860})
    r = run(NotInTiger([neighbour]), "28869 Fanning Dr NW, Madison, AL")
    assert r["status"] == "street_only" and r["suggestions"][0]["address_number_int"] == 28860


def test_city_search_in_a_county_outside_nad_is_not_covered():
    assert run(NotInTiger([], in_nad=False), "28869 Nosuch Dr, Madison, AL")["status"] == "not_covered"


def test_city_search_needs_a_known_city():
    r = run(NotInTiger([FANNING], area=False), "28869 Fanning Dr NW, Madisonn, AL")
    assert r["status"] == "geocode_failed"
    assert any("not a Census place" in n for n in r["notes"])
    up = NotInTiger([FANNING])
    assert run(up, "28869 Fanning Dr NW")["status"] == "geocode_failed" and up.wheres == []


def test_city_lookup_outage_is_an_upstream_error():
    r = run(NotInTiger([FANNING], city_error="HTTP 503"), "28869 Fanning Dr NW, Madison, AL")
    assert r["status"] == "upstream_error"


def test_name_token_survives_normalization():
    from app.address import StreetKey
    from app.verify import _name_token
    assert _name_token(StreetKey.from_text("Saint Charles Ave")) == "CHARLES"
    assert _name_token(StreetKey.from_text("W 5th St")) == "5"
    assert _name_token(StreetKey.from_text("O'Neal Rd")) == "NEAL"  # LIKE '%NEAL%' still finds O'Neal


def test_zip_area_is_searched_for_the_street_when_census_misses():
    up = NotInTiger([FANNING])
    r = run(up, "28869 Fanning Dr NW, Madison, AL 35756")
    assert r["status"] == "verified" and r["geocode"] == {"source": "zcta", "zip": "35756", "extent": r["geocode"]["extent"]}
    assert up.wheres == ["Add_Number = 28869 AND St_Name LIKE '%FANNING%'"]


class EmptyOverture:
    """Overture with no address points in the area (Savannah, GA); `places` are its business listings."""

    def __init__(self, places=()):
        self.places, self.themes = list(places), []

    async def release(self, refresh=False):
        return "2026-09-23.1"

    async def query(self, bbox, number=None, limit=5000, timeout=None, any_part=False, theme="addresses"):
        self.themes.append(theme)
        return self.places if theme == "places" else []


def test_overture_miss_over_an_area_keeps_not_covered():
    # Georgia is not in NAD and Overture has nothing around Port Wentworth: still "can't tell", not "doesn't exist"
    up, ov = NotInTiger([], in_nad=False), EmptyOverture()
    r = asyncio.run(verify_address(up, parse_oneline("120 CROSSGATE DR,PORT WENTWORTH,GA US"), overture=ov))
    assert r["status"] == "not_covered" and r["fallback"]["status"] == "not_found" and not r["fallback"]["adopted"]
    # no business listed there either: a places miss says nothing about the address
    assert ov.themes == ["addresses", "places"] and r["fallback"]["places"]["status"] == "not_found"
    assert not r["fallback"]["places"]["adopted"] and r["data_source"] == "nad"


class NotInNad(FakeUpstream):
    """Census places the address in a county missing from NAD."""

    async def resolve_coverage(self, lat, lon, county=None, state=None):
        return {"method": "point", "county": "Maricopa", "state": "AZ", "status_code": 0, "status": "No data", "in_nad": False}


class OtherStreetOverture(EmptyOverture):
    """Overture has address points nearby, none on this street."""

    async def query(self, bbox, number=None, limit=5000, timeout=None, any_part=False, theme="addresses"):
        if theme == "addresses" and number is None:
            from app.overture import overture_record
            return [overture_record({"id": "a1", "number": "5", "street": "N 17th Ave", "lon": ORIGIN[1], "lat": ORIGIN[0]})]
        return await super().query(bbox, number, limit, timeout, any_part, theme)


def test_listing_after_an_adopted_overture_miss_says_one_answer():
    # NAD has no data there and Overture's not_found was adopted; then a business listing verifies it
    text = "1716 W Washington St, Phoenix, AZ 85007"
    r = asyncio.run(verify_address(NotInNad(), parse_oneline(text), overture=OtherStreetOverture()))
    assert r["data_source"] == "overture" and r["fallback"]["adopted"]  # without the listing
    place = _place("1716 W Washington St", locality="Phoenix", postcode="85007", region="AZ", lat=ORIGIN[0], lon=ORIGIN[1])
    r = asyncio.run(verify_address(NotInNad(), parse_oneline(text), overture=OtherStreetOverture([place])))
    assert r["status"] == "verified" and r["data_source"] == "overture_places" and r["nad_status"] == "not_covered"
    assert r["fallback"]["status"] == "not_found" and not r["fallback"]["adopted"] and r["fallback"]["places"]["adopted"]
    assert [n for n in r["notes"] if "answered from" in n] == [next(n for n in r["notes"] if "places: 'Walmart'" in n)]
    assert "Overture fallback (2026-09-23.1) returned 'not_found'" in r["notes"]


def _street_notes(text):
    r = run(FakeUpstream(street=[nad(1700), nad(1702)]), text)
    assert r["status"] == "verified"
    return [n for n in r["notes"] if n.startswith("street written")]


def test_standard_abbreviations_say_nothing_about_the_street():
    assert _street_notes("1700 West Washington Street, Phoenix, AZ 85007") == []
    assert _street_notes("1700 W Washington St, Phoenix, AZ 85007") == []


def test_misspelled_street_names_the_record():
    assert _street_notes("1700 W Washingtn St, Phoenix, AZ 85007") == [
        "street written 'W Washingtn St', NAD record has 'West Washington Street'"]


def test_left_out_direction_or_type_names_the_record():
    assert _street_notes("1700 Washington St, Phoenix, AZ 85007") == [
        "street written 'Washington St', NAD record has 'West Washington Street'"]
    assert _street_notes("1700 W Washington, Phoenix, AZ 85007") == [
        "street written 'W Washington', NAD record has 'West Washington Street'"]
