"""Round-2 repairs of the matcher (match.py / places.py), one test per audit item. Offline: provider answers are
trimmed from the real ones the audit harness cached."""

import asyncio

import pytest

from app.intl import countries as C
from app.intl import core, europe, osm
from app.intl.base import Lookup, cand
from app.intl.match import decide, norm_unit, num_matches, street_sim
from app.intl.parse import parse_line
from app.intl.places import local_city, same_city

CC = C.COUNTRIES
OFFICIAL = dict(authority="official", dataset="REG")


def _p(text, cc):
    return parse_line(text, CC[cc])


def _ban(number, street, postcode, city, kind="housenumber"):
    """A BAN (fr_ban) feature as the Géoplateforme returns it."""
    label = " ".join(p for p in (number, street, postcode, city) if p)
    return {"type": "Feature", "geometry": {"type": "Point", "coordinates": [3.06, 50.63]},
            "properties": {"type": kind, "housenumber": number, "street": street if kind == "housenumber" else None,
                           "name": street, "postcode": postcode, "city": city, "id": label, "label": label}}


def _fr(*feats):
    return europe.fr_ban_cands({"features": list(feats)})


def _ch(*labels):
    return europe.ch_cands({"results": [{"attrs": {"label": f"<b>{x}</b>", "lat": 47.0, "lon": 9.0}} for x in labels]})


def _ov(number, street, postcode=None, city=None, unit=None, area=None, country="DE"):
    """An Overture row as osm.overture() turns it into a candidate (with the Photon place it was searched around)."""
    row = {"address_number": number, "street": street, "postcode": postcode, "city": city, "lat": 50.0, "lon": 8.0,
           "uuid": f"{street}{number}{unit}", "subaddress": {"unit": unit} if unit else {}}
    return osm.overture_cand(row, (50.0, 8.0), country, area or {"area_postcode": None, "area_city": None})


def _photon(kind, street, number=None, postcode=None, city=None, county=None, cc="SE", name=None):
    p = {"type": kind, "countrycode": cc, "osm_type": "N", "osm_id": hash((street, number, postcode)) % 10**8,
         "postcode": postcode, "city": city, "county": county}
    if kind == "street":
        p["name"] = street
    else:
        p.update(street=street, housenumber=number, name=name)
    return {"type": "Feature", "geometry": {"type": "Point", "coordinates": [11.96, 57.7]}, "properties": p}


# ------------------------------------------------------------------- same-named towns (R15, R25, R26, R47, S10)

@pytest.mark.parametrize("want, have, cc", [
    ("Lille", "Marquette-lez-Lille", "FR"), ("Lyon", "Sainte-Foy-lès-Lyon", "FR"), ("Metz", "Montigny-lès-Metz", "FR"),
    ("Nancy", "Vandœuvre-lès-Nancy", "FR"), ("Madrid", "Las Rozas de Madrid", "ES"), ("Wien", "Breitenfurt bei Wien", "AT"),
    ("Saint-Denis-de-Pile", "Saint-Denis", "FR"), ("Bergen op Zoom", "Bergen", "NL"), ("Neu-Ulm", "Ulm", "DE"),
    ("Bad Homburg", "Homburg", "DE"), ("Buchs SG", "Buchs ZH", "CH"),
    ("Manchester", "Greater Manchester", "GB"), ("Wien", "Wiener Neustadt", "AT"),
])
def test_r15_a_town_containing_the_input_town_is_another_town(want, have, cc):
    assert same_city(want, have, cc) is False


@pytest.mark.parametrize("want, have, cc", [("Buchs", "Buchs ZH", "CH"), ("Bergen", "Bergen (NH)", "NL")])
def test_r15_the_start_of_a_longer_name_is_left_to_the_postcode(want, have, cc):
    # round 3 [29]: not the same town, but not another one either - the postcode (or the located street) decides. Round
    # 4 [12]: only for namesakes a qualifier tells apart; a name joined on ('Saint-Denis-de-Pile', 'Villeneuve-lès-
    # Avignon', 'Bergen op Zoom') is another town (test_r4_12_*)
    assert same_city(want, have, cc) is None


@pytest.mark.parametrize("want, have, cc", [
    ("Paris 11e", "Paris", "FR"), ("Lyon", "Lyon 3e Arrondissement", "FR"), ("Paris", "Paris Cedex 15", "FR"),
    ("Praha 1", "Praha", "CZ"), ("Budapest", "Budapest XI", "HU"), ("Dublin 2", "Dublin", "IE"), ("Munich", "München", "DE"),
    ("Brussel", "Bruxelles - Brussel", "BE"), ("Ukkel", "Uccle - Ukkel", "BE"), ("Graz", "Graz,01.Bez.:Innere Stadt", "AT"),
    ("København K", "København", "DK"), ("Lisboa", "1200-815 Lisboa", "PT"), ("Buchs ZH", "Buchs ZH", "CH"),
])
def test_r15_districts_exonyms_and_bilingual_names_are_the_same_town(want, have, cc):
    assert same_city(want, have, cc) is True


def test_r15_lille_address_is_not_verified_in_marquette_lez_lille():
    # BAN: the postcode query finds Lille's own street, the retry without it Marquette-lez-Lille's namesake
    cands = _fr(_ban("10", "Rue Faidherbe (Lille)", "59800", "Lille"), _ban("10", "Rue Faidherbe", "59520", "Marquette-lez-Lille"),
                _ban("10", "Rue Faidherbe (Hellemmes)", "59260", "Lille"))
    d = decide(_p("10 Rue Faidherbe, 59800 Lille", "FR"), cands, **OFFICIAL)
    assert d["status"] == "verified" and d["best_match"]["postcode"] == "59800" and d["notes"] == []
    d = decide(_p("10 Rue Faidherbe, 59800 Lille", "FR"), cands[1:], **OFFICIAL)  # only the namesakes: not this address
    assert not d["verified"] and any("only in other places" in n for n in d["notes"])


def test_r15_buchs_sg_address_is_not_verified_at_buchs_zh():
    cands = _ch("Bahnhofstrasse 2 9470 Buchs SG", "Bahnhofstrasse 4 9470 Buchs SG", "Bahnhofstrasse 1 8107 Buchs ZH")
    d = decide(_p("Bahnhofstrasse 1, 9470 Buchs", "CH"), cands, **OFFICIAL)
    assert d["status"] == "street_only" and not any("postcode differs" in n for n in d["notes"])
    assert [c["postcode"] for c in d["candidates"]] == ["8107"]
    # no postcode: 'Buchs' may be any of the Buchs; the only one with this address is it (round 3 [29]), with a note
    d = decide(_p("Bahnhofstrasse 1, Buchs", "CH"), _ch("Bahnhofstrasse 1 8107 Buchs ZH"), **OFFICIAL)
    assert d["status"] == "verified" and any("taken as Buchs ZH" in n for n in d["notes"])
    d = decide(_p("Bahnhofstrasse 1, Buchs", "CH"), _ch("Bahnhofstrasse 1 8107 Buchs ZH", "Bahnhofstrasse 1 9470 Buchs SG"),
               **OFFICIAL)
    assert d["status"] == "not_covered"
    # with its postcode the short name is fine, and is not a 'town differs'
    d = decide(_p("Bahnhofstrasse 1, 8107 Buchs", "CH"), _ch("Bahnhofstrasse 1 8107 Buchs ZH"), **OFFICIAL)
    assert d["status"] == "verified" and d["best_match"]["match"]["city_match"] is True and d["notes"] == []


def test_r47_beaumont_54470_is_not_verified_at_beaumont_89250():
    a = _p("25 Grande Rue, 54470 Beaumont", "FR")
    cands = _fr(_ban(None, "Grande Rue", "54470", "Beaumont", kind="street"), _ban("25", "Grande Rue", "89250", "Beaumont"),
                _ban("25", "Grande Rue", "51360", "Beaumont-sur-Vesle"))
    d = decide(a, cands, **OFFICIAL)
    assert d["status"] == "street_only" and not d["verified"]
    assert any("only in other places (89250 Beaumont" in n for n in d["notes"])


def test_r25_polish_village_with_a_far_postcode_is_another_village():
    pl = europe.pl_cands({"type": "address", "results": {
        "1": {"city": "Zielonki", "street": None, "number": "12", "code": "28-131"},
        "2": {"city": "Zielonki", "street": None, "number": "12", "code": "28-340"}}})
    d = decide(_p("Zielonki 12, 32-087 Zielonki", "PL"), pl, **OFFICIAL)
    assert d["status"] == "not_found" and any("only in other places" in n for n in d["notes"])
    pl = europe.pl_cands({"type": "address", "results": {
        "1": {"city": "Przybysławice", "street": None, "number": "12", "code": "32-046"}}})
    assert decide(_p("Przybysławice 12, 99-999 Przybysławice", "PL"), pl, **OFFICIAL)["status"] == "not_found"


def test_r26_villeneuve_is_not_villeneuve_les_avignon():
    cands = _fr(_ban("10", "Rue de la République", "30400", "Villeneuve-lès-Avignon"),
                _ban("10", "Rue de la République", "31270", "Villeneuve-Tolosane"), _ban("10", "La Placette", "04180", "Villeneuve"))
    d = decide(_p("10 Rue de la République, 04180 Villeneuve", "FR"), cands, **OFFICIAL)
    assert d["status"] == "not_found" and len(d["candidates"]) >= 2
    # a postcode one digit off leaves it to the town, and Villeneuve-lès-Avignon is another town than 'Villeneuve'
    # (round 4 [12]; round 3 verified it): unlike 'Newcastle NE1 6BX' for Newcastle upon Tyne NE1 6BR, a known big town
    d = decide(_p("10 Rue de la République, 30401 Villeneuve", "FR"), cands, **OFFICIAL)
    assert not d["verified"] and d["best_match"] is None


def test_s10_one_town_with_several_streets_of_the_name_needs_the_postcode():
    hits = [cand("address", number="5", street="Hauptstraße", postcode=pc, city="Berlin") for pc in ("10827", "10317")]
    d = decide(_p("Hauptstraße 5, Berlin", "DE"), hits, **OFFICIAL)
    assert d["status"] == "not_covered" and not d["verified"] and any("add the postcode" in n for n in d["notes"])
    d = decide(_p("Hauptstraße 5, 10317 Berlin", "DE"), hits, **OFFICIAL)
    assert d["status"] == "verified" and d["best_match"]["postcode"] == "10317"
    assert decide(_p("Hauptstraße 5, Berlin", "DE"), hits[:1], **OFFICIAL)["status"] == "verified"
    units = [cand("address", number="8", street="Damrak", postcode="1012LG", city="Amsterdam", unit=u) for u in "AB"]
    assert decide(_p("Damrak 8, Amsterdam", "NL"), units, **OFFICIAL)["status"] == "verified"  # one building, one postcode
    feats = [_photon("house", "Norra Hamngatan", "18", pc, "Göteborg", name=n) for pc, n in (("411 09", None), ("411 06", "Café"))]
    d = decide(_p("Norra Hamngatan 18, Göteborg", "SE"), osm.photon_cands(feats), authority="osm", dataset="OpenStreetMap")
    assert d["status"] == "verified"  # one building that OSM gives two neighbouring postcodes
    pl = europe.pl_cands({"type": "address", "results": {str(i): {"city": "Zielonki", "street": None, "number": "12", "code": pc}
                                                         for i, pc in enumerate(("28-131", "28-340"))}})
    assert decide(_p("Zielonki 12, Zielonki", "PL"), pl, **OFFICIAL)["status"] == "not_covered"
    # records whose town the source doesn't name (Icelandic postcodes 360, 750) don't hide the one that is in it
    hms = europe.is_cands({"features": [{"properties": {"HUSNR": 12, "HEITI_NF": "Hafnargata", "POSTNR": pc}} for pc in (230, 360, 750)]})
    d = decide(_p("Hafnargata 12, Reykjanesbær", "IS"), hms, **OFFICIAL)
    assert d["status"] == "verified" and d["best_match"]["postcode"] == "230"


def test_s10_same_name_town_with_a_far_postcode_and_no_town():
    cands = _fr(_ban("50", "Rue Jean Chatel", "97400", "Saint-Denis"))
    d = decide(_p("50 Rue Jean Chatel, 93200 Saint-Denis", "FR"), cands, **OFFICIAL)
    assert d["status"] == "not_found" and any("97400 Saint-Denis" in n for n in d["notes"])
    # Photon picks one of the towns with the street: without a town that is no proof
    d = decide(_p("Bahnhofstraße 1", "DE"), [_ov("1", "Bahnhofstraße", area={"area_postcode": "28790", "area_city": "Schwanewede"})],
               authority="overture", dataset="Overture Maps")
    assert d["status"] == "not_covered" and not d["verified"] and any("add the town" in n for n in d["notes"])
    # a postcode alone, another town's: only a typo in its last digits is the same place
    d = decide(_p("Bahnhofstraße 1, 99423", "DE"), [cand("address", number="1", street="Bahnhofstraße", postcode="99084", city="Erfurt")],
               **OFFICIAL)
    assert not d["verified"]
    d = decide(_p("Unter den Linden 77, 10115", "DE"), [cand("address", number="77", street="Unter den Linden", postcode="10117",
                                                            city="Berlin")], **OFFICIAL)
    assert d["status"] == "verified" and any("postcode differs" in n for n in d["notes"])


# ------------------------------------------------------------------------------- street names (R16, R17, R24, R34)

@pytest.mark.parametrize("want, have", [
    ("Jerozolimskie", "Aleje Jerozolimskie"), ("Castellana", "Paseo de la Castellana"),
    ("Champs-Élysées", "Avenue des Champs-Élysées"), ("Puerta del Sol", "Plaza de la Puerta del Sol"),
    ("Dr.-Otto-Nuschke-Straße", "Otto-Nuschke-Straße"), ("Rue du Dr Roux", "Rue du Docteur Roux"), ("Diagonal", "AVINGUDA DIAGONAL"),
    ("Narva", "Narva mnt"), ("ul. Aleje Jerozolimskie", "Aleje Jerozolimskie"), ("Mulholland Dr", "Mulholland Drive"),
])
def test_r16_a_type_word_left_out_is_the_same_street(want, have):
    assert street_sim(want, have) == 1.0


@pytest.mark.parametrize("want, have", [
    ("Markt", "Marktstraße"), ("Burg", "Burgstraat"), ("Am Bahnhof", "Bahnhofstraße"), ("Markt", "Marktplatz"),
    ("Calle Mayor", "Plaza Mayor"), ("rue de la Paix", "Avenue de la Paix"), ("Ringstraße", "Ring"), ("Lindenweg", "Lindenstraße"),
])
def test_r16_a_glued_or_another_type_is_another_street(want, have):
    assert street_sim(want, have) < 0.85


def test_r16_addresses_written_without_their_type_word_verify():
    es = europe.es_cands([{"type": "portal", "address": "PASEO CASTELLANA 81, Madrid", "portalNumber": 81, "postalCode": "28046",
                           "muni": "Madrid"}])
    d = decide(_p("Castellana 81, 28046 Madrid", "ES"), es, **OFFICIAL)
    assert d["status"] == "verified" and d["confidence"] == 0.97
    d = decide(_p("101 Champs-Élysées, 75008 Paris", "FR"), _fr(_ban("101", "Avenue des Champs Élysées", "75008", "Paris")), **OFFICIAL)
    assert d["status"] == "verified" and d["confidence"] == 0.97


@pytest.mark.parametrize("want, have, cc", [
    ("Brīvības iela", "Brīvības gatve", "LV"), ("Brīvības iela", "Brīvības gatve", None), ("Prešernova ulica", "Prešernova cesta", "SI"),
    ("Via Augusta", "Calle Augusta", "ES"), ("ul. Polna", "pl. Polna", "PL"),
])
def test_r17_two_different_types_are_two_streets(want, have, cc):
    assert street_sim(want, have, cc) < 0.85


def test_r17_latvian_gatve_is_its_own_type():
    assert street_sim("Brīvības gatve", "Brīvības g.", "LV") == 1.0 and street_sim("Jogailos gatvė", "Jogailos g.", "LT") == 1.0
    assert street_sim("Brīvības", "Brīvības gatve", "LV") == 1.0  # written without its type
    # Polish 'ul.' is written for an 'al.' too: compatible, but not the exact name ('al. Wolności' vs 'ul. Wolności')
    assert 0.85 <= street_sim("ul. Wolności", "al. Wolności", "PL") < 0.999
    assert street_sim("ul. Mickiewicza", "Aleja Adama Mickiewicza", "PL") >= 0.85
    d = decide(_p("Brivibas iela 300, Rīga", "LV"), [_ov("300", "Brīvības gatve", "LV-1006", "Rīga", country="LV"),
                                                    _ov("95", "Brīvības iela", "LV-1001", "Rīga", country="LV")],
               authority="overture", dataset="Overture Maps")
    assert d["status"] == "street_only" and not d["verified"]


@pytest.mark.parametrize("want, have, cc", [
    ("High Street", "Kensington High Street", "GB"), ("King Street", "King William Street", "GB"),
    ("Calle Real", "Calle Real de Arriba", "ES"), ("Rue Saint-Jean", "Rue Jean", "FR"), ("Via San Marco", "Via Marco", "IT"),
    ("Schillerstraße", "Friedrich-Schiller-Straße", "DE"),
])
def test_r24_a_longer_name_is_another_street(want, have, cc):
    assert street_sim(want, have, cc) < 0.85


def test_r24_kensington_high_street_does_not_verify_high_street():
    assert street_sim("Rue Saint-Jean", "Rue Jean") < 0.85 and street_sim("Via San Marco", "Via Marco") < 0.85
    feats = [_photon("house", "Kensington High Street", "12", "W8 5SF", "London", cc="GB")]
    d = decide(_p("12 High Street, London", "GB"), osm.photon_cands(feats), authority="osm", dataset="OpenStreetMap")
    assert not d["verified"]


@pytest.mark.parametrize("want, have, cc", [
    ("ul. Kościuszki", "Tadeusza Kościuszki", "PL"), ("Via Garibaldi", "Via Giuseppe Garibaldi", "IT"),
    ("Via Verdi", "VIA VERDI GIUSEPPE", "IT"), ("Avenue Foch", "Avenue Maréchal Foch", "FR"), ("Rue Gambetta", "Rue Léon Gambetta", "FR"),
])
def test_r24_streets_named_after_people_still_match_without_the_first_name(want, have, cc):
    assert street_sim(want, have, cc) >= 0.85


@pytest.mark.parametrize("want, have", [
    ("Hamngatan", "Norra Hamngatan"), ("Voldgade", "Vester Voldgade"), ("Farimagsgade", "Nørre Farimagsgade"),
    ("Storgatan", "Södra Storgatan"), ("Vesterbrogade", "Nørre Vesterbrogade"), ("Rantatie", "Pohjois Rantatie"),
])
def test_r34_nordic_direction_words_make_another_street(want, have):
    assert street_sim(want, have) < 0.85 and street_sim(want, have, "SE") < 0.85


def test_r34_hamngatan_is_not_verified_on_norra_or_ostra_hamngatan():
    feats = [_photon("house", s, "18", pc, "Göteborg") for s, pc in (("Norra Hamngatan", "411 06"), ("Östra Hamngatan", "411 09"),
                                                                    ("Västra Hamngatan", "411 17"))]
    d = decide(_p("Hamngatan 18, 411 06 Göteborg", "SE"), osm.photon_cands(feats), authority="osm", dataset="OpenStreetMap")
    assert not d["verified"]


# ------------------------------------------------------------------------------------ BAN's '(Commune)' (R18)

def test_r18_lille_street_is_not_the_hellemmes_or_lomme_one():
    cands = _fr(_ban("10", "Rue Léon Gambetta (Lille)", "59000", "Lille"), _ban("10", "Rue Léon Gambetta (Hellemmes)", "59260", "Lille"))
    d = decide(_p("10 Rue Léon Gambetta, 59000 Lille", "FR"), cands, **OFFICIAL)
    assert d["status"] == "verified" and d["best_match"]["postcode"] == "59000" and d["notes"] == []
    # Lille's own street has no 5: Lomme's and Hellemmes' (other former communes) don't stand in for it
    cands = _fr(_ban(None, "Rue Jean Jaurés (Lille)", "59000", "Lille", kind="street"),
                _ban("5", "Rue Jean Jaurés (Lomme)", "59160", "Lille"), _ban("5", "Rue Jean Jaurés (Hellemmes)", "59260", "Lille"))
    d = decide(_p("5 Rue Jean Jaurès, 59000 Lille", "FR"), cands, **OFFICIAL)
    assert d["status"] == "street_only" and not d["verified"]
    assert any("59160 Lille (Lomme)" in n for n in d["notes"])
    assert decide(_p("5 Rue Jean Jaurès, 59160 Lomme", "FR"), cands, **OFFICIAL)["status"] == "verified"


# --------------------------------------------------------------------------------- the exact-name rule (R33, S12)

def test_s12_another_towns_exact_name_does_not_hide_the_local_street():
    cands = _fr(_ban("10", "Avenue Maréchal Foch", "69006", "Lyon"), _ban("10", "Avenue Foch", "89000", "Auxerre"))
    d = decide(_p("10 Avenue Foch, 69006 Lyon", "FR"), cands, **OFFICIAL)
    assert d["status"] == "verified" and d["best_match"]["postcode"] == "69006"
    assert any("street name differs" in n for n in d["notes"])
    # the register's own street of exactly that name in the town still keeps a look-alike out
    d = decide(_p("Kirchstraße 4, 10115 Berlin", "DE"), [cand("address", number="4", street="Kirschstraße", postcode="10115", city="Berlin"),
                                                      cand("street", street="Kirchstraße", postcode="10115", city="Berlin")], **OFFICIAL)
    assert d["status"] == "street_only"


def test_r33_photon_street_does_not_demand_the_registers_exact_spelling():
    a = _p("Rua de São Bento 98, 1200-820 Lisboa", "PT")
    area = {"area_postcode": "1200-820", "area_city": "Lisboa"}
    cands = [_ov("98", "R S BENTO", "1200-820", None, unit="1 ESQ", area=area, country="PT")]
    cands += [c for c in osm.photon_cands([_photon("street", "Rua de São Bento", postcode="1200-821", city="Lisboa", cc="PT")])]
    d = decide(a, cands, authority="overture", dataset="Overture Maps", incomplete=osm.INCOMPLETE["PT"])
    assert d["status"] == "verified" and d["best_match"]["street"] == "R S BENTO"


# ------------------------------------------------------------------------------------ units (R21, R22, R30, S13, S14)

def _nl(number, unit=None):
    return cand("address", number=number, street="Eerste Jan Steenstraat", unit=unit, postcode="1072NK", city="Amsterdam")


def test_r21_the_buildings_other_units_are_listed():
    cands = [_nl("8", u) for u in "BCDE"]
    d = decide(_p("Eerste Jan Steenstraat 8-F, 1072NK Amsterdam", "NL"), cands, authority="official", dataset="BAG", unit_support=True)
    assert d["status"] == "unit_not_found" and [c["unit"] for c in d["candidates"]] == ["C", "D", "E"]
    d = decide(_p("Eerste Jan Steenstraat 8, 1072NK Amsterdam", "NL"), cands, authority="official", dataset="BAG", unit_support=True)
    assert d["status"] == "verified" and len(d["candidates"]) == 3


def test_r22_a_missing_unit_is_unit_match_false():
    a = _p("Damrak 1-3, 1012LG Amsterdam", "NL")
    d = decide(a, [cand("address", number="1", street="Damrak", postcode="1012LG", city="Amsterdam")], authority="official",
               dataset="BAG", unit_support=True)
    assert d["status"] == "unit_not_found" and d["best_match"]["match"]["unit_match"] is False


def test_r30_s19_norwegian_leil_h_number_is_the_unit():
    assert norm_unit("leil. H0201") == norm_unit("H0201") == norm_unit("leilighet H0201") == "h0201"
    data = {"adresser": [{"adressenavn": "Storgata", "nummer": 11, "postnummer": "8300", "poststed": "SVOLVÆR",
                          "adressetekst": "Storgata 11", "bruksenhetsnummer": ["H0101", "H0201"]}]}
    a = _p("Storgata 11, leil. H0201, 8300 Svolvær", "NO")
    d = decide(a, europe.no_cands(data, a.unit), authority="official", dataset="Matrikkelen", unit_support=True)
    assert d["status"] == "verified" and d["match_level"] == "unit" and d["best_match"]["unit"] == "H0201"


def test_s13_s14_overture_letter_units_and_unchecked_flats():
    a = _p("Via del Corso 336/A, 00186 Roma", "IT")
    d = decide(a, [_ov("336", "VIA DEL CORSO", None, "Roma", country="IT"), _ov("336", "VIA DEL CORSO", None, "Roma", "A", country="IT")],
               authority="overture", dataset="Overture Maps")
    assert d["status"] == "verified" and d["best_match"]["unit"] == "A" and not any("not checked" in n for n in d["notes"])
    a = _p("Studiestræde 12, 9. tv, 1455 København K", "DK")
    d = decide(a, [_ov("12", "Studiestræde", "1455", "København K", "2 tv", country="DK"),
                   _ov("12", "Studiestræde", "1455", "København K", country="DK")], authority="overture", dataset="Overture Maps")
    assert d["status"] == "verified" and d["best_match"]["unit"] is None and d["best_match"]["match"]["unit_match"] is None
    assert any("not checked" in n for n in d["notes"])


# ------------------------------------------------------------------------------------------- numbers (R60)

def test_r60_a_range_holds_its_first_and_last_number():
    fi = CC["FI"]
    for number, rng, want in [("11", "11-14", "range"), ("11", "11-35", "range"), ("13", "11-35", "range"), ("2", "2-5", "range"),
                              ("5", "2-5", "range"), ("12", "11-13", None), ("13", "11-13", "range"), ("36", "11-35", None)]:
        assert num_matches(parse_line(f"Pohjoisesplanadi {number}, 00170 Helsinki", fi), rng) == want, (number, rng)
    data = {"features": [{"geometry": {"type": "Point", "coordinates": [24.95, 60.16]}, "properties": {
        "address_fin": "Pohjoisesplanadi 11-35", "address_name_fin": "Pohjoisesplanadi", "number_part_of_address_number": 11,
        "number_part_of_address_number2": 35, "postal_code": "00170", "postal_office_fin": "HELSINKI"}}]}
    a = parse_line("Pohjoisesplanadi 11, 00170 Helsinki", fi)
    assert decide(a, europe.fi_cands(data, a), **OFFICIAL)["status"] == "verified"
    # a Czech building/orientation pair is no range
    assert num_matches(_p("Vodičkova 13, 110 00 Praha 1", "CZ"), "5/25") is None


# ---------------------------------------------------------------------- Overture / OSM place checks (R55, R07)

def test_r55_overture_row_with_its_own_town_and_postcode_is_not_the_input_town():
    area = {"area_postcode": "74235", "area_city": "Erlenbach"}
    d = decide(_p("Hauptstraße 5, 74235 Erlenbach", "DE"), [_ov("5", "Hauptstraße", "74189", "Weinsberg", area=area)],
               authority="overture", dataset="Overture Maps")
    assert not d["verified"]
    # a row without a town of its own is the town Photon located the street in
    d = decide(_p("Hauptstraße 5, 74235 Erlenbach", "DE"), [_ov("5", "Hauptstraße", area=area)], authority="overture",
               dataset="Overture Maps")
    assert d["status"] == "verified"
    # Photon's town still shows a wrong input town next to a matching postcode
    d = decide(_p("Hauptplatz 1, 8010 Wien", "AT"), [_ov("1", "Hauptplatz", "8010", "Graz", area={"area_postcode": "8010",
                                                                                             "area_city": "Graz"}, country="AT")],
               authority="overture", dataset="Overture Maps")
    assert d["status"] == "verified" and any("town differs" in n for n in d["notes"])
    feats = [_photon("house", "High Street", "12", "SK1 1AA", "Stockport", "Greater Manchester", cc="GB")]
    assert not decide(_p("12 High Street, Manchester", "GB"), osm.photon_cands(feats), authority="osm", dataset="OSM")["verified"]


def test_r07_co_cork_is_county_cork():
    assert same_city("Co. Cork", "County Cork", "IE") and not same_city("Co. Cork", "Cork", "IE")
    feats = [_photon("house", "Main Street", "12", None, "Kinsale", "County Cork", cc="IE")]
    d = decide(_p("12 Main Street, Kinsale, Co. Cork, P17 X123", "IE"), osm.photon_cands(feats), authority="osm", dataset="OSM")
    assert d["status"] == "verified"


# ------------------------------------------------------------------------------- Frankfurt (Oder) (R10, R48)

def test_r10_r48_a_bare_or_qualified_namesake_is_not_rewritten():
    assert local_city("Frankfurt", "DE") == "Frankfurt" and local_city("Freiburg", "DE") == "Freiburg"
    assert local_city("Newcastle", "GB") == "Newcastle" and local_city("Frankfurt (Oder)", "DE") == "Frankfurt (Oder)"
    assert local_city("Francfort", "DE") == "Frankfurt am Main" and local_city("Vienna", "AT") == "Wien"
    assert same_city("Frankfurt (Oder)", "Frankfurt am Main", "DE") is False
    assert same_city("Frankfurt (Oder)", "Frankfurt (Oder)", "DE") is True
    assert same_city("Frankfurt", "Frankfurt am Main", "DE") is None  # which Frankfurt, only a postcode can say


@pytest.mark.parametrize("want, have, same", [
    ("Berlin (Mitte)", "Berlin", True), ("Musterstadt (Neudorf)", "Musterstadt", True), ("Halle (Saale)", "Halle (Saale)", True),
    ("Halle (Saale)", "Halle an der Saale", True), ("Frankfurt (Oder)", "Frankfurt an der Oder", True),
    ("Frankfurt (Oder)", "Frankfurt am Main", False), ("Halle (Saale)", "Halle (Westf.)", False),
    ("Frankfurt", "Frankfurt (Oder)", None), ("Berlin (Mitte)", "Neu-Ulm", False),
])
def test_r10_r48_a_bracket_after_the_town_narrows_it(want, have, same):
    # the parser keeps 'Frankfurt (Oder)' / 'Berlin (Mitte)': a district in brackets is still the town, another
    # qualifier is another town, and a bare name can't say which namesake it is
    assert same_city(want, have, "DE") is same
    assert local_city(want, "DE") == want


class _Ctx:
    ov = object()


def test_r48_frankfurt_oder_is_not_matched_in_frankfurt_am_main(monkeypatch):
    calls = []

    def fake(key, lk):
        async def provider(ctx, a):
            calls.append((key, a.city))
            return lk
        return provider

    area = {"area_postcode": "60311", "area_city": "Frankfurt am Main"}
    lk = Lookup(cands=[_ov("20", "Berliner Straße", None, "Frankfurt am Main", area=area)])
    monkeypatch.setitem(core.PROVIDERS, "overture", fake("overture", lk))
    monkeypatch.setitem(core.PROVIDERS, "osm", fake("osm", Lookup(status="geocode_failed")))
    out = asyncio.run(core.verify_intl(_Ctx(), None, "DE", address="Berliner Straße 20, Frankfurt (Oder)"))
    assert not out["verified"] and not any("Frankfurt am Main" in n for n in out["notes"] if "read as" in n)
    assert calls[0][1] != "Frankfurt am Main"


def test_mistyped_postcode_in_the_same_town_still_verifies():
    # a numbered district names one place: Praha 2 with Praha 1's postcode is Praha 2 with a typo
    rec = {"kind": "address", "number": "9", "street": "náměstí Míru", "street_alt": [], "postcode": "12000",
           "city": "Praha 2", "formatted": "náměstí Míru 820/9, Vinohrady, 12000 Praha 2", "lat": 50.07, "lon": 14.44}
    d = decide(_p("náměstí Míru 9, 110 00 Praha 2", "CZ"), [rec], authority="official", dataset="RÚIAN")
    assert d["status"] == "verified" and d["best_match"]["match"]["zip_match"] is False
    # a row next to the street Photon located in München's 80331, with München's other postcode
    row = {**rec, "number": "5", "street": "Hauptstraße", "postcode": "81241", "city": "München",
           "formatted": "Hauptstraße 5, 81241 München", "distance_m": 350.0, "area_pc_ok": True}
    assert decide(_p("Hauptstraße 5, 80331 München", "DE"), [row], authority="overture", dataset="Overture Maps")["verified"]
    # round 3 [24]: not when Photon located the street by its town only (it may be a namesake's)
    row["area_pc_ok"] = False
    assert not decide(_p("Hauptstraße 5, 80331 München", "DE"), [row], authority="overture", dataset="Overture Maps")["verified"]
    # but a register record of a namesake town far away is not the input's (no distance: found by a nationwide search)
    far = {**rec, "number": "25", "street": "Grande Rue", "postcode": "89250", "city": "Beaumont",
           "formatted": "25 Grande Rue 89250 Beaumont"}
    assert not decide(_p("25 Grande Rue, 54470 Beaumont", "FR"), [far], authority="official", dataset="BAN")["verified"]
