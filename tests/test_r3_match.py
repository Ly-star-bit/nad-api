"""Round-3 repairs of the matcher (match.py / places.py / osm.py), one test per audit item, offline: the provider
answers are canned in the shape of the cached live ones (Photon features, Overture rows, register records)."""

import asyncio

import pytest

from app.intl import countries as C
from app.intl import europe, osm
from app.intl.base import cand
from app.intl.match import decide, num_matches, score, street_sim
from app.intl.parse import parse_line
from app.intl.places import same_city

CC = C.COUNTRIES
OFFICIAL = dict(authority="official", dataset="REG")
M = 1 / 111_000  # one metre north, in degrees


def _p(text, cc):
    return parse_line(text, CC[cc])


def _feat(typ, lat, lon, cc="DE", **p):
    p = {"osm_type": "W", "osm_id": hash((typ, lat, lon, p.get("housenumber"))) % 10**7, "type": typ, "countrycode": cc, **p}
    return {"type": "Feature", "geometry": {"type": "Point", "coordinates": [lon, lat]}, "properties": p}


def _row(number, street, lat, lon, pc=None, city=None, unit=None):
    return {"uuid": f"{street}-{number}-{unit}-{lat}", "address_number": number, "street": street, "postcode": pc,
            "city": city, "subaddress": {"unit": unit}, "lat": lat, "lon": lon, "state": None, "source": "x",
            "license": "CC0", "updated": None}


class _Ov:
    def __init__(self, by_number, nearby):
        self.by_number, self.nearby = by_number, nearby

    async def query(self, bbox, number=None, limit=5000, timeout=None, any_part=False):
        return self.nearby if number is None else self.by_number


class _Ctx:
    def __init__(self, ov):
        self.ov = ov

    async def get(self, service, url, params, *, expect=dict):
        return {}


def _overture(monkeypatch, a, feats, by_number, nearby=()):
    """osm.overture() end to end on canned Photon features and Overture rows -> the verdict."""
    async def photon(ctx, a, **kw):
        return feats
    monkeypatch.setattr(osm, "photon", photon)
    lk = asyncio.run(osm.overture(_Ctx(_Ov(list(by_number), list(nearby))), a))
    if lk.status:
        return {"status": lk.status, "verified": False, "notes": lk.notes, "best_match": None}
    return decide(a, lk.cands, authority="overture", dataset="Overture Maps", area_empty=lk.area_empty)


def _osm(feats, a):
    geo, f, _ = osm.locate(feats, a)
    cands = osm.photon_cands(feats, (geo["lat"], geo["lon"]) if geo else None)
    for c in cands:
        c["area_pc_ok"] = osm._pc_ok(f, a)
    return decide(a, cands, authority="osm", dataset="OSM")


def _ch(*labels):
    return europe.ch_cands({"results": [{"attrs": {"label": f"<b>{x}</b>", "lat": 47.0, "lon": 9.0}} for x in labels]})


def _rec(number, street, pc, city, **kw):
    return cand("address", number=number, street=street, postcode=pc, city=city, **kw)


# --------------------------------------------------------------- [20] [28] [33] Maienstraße is not Marienstraße

O_FR = (47.98, 7.86)
_MAIEN = _feat("street", *O_FR, name="Maienstraße", postcode="79102", city="Freiburg im Breisgau", district="Wiehre",
               extent=[O_FR[1] - 0.0005, O_FR[0] + 0.0003, O_FR[1] + 0.0005, O_FR[0] - 0.0003])
_MAIEN_ROWS = [_row(str(n), "Maienstraße", O_FR[0] + d * M, O_FR[1], city="Freiburg im Breisgau")
               for n, d in ((1, 15), (2, 16), (3, 33), (4, 43))]
_MARIEN_9 = _row("9", "Marienstraße", O_FR[0] + 586 * M, O_FR[1], city="Freiburg im Breisgau")


@pytest.mark.parametrize("text", ["Maienstraße 9, 79102 Freiburg", "Maienstraße 9, 79102 Freiburg im Breisgau"])
def test_r3_20_28_33_maienstrasse_9_is_not_marienstrasse_9(monkeypatch, text):
    d = _overture(monkeypatch, _p(text, "DE"), [_MAIEN], [_MARIEN_9], _MAIEN_ROWS)
    assert d["status"] == "street_only" and not d["verified"]
    assert {c["number"] for c in d["suggestions"]} == {"1", "2", "3", "4"}


@pytest.mark.parametrize("number", ["2", "3"])
def test_r3_20_maienstrasse_real_numbers_verify_with_the_short_town(monkeypatch, number):
    # Photon found only the street (no house, so no postcode for the rows): the row on the located street is it
    rows = [r for r in _MAIEN_ROWS if r["address_number"] == number]
    d = _overture(monkeypatch, _p(f"Maienstraße {number}, 79102 Freiburg", "DE"), [_MAIEN], rows + [_MARIEN_9], _MAIEN_ROWS)
    assert d["status"] == "verified" and d["best_match"]["street"] == "Maienstraße"
    assert not any("other places" in n or "town differs" in n for n in d["notes"])


def test_r3_33_an_exact_name_row_on_the_located_street_blocks_a_similar_name(monkeypatch):
    # the exact street's rows say another town (a far postcode): still no 'Marienstraße 9' for 'Maienstraße 9'
    rows = [{**r, "postcode": "88400"} for r in _MAIEN_ROWS]
    d = _overture(monkeypatch, _p("Maienstraße 9, 79102 Freiburg", "DE"), [_MAIEN], [{**_MARIEN_9, "postcode": "79102"}], rows)
    assert not d["verified"]


def test_r3_33_kirchstrasse_postcode_typo_is_not_kirschstrasse():
    o = (52.50, 13.33)
    a = _p("Kirchstraße 5, 10789 Berlin", "DE")
    area = {"area_postcode": None, "area_city": "Berlin", "pc_ok": False}
    kirch = osm.overture_cand(_row("5", "Kirchstraße", o[0] + 30 * M, o[1], "10719", "Charlottenburg"), o, "DE", area)
    kirsch = osm.overture_cand(_row("5", "Kirschstraße", o[0] + 800 * M, o[1], "10789", "Wilmersdorf"), o, "DE", area)
    d = decide(a, [kirch, kirsch], authority="overture", dataset="Overture Maps")
    assert d["status"] == "verified" and d["best_match"]["street"] == "Kirchstraße"
    assert "postcode differs: input 10789 vs Overture Maps 10719" in d["notes"]
    # only the Charlottenburg row (round 3 [34]): the same
    d = decide(a, [kirch], authority="overture", dataset="Overture Maps")
    assert d["status"] == "verified" and any("postcode differs" in n for n in d["notes"])


# ------------------------------------------------------- [21] [32] [51] short, district and province forms of a town

@pytest.mark.parametrize("want, have, cc, same", [
    # the record's town with the input's district or qualifier: the same town
    ("Berlin-Mitte", "Berlin", "DE", True), ("Hamburg-Altona", "Hamburg", "DE", True), ("Köln-Ehrenfeld", "Köln", "DE", True),
    ("Köln-Porz", "Köln", "DE", True), ("Warszawa-Mokotów", "Warszawa", "PL", True), ("Kraków-Podgórze", "Kraków", "PL", True),
    ("Roma RM", "Roma", "IT", True), ("Milano MI", "Milano", "IT", True), ("Torino TO", "Torino", "IT", True),
    ("Firenze FI", "Firenze", "IT", True), ("Amsterdam-Noord", "Amsterdam", "NL", True), ("Brno-střed", "Brno", "CZ", True),
    ("Wien-Favoriten", "Wien", "AT", True), ("München-Schwabing", "München", "DE", True), ("Lucan Co. Dublin", "Lucan", "IE", True),
    ("Baden AG", "Baden", "CH", True), ("Reggio Emilia", "Reggio nell'Emilia", "IT", True),
    # the start of a longer name: the postcode or the located street decides
    ("Frankfurt", "Frankfurt am Main", "DE", None), ("Freiburg", "Freiburg im Breisgau", "DE", None),
    ("Halle", "Halle (Saale)", "DE", None), ("Offenbach", "Offenbach am Main", "DE", None),
    ("Mülheim", "Mülheim an der Ruhr", "DE", None), ("Ludwigshafen", "Ludwigshafen am Rhein", "DE", None),
    ("Brandenburg", "Brandenburg an der Havel", "DE", None), ("Oldenburg", "Oldenburg (Oldb)", "DE", None),
    ("Kempten", "Kempten (Allgäu)", "DE", None), ("Newcastle", "Newcastle upon Tyne", "GB", None),
    ("Brighton", "Brighton and Hove", "GB", None), ("Bratislava", "Bratislava-Staré Mesto", "SK", None),
    ("Košice", "Košice-Staré Mesto", "SK", None), ("Berlin", "Berlin-Mitte", "DE", None),
    # another town
    ("Lille", "Marquette-lez-Lille", "FR", False), ("Lyon", "Sainte-Foy-lès-Lyon", "FR", False),
    ("Wien", "Breitenfurt bei Wien", "AT", False), ("Frankfurt (Oder)", "Frankfurt am Main", "DE", False),
    ("Buchs SG", "Buchs ZH", "CH", False), ("Buchs AG", "Buchs SG", "CH", False), ("Wien 1", "Wien 14", "AT", False),
    ("Saint-Denis-de-Pile", "Saint-Denis", "FR", False), ("Frankfurt am Main", "Frankfurt", "DE", False),
    # round 4 [12]: a longer name of its own, not a known big town's full name - Kingston upon Hull, Reggio di Calabria
    ("Kingston", "Kingston upon Thames", "GB", False), ("Reggio", "Reggio Emilia", "IT", False),
])
def test_r3_21_32_51_town_comparison_is_three_valued(want, have, cc, same):
    assert same_city(want, have, cc) is same


@pytest.mark.parametrize("text, city, district", [
    ("Unter den Linden 77, Berlin-Mitte", "Berlin", "Mitte"), ("Hauptstraße 400, Köln-Porz", "Köln", "Porz"),
    ("ul. Puławska 10, Warszawa-Mokotów", "Warszawa", "Mokotów"), ("Zeil 106, Frankfurt", "Frankfurt am Main", "Innenstadt"),
    ("Römerberg 23, Frankfurt", "Frankfurt am Main", "Altstadt"), ("Rathausplatz 2, Freiburg", "Freiburg im Breisgau", "Altstadt"),
])
def test_r3_32_51_photon_finds_the_street_in_a_short_or_district_town(text, city, district):
    cc = "PL" if "Warszawa" in text else "DE"
    a = _p(text, cc)
    f = _feat("house", 50.0, 8.0, cc=cc, street=a.street.replace("ul. ", ""), housenumber=a.number, postcode="10117",
              city=city, district=district)
    assert osm.place_ok(f, a) is not False and osm.locate([f], a)[0] is not None


@pytest.mark.parametrize("text, row_city, distance", [
    ("Zeil 106, 60313 Frankfurt", "Frankfurt am Main", 104.7), ("Zeil 106, Frankfurt", "Frankfurt am Main", 104.7),
    ("Schloßstraße 28, 45468 Mülheim", "Mülheim an der Ruhr", 235.6), ("Römerberg 23, Frankfurt", "Frankfurt am Main", 20.0),
    ("Rathausplatz 2, Freiburg", "Freiburg im Breisgau", 20.0),
])
def test_r3_21_32_overture_row_of_the_official_town_name_verifies(monkeypatch, text, row_city, distance):
    a = _p(text, "DE")
    o = (50.11, 8.68)
    f = _feat("house", *o, street=a.street, housenumber=a.number, postcode=a.postcode or "60313", city=row_city)
    d = _overture(monkeypatch, a, [f], [_row(a.number, a.street, o[0] + distance * M, o[1], city=row_city)])
    assert d["status"] == "verified" and d["best_match"]["city"] == row_city
    assert not any("other places" in n or "town differs" in n for n in d["notes"])


def test_r3_21_roma_rm_milano_mi(monkeypatch):
    o = (41.90, 12.48)
    for text, street, city in (("Via del Corso 75, 00186 Roma RM", "VIA DEL CORSO", "Roma"),
                               ("Via Torino 61, 20123 Milano MI", "VIA TORINO", "Milano")):
        a = _p(text, "IT")
        f = _feat("house", *o, cc="IT", street=a.street, housenumber=a.number, postcode=a.postcode, city=city)
        d = _overture(monkeypatch, a, [f], [_row(a.number, street, o[0] + 17 * M, o[1], city=city)])
        assert d["status"] == "verified" and d["best_match"]["match"]["city_match"] is True
        assert not any("town differs" in n for n in d["notes"])


def test_r3_21_berlin_mitte_row_named_after_the_ortsteil(monkeypatch):
    o = (52.5163, 13.3805)
    a = _p("Unter den Linden 77, Berlin-Mitte", "DE")
    f = _feat("street", *o, name="Unter den Linden", postcode="10117", city="Berlin", district="Mitte",
              extent=[o[1] - 0.003, o[0] + 0.0005, o[1] + 0.003, o[0] - 0.0005])
    d = _overture(monkeypatch, a, [f], [_row("77", "Unter den Linden", o[0] + 25 * M, o[1], "10117", "Mitte")])
    assert d["status"] == "verified"


def test_r3_21_newcastle_postcode_typo():
    a = _p("100 Grey Street, Newcastle NE1 6BX", "GB")
    feats = [_feat("house", 54.97, -1.61, cc="GB", name="Theatre Royal", street="Grey Street", housenumber="100",
                   postcode="NE1 6BR", city="Newcastle upon Tyne", district="Grainger Town", county="Tyne and Wear")]
    d = _osm(feats, a)
    assert d["status"] == "verified" and "postcode differs: input NE1 6BX vs OSM NE1 6BR" in d["notes"]


def test_r3_21_a_london_borough_is_london():
    a = _p("12 Kensington High Street, London W8 5SB", "GB")
    feats = [_feat("house", 51.50, -0.19, cc="GB", street="Kensington High Street", housenumber="12", postcode="W8 5SA",
                   city="Royal Borough of Kensington and Chelsea", district="Kensington", county="Greater London")]
    assert "London" in osm._towns(feats[0]["properties"])
    d = _osm(feats, a)
    assert d["status"] == "verified" and any("postcode differs" in n for n in d["notes"])
    # not Greater Manchester's towns (R55)
    stockport = _feat("house", 53.41, -2.16, cc="GB", street="High Street", housenumber="12", postcode="SK1 1AA",
                      city="Stockport", county="Greater Manchester")
    assert osm.place_ok(stockport, _p("12 High Street, Manchester", "GB")) is False


# ------------------------------------------------------------------------------ [29] a town without its qualifier

_BERGEN = [_rec("1", "Kerkstraat", pc, town) for pc, town in (("1861KR", "Bergen (NH)"), ("5854AJ", "Bergen L"),
                                                               ("1865AM", "Bergen aan Zee"), ("4611NV", "Bergen op Zoom"))]


def test_r3_29_bergen():
    d = decide(_p("Kerkstraat 1, Bergen (NH)", "NL"), _BERGEN, **OFFICIAL)
    assert d["status"] == "verified" and d["best_match"]["postcode"] == "1861KR"
    d = decide(_p("Kerkstraat 1, Bergen", "NL"), _BERGEN, **OFFICIAL)
    assert d["status"] == "not_covered" and any("add the postcode" in n for n in d["notes"])


@pytest.mark.parametrize("text, labels, status, pc", [
    ("Obere Bahnhofstrasse 11, Wil", ["Obere Bahnhofstrasse 11 9500 Wil SG", "Obere Bahnhofstrasse 11a 9500 Wil SG"],
     "verified", "9500"),
    ("Bahnhofstrasse 2, Buchs", ["Bahnhofstrasse 2 9470 Buchs SG", "Bahnhofstrasse 2 3360 Herzogenbuchsee",
                                 "Bahnhofstrasse 2 4625 Oberbuchsiten", "Bahnhofstrasse 2a 8107 Buchs ZH"], "verified", "9470"),
    ("Bahnhofstrasse 1, Buchs", ["Bahnhofstrasse 1 8107 Buchs ZH", "Bahnhofstrasse 1 3053 Münchenbuchsee",
                                 "Bahnhofstrasse 1 3360 Herzogenbuchsee"], "verified", "8107"),
    ("Obere Bahnhofstrasse 10, Wil", ["Obere Bahnhofstrasse 9 9500 Wil SG", "Obere Bahnhofstrasse 11 9500 Wil SG"],
     "street_only", None),
    # the control: with Buchs SG's postcode, Buchs ZH's number 1 is not it
    ("Bahnhofstrasse 1, 9470 Buchs", ["Bahnhofstrasse 1 8107 Buchs ZH", "Bahnhofstrasse 10 9470 Buchs SG"], "street_only", None),
])
def test_r3_29_wil_and_buchs(text, labels, status, pc):
    d = decide(_p(text, "CH"), _ch(*labels), **OFFICIAL)
    assert d["status"] == status
    if pc:
        assert d["best_match"]["postcode"] == pc and any("taken as" in n for n in d["notes"])


def test_r3_31_johvi_is_johvi_vald():
    # ee_cands gives Jõhvi vald's bare name as a town (round 4 [16]): 'Jõhvi' no longer counts as compatible with
    # 'Jõhvi vald' itself
    rec = _rec("7", "Narva mnt", "41701", "Jõhvi vald", city_alt=["Voka alevik", "Voka", "Jõhvi"])
    d = decide(_p("Narva mnt 7, Jõhvi", "EE"), [rec], **OFFICIAL)
    assert d["status"] == "verified"


# ------------------------------------------------------------------------------------- [30] bilingual town names

_ELX = [_rec(n, "PLAZA BAIX DE", "03202", "Elx/Elche") for n in ("1", "2", "3")]


@pytest.mark.parametrize("text, pc_note", [
    ("Plaça de Baix 1, Elche", False), ("Plaça de Baix 1, 03203 Elche", True), ("Plaça de Baix 1, 03203 Elx", True),
    ("Plaça de Baix 1, 03202 Elche", False),
])
def test_r3_30_elx_elche(text, pc_note):
    d = decide(_p(text, "ES"), _ELX, **OFFICIAL)
    assert d["status"] == "verified" and d["best_match"]["number"] == "1"
    assert any("postcode differs" in n for n in d["notes"]) is pc_note
    assert not any("town differs" in n or "other places" in n for n in d["notes"])


@pytest.mark.parametrize("want, have", [("Elche", "Elx/Elche"), ("Alicante", "Alacant/Alicante"),
                                        ("San Sebastián", "Donostia/San Sebastián"), ("Donostia", "Donostia / San Sebastián")])
def test_r3_30_either_part_of_a_bilingual_name(want, have):
    assert same_city(want, have, "ES") is True


# ---------------------------------------------------------------------------------------------- [37] cosmetic

def test_r3_37_no_town_differs_note_for_a_district_or_short_form():
    d = decide(_p("Hauptstraße 400, 51143 Köln-Porz", "DE"), [_rec("400", "Hauptstr.", "51143", "Köln")], **OFFICIAL)
    assert d["status"] == "verified" and d["best_match"]["match"]["city_match"] is True and d["notes"] == []
    d = decide(_p("Piazza Prampolini 1, 42121 Reggio Emilia", "IT"),
               [_rec("1", "Piazza Prampolini", "42121", "Reggio nell'Emilia")], **OFFICIAL)
    assert d["best_match"]["match"]["city_match"] is True and not any("town differs" in n for n in d["notes"])
    d = decide(_p("Zeil 106, 60313 Frankfurt", "DE"), [_rec("106", "Zeil", "60313", "Frankfurt am Main")], **OFFICIAL)
    assert d["best_match"]["match"]["city_match"] is True and d["notes"] == []
    # Overture's Köln district code is kept when there is no BAN district
    row = osm.overture_cand(_row("17", "Mainstr. Wv", 50.9, 7.0, "51149", "Köln"), (50.9, 7.0), "DE")
    assert score(_p("Mainstraße 17, 51149 Köln", "DE"), row)["district"] == "Wv"


# ------------------------------------------------------------------ [22] [34] a postcode typo in a districted city

@pytest.mark.parametrize("text, number, street, row_pc, row_city, cc", [
    ("Unter den Linden 77, 10115 Berlin", "77", "Unter den Linden", "10117", "Mitte", "DE"),
    ("Kurfürstendamm 21, 10789 Berlin", "21", "Kurfürstendamm", "10719", "Charlottenburg", "DE"),
    ("Obchodná 25, 811 05 Bratislava", "553/25", "Obchodná", "81106", "Bratislava-Staré Mesto", "SK"),
])
def test_r3_22_34_neighbouring_postcode_verifies_with_a_note(monkeypatch, text, number, street, row_pc, row_city, cc):
    a = _p(text, cc)
    o = (52.51, 13.38)
    town = "Bratislava" if cc == "SK" else "Berlin"
    f = _feat("house", *o, cc=cc, street=street, housenumber=number, postcode=row_pc, city=town)
    d = _overture(monkeypatch, a, [f], [_row(number, street, o[0] + 26 * M, o[1], row_pc, row_city)])
    assert d["status"] == "verified" and any(n.startswith("postcode differs") for n in d["notes"])
    assert not any("other places" in n for n in d["notes"])


def test_r3_22_r55_a_far_own_postcode_is_still_another_town():
    o = (49.14, 9.28)
    area = {"area_postcode": None, "area_city": "Erlenbach", "pc_ok": True}
    own = osm.overture_cand(_row("5", "Hauptstraße", o[0] + 50 * M, o[1], "74189", "Weinsberg"), o, "DE", area)
    assert score(_p("Hauptstraße 5, 74235 Erlenbach", "DE"), own)["match"]["place"] is False


# ------------------------------------------------------------------------------------- [24] same_area (offline)

@pytest.mark.parametrize("distance", [50.0, 1500.0])
def test_r3_24_erlenbach_record_with_a_far_postcode(distance):
    row = {**_rec("5", "Hauptstraße", "63906", "Erlenbach"), "distance_m": distance, "city_loose": True}
    d = decide(_p("Hauptstraße 5, 74235 Erlenbach", "DE"), [row], authority="overture", dataset="Overture Maps")
    assert not d["verified"]


@pytest.mark.parametrize("text, rec, cc", [
    ("Hauptstraße 1, 1010 Wien 1", ("1", "Hauptstraße", "1140", "Wien"), "AT"),
    ("Fő utca 10, 1011 Budapest I", ("10", "Fő utca", "1225", "Budapest"), "HU"),
    ("Fő utca 10, 1011 Budapest XI.", ("10", "Fő utca", "1225", "Budapest"), "HU"),
])
def test_r3_24_a_district_number_on_one_side_only_is_no_same_area(text, rec, cc):
    d = decide(_p(text, cc), [{**_rec(*rec), "distance_m": 0.0}], **OFFICIAL)
    assert not d["verified"]


def test_r3_24_newport_located_by_its_namesake():
    a = _p("123 High Street, Newport NP20 1AA", "GB")
    feats = [_feat("house", 50.70, -1.29, cc="GB", street="High Street", housenumber="123", postcode="PO30 1SS", city="Newport")]
    assert not _osm(feats, a)["verified"]


def test_r3_24_same_numbered_district_with_a_mistyped_postcode():
    rec = _rec("9", "náměstí Míru", "12000", "Praha 2")
    assert decide(_p("náměstí Míru 9, 110 00 Praha 2", "CZ"), [rec], **OFFICIAL)["status"] == "verified"


# ------------------------------------------------------------------------------- [27] a square is not the street

@pytest.mark.parametrize("want, have, cc", [
    ("Palackého", "Palackého náměstí", "CZ"), ("Wolności", "plac Wolności", "PL"), ("Míru", "náměstí Míru", "CZ"),
    ("Palackého", "Palackého třída", "CZ"), ("Masarykovo náměstí", "Masarykova", "CZ"), ("Václavské náměstí", "Václavská", "CZ"),
])
def test_r3_27_a_square_or_avenue_is_another_street(want, have, cc):
    assert street_sim(want, have, cc) < 0.85


def test_r3_27_palackeho():
    recs = [_rec("4", "Palackého", "11000", "Praha 1", formatted="Palackého 722/4, Nové Město, 11000 Praha 1"),
            _rec("4", "Palackého náměstí", "12800", "Praha 2", formatted="Palackého náměstí 375/4, Nové Město, 12800 Praha 2")]
    d = decide(_p("Palackého 4, Praha", "CZ"), recs, **OFFICIAL)
    assert d["status"] == "verified" and "722/4" in d["best_match"]["formatted"]
    d = decide(_p("Palackého 4, 128 00 Praha 2", "CZ"), recs, **OFFICIAL)
    assert d["status"] == "not_found" and not d["verified"]
    # Brno has no Václavské náměstí: Overture's Václavská rows are not that square
    rows = [{**_rec(n, "Václavská", "60300", "Staré Brno"), "distance_m": 5.0, "city_loose": True} for n in ("39", "38")]
    assert decide(_p("Václavské náměstí 1, Brno", "CZ"), rows, authority="overture", dataset="Overture Maps")["status"] == "not_found"


# ------------------------------------------------------------------------------- [23] [35] given names left out

@pytest.mark.parametrize("want, have, cc", [
    ("Barona iela", "Krišjāņa Barona iela", "LV"), ("Valdemāra iela", "Krišjāņa Valdemāra iela", "LV"),
    ("Place Flagey", "Place Eugène Flagey", "BE"), ("Rue Dansaert", "Rue Antoine Dansaert", "BE"),
    ("Avenue Roosevelt", "Avenue Franklin Roosevelt", "BE"),
])
def test_r3_23_35_a_street_without_its_given_name(want, have, cc):
    assert 0.85 <= street_sim(want, have, cc) < 0.999


@pytest.mark.parametrize("want, have, cc", [
    ("High Street", "Kensington High Street", "GB"), ("King Street", "King William Street", "GB"),
    ("Hamngatan", "Norra Hamngatan", "SE"), ("Brivibas iela", "Brīvības gatve", "LV"), ("Calle Real", "Calle Real de Arriba", "ES"),
])
def test_r3_23_r24_controls_stay_other_streets(want, have, cc):
    assert street_sim(want, have, cc) < 0.85


def test_r3_23_barona_iela_verifies_with_a_note():
    rec = _rec("46", "Krišjāņa Barona iela", "LV-1011", "Rīga")
    d = decide(_p("Barona iela 46, Rīga", "LV"), [rec], **OFFICIAL)
    assert d["status"] == "verified" and any("street name differs" in n for n in d["notes"])


# ------------------------------------------------------------------- R24: a longer name that is not a given name

@pytest.mark.parametrize("want, have, cc", [
    ("Rue de la Gare", "Rue de l'Ancienne Gare", "FR"), ("Rue Notre-Dame", "Rue Notre-Dame des Champs", "FR"),
    ("Rue des Écoles", "Rue des Écoles Normales", "FR"), ("Chemin du Moulin", "Chemin du Moulin à Vent", "FR"),
    ("Via Santa Maria", "Via Santa Maria Maggiore", "IT"), ("ul. Nowa", "ul. Nowa Wieś", "PL"),
    ("ul. Polna", "ul. Polna Boczna", "PL"), ("ul. Ogrodowa", "ul. Ogrodowa Mała", "PL"),
])
def test_r3_r24_extra_words_that_are_no_given_name(want, have, cc):
    assert street_sim(want, have, cc) < 0.85


@pytest.mark.parametrize("want, have, cc", [
    ("ul. Kościuszki", "ul. Tadeusza Kościuszki", "PL"), ("Avenue Foch", "Avenue du Maréchal Foch", "FR"),
    ("Via Verdi", "VIA VERDI GIUSEPPE", "IT"), ("rue de Rivoli Paris", "Rue de Rivoli", "FR"),
])
def test_r3_r24_given_names_still_left_out(want, have, cc):
    assert street_sim(want, have, cc) >= 0.85


# ------------------------------------------------------------------------------------ [54] [25] number ranges

@pytest.mark.parametrize("text, rng", [
    ("15 High Street, Oxford OX1 4AA", "2-36"), ("3 High Street, Oxford OX1 4AA", "2-36"),
    ("88 Brompton Road, London SW3 1ER", "87-135"), ("100 Brompton Road, London SW3 1ER", "87-135"),
])
def test_r3_54_25_a_wide_range_has_one_side_of_the_street(text, rng):
    a = _p(text, "GB")
    feats = [_feat("house", 51.75, -1.25, cc="GB", street=a.street, housenumber=rng, postcode=a.postcode,
                   city=a.city, name="Harrods" if "Brompton" in text else None)]
    d = _osm(feats, a)
    assert d["status"] == "not_covered" and not d["verified"]


def test_r3_54_25_range_rules():
    de = _p("Hauptstraße 20, 10115 Berlin", "DE")
    assert num_matches(de, "1-99") is None and num_matches(de, "2-40") == "range" and num_matches(de, "11/12") is None
    assert num_matches(_p("Hauptstraße 13, 10115 Berlin", "DE"), "11/15") == "range"
    fi = _p("Pohjoisesplanadi 12, 00170 Helsinki", "FI")
    assert num_matches(fi, "11-35", block=True) == "range" and num_matches(fi, "11-35") is None
    assert num_matches(fi, "11-13", block=True) is None  # a short block is one side of the street
    ryhti = _rec("11-35", "Pohjoisesplanadi", "00170", "Helsinki", source="Ryhti")
    assert decide(fi, [ryhti], **OFFICIAL)["status"] == "verified"


# ----------------------------------------------------------------------------------- [26] Swedish 'g.' abbreviation

@pytest.mark.parametrize("want, have, cc", [("Drottningg.", "Drottninggatan", "SE"), ("Kungsg.", "Kungsgatan", "SE"),
                                            ("Storg.", "Storgata", "NO"), ("Vesterg.", "Vestergade", "DK")])
def test_r3_26_g_abbreviation(want, have, cc):
    assert street_sim(want, have, cc) >= 0.85 and street_sim(have, want, cc) >= 0.85


# --------------------------------------------------------------------------------------- S04 Dublin districts

@pytest.mark.parametrize("text, verified", [("12 Main Street, Dublin 2, Ireland", False), ("12 Main Street, Dublin 4", True),
                                            ("12 Main Street, Dublin", True)])
def test_r3_s04_dublin_district_against_the_eircode(text, verified):
    feats = [_feat("house", 53.32, -6.23, cc="IE", name="Marian Gale", street="Main Street", housenumber="12",
                   postcode="D04 V4Y1", city="Dublin", county="Dublin 4")]
    assert _osm(feats, _p(text, "IE"))["verified"] is verified


# ---------------------------------------------------------------- Overture's Czech rows: orientation number as unit

def test_r3_cz_overture_orientation_number_in_the_unit():
    o = (49.18, 16.63)
    c = osm.overture_cand(_row("618", "Mariánské náměstí", o[0], o[1], "61700", "Komárov", unit="2"), o, "CZ")
    parts = osm._number_parts(c, "CZ")
    assert [p["number"] for p in parts] == ["2", "618", "618/2"] and all(p["unit"] is None for p in parts)


# ---------------------------------------------------------------- ES / PT: a title left out ('Calle Larios')

def test_r3_13_calle_larios_is_calle_marques_de_larios():
    rec = _rec("5", "CALLE MARQUES DE LARIOS", "29015", "Málaga")
    d = decide(_p("Calle Larios 5, 29005 Málaga", "ES"), [rec], **OFFICIAL)
    assert d["status"] == "verified" and any("street name differs" in n for n in d["notes"])
    assert street_sim("Rua Pombal", "Rua Marquês de Pombal", "PT") >= 0.85


@pytest.mark.parametrize("want, have", [("Calle Real", "CALLE CIUDAD REAL"), ("Calle Real", "Calle Real de Arriba"),
                                        ("Calle Mayor", "Calle Mayor Baja")])
def test_r3_13_es_other_words_than_a_title_make_another_street(want, have):
    assert street_sim(want, have, "ES") < 0.85


def test_r3_13_getafe_calle_real_is_not_ciudad_real():
    d = decide(_p("Calle Real 10, 28901 Getafe", "ES"), [_rec("10", "CALLE CIUDAD REAL", "28901", "Getafe")], **OFFICIAL)
    assert not d["verified"]
