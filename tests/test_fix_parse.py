"""Round-2 parse repairs: each test is named after the audit item it pins (R.. regressions, S.. partly fixed items).
Offline: verdicts use canned provider records, the Latvian register a canned answer."""

import asyncio

import pytest

from app.intl import countries as C
from app.intl import core
from app.intl.base import Lookup
from app.intl.match import decide
from app.intl.parse import is_unit_line, parse_line


def _cols(c, street, city="", postcode=""):
    """Street / city / postcode columns as the batch page joins them into one line."""
    return parse_line(", ".join(p for p in (street, f"{postcode} {city}".strip()) if p), c)


CC = C.COUNTRIES


def _c(number, street, pc, city, unit=None, kind="address", city_alt=None):
    return {"kind": kind, "number": number, "street": street, "postcode": pc, "city": city, "unit": unit,
            "formatted": f"{street} {number}, {pc or ''} {city}", "street_alt": [], "city_alt": city_alt or []}


def _line(cc, text, **want):
    a = parse_line(text, CC[cc])
    assert {k: getattr(a, k) for k in want} == want, text
    return a


def _decide(a, cands, authority="official"):
    return decide(a, cands, authority=authority, dataset="X")


# ------------------------------------------------------------------------------------------- the town

def test_r04_town_written_before_the_street():
    a = _line("HU", "Debrecen, Váci utca 10.", street="Váci utca", number="10", city="Debrecen", extra=[])
    d = _decide(a, [_c("10", "Váci utca", "1052", "Budapest")], authority="osm")
    assert d["status"] == "not_found" and any("only in other places" in n for n in d["notes"])
    a = _line("NL", "Rotterdam, Damrak 1", street="Damrak", number="1", city="Rotterdam")
    assert _decide(a, [_c("1", "Damrak", "1012LG", "Amsterdam")])["status"] == "not_found"
    _line("CZ", "Brno, Na Příkopě 12", city="Brno")
    _line("NL", "Jan Jansen, Rotterdam, Damrak 1", city="Rotterdam", extra=["Jan Jansen"])  # the part next to the street
    # not a recipient, and not when a town part follows the street
    _line("FR", "M. Jean Dupont, 5 rue de Rivoli", city="", extra=["M. Jean Dupont"])
    _line("DE", "Herr Max Mustermann, Hauptstraße 12", city="")
    _line("IE", "Apartment 5, The Elms, 12 Main Street, Cork T12 AB12", city="")


def test_r07_district_before_the_town_and_county_after_it():
    a = _line("NL", "Damrak 1, Centrum, Amsterdam", city="Amsterdam", extra=["Centrum"])
    assert _decide(a, [_c("1", "Damrak", "1012LG", "Amsterdam")])["status"] == "verified"
    # Photon's answer for the Irish address: town Kinsale, county 'County Cork', no postcode
    a = _line("IE", "12 Main Street, Kinsale, Co. Cork, P17 X123", city="Kinsale", postcode="P17 X123", extra=["Co. Cork"])
    d = _decide(a, [_c("12", "Main Street", None, "Kinsale", city_alt=["County Cork"])], authority="osm")
    assert d["status"] == "verified"
    a = _line("GB", "10 Market Place, Kendal, Cumbria, LA9 4TN", city="Kendal", extra=["Cumbria"])
    assert _decide(a, [_c("10", "Market Place", None, "Kendal")], authority="osm")["status"] == "verified"
    _line("IE", "12 Main Street, Kinsale, Co. Cork", city="Kinsale", extra=["Co. Cork"])
    _line("DE", "Hauptstraße 12, München, Bayern, 80331", city="München", postcode="80331", extra=["Bayern"])
    # the settlement, not the parish: In-ADS has Voka alevik in Jõhvi vald, so the parish written may be another
    _line("EE", "Narva mnt 7, Voka alevik, Toila vald", city="Voka alevik", extra=["Toila vald"])
    # notes, recipients and institutions after the town are not the town
    _line("DE", "Hauptstraße 12, München, bitte klingeln", city="München")
    _line("DE", "Kurfürstendamm 21, Berlin, Firma XY GmbH", city="Berlin")


def test_r07_two_plain_names_keep_the_first_town():
    # the second name can be the canton / province: taking it would verify the street in another town
    a = _line("CH", "Bahnhofstrasse 1, Winterthur, Zürich", city="Winterthur", extra=["Zürich"])
    d = _decide(a, [_c("1", "Bahnhofstrasse", "8001", "Zürich")])
    assert d["status"] == "not_found" and not d["verified"]
    _line("IT", "Via Roma 10, Sesto San Giovanni, Milano", city="Sesto San Giovanni")
    _line("ES", "Calle Mayor 5, Badalona, Barcelona", city="Badalona")


def test_r10_r48_bracket_qualifier_stays_with_the_town(monkeypatch):
    _line("DE", "Leipziger Straße 50, Frankfurt (Oder)", city="Frankfurt (Oder)", extra=[])
    _line("DE", "Karl-Marx-Straße 1, 15230 Frankfurt (Oder)", city="Frankfurt (Oder)", postcode="15230", extra=[])
    _line("DE", "Leipziger Straße 10, 06108 Halle (Saale)", city="Halle (Saale)")
    _line("DE", "Hauptstraße 1, Freiburg (Elbe), Tel. 04779 123456", city="Freiburg (Elbe)")
    _line("IT", "Via XX Settembre 97, 00187 Roma (RM)", city="Roma", extra=["(RM)"])  # a province code is not
    _line("DE", "Hauptstraße 12 (Hinterhaus), 80331 München", city="München", extra=["Hinterhaus"])

    def run(rec):
        async def provider(ctx, a):
            return Lookup(cands=[rec])
        for key in CC["DE"].providers:
            monkeypatch.setitem(core.PROVIDERS, key, provider)
        return asyncio.run(core.verify_intl(type("Ctx", (), {"ov": object()})(), None, "DE",
                                            address="Berliner Straße 20, Frankfurt (Oder)"))

    out = run(_c("20", "Berliner Straße", "60311", "Frankfurt am Main"))  # same number in the other Frankfurt
    assert out["status"] == "not_found" and out["parsed"]["city"] == "Frankfurt (Oder)"
    assert not any("read as Frankfurt am Main" in n for n in out["notes"])
    assert run(_c("20", "Berliner Straße", "15230", "Frankfurt (Oder)"))["status"] == "verified"


# --------------------------------------------------------------------------------------- street parts

@pytest.mark.parametrize("cc, text, street", [
    ("IE", "23 O'Connell Street Upper, Dublin 1", "O'Connell Street Upper"),
    ("IE", "40 Baggot Street Lower, Dublin 2", "Baggot Street Lower"),
    ("GB", "123 Notting Hill Gate, London W11 3QA", "Notting Hill Gate"),
    ("GB", "12 Park Road West, Birkenhead", "Park Road West"),
    ("GB", "1 Hyde Park Corner", "Hyde Park Corner"),
    ("GB", "4 High Street North", "High Street North"),
    ("GB", "123 Notting Hill Gate London W11 3QA", "Notting Hill Gate"),
])
def test_r05_english_street_keeps_its_last_word(cc, text, street):
    a = parse_line(text, CC[cc])
    assert a.street == street and a.city not in ("Upper", "Lower", "West", "Corner", "North", "Gate")


def test_r05_town_glued_to_an_english_street():
    _line("GB", "10 Downing Street London SW1A 2AA", street="Downing Street", city="London", postcode="SW1A 2AA")
    _line("GB", "John Smith, 10 Downing Street London SW1A 2AA", street="Downing Street", city="London")
    _line("IE", "12 Main Street Dublin 2 D02 X285", street="Main Street", city="Dublin 2")
    _line("IE", "apt 4 12 main street dublin 2", street="main street", city="dublin 2", unit="apt 4")
    # without a postcode too when what follows is place-shaped (round-3 [17]: gluing it lost the town), never in a
    # street column
    _line("GB", "10 Downing Street London", street="Downing Street", city="London")
    a = _cols(CC["IE"], "23 O'Connell Street Upper", "Dublin 1", "D01 C3W7")
    assert (a.street, a.number) == ("O'Connell Street Upper", "23")


@pytest.mark.parametrize("cc, street_col, street, number", [
    ("FR", "18 bis, rue de la République", "rue de la République", "18bis"),
    ("FR", "12, rue du 4 Septembre", "rue du 4 Septembre", "12"),
    ("FR", "12, rue du 11 Novembre 1918", "rue du 11 Novembre 1918", "12"),
    ("FR", "18 ter, avenue Foch", "avenue Foch", "18ter"),
    ("BE", "Rue Royale, 12 bis", "Rue Royale", "12bis"),
    ("LU", "5 bis, rue de Hollerich", "rue de Hollerich", "5bis"),
    ("IT", "Via 4 Novembre, 10", "Via 4 Novembre", "10"),
    ("IT", "Viale 24 Maggio, 15", "Viale 24 Maggio", "15"),
    ("ES", "Calle 2 de Mayo, 5", "Calle 2 de Mayo", "5"),
    ("PL", "ul. 3 Maja, 5", "ul. 3 Maja", "5"),
    ("PT", "Avenida 5 de Outubro, 100", "Avenida 5 de Outubro", "100"),
    ("DE", "Straße des 17. Juni, 135", "Straße des 17 Juni", "135"),
    ("DE", "c/o Müller, Hauptstraße 12", "Hauptstraße", "12"),   # a recipient part is still not glued to the street
])
def test_r06_comma_between_street_and_number_in_the_street_column(cc, street_col, street, number):
    a = _cols(CC[cc], street_col, "X", "")
    assert (a.street, a.number) == (street, number)


def test_r06_street_column_verifies():
    a = _cols(CC["FR"], "18 bis, rue de la République", "Lyon", "69002")
    assert _decide(a, [_c("18bis", "Rue de la République", "69002", "Lyon")])["status"] == "verified"


@pytest.mark.parametrize("cc, text, street, number, city", [
    ("IS", "Laugavegur 120, Reykjavík", "Laugavegur", "120", "Reykjavík"),
    ("IS", "Suðurlandsbraut 100, Reykjavík", "Suðurlandsbraut", "100", "Reykjavík"),
    ("FO", "Tinganesvegur 101, Tórshavn", "Tinganesvegur", "101", "Tórshavn"),
    ("BE", "Chaussée de Waterloo 1151, Uccle", "Chaussée de Waterloo", "1151", "Uccle"),
    ("BE", "Chaussée d'Alsemberg 1021, Uccle", "Chaussée d'Alsemberg", "1021", "Uccle"),
])
def test_r08_number_ending_the_first_part_is_the_house_number(cc, text, street, number, city):
    a = _line(cc, text, street=street, number=number, city=city, postcode="")
    assert _decide(a, [_c(number, street, "105", city)])["status"] == "verified"


def test_r08_postcode_first_still_read():
    _line("DE", "80331 München, Hauptstraße 12", street="Hauptstraße", number="12", postcode="80331", city="München")
    _line("AT", "A-5020 Salzburg, Getreidegasse 9", postcode="5020", city="Salzburg")
    _line("DE", "Berlin 10117, Unter den Linden 77", street="Unter den Linden", postcode="10117", city="Berlin")


@pytest.mark.parametrize("cc, text, unit, city", [
    ("ES", "Calle Mayor 5, 2 Izquierda, Madrid", "2 Izquierda", "Madrid"),
    ("PL", "ul. Długa 5, 3 piętro, Gdańsk", "3 piętro", "Gdańsk"),
    ("FI", "Mannerheimintie 10, 3 krs, Helsinki", "3 krs", "Helsinki"),
    ("DE", "Industriestraße 5, Tor 3, Neuss", "Tor 3", "Neuss"),
    ("DE", "Hauptstraße 12, Laden 2, Köln", "Laden 2", "Köln"),
    ("PT", "Rua Augusta 100, Loja 2, Lisboa", "Loja 2", "Lisboa"),
    ("ES", "Calle Mayor 5, Local 2, Madrid", "Local 2", "Madrid"),
])
def test_r09_unit_part_is_neither_town_nor_postcode(cc, text, unit, city):
    a = _line(cc, text, unit=unit, city=city, postcode="")
    assert not a.warnings


def test_r09_padding_and_districts():
    _line("DE", "Jungfernstieg 10, 1067 Dresden", postcode="01067", city="Dresden")
    _line("NO", "Storgata 10, 155 Oslo", postcode="0155", city="Oslo")
    _line("ES", "Calle Mayor 5, 123 Local, Madrid", postcode="", city="Madrid", extra=["123 Local"])
    _line("CZ", "Na Příkopě 15, Praha 1", city="Praha 1")
    _line("ES", "Calle Mayor 5, Bloque 2, Madrid", city="Madrid", extra=["Bloque 2"])  # a later town: not a district


def test_r58_number_comma_street_and_postcode_in_one_part():
    a = _line("FR", "12, rue de la Paix 75002 Paris", street="rue de la Paix", number="12", postcode="75002", city="Paris")
    assert _decide(a, [_c("12", "Rue de la Paix", "75002", "Paris")])["status"] == "verified"
    _line("FR", "55, rue du Faubourg Saint-Honoré 75008 Paris", street="rue du Faubourg Saint-Honoré", number="55")
    _line("LU", "42, Place Guillaume II L-1648 Luxembourg", street="Place Guillaume II", number="42", postcode="1648")
    _line("FR", "Jean Dupont, 12, rue de la Paix 75002 Paris", street="rue de la Paix", extra=["Jean Dupont"])


class _LvCtx:
    """The Latvian register answers only for block 2 of house 386."""
    ov = None

    def __init__(self):
        self.asked = []

    async def get(self, service, url, params, **kw):
        self.asked.append(params.get("q"))
        recs = [{"STD": "Brīvības gatve 386 k-2, Rīga, LV-1024", "NOSAUKUMS": "386 k-2", "ATRIB": "LV-1024", "KODS": 1}]
        return {"result": {"records": recs if "386 k-2" in params.get("q", "") else []}}


@pytest.mark.parametrize("text", ["Brīvības gatve 386 k2, Rīga", "Brīvības gatve 386 k.2, Rīga",
                                  "Brīvības gatve 386 korpuss 2, Rīga", "Brīvības gatve 386 k-2, Rīga"])
def test_r29_latvian_block_number(text):
    _line("LV", text, street="Brīvības gatve", number="386k-2", city="Rīga")
    ctx = _LvCtx()
    out = asyncio.run(core.verify_intl(ctx, None, "LV", address=text))
    assert out["status"] == "verified" and "386 k-2" in out["best_match"]["formatted"]
    assert "386 k-2" in ctx.asked[0]  # the register is asked for the block, in its own spelling


def test_r29_text_after_the_number_is_never_dropped():
    _line("NL", "Keizersgracht 123 III, 1015 CJ Amsterdam", number="123", extra=["III"])
    _line("BE", "Frankrijklei 37 b3, 2000 Antwerpen", number="37", extra=["b3"])
    _line("NL", "Keizersgracht 123 III, Amsterdam", city="Amsterdam", extra=["III"])
    _line("IT", "Via Roma 10 - 00100 Roma", extra=[])  # punctuation is not text


def test_r61_unit_word_after_the_house_number():
    _line("DE", "Ringstraße 7 EG links, 12345 Musterstadt", street="Ringstraße", number="7", unit="EG links")
    _line("DE", "Ringstraße 7 OG, 12345 Musterstadt", number="7", unit="OG")
    _line("DE", "Hauptstraße 12. OG 3, 80331 München", street="Hauptstraße", number="12", unit="OG 3")


def test_r13_h_number_as_its_own_part():
    assert is_unit_line("H0201")
    _line("NO", "Bryggen 15, H0201, 5003 Bergen", street="Bryggen", number="15", unit="H0201", extra=[])


def test_r11_street_named_after_an_institution():
    _line("DE", "Universitätsstraße 150, Gebäude NA 02/99, 44801 Bochum", street="Universitätsstraße", number="150",
          extra=["Gebäude NA 02/99"])
    _line("DE", "Klinikstraße 5, Gebäude 2 3, 12345 Musterstadt", street="Klinikstraße", number="5")
    _line("FR", "Bâtiment A 12, 12 Rue de l'Université, 75007 Paris", street="Rue de l'Université", number="12")
    _line("GB", "Spa House 3, 10 Spa Road, London SE16 3QT", street="Spa Road", number="10")
    # companies and institutions are still not the street
    _line("DE", "3M Deutschland GmbH, Carl-Schurz-Str. 1, 41453 Neuss", street="Carl-Schurz-Str.", number="1")
    _line("DE", "Hotel Adlon 1, Unter den Linden 77, 10117 Berlin", street="Unter den Linden", number="77")
    _line("IT", "Rossi S.p.A. 2, Via Roma 10, 20121 Milano", street="Via Roma", number="10")
    _line("BE", "Rue Neuve 1, Spa", city="Spa")


# ------------------------------------------------------------------------------------------- optional

def test_s05_number_first_bis_in_a_line():
    _line("FR", "18 bis, rue de la République, 69002 Lyon", street="rue de la République", number="18bis")
    _line("BE", "Rue Royale, 12 bis, 1000 Bruxelles", street="Rue Royale", number="12bis")
    _line("IT", "Via 4 Novembre, 10, 00187 Roma", street="Via 4 Novembre", number="10")
    _line("FR", "Rue Inexistante, 2, Paris", street="Rue Inexistante", number="2", city="Paris")


def test_s07_district_written_before_the_street():
    a = _line("CZ", "Praha 1, Na Příkopě 15, 110 00", street="Na Příkopě", number="15", postcode="110 00", city="Praha 1")
    assert _decide(a, [_c("583/15", "Na příkopě", "11000", "Praha 1")])["status"] in ("verified", "street_only")


def test_s09_po_boxes():
    for text in ("PL 123, 00101 Helsinki", "PB 50, 00101 Helsingfors", "Box 100, 00101 Helsinki"):
        a = parse_line(text, CC["FI"])
        assert a.po_box and not a.number and a.postcode == "00101", text
    for cc, col in (("DE", "Postfach 1234"), ("FR", "BP 123"), ("NL", "Postbus 123")):
        a = parse_line(col, CC[cc])
        assert a.po_box == col and not a.street and any("PO box" in w for w in a.warnings), col
    _line("CH", "Postfach, 8021 Zürich", po_box="Postfach", postcode="8021", city="Zürich")
    _line("GB", "PO Box 123, London SW1A 2AA", po_box="PO Box 123", postcode="SW1A 2AA", city="London")
    _line("CH", "Bahnhofstrasse 1, Postfach, 8001 Zürich", street="Bahnhofstrasse", number="1", city="Zürich")


def test_s04_portuguese_postcode_without_its_extension():
    a = _line("PT", "Rua Augusta 100, 1100 Lisboa", city="Lisboa", postcode="")
    assert any("1100" in w for w in a.warnings)
    d = _decide(a, [_c("100", "R AUGUSTA", "7940-128", "Cuba")], authority="overture")
    assert d["status"] == "not_found"  # the same street in another town is not this address
    _line("PT", "Rua Augusta 100, 1100053 Lisboa", postcode="1100-053", city="Lisboa")
    _line("PT", "Rua Augusta 100, 1100 053 Lisboa", postcode="1100-053", city="Lisboa")


def test_s06_flats_after_the_number():
    _line("LT", "Gedimino pr. 9 butas 12, 01103 Vilnius", street="Gedimino pr.", number="9", unit="butas 12")
    _line("ES", "Calle Mayor 5 3ºB, 28013 Madrid", street="Calle Mayor", number="5", unit="3oB")
    _line("ES", "Calle Alcala 48 piso 3 puerta B, 28014 Madrid", street="Calle Alcala", number="48")
