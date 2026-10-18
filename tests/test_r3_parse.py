"""Round-3 parse repairs: each test is named after the audit item it pins ([N] regressions, R../S.. earlier items).
Offline: verdicts use canned provider records."""

import pytest

from app.intl import countries as C
from app.intl.match import decide
from app.intl.parse import parse_line

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


# ------------------------------------------------------------------------- the town and what is not the town

@pytest.mark.parametrize("cc, text, city, name, street", [
    ("LU", "Marc Weber, 23 rue du Marché-aux-Herbes, Luxembourg", "Luxembourg", "Marc Weber", "rue du Marché-aux-Herbes"),
    ("MC", "Jean Martin, 2 Boulevard des Moulins, Monaco", "Monaco", "Jean Martin", "Boulevard des Moulins"),
    ("SM", "Mario Rossi, Via Giacomo Matteotti 5, San Marino", "San Marino", "Mario Rossi", "Via Giacomo Matteotti"),
    ("GI", "John Smith, 10 Main Street, Gibraltar", "Gibraltar", "John Smith", "Main Street"),
    ("LU", "Société Générale, 11 avenue Emile Reuter, Luxembourg", "Luxembourg", "Société Générale", "avenue Emile Reuter"),
    ("MC", "Martin, 2 Boulevard des Moulins, Monaco", "Monaco", "Martin", "Boulevard des Moulins"),  # one word too
])
def test_r3_12_city_state_town_is_not_the_recipient(cc, text, city, name, street):
    a = _line(cc, text, city=city, street=street)
    assert name in a.extra


def test_r3_12_city_state_verifies_in_the_written_town():
    a = parse_line("Marc Weber, 23 rue du Marché-aux-Herbes, Luxembourg", CC["LU"])
    d = _decide(a, [_c("23", "Rue du Marché-aux-Herbes", "1728", "Luxembourg")])
    assert d["status"] == "verified"
    _line("LU", "23 rue du Marché-aux-Herbes, Luxembourg", city="Luxembourg", extra=[])  # unchanged without the name


@pytest.mark.parametrize("cc, text, name, street", [
    ("NL", "Jan Jansen, Damrak 1", "Jan Jansen", "Damrak"),
    ("DE", "Bäckerei Müller, Hauptstraße 12", "Bäckerei Müller", "Hauptstraße"),
    ("GB", "John Smith, 221B Baker Street", "John Smith", "Baker Street"),
    ("DE", "Max Mustermann, Hauptstraße 12", "Max Mustermann", "Hauptstraße"),
    ("FR", "Société Dupont, 5 rue de Rivoli", "Société Dupont", "rue de Rivoli"),
    ("DE", "Familie Müller, Hauptstraße 12", "Familie Müller", "Hauptstraße"),
    ("CH", "Musterfirma AG, Bahnhofstrasse 1", "Musterfirma AG", "Bahnhofstrasse"),
])
def test_r3_18_62_recipient_before_the_street_is_not_the_town(cc, text, name, street):
    a = _line(cc, text, city="", street=street, extra=[name])


def test_r3_18_recipient_before_the_street_verifies_nationwide():
    a = parse_line("Jan Jansen, Damrak 1", CC["NL"])
    d = _decide(a, [_c("1", "Damrak", "1012LG", "Amsterdam")])
    assert d["status"] == "verified" and any("no town or postcode" in n for n in d["notes"])


@pytest.mark.parametrize("cc, text, city", [
    # a place before the street is still the town: a known one, a numbered district, one word, a postcode with it
    ("NL", "Rotterdam, Damrak 1", "Rotterdam"), ("AT", "Wien, Graben 19", "Wien"),
    ("PL", "Kraków, ul. Grodzka 5", "Kraków"), ("CZ", "Praha 2, Vinohradská 12", "Praha 2"),
    ("HU", "Szeged, Kárász utca 5.", "Szeged"), ("HU", "Debrecen, Váci utca 10.", "Debrecen"),
    ("CZ", "Praha 1, Na Příkopě 15, 110 00", "Praha 1"), ("CH", "Baden, Badstrasse 10", "Baden"),
    ("ES", "Getafe, Calle Real 10", "Getafe"), ("GB", "Kendal, 14 Market Street", "Kendal"),
    ("DK", "København V, Vesterbrogade 10", "København V"), ("HU", "Budapest VI., Andrássy út 12.", "Budapest VI."),
])
def test_r3_18_place_before_the_street_is_the_town(cc, text, city):
    _line(cc, text, city=city, extra=[])


# ------------------------------------------------------------------------------ brackets after the town

@pytest.mark.parametrize("cc, text, city, note", [
    ("ES", "Calle Mayor 5, 28801 Alcalá de Henares (Madrid)", "Alcalá de Henares", "(Madrid)"),
    ("ES", "Calle Larios 5, 29005 Málaga (Málaga)", "Málaga", "(Málaga)"),
    ("ES", "Calle Real 10, 28901 Getafe (Madrid)", "Getafe", "(Madrid)"),
    ("ES", "Calle Mayor 1, 28801 Alcalá de Henares (Madrid)", "Alcalá de Henares", "(Madrid)"),
    ("PL", "ul. Długa 5, 80-827 Gdańsk (Pomorskie)", "Gdańsk", "(Pomorskie)"),
    ("AT", "Hauptplatz 1, 8010 Graz (Steiermark)", "Graz", "(Steiermark)"),
    ("DE", "Marienplatz 1, 80331 München (Bayern)", "München", "(Bayern)"),
    ("IT", "Via Roma 1, 20099 Sesto San Giovanni (Milano)", "Sesto San Giovanni", "(Milano)"),
    ("IT", "Via Roma 1, 20121 Milano (MI)", "Milano", "(MI)"),
])
def test_r3_13_52_province_in_brackets_is_a_note(cc, text, city, note):
    a = _line(cc, text, city=city)
    assert note in a.extra and "(" not in a.city  # the registers are asked for the bare town


@pytest.mark.parametrize("cc, text, city", [
    ("NL", "Kerkstraat 1, Bergen (NH)", "Bergen (NH)"),
    ("NL", "Kerkstraat 1, 5854 AJ Bergen (L)", "Bergen (L)"),
    ("DE", "Karl-Marx-Straße 1, 15230 Frankfurt (Oder)", "Frankfurt (Oder)"),
    ("DE", "Markt 1, 06108 Halle (Saale)", "Halle (Saale)"),
    ("DE", "Markt 1, Halle (Saale), Deutschland", "Halle (Saale)"),
])
def test_r3_13_namesake_qualifier_stays_in_the_town(cc, text, city):
    _line(cc, text, city=city)


# ------------------------------------------------------------------------------- a county next to the town

@pytest.mark.parametrize("cc, text, city, county, postcode", [
    ("GB", "10 Downing Street, Cambridge, Cambridgeshire CB2 3DT", "Cambridge", "Cambridgeshire", "CB2 3DT"),
    ("IE", "12 Main Street, Kinsale, County Cork P17 X123", "Kinsale", "County Cork", "P17 X123"),
    ("GB", "3 The Crescent, Leeds, West Yorkshire LS6 2NW", "Leeds", "West Yorkshire", "LS6 2NW"),
    ("GB", "14 Market Street, Kendal, Cumbria LA9 4TN", "Kendal", "Cumbria", "LA9 4TN"),
    # R07: the county sharing the postcode's part
    ("GB", "10 Market Place, Kendal, Cumbria LA9 4TN", "Kendal", "Cumbria", "LA9 4TN"),
    ("IE", "12 Main Street, Lucan, Co. Dublin K78 E2F7", "Lucan", "Co. Dublin", "K78 E2F7"),
    # [15]: glued to the town
    ("IE", "12 Main Street, Lucan Co. Dublin", "Lucan", "Co. Dublin", ""),
    ("IE", "12 Main Street, Kinsale Co. Cork P17 X123", "Kinsale", "Co. Cork", "P17 X123"),
    ("GB", "10 Market Place, Kendal Cumbria LA9 4TN", "Kendal", "Cumbria", "LA9 4TN"),
    ("GB", "1 High Street, Newport Isle of Wight PO30 1AA", "Newport", "Isle of Wight", "PO30 1AA"),
    # its own part (already right)
    ("IE", "12 Main Street, Kinsale, Co. Cork, P17 X123", "Kinsale", "Co. Cork", "P17 X123"),
    ("GB", "10 Market Place, Kendal, Cumbria, LA9 4TN", "Kendal", "Cumbria", "LA9 4TN"),
])
def test_r3_14_15_county_next_to_the_town(cc, text, city, county, postcode):
    a = _line(cc, text, city=city, postcode=postcode)
    assert county in a.extra


@pytest.mark.parametrize("cc, text, record", [
    # Photon's house records carry the town and the county but no postcode
    ("IE", "12 Main Street, Kinsale, County Cork P17 X123", _c("12", "Main Street", None, "Kinsale", city_alt=["County Cork"])),
    ("GB", "3 The Crescent, Leeds, West Yorkshire LS6 2NW", _c("3", "The Crescent", None, "Leeds")),
    ("IE", "12 Main Street, Lucan Co. Dublin", _c("12", "Main Street", None, "Lucan")),
    ("IE", "12 Main Street, Kinsale Co. Cork P17 X123", _c("12", "Main Street", None, "Kinsale")),
    ("GB", "10 Market Place, Kendal Cumbria LA9 4TN", _c("10", "Market Place", None, "Kendal")),
    ("GB", "10 Market Place, Kendal, Cumbria LA9 4TN", _c("10", "Market Place", None, "Kendal")),
])
def test_r3_14_15_county_next_to_the_town_verifies(cc, text, record):
    assert _decide(parse_line(text, CC[cc]), [record], authority="osm")["status"] == "verified"


@pytest.mark.parametrize("cc, text, city", [
    ("GB", "1 High Street, Stratford upon Avon CV37 6AU", "Stratford upon Avon"),  # 'Avon' is part of the name
    ("GB", "1 High Street, West Yorkshire LS6 2NW", "West Yorkshire"),  # a county alone stays what was written
    ("IE", "12 Main Street, Dublin 2, Ireland", "Dublin 2"),  # S04: the district is the match's to compare
])
def test_r3_14_county_cut_leaves_town_names(cc, text, city):
    _line(cc, text, city=city)


# ------------------------------------------------------------------------- the Swiss canton after the town

@pytest.mark.parametrize("text, city", [
    ("Badstrasse 10, Baden AG", "Baden AG"), ("Zentralstrasse 12, Wohlen AG", "Wohlen AG"),
    ("Bahnhofstrasse 1, Reinach AG", "Reinach AG"), ("Hauptstrasse 10, Buchs AG", "Buchs AG"),
    ("Seetalstrasse 5, Muri AG", "Muri AG"), ("Badstrasse 10, 5400 Baden AG", "Baden AG"),
])
def test_r3_16_canton_aargau_is_not_a_legal_form(text, city):
    _line("CH", text, city=city, extra=[])


def test_r3_16_company_with_ag_is_still_not_the_town():
    _line("CH", "Musterfirma AG, Bahnhofstrasse 1, 8001 Zürich", city="Zürich", extra=["Musterfirma AG"])
    _line("CH", "Bahnhofstrasse 1, Muster Holding AG", city="", extra=["Muster Holding AG"])


# ------------------------------------------------------------- the town glued to the street, no postcode

@pytest.mark.parametrize("cc, text, street, number, city", [
    ("GB", "10 Downing Street London", "Downing Street", "10", "London"),
    ("GB", "221B Baker Street London", "Baker Street", "221B", "London"),
    ("IE", "12 Main Street Swords", "Main Street", "12", "Swords"),
    ("GB", "14 Market Street Kendal", "Market Street", "14", "Kendal"),
    ("GB", "8 Station Road Cambridge", "Station Road", "8", "Cambridge"),
    ("IE", "15 Shop Street Galway", "Shop Street", "15", "Galway"),
    ("GB", "12 High Street Oxford", "High Street", "12", "Oxford"),
    ("IE", "12 Main Street Kinsale", "Main Street", "12", "Kinsale"),
    # the street's own last word stays (R05)
    ("IE", "12 O'Connell Street Upper", "O'Connell Street Upper", "12", ""),
    ("GB", "10 Park Road West", "Park Road West", "10", ""),
    ("GB", "1 Hyde Park Corner", "Hyde Park Corner", "1", ""),
    ("GB", "4 High Street North", "High Street North", "4", ""),
    # S08: a French town only when it is a known one, and not 'rue de Paris'
    ("FR", "5 rue de Rivoli Paris", "rue de Rivoli", "5", "Paris"),
    ("FR", "12 rue de la Paix Paris", "rue de la Paix", "12", "Paris"),
    ("FR", "15 rue Sainte-Catherine Bordeaux", "rue Sainte-Catherine", "15", "Bordeaux"),
    ("FR", "5 rue de Paris", "rue de Paris", "5", ""),
])
def test_r3_17_53_town_glued_to_the_street(cc, text, street, number, city):
    _line(cc, text, street=street, number=number, city=city)


def test_r3_17_glued_town_verifies():
    a = parse_line("10 Downing Street London", CC["GB"])
    assert _decide(a, [_c("10", "Downing Street", "SW1A 2AA", "London")], authority="osm")["status"] == "verified"


# ---------------------------------------------------------------------------------- flats after the number

@pytest.mark.parametrize("cc, text, street, number, unit", [
    ("ES", "Carrer de Mallorca 401 1r 2a, 08013 Barcelona", "Carrer de Mallorca", "401", "1r 2a"),
    ("ES", "Carrer de Mallorca 401 2n 1a, 08013 Barcelona", "Carrer de Mallorca", "401", "2n 1a"),
    ("ES", "Calle Mayor 5 3º B, 28013 Madrid", "Calle Mayor", "5", "3o B"),
    ("HU", "Andrássy út 12. II. em. 3., 1061 Budapest", "Andrássy út", "12", "II. em. 3."),
    ("HU", "Andrássy út 12. fsz. 1., 1061 Budapest", "Andrássy út", "12", "fsz. 1."),
    ("HU", "Andrássy út 12 fsz. 1, 1061 Budapest", "Andrássy út", "12", "fsz. 1"),
    ("NL", "Eerste Jan Steenstraat 2-III, 1072 NB Amsterdam", "Eerste Jan Steenstraat", "2", "III"),
    ("FI", "Mannerheimintie 30 A, 00100 Helsinki", "Mannerheimintie", "30", "A"),
    ("FI", "Mannerheimintie 30 A 5, 00100 Helsinki", "Mannerheimintie", "30", "A 5"),
    ("FI", "Mannerheimintie 30a, 00100 Helsinki", "Mannerheimintie", "30a", ""),  # a house letter
    ("SE", "Storgatan 5 A, 11122 Stockholm", "Storgatan", "5A", ""),  # Swedish: the house letter
])
def test_r3_S06_flat_is_not_the_house_number(cc, text, street, number, unit):
    _line(cc, text, street=street, number=number, unit=unit)


@pytest.mark.parametrize("cc, text, record", [
    ("ES", "Carrer de Mallorca 401 1r 2a, 08013 Barcelona", _c("401", "Carrer de Mallorca", "08013", "Barcelona")),
    ("HU", "Andrássy út 12. II. em. 3., 1061 Budapest", _c("12", "Andrássy út", "1061", "Budapest")),
    ("FI", "Mannerheimintie 30 A, 00100 Helsinki", _c("30", "Mannerheimintie", "00100", "Helsinki")),
])
def test_r3_S06_flat_verifies_the_building(cc, text, record):
    assert _decide(parse_line(text, CC[cc]), [record])["status"] == "verified"


@pytest.mark.parametrize("text", ["1061 Budapest Andrássy út 12", "1061 Budapest, Andrássy út 12"])
def test_r3_S07_hungarian_line_postcode_town_street(text):
    _line("HU", text, street="Andrássy út", number="12", postcode="1061", city="Budapest")
