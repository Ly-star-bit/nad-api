"""Round-4 repairs of the matcher (match.py / places.py / osm.py), one test per audit item, offline: the provider
answers are canned in the shape of the cached live ones (Photon features, Overture rows, register records)."""

import pytest

from app.intl import countries as C
from app.intl import osm
from app.intl.base import cand
from app.intl.match import decide, score, street_sim
from app.intl.parse import parse_line
from app.intl.places import namesake, same_city

CC = C.COUNTRIES
OFFICIAL = dict(authority="official", dataset="REG")
OVERTURE = dict(authority="overture", dataset="Overture Maps")
M = 1 / 111_000  # one metre north, in degrees


def _p(text, cc):
    return parse_line(text, CC[cc])


def _rec(number, street, pc, city, **kw):
    return cand("address", number=number, street=street, postcode=pc, city=city, **kw)


def _feat(typ, lat, lon, cc="DE", **p):
    p = {"osm_type": "W", "osm_id": hash((typ, lat, lon, p.get("housenumber"), p.get("city"))) % 10**7, "type": typ,
         "countrycode": cc, **p}
    return {"type": "Feature", "geometry": {"type": "Point", "coordinates": [lon, lat]}, "properties": p}


def _osm(feats, a):
    geo, f, _ = osm.locate(feats, a)
    cands = osm.photon_cands(feats, (geo["lat"], geo["lon"]) if geo else None)
    for c in cands:
        c["area_pc_ok"] = osm._pc_ok(f, a)
    return decide(a, cands, authority="osm", dataset="OSM")


def _ov(number, street, pc, city, d=30.0, area_city=None, on=False):
    """An Overture row as osm.overture() hands it to decide."""
    c = _rec(number, street, pc, city, lat=50.0, lon=8.0, id=f"{street}{number}{pc}{d}", distance_m=d)
    c.update(city_loose=True, area_city=area_city, area_postcode=None, district=None, area_pc_ok=False, on_street=on)
    return c


# ------------------------------------------------------------- [12] the start of another town's name is not that town

_LONGER = [("Saint-Martin", "Saint-Martin-d'Hères", "FR"), ("Mons", "Mons-en-Barœul", "FR"), ("Bergen", "Bergen op Zoom", "NL"),
           ("Saint-Denis", "Saint-Denis-en-Val", "FR"), ("Saint-Denis", "Saint-Denis-de-Pile", "FR"),
           ("Villeneuve", "Villeneuve-lès-Avignon", "FR"), ("Ostrów", "Ostrów Wielkopolski", "PL"),
           ("Nowy Dwór", "Nowy Dwór Mazowiecki", "PL"), ("Ústí", "Ústí nad Labem", "CZ"), ("Alcalá", "Alcalá de Henares", "ES"),
           ("Santa Cruz", "Santa Cruz de Tenerife", "ES"), ("London", "London Colney", "GB"), ("Stoke", "Stoke Gifford", "GB"),
           ("Newcastle", "Newcastle-under-Lyme", "GB"), ("Weil", "Weil am Rhein", "DE"),
           ("Neustadt", "Neustadt an der Weinstraße", "DE"), ("Saint-Martin", "Saint-Martin-Lys", "FR"),
           ("Kingston", "Kingston upon Hull", "GB"), ("Reggio", "Reggio di Calabria", "IT"), ("Bergen", "Bergen aan Zee", "NL"),
           ("Tartu", "Tartu vald", "EE"), ("Viljandi", "Viljandi linn", "EE")]


@pytest.mark.parametrize("want, have, cc", _LONGER)
def test_r4_12_a_longer_name_of_its_own_is_another_town(want, have, cc):
    assert same_city(want, have, cc) is False and same_city(have, want, cc) is False
    assert not namesake(want, have, cc)


@pytest.mark.parametrize("want, have, cc, qualified", [
    ("Frankfurt", "Frankfurt am Main", "DE", False), ("Freiburg", "Freiburg im Breisgau", "DE", False),
    ("Mülheim", "Mülheim an der Ruhr", "DE", False), ("Newcastle", "Newcastle upon Tyne", "GB", False),
    ("Offenbach", "Offenbach am Main", "DE", False), ("Las Palmas", "Las Palmas de Gran Canaria", "ES", False),
    ("Halle", "Halle (Saale)", "DE", True), ("Frankfurt", "Frankfurt (Oder)", "DE", True), ("Buchs", "Buchs ZH", "CH", True),
    ("Wil", "Wil SG", "CH", True), ("Bergen", "Bergen (NH)", "NL", True), ("Bergen", "Bergen L", "NL", True),
    ("Voka", "Voka alevik", "EE", False), ("Bratislava", "Bratislava-Staré Mesto", "SK", False),
    ("Košice", "Košice-Juh", "SK", False), ("Brno", "Brno-střed", "CZ", False), ("Monaco", "Monaco-Ville", "MC", False),
])
def test_r4_12_namesakes_and_big_towns_full_names_stay_compatible(want, have, cc, qualified):
    assert same_city(want, have, cc) is None
    assert namesake(want, have, cc) is qualified


@pytest.mark.parametrize("text, street, pc, town", [
    ("1 Avenue Gabriel Péri, Saint-Martin", "Avenue Gabriel Peri", "38400", "Saint-Martin-d'Hères"),
    ("1 Avenue Centrale, Saint-Martin", "Avenue Centrale", "38400", "Saint-Martin-d'Hères"),
    ("10 Rue Jean Jaurès, Mons", "Rue Jean Jaurès", "59370", "Mons-en-Barœul"),
    ("5 Rue de la Gare, Saint-Denis", "Rue de la Gare", "45560", "Saint-Denis-en-Val"),
    ("5 Rue de la Gare, Saint-Denis", "Rue de la Gare", "33910", "Saint-Denis-de-Pile"),
])
def test_r4_12_fr_register_row_in_a_longer_named_commune(text, street, pc, town):
    num = text.split()[0]
    d = decide(_p(text, "FR"), [_rec(num, street, pc, town)], **OFFICIAL)
    assert d["status"] == "not_found" and any("only in other places" in n for n in d["notes"])


@pytest.mark.parametrize("text, cc, row", [
    ("Grote Markt 1, Bergen", "NL", ("1", "Grote Markt", "4611NR", "Bergen op Zoom")),
    ("ul. Kolejowa 5, Ostrów", "PL", ("5", "ulica Kolejowa", "63-400", "Ostrów Wielkopolski")),
    ("ul. Kolejowa 5, Nowy Dwór", "PL", ("5", "ulica Kolejowa", "05-100", "Nowy Dwór Mazowiecki")),
    ("Masarykova 5, Ústí", "CZ", ("5", "Masarykova", "40001", "Ústí nad Labem")),
    ("Calle Mayor 5, Alcalá", "ES", ("5", "CALLE MAYOR", "28801", "Alcalá de Henares")),
    ("Calle Castillo 5, Santa Cruz", "ES", ("5", "CALLE CASTILLO", "38003", "Santa Cruz de Tenerife")),
])
def test_r4_12_register_row_in_a_longer_named_town(text, cc, row):
    assert not decide(_p(text, cc), [_rec(*row)], **OFFICIAL)["verified"]


def test_r4_12_namesake_on_a_register_is_taken_with_a_hedged_note():
    d = decide(_p("Kerkstraat 1, Bergen", "NL"), [_rec("1", "Kerkstraat", "1861KR", "Bergen (NH)")], **OFFICIAL)
    assert d["status"] == "verified"
    assert any("taken as Bergen (NH)" in n and "add the postcode" in n for n in d["notes"])
    assert not any("only place" in n for n in d["notes"])  # the register search has a limit: it proves no uniqueness


@pytest.mark.parametrize("text, street, pc, city, county", [
    ("10 High Street, London", "High Street", "AL2 1JG", "London Colney", "Hertfordshire"),
    ("10 Church Road, Stoke", "Church Road", "BS34 8AA", "Stoke Gifford", "South Gloucestershire"),
    ("10 High Street, Newcastle", "High Street", "ST5 1QL", "Newcastle-under-Lyme", "Staffordshire"),
])
def test_r4_12_osm_house_in_a_longer_named_town(text, street, pc, city, county):
    feats = [_feat("house", 52.0, -1.0, cc="GB", street=street, housenumber="10", postcode=pc, city=city, county=county)]
    assert not _osm(feats, _p(text, "GB"))["verified"]


@pytest.mark.parametrize("text, town", [("Bahnhofstraße 5, Weil", "Weil am Rhein"),
                                        ("Hauptstraße 5, Neustadt", "Neustadt an der Weinstraße")])
def test_r4_12_overture_row_in_a_longer_named_town(text, town):
    num = text.split(",")[0].split()[-1]
    row = _ov(num, text.split(" " + num)[0], None, town, area_city=town, on=True)
    assert not decide(_p(text, "DE"), [row], **OVERTURE)["verified"]


def test_r4_12_overture_or_osm_namesake_needs_the_postcode():
    # Frankfurt (Oder): Photon found it, Overture has the number; but 'Frankfurt' may be either Frankfurt
    row = _ov("5", "Bahnhofstraße", None, "Frankfurt (Oder)", area_city="Frankfurt (Oder)", on=True)
    d = decide(_p("Bahnhofstraße 5, Frankfurt", "DE"), [row], **OVERTURE)
    assert d["status"] == "not_covered" and any("add the postcode" in n for n in d["notes"])
    feats = [_feat("house", 52.34, 14.55, street="Bahnhofstraße", housenumber="5", postcode="15230", city="Frankfurt (Oder)")]
    d = _osm(feats, _p("Bahnhofstraße 5, Frankfurt", "DE"))
    assert d["status"] == "not_covered" and not d["verified"]
    # ...the postcode says which: the row's, a typo of it, or the one Photon located the street in for a row without one
    row = _ov("5", "Bahnhofstraße", "15230", "Frankfurt (Oder)", area_city="Frankfurt (Oder)", on=True)
    assert decide(_p("Bahnhofstraße 5, 15230 Frankfurt", "DE"), [row], **OVERTURE)["status"] == "verified"
    d = decide(_p("Bahnhofstraße 5, 15231 Frankfurt", "DE"), [row], **OVERTURE)
    assert d["status"] == "verified" and any("postcode differs" in n for n in d["notes"])
    row = {**_ov("5", "Bahnhofstraße", None, "Frankfurt (Oder)", area_city="Frankfurt (Oder)", on=True), "area_pc_ok": True}
    assert decide(_p("Bahnhofstraße 5, 15230 Frankfurt", "DE"), [row], **OVERTURE)["status"] == "verified"
    row["area_pc_ok"] = False  # a postcode, but nothing confirms it: no 'no postcode' claim in the note
    d = decide(_p("Bahnhofstraße 5, 15230 Frankfurt", "DE"), [row], **OVERTURE)
    assert d["status"] == "not_covered" and not any("no postcode in the address" in n for n in d["notes"])
    # the big town's full name is no namesake: Zeil 106 / Rathausplatz 2 still verify without a postcode
    for text, town in (("Zeil 106, Frankfurt", "Frankfurt am Main"), ("Rathausplatz 2, Freiburg", "Freiburg im Breisgau")):
        num = text.split(",")[0].split()[-1]
        row = _ov(num, text.split(" " + num)[0], None, town, area_city=town, on=True)
        assert decide(_p(text, "DE"), [row], **OVERTURE)["status"] == "verified"


def test_r4_12_newcastle_upon_tyne_with_a_postcode_typo_still_verifies():
    feats = [_feat("house", 54.97, -1.61, cc="GB", name="Theatre Royal", street="Grey Street", housenumber="100",
                   postcode="NE1 6BR", city="Newcastle upon Tyne", county="Tyne and Wear")]
    assert _osm(feats, _p("100 Grey Street, Newcastle NE1 6BX", "GB"))["status"] == "verified"


# ------------------------------------------------------------------ [13] Mazā Pils iela is not Pils iela

@pytest.mark.parametrize("want, have, cc", [
    ("Pils iela", "Mazā Pils iela", "LV"), ("Pils iela", "Lielā Pils iela", "LV"), ("Smilšu iela", "Mazā Smilšu iela", "LV"),
    ("Kalēju iela", "Mazā Kalēju iela", "LV"), ("Skārņu iela", "Mazā Skārņu iela", "LV"), ("Vaļņu iela", "Mazā Vaļņu iela", "LV"),
    ("Rīgas iela", "Vecā Rīgas iela", "LV"), ("Viru", "Vana-Viru", "EE"), ("Sadama", "Uus-Sadama", "EE"),
    ("Pärnu mnt", "Vana-Pärnu mnt", "EE"), ("Viru tn", "Väike-Viru tn", "EE"), ("Karja", "Suur-Karja", "EE"),
    ("Stiklių g.", "Mažoji Stiklių g.", "LT"), ("Vilniaus g.", "Senoji Vilniaus g.", "LT"),
    ("Pylimo g.", "Didžioji Pylimo g.", "LT"), ("Rue de Louvain", "Chaussée de Louvain", "BE"),
    ("Rue de Namur", "Chaussée de Namur", "BE"), ("Rue de Wavre", "Chaussée de Wavre", "BE"),
    ("Rue de Paris", "Chaussée de Paris", "FR"), ("Kerkstraat", "Sint-Kerkstraat", "BE"),
])
def test_r4_13_a_size_or_age_word_or_another_type_makes_another_street(want, have, cc):
    assert street_sim(want, have, cc) < 0.85 and street_sim(have, want, cc) < 0.85


@pytest.mark.parametrize("want, have, cc", [
    ("Barona iela", "Krišjāņa Barona iela", "LV"), ("Valdemāra iela", "Krišjāņa Valdemāra iela", "LV"),
    ("Rue Dansaert", "Rue Antoine Dansaert", "BE"), ("Place Flagey", "Place Eugène Flagey", "BE"),
    ("Chée de Louvain", "Chaussée de Louvain", "BE"), ("Sint-Jansstraat", "St. Jansstraat", "BE"),
    ("Mazā Pils iela", "Mazā Pils iela", "LV"), ("Vana-Viru", "Vana-Viru", "EE"), ("Leuvensesteenweg", "Leuvensesteenweg", "BE"),
])
def test_r4_13_given_names_and_spellings_still_match(want, have, cc):
    assert street_sim(want, have, cc) >= 0.85


@pytest.mark.parametrize("number", ["19", "1"])
def test_r4_13_pils_iela_is_not_verified_as_maza_pils_iela(number):
    d = decide(_p(f"Pils iela {number}, Rīga", "LV"), [_rec(number, "Mazā Pils iela", "LV-1050", "Rīga")], **OFFICIAL)
    assert d["status"] == "not_found" and not d["verified"]


# ------------------------------------------------------- [14] a neighbouring village's same-named street (Overture)

@pytest.mark.parametrize("text, rows", [
    ("Hauptstraße 5, 74235 Erlenbach", [("5", "Hauptstraße", "74232", "Abstatt", 1200.0, None, False),
                                        ("3", "Hauptstraße", "74235", "Erlenbach", 30.0, "Erlenbach", True),
                                        ("7", "Hauptstraße", "74235", "Erlenbach", 60.0, "Erlenbach", True)]),
    ("Hauptstraße 5, 74235 Erlenbach", [("5", "Hauptstraße", "74232", "Abstatt", 250.0, "Erlenbach", False),
                                        ("3", "Hauptstraße", "74235", "Erlenbach", 30.0, "Erlenbach", True)]),
    ("Dorfstraße 5, 17094 Groß Nemerow", [("5", "Dorfstraße", "17099", "Galenbeck", 900.0, None, False),
                                          ("2", "Dorfstraße", "17094", "Groß Nemerow", 30.0, "Groß Nemerow", True)]),
])
def test_r4_14_neighbouring_village_with_a_close_postcode_is_another_town(text, rows):
    d = decide(_p(text, "DE"), [_ov(*r) for r in rows], **OVERTURE)
    assert d["status"] == "street_only" and not d["verified"]


@pytest.mark.parametrize("text, number, street, row_pc, row_city, d, on", [
    ("Unter den Linden 77, 10115 Berlin", "77", "Unter den Linden", "10117", "Mitte", 25.0, True),
    ("Unter den Linden 77, 10115 Berlin", "77", "Unter den Linden", "10117", "Mitte", 25.0, False),
    ("Kurfürstendamm 21, 10789 Berlin", "21", "Kurfürstendamm", "10719", "Charlottenburg", 400.0, True),
])
def test_r4_14_the_input_towns_row_by_the_located_street_still_counts(text, number, street, row_pc, row_city, d, on):
    row = {**_ov(number, street, row_pc, row_city, d, "Berlin", on), "area_pc_ok": True}
    assert score(_p(text, "DE"), row)["match"]["place"] is not False


# ------------------------------------------------- [15] [25] an input town with an extra word is another town

@pytest.mark.parametrize("want, have, cc", [
    ("Newport Pagnell", "Newport", "GB"), ("Sutton Coldfield", "Sutton", "GB"), ("Kingston Bagpuize", "Kingston", "GB"),
    ("Bury St Edmunds", "Bury", "GB"), ("Milton Keynes", "Milton", "GB"), ("Bergen auf Rügen", "Bergen", "DE"),
    ("San Giovanni Rotondo", "San Giovanni", "IT"), ("Königs Wusterhausen", "Königs", "DE"), ("Baden-Baden", "Baden", "DE"),
    ("Mülheim-Kärlich", "Mülheim", "DE"), ("Halle Westfalen", "Halle", "DE"), ("Stoke on Trent", "Stoke", "GB"),
    ("Bergen aan Zee", "Bergen", "NL"), ("Weil am Rhein", "Weil", "DE"),
])
def test_r4_15_25_a_longer_input_town_is_another_town(want, have, cc):
    assert same_city(want, have, cc) is False and same_city(have, want, cc) is False


@pytest.mark.parametrize("want, have, cc", [
    ("Berlin-Mitte", "Berlin", "DE"), ("Köln-Porz", "Köln", "DE"), ("Warszawa-Mokotów", "Warszawa", "PL"),
    ("Hamburg-Altona", "Hamburg", "DE"), ("Roma RM", "Roma", "IT"), ("Milano MI", "Milano", "IT"),
    ("Baden AG", "Baden", "CH"), ("Bergen L", "Bergen", "NL"), ("Lucan Co. Dublin", "Lucan", "IE"),
    ("Voka alevik", "Voka", "EE"), ("Reykjavík Ísland", "Reykjavík", "IS"), ("Paris 11e", "Paris", "FR"),
    ("København K", "København", "DK"), ("Frankfurt (Oder)", "Frankfurt", "DE"), ("Graz (Steiermark)", "Graz", "AT"),
])
def test_r4_15_25_a_district_code_or_county_after_the_town_is_the_town(want, have, cc):
    assert same_city(want, have, cc) is True


@pytest.mark.parametrize("text, pc, city", [
    ("5 High Street, Newport Pagnell", "NP20 1AA", "Newport"), ("12 High Street, Bury St Edmunds", "BL9 0AA", "Bury"),
    ("12 High Street, Sutton Coldfield", "SM1 1AA", "Sutton"),
])
def test_r4_15_25_osm_house_in_the_shorter_namesake(text, pc, city):
    num = text.split()[0]
    feats = [_feat("house", 51.5, -1.0, cc="GB", street="High Street", housenumber=num, postcode=pc, city=city)]
    assert not _osm(feats, _p(text, "GB"))["verified"]


def test_r4_15_overture_row_in_bergen_is_not_bergen_auf_ruegen():
    row = _ov("5", "Hauptstraße", None, "Bergen", area_city="Bergen", on=True)
    assert not decide(_p("Hauptstraße 5, Bergen auf Rügen", "DE"), [row], **OVERTURE)["verified"]


def test_r4_25_greater_london_is_london_only_in_a_london_postal_district():
    def props(pc, city):
        return {"type": "house", "street": "High Street", "housenumber": "12", "postcode": pc, "city": city,
                "county": "Greater London"}
    assert "London" not in osm._towns(props("CR0 1AA", "Croydon"))
    assert "London" not in osm._towns(props("SM1 1AA", "London Borough of Sutton"))
    assert "London" not in osm._towns(props(None, "Croydon"))
    for pc in ("W8 5SA", "SW1A 2AA", "NW1 6XE", "EC1A 1BB", "E1 6AN", "N1 9GU", "SE1 7PB", "WC2N 5DU"):
        assert "London" in osm._towns(props(pc, "Royal Borough of Kensington and Chelsea"))
    feats = [_feat("house", 51.37, -0.10, cc="GB", street="High Street", housenumber="12", postcode="CR0 1AA",
                   city="Croydon", county="Greater London")]
    assert not _osm(feats, _p("12 High Street, London", "GB"))["verified"]
    feats = [_feat("house", 51.50, -0.19, cc="GB", street="Kensington High Street", housenumber="12", postcode="W8 5SA",
                   city="Royal Borough of Kensington and Chelsea", county="Greater London")]
    assert _osm(feats, _p("12 Kensington High Street, London", "GB"))["status"] == "verified"


# ------------------------------------------------- S13 a unit Overture does not list is not 'no unit data'

def test_r4_s13_unchecked_unit_note_names_the_units_overture_lists():
    rows = [_ov("12", "Studiestræde", "1455", "København K", d=10) for _ in range(4)]
    for r, u in zip(rows, (None, "2 tv", "3 tv", "st th")):
        r["unit"], r["id"] = u, f"x{u}"
    d = decide(_p("Studiestræde 12, 9. tv, 1455 København K", "DK"), rows, **OVERTURE)
    assert d["status"] == "verified" and d["best_match"]["unit"] is None
    assert any("incomplete" in n and "'9. tv' was not checked" in n and "2 tv, 3 tv, st th" in n for n in d["notes"])
    assert not any("has no flat / unit data" in n for n in d["notes"])
