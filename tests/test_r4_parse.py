"""Round-4 parse repairs: each test is named after the final audit's item it pins. Offline: verdicts use canned
provider records."""

import pytest

from app.intl import countries as C
from app.intl.match import decide
from app.intl.parse import parse_line


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


# ------------------------------------------------------------- [6] city-states: the commune before the street

@pytest.mark.parametrize("cc, text, city, street", [
    ("LU", "Bertrange, 100 route d'Arlon, Luxembourg", "Bertrange", "route d'Arlon"),
    ("LU", "Mamer, 5 route d'Arlon, Luxembourg", "Mamer", "route d'Arlon"),
    ("LU", "Esch-sur-Alzette, 5 rue de la Gare, Luxembourg", "Esch-sur-Alzette", "rue de la Gare"),
    ("LU", "Differdange, 5 rue de la Gare, Luxembourg", "Differdange", "rue de la Gare"),
    ("LU", "Strassen, 5 route d'Arlon, Luxembourg", "Strassen", "route d'Arlon"),
    ("LU", "Strassen, 150 route d'Arlon, Luxembourg", "Strassen", "route d'Arlon"),
    ("LU", "Ettelbruck, 5 Grand-Rue, Luxembourg", "Ettelbruck", "Grand-Rue"),
    ("SM", "Serravalle, Via Ranco 5, San Marino", "Serravalle", "Via Ranco"),
    ("SM", "Borgo Maggiore, Via 28 Luglio 5, San Marino", "Borgo Maggiore", "Via 28 Luglio"),
    ("SM", "Borgo Maggiore, Via 28 Luglio 5", "Borgo Maggiore", "Via 28 Luglio"),
    ("AD", "Escaldes-Engordany, Avinguda Carlemany 10, Andorra", "Escaldes-Engordany", "Avinguda Carlemany"),
    ("LU", "Bertrange, 100 route d'Arlon", "Bertrange", "route d'Arlon"),  # unchanged without the country
])
def test_r4_6_city_state_commune_before_the_street_is_the_town(cc, text, city, street):
    _line(cc, text, city=city, street=street, extra=[])


@pytest.mark.parametrize("cc, text, city, name", [
    # a recipient is still not the town, and the capital stays ([12] of round 3)
    ("LU", "Marc Weber, 23 rue du Marché-aux-Herbes, Luxembourg", "Luxembourg", "Marc Weber"),
    ("LU", "Marc, 23 rue du Marché-aux-Herbes, Luxembourg", "Luxembourg", "Marc"),
    ("LU", "Jean Muller, 5 rue de la Gare, Luxembourg", "Luxembourg", "Jean Muller"),
    ("LU", "Société Générale, 11 avenue Emile Reuter, Luxembourg", "Luxembourg", "Société Générale"),
    ("LU", "Hôtel Le Royal, 12 boulevard Royal, Luxembourg", "Luxembourg", "Hôtel Le Royal"),
    ("LU", "Kirchberg, 42 avenue JF Kennedy, Luxembourg", "Luxembourg", "Kirchberg"),  # a quarter of the capital
    ("MC", "Jean Martin, 2 Boulevard des Moulins, Monaco", "Monaco", "Jean Martin"),
    ("MC", "Café de Paris, Place du Casino, Monaco", "Monaco", "Café de Paris"),
    ("SM", "Mario Rossi, Via Giacomo Matteotti 5, San Marino", "San Marino", "Mario Rossi"),
    ("SM", "Hotel Titano, Contrada del Collegio 31, San Marino", "San Marino", "Hotel Titano"),
    ("AD", "Joan Puig, Avinguda Meritxell 10, Andorra", "Andorra", "Joan Puig"),
])
def test_r4_6_city_state_recipient_before_the_street_is_not_the_town(cc, text, city, name):
    a = _line(cc, text, city=city)
    assert name in a.extra


def test_r4_6_city_state_commune_is_not_verified_in_the_capital():
    # 'not used for matching: Bertrange' and verified 0.97 at '100, Route d'Arlon 1150 Luxembourg' (another commune)
    a = parse_line("Bertrange, 100 route d'Arlon, Luxembourg", CC["LU"])
    assert _decide(a, [_c("100", "Route d'Arlon", "1150", "Luxembourg")])["status"] != "verified"
    a = parse_line("Mamer, 5 route d'Arlon, Luxembourg", CC["LU"])
    assert _decide(a, [_c("5", "Route d'Arlon", "8211", "Mamer")])["status"] == "verified"
    a = parse_line("Marc Weber, 23 rue du Marché-aux-Herbes, Luxembourg", CC["LU"])
    assert _decide(a, [_c("23", "Rue du Marché-aux-Herbes", "1728", "Luxembourg")])["status"] == "verified"


# --------------------------------------------------------- [7] a town of several words before the street

@pytest.mark.parametrize("cc, text, city, street", [
    ("ES", "Alcalá de Henares, Calle Mayor 5", "Alcalá de Henares", "Calle Mayor"),
    ("CZ", "Ústí nad Labem, Mírové náměstí 1", "Ústí nad Labem", "Mírové náměstí"),
    ("NL", "Bergen op Zoom, Grote Markt 1", "Bergen op Zoom", "Grote Markt"),
    ("AT", "Wiener Neustadt, Hauptplatz 1", "Wiener Neustadt", "Hauptplatz"),
    ("DE", "Mülheim an der Ruhr, Schloßstraße 28", "Mülheim an der Ruhr", "Schloßstraße"),
    ("DE", "Bad Homburg, Louisenstraße 1", "Bad Homburg", "Louisenstraße"),
    ("AT", "Sankt Pölten, Rathausplatz 1", "Sankt Pölten", "Rathausplatz"),
    ("FR", "La Rochelle, 5 rue du Palais", "La Rochelle", "rue du Palais"),
    ("BE", "La Louvière, Rue Albert 1er 10", "La Louvière", "Rue Albert 1er"),
    ("PL", "Nowy Sącz, ul. Długa 5", "Nowy Sącz", "ul. Długa"),
    ("IT", "Sesto San Giovanni, Via Roma 10", "Sesto San Giovanni", "Via Roma"),
    ("ES", "San Sebastián de los Reyes, Calle Real 1", "San Sebastián de los Reyes", "Calle Real"),
    ("DE", "Weil am Rhein, Hauptstraße 1", "Weil am Rhein", "Hauptstraße"),
    ("AT", "Sankt Johann im Pongau, Hauptstraße 1", "Sankt Johann im Pongau", "Hauptstraße"),
    ("CH", "La Chaux-de-Fonds, Avenue Léopold-Robert 1", "La Chaux-de-Fonds", "Avenue Léopold-Robert"),
    ("CZ", "Nové Město nad Metují, Náměstí 1", "Nové Město nad Metují", "Náměstí"),
    ("GB", "Stratford upon Avon, 1 High Street", "Stratford upon Avon", "High Street"),
    ("GB", "St Albans, 10 High Street", "St Albans", "High Street"),
    ("FR", "Saint Etienne, 5 rue de la République", "Saint Etienne", "rue de la République"),
])
def test_r4_7_town_of_several_words_before_the_street_is_the_town(cc, text, city, street):
    _line(cc, text, city=city, street=street, extra=[])


@pytest.mark.parametrize("cc, text", [
    # people and companies: no joiner, a person's particle, 'de' where people carry it, initials, a business word
    ("NL", "Jan Jansen, Damrak 1"), ("DE", "Max Mustermann, Hauptstraße 12"), ("FR", "Société Dupont, 5 rue de Rivoli"),
    ("DE", "Familie Müller, Hauptstraße 12"), ("DE", "Bäckerei Müller, Hauptstraße 12"),
    ("NL", "Jan de Vries, Damrak 1"), ("NL", "dhr. J. de Vries, Damrak 1"), ("NL", "Jan van der Berg, Damrak 1"),
    ("DE", "Ursula von der Leyen, Hauptstraße 12"), ("DE", "Max Rosenberg, Hauptstraße 1"),
    ("DE", "Peter Neuhausen, Hauptstraße 1"), ("DE", "Bäckerei Neustadt, Hauptstraße 1"),
    ("FR", "Charles de Gaulle, 5 rue de Rivoli"), ("FR", "Le Goff, 5 rue de Rivoli"),
    ("FR", "Société de Transport, 5 rue de Rivoli"), ("FR", "Café de Paris, 5 rue de Rivoli"),
    ("FR", "Hôtel de Ville, 5 rue de Rivoli"), ("FR", "Mairie de Lyon, 5 rue de la République"),
    ("ES", "María de la Cruz, Calle Mayor 5"), ("ES", "Ayuntamiento de Madrid, Calle Mayor 5"),
    ("PT", "Maria de Souza, Rua Augusta 100"), ("PT", "João da Silva, Rua Augusta 100"),
    ("IT", "Comune di Milano, Via Roma 10"), ("IT", "Marco di Stefano, Via Roma 10"), ("IT", "Luigi Di Maio, Via Roma 10"),
    ("IT", "Residenza Le Rose, Via Roma 10"), ("IT", "Piazza del Duomo, Via Roma 10"),
    ("GB", "Box Cottage, 1 High Street"), ("CZ", "Jan Novák, Mírové náměstí 1"),
    ("ES", "J. de la Cruz, Calle Mayor 5"), ("GB", "St John Ambulance, 10 High Street"),
])
def test_r4_7_recipient_before_the_street_is_still_not_the_town(cc, text):
    a = _line(cc, text, city="")
    assert text.split(",")[0] in a.extra


def test_r4_7_town_before_the_street_verifies_there():
    a = parse_line("Alcalá de Henares, Calle Mayor 5", CC["ES"])
    assert _decide(a, [_c("5", "CALLE MAYOR", "28801", "Alcalá de Henares")])["status"] == "verified"
    a = parse_line("Bergen op Zoom, Grote Markt 1", CC["NL"])
    assert _decide(a, [_c("1", "Grote Markt", "4611NR", "Bergen op Zoom")])["status"] == "verified"


# ---------------------------------------------- [8] GB/IE: a floor after the street; 'St' starting the town

@pytest.mark.parametrize("cc, text, street, unit, city", [
    ("GB", "10 Park Road Ground Floor", "Park Road", "Ground Floor", ""),
    ("GB", "10 Park Road Basement", "Park Road", "Basement", ""),
    ("GB", "10 Park Road Rear", "Park Road", "Rear", ""),
    ("GB", "10 Park Road Top Flat", "Park Road", "Top Flat", ""),
    ("GB", "10 Park Road First Floor Flat", "Park Road", "First Floor Flat", ""),
    ("GB", "10 Park Road Rear Building", "Park Road", "Rear Building", ""),
    ("IE", "12 Main Street Ground Floor", "Main Street", "Ground Floor", ""),
    ("GB", "10 Park Road Ground Floor London", "Park Road", "Ground Floor", "London"),
    ("GB", "10 Church Street Rear of Shop", "Church Street", "Rear of Shop", ""),
    ("GB", "10 High Street St Albans", "High Street", "", "St Albans"),
    ("GB", "5 Fore Street St Ives", "Fore Street", "", "St Ives"),
    ("GB", "5 Church Street St Helens", "Church Street", "", "St Helens"),
    ("GB", "10 Market Street St Andrews", "Market Street", "", "St Andrews"),
    ("GB", "10 High Street St Albans AL3 4EL", "High Street", "", "St Albans"),
    # unchanged: 'St' as the street's own type word, a street word after the floor-like word
    ("GB", "10 Downing St London", "Downing St", "", "London"),
    ("GB", "10 St Johns Road London", "St Johns Road", "", "London"),
    ("GB", "10 Back Lane Leeds", "Back Lane", "", "Leeds"),
    ("GB", "10 Topsham Road Exeter", "Topsham Road", "", "Exeter"),
])
def test_r4_8_floor_is_not_the_town_and_st_starts_it(cc, text, street, unit, city):
    _line(cc, text, street=street, unit=unit, city=city)


# ------------------------------------------------------------- [9] CH/LI: a company 'X AG' after the street

@pytest.mark.parametrize("cc, text", [
    ("CH", "Bahnhofstrasse 1, Treuhand AG"), ("CH", "Bahnhofstrasse 1, Muster AG"), ("CH", "Bahnhofstrasse 1, Zug AG"),
    ("LI", "Städtle 10, Muster AG"),
])
def test_r4_9_company_ag_after_the_street_is_not_the_town(cc, text):
    a = _line(cc, text, city="")
    assert text.split(", ")[1] in a.extra


@pytest.mark.parametrize("text, city", [
    ("Badstrasse 10, Baden AG", "Baden AG"), ("Hauptstrasse 10, Buchs AG", "Buchs AG"),
    ("Zentralstrasse 12, Wohlen AG", "Wohlen AG"), ("Kirchgasse 3, Brugg AG", "Brugg AG"),
])
def test_r4_9_aargau_town_with_its_canton_is_still_the_town(text, city):
    _line("CH", text, city=city)


# -------------------------------------------------------------- [11] structured 'Town, County' city column

def test_r4_11_structured_town_comma_county_has_no_trailing_comma():
    a = _cols(CC["GB"], "10 Market Place", "Kendal, Cumbria", "LA9 4TN")
    assert a.city == "Kendal" and "Cumbria" in a.extra


# ------------------------------------------------------ S07 the district number with the town after the postcode

@pytest.mark.parametrize("cc, text, city, street, number", [
    ("CZ", "110 00 Praha 1 Na Příkopě 15", "Praha 1", "Na Příkopě", "15"),
    ("CZ", "11000 Praha 1 Na Příkopě 15", "Praha 1", "Na Příkopě", "15"),
    ("CZ", "110 00 Praha Na Příkopě 15", "Praha", "Na Příkopě", "15"),
    ("HU", "1061 Budapest Andrássy út 12", "Budapest", "Andrássy út", "12"),
    ("FR", "75001 Paris 5 rue de Rivoli", "Paris", "rue de Rivoli", "5"),  # the number is the house's, not a district
    # a street named after a date is not a district
    ("CZ", "110 00 Praha 17. listopadu 2", "Praha", "17 listopadu", "2"),
    ("CZ", "110 00 Praha 28. října 5", "Praha", "28 října", "5"),
    ("CZ", "140 00 Praha 5. května 10", "Praha", "5 května", "10"),
    ("SK", "811 06 Bratislava 29. augusta 10", "Bratislava", "29 augusta", "10"),
])
def test_r4_s07_district_number_goes_with_the_town(cc, text, city, street, number):
    _line(cc, text, city=city, street=street, number=number)
