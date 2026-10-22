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


@pytest.mark.parametrize("number, street, levels, expected", [
    ("1", "Rātslaukums 1, Rīga, LV-1050", ["Rīga LV-1050"], ("Rātslaukums", "Rīga", None)),
    ("2C", "Ventas iela 2C, Sigulda, Siguldas nov., LV-2150", ["Siguldas nov. LV-2150", "Sigulda Siguldas nov., LV-2150"],
     ("Ventas iela", "Sigulda", "Siguldas nov.")),
    ("21", "Egļu iela 21, Ozolnieki, Ozolnieku pag., Jelgavas nov., LV-3018",
     ["Ozolnieku pag. Jelgavas nov., LV-3018", "Ozolnieki Ozolnieku pag., Jelgavas nov., LV-3018"],
     ("Egļu iela", "Ozolnieki", "Jelgavas nov.")),
    ("7", "Ērgļu iela 7, Jelgava, LV-300", ["Jelgava LV-3007"], ("Ērgļu iela", "Jelgava", None)),  # cut-off postcode
])
def test_latvian_rows_carry_the_whole_line_as_the_street(number, street, levels, expected):
    rec = overture_record({"id": "x", "number": number, "street": street, "postcode": "LV-1050", "dataset": "lv/countrywide",
                           "address_levels": [{"value": v} for v in levels]})
    assert (rec["street"], rec["city"], rec["state"]) == expected
    assert rec["municipality"] == expected[1] and rec["address_number"] == number


def test_a_comma_in_a_street_name_is_not_a_whole_line():
    rec = overture_record({**ROW, "number": "12", "street": "Kalscheurer Weg,Weg S"})  # Köln's allotment paths
    assert (rec["street"], rec["city"]) == ("Kalscheurer Weg,Weg S", "MOUNTAIN VIEW")


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


def test_warmup_uses_its_own_timeout_and_retries():
    import asyncio

    from app import config
    from app.overture import OvertureClient
    from app.upstream import UpstreamError

    calls = []

    class Client(OvertureClient):
        async def query(self, bbox, number=None, limit=5000, timeout=None, theme="addresses"):
            calls.append((theme, timeout))
            if len(calls) == 2:
                raise UpstreamError("overture", "query timed out")
            return []

    asyncio.run(Client(http=None).warmup(retry_after_s=(0, 0)))
    # places failed once, both ran again on the retry (addresses would be cached), then it stopped
    assert calls == [(t, config.OVERTURE_WARMUP_TIMEOUT_S) for t in ("addresses", "places") * 2]


def test_unit_written_with_its_building():
    from app.upstream import nad_record

    rec = nad_record({"Add_Number": 10, "St_Name": "Elm", "St_PosTyp": "Street", "StNam_Full": "Elm Street",
                      "Building": "B", "Unit": "201", "Latitude": 1.0, "Longitude": 1.0})
    for text in ("10 Elm St Apt B201", "10 Elm St Unit B-201", "10 Elm St Apt 201"):
        pa = parse_oneline(text)
        assert score_candidate(rec, pa, [StreetKey.from_text(pa.street_text)], None, "")["match"]["unit_match"], text


def test_null_text_copied_from_nad_is_empty():
    from app.overture import overture_record
    rec = overture_record({"id": "x", "number": "28869 <Null>", "street": "Fanning Drive Northwest", "unit": "<Null>",
                           "address_levels": [{"value": "AL"}], "dataset": "NAD"})
    assert rec["address_number"] == "28869" and rec["address_number_suffix"] is None
    assert rec["subaddress"]["unit"] is None


def _fake_duckdb(monkeypatch, broken: int):
    """duckdb.connect() opens fake connections; the first `broken` of them were invalidated by a fatal error."""
    import duckdb

    opened = []

    class Cursor:
        def __init__(self, ok):
            self.ok, self.description = ok, [("id",), ("number",)]

        def execute(self, sql, params=None):
            if not self.ok:
                raise duckdb.FatalException("FATAL Error: Failed: database has been invalidated because of a previous "
                                            "fatal error. The database must be restarted prior to being used again.")
            return self

        def fetchall(self):
            return [("x", "1410")]

        def close(self):
            pass

    class Con:
        def __init__(self):
            self.ok = len(opened) >= broken
            opened.append(self)

        def execute(self, sql, params=None):
            return self

        def cursor(self):
            return Cursor(self.ok)

    monkeypatch.setattr(duckdb, "connect", Con)
    return opened


def test_invalidated_database_is_reopened(monkeypatch):
    import asyncio

    from app.overture import OvertureClient

    opened = _fake_duckdb(monkeypatch, broken=1)
    client = OvertureClient(http=None)
    rows = asyncio.run(client._run("2026-10-21.0", {"xmin": 0, "xmax": 1, "ymin": 0, "ymax": 1}, None, 1, 5.0))
    assert rows == [{"id": "x", "number": "1410"}] and len(opened) == 2 and client._con is opened[1]


def test_invalidated_twice_is_an_upstream_error(monkeypatch):
    import asyncio

    from app.overture import OvertureClient
    from app.upstream import UpstreamError

    opened = _fake_duckdb(monkeypatch, broken=2)
    client = OvertureClient(http=None)
    with pytest.raises(UpstreamError, match="invalidated"):
        asyncio.run(client._run("2026-10-21.0", {"xmin": 0, "xmax": 1, "ymin": 0, "ymax": 1}, None, 1, 5.0))
    assert len(opened) == 2 and client._con is None  # retried once, and the next query opens a third


PLACE = {"id": "08985419-933b-4385-9d9b-73619b75ec0c", "name": "Walmart", "freeform": "1410 United Dr",
         "locality": "Shippensburg", "postcode": "17257-9552", "region": "PA", "category": "shopping",
         "confidence": 0.92, "operating_status": "open", "lon": -77.5274, "lat": 40.0150, "dataset": "meta",
         "license": "CDLA-Permissive-2.0", "update_time": "2026-10-12T00:00:00.000Z"}


@pytest.mark.parametrize("freeform, number, street, unit", [
    ("1410 United Dr", "1410", "United Dr", None),
    ("4532 United Dr Ste 100", "4532", "United Dr", "Ste 100"),
    ("1410 United Dr, Ste 100", "1410", "United Dr", "Ste 100"),
    ("1410 United Dr, Shippensburg", "1410", "United Dr", None),
    ("Weis Market, 1075 W King St", "1075", "W King St", None),  # the part with the number
])
def test_place_freeform_address(freeform, number, street, unit):
    from app.overture import place_record
    rec = place_record({**PLACE, "freeform": freeform})
    assert (rec["address_number"], rec["street"], rec["subaddress"]["unit"]) == (number, street, unit)


def test_place_record_mapping():
    from app.overture import place_record
    rec = place_record(PLACE)
    assert rec["formatted"] == "Walmart, 1410 United Dr, Shippensburg, PA 17257"
    assert (rec["city"], rec["state"], rec["zip"], rec["zip4"]) == ("Shippensburg", "PA", "17257", "9552")
    assert rec["address_number_int"] == 1410 and rec["source"] == "overture_places:meta" and rec["landmark"] == "Walmart"
    assert rec["place"] == {"name": "Walmart", "category": "shopping", "confidence": 0.92, "operating_status": "open"}
    assert place_record({**PLACE, "region": None})["state"] is None  # not the town moved up a level


def test_places_and_addresses_are_cached_apart():
    import asyncio

    from app.overture import OvertureClient

    client, ran = OvertureClient(http=None), []

    async def release(refresh=False):
        return "2026-10-21.0"

    async def run(release, bbox, number, limit, timeout, any_part=False, theme="addresses"):
        ran.append(theme)
        return [PLACE] if theme == "places" else []

    client.release, client._run = release, run
    box = {"xmin": -77.6, "xmax": -77.4, "ymin": 39.9, "ymax": 40.1}

    async def both():
        return (await client.query(box, number=1410), await client.query(box, number=1410, theme="places"),
                await client.query(box, number=1410, theme="places"))

    addresses, places, again = asyncio.run(both())
    assert addresses == [] and places[0]["landmark"] == "Walmart" and again == places and ran == ["addresses", "places"]
