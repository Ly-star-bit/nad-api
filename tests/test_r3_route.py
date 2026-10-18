"""Round-3 routing repairs: which pipeline a request goes to (app.main._country). Offline; the numbers are the round-3
audit's regression indexes."""

import pytest
from fastapi import HTTPException

from app.intl import countries as C
from app.main import _country
from app.models import VerifyRequest


def route(**req) -> str:
    try:
        return _country(VerifyRequest(**req))
    except HTTPException as e:
        assert e.status_code == 422
        return "422"


def line(text, **kw) -> dict:
    return {"address": text, **kw}


# [1] / [50]: a trailing AL/DE/MD/ME/MT after a US street, a unit or a fitting ZIP is the state
@pytest.mark.parametrize("text", [
    "1000 N West St, Wilmington, 19801, DE", "36470 Seaside Outlet Dr, Rehoboth Beach, 19971, DE",
    "12345 Coastal Hwy, Lewes, 19958 DE", "100 Main St, Portland, 04101, ME", "1301 E 6th Ave, Ste 100, Helena, MT",
    "100 Main St, Box 123, Billings, MT", "John Smith, 1234 Main St, Birmingham, AL", "Acme Inc, 1234 Main St, Baltimore, MD",
    "John Smith\n12345 Coastal Hwy\nLewes DE", "1 Main St, Dover, 19901, DE", "123 Main St, 19801 Wilmington, DE",
    "1 Main St, Apt 5, 19901, DE", "Acme Inc, 1234 Main St, Annapolis, 21401, MD", "1234 Main St, Bangor, 04401, ME",
    "1234 Broadway, Birmingham, AL", "1234 Broadway Birmingham AL", "Acme, Billings Box 1234, MT", "Lewes 19958, DE",
    "ACME Corp., 1234 Commerce Blvd, Baltimore, MD",
])
def test_r3_1_us_state_codes_after_a_us_street_or_zip_stay_us(text):
    assert route(**line(text)) == "US"


@pytest.mark.parametrize("text, code", [
    ("Hauptstraße 5, 80331 München, DE", "DE"), ("Hauptstraße 5, 80331, München, DE", "DE"),
    ("Unter den Linden 77, 10117 Berlin, DE", "DE"), ("Hauptstraße 5 10115 Berlin DE", "DE"),
    ("Unter den Linden 77, Berlin 10117, DE", "DE"), ("Bulevar Svetog Petra Cetinjskog 1, 81000 Podgorica, ME", "ME"),
    ("Republic Street 1, Valletta VLT 1117, MT", "MT"), ("Republic Street 1, Valletta VLT 1117 MT", "MT"),
])
def test_r3_1_the_country_code_after_its_own_postcode(text, code):
    assert route(**line(text)) == code


# [2]: a state cell as ERPs export it, next to a US ZIP
@pytest.mark.parametrize("street, city, state, zip_", [
    ("1000 N West St", "Wilmington", "ＤＥ", "19801"), ("100 Main St", "Wilmington", "Delaware (DE)", "19801"),
    ("100 Main St", "Wilmington", "DE (Delaware)", "19801"), ("100 Main St", "Wilmington", "DE - Delaware", "19801"),
    ("100 Main St", "Baltimore", "MD (Maryland)", "21201"), ("100 Main St", "Baltimore", "ＭＤ", "21201"),
    ("100 Main St", "Baltimore", "M.D.", "21201"), ("100 Main St", "Portland", "Maine (ME)", "04101"),
    ("100 Main St", "Portland", "ＭＥ", "04101"), ("100 Main St", "Birmingham", "AL - Alabama", "35203"),
    ("100 Main St", "Billings", "ＭＴ", "59101"), ("100 Main St", "Richmond", "ＶＡ", "23219"),
    ("600 Dexter Ave", "Montgomery", "Ala.", "36104"), ("100 Main St", "Phoenix", "ＡＺ", "85007"),
    ("100 Main St", "Phoenix", "AZ (Arizona)", "85007"), ("100 Main St", "Dover", "Del.", "19901"),
    ("100 Main St", "Baltimore", "Md.", "21201"), ("100 Main St", "Phoenix", "DEU", "85007"),
    ("100 Main St", "Newark", "Jersey", "07102"),
])
def test_r3_2_erp_state_cells_with_a_us_zip_stay_us(street, city, state, zip_):
    assert route(street=street, city=city, state=state, zip=zip_) == "US"


def test_r3_2_state_cell_labels():
    assert [C.state_cell(v) for v in ("Delaware (DE)", "M.D.", "Ala.", "ＭＥ", "Germany (DE)", "ALA", "France")] == [
        "DE", "MD", "AL", "ME", "", "", ""]


# [3] / [48] / [64]: the country written out at the end beats a province + CAP that looks like a state + ZIP
@pytest.mark.parametrize("text, code", [
    ("Via Roma 10, Milano, MI 20121, Italy", "IT"), ("Via Roma 10, Milano MI 20121, Italy", "IT"),
    ("Via Roma 10, 20121 Milano, MI, Italy", "IT"), ("Via Maqueda 100, Palermo PA 90133, Italy", "IT"),
    ("Via Etnea 1, Catania CT 95124, Italy", "IT"), ("Via Roma 1, Cagliari CA 09124, Italy", "IT"),
    ("Via Roma 10, 95131 Catania, CT, Italy", "IT"), ("Via Roma 10, Trento TN 38122, Italy", "IT"),
    ("Via Roma 10, Milano, MI, Italy", "IT"), ("Rue du Seyon 1, 2000 Neuchâtel, NE, Switzerland", "CH"),
    ("Damrak 1, 1012 LG Amsterdam, NH, Holland", "NL"),
    # ... while a US line keeps its recipient or town
    ("100 Main St, Holland, MI 49423", "US"), ("1 Main St, Springfield, IL 62701, Holland", "US"),
    ("Springfield, IL 62701, Holland", "US"), ("100 Main St, Springfield, IL 62701, Jordan", "US"),
    ("1 Main St, Boston, MA, IT", "US"), ("Italia, 123 Main St, Springfield, IL", "US"),
])
def test_r3_3_country_written_out_after_a_province(text, code):
    assert route(**line(text)) == code


def test_r3_64_a_default_never_beats_usa():
    assert route(**line("100 Main St, Holland, MI 40000, USA", default_country="IT")) == "US"
    assert route(**line("Via Roma 10, Milano MI 20121, USA", default_country="IT")) == "US"


# [4] / [9] / [49] / [57] / [58] / [67]: a batch default and a trailing code that is also a US state
@pytest.mark.parametrize("req, code", [
    (line("Via Roma 10, Milano, MI, 20121"), "IT"), (line("Via Garibaldi 1, 98122 Messina ME"), "IT"),
    (line("Via Roma 10, Milano MI"), "IT"), (line("Via Roma 10, Milano, MI"), "IT"),
    (line("Via Roma 10, Trento, TN 38122"), "IT"), (line("Corso Palladio 1, Vicenza VI 36100"), "IT"),
    (line("Piazza del Popolo 1, Ascoli Piceno AP 63100"), "IT"), (line("Via Roma 10 20121 Milano MI"), "IT"),
    (line("Via Roma 1 22100 Como CO"), "IT"), (line("Via Maqueda 100 90133 Palermo PA"), "IT"),
    (line("Piazza della Signoria 5, 50122, Firenze, FI"), "IT"), (line("Via del Corso 10, 00186, Roma, RM"), "IT"),
    (line("Via Roma 1, 03100 Frosinone FR"), "IT"),
    ({"street": "Via Roma 10", "city": "Trento", "state": "TN", "zip": "38122"}, "IT"),
    ({"street": "Corso Palladio 1", "city": "Vicenza", "state": "VI", "zip": "36100"}, "IT"),
    # stays American: a US street, a PO box, a base, or a ZIP of that state with no European street
    (line("1 Woodward Ave, Detroit, MI 48226"), "US"), (line("Memphis, TN 38122"), "US"),
    ({"street": "PO Box 5", "city": "Memphis", "state": "TN", "zip": "38122"}, "US"),
    ({"street": "1000 Poplar Ave", "city": "Memphis", "state": "TN", "zip": "38122"}, "US"),
    (line("Unit 5, PSC 1234, APO AP 96204"), "US"), (line("100 Main St, Holland, MI 40000"), "US"),
    (line("Bahnhofstrasse 1, 8001 Zürich CH"), "CH"),  # CH is also Chieti, but 8001 is no Italian CAP
])
def test_r3_4_italian_batch(req, code):
    assert route(**req, default_country="IT") == code


@pytest.mark.parametrize("req, default, code", [
    ({"street": "Damrak 1", "city": "Amsterdam", "state": "NH", "zip": "1012 LG"}, "NL", "NL"),
    ({"street": "Oudegracht 1", "city": "Utrecht", "state": "UT", "zip": "3511 AA"}, "NL", "NL"),
    ({"street": "Stadhuisplein 1", "city": "Almere", "state": "FL", "zip": "1315 KA"}, "NL", "NL"),
    ({"street": "Rue du Seyon 1", "city": "Neuchâtel", "state": "NE", "zip": "2000"}, "CH", "CH"),
    ({"street": "Calle Larios 1", "city": "Málaga", "state": "MA", "zip": "29005"}, "ES", "ES"),
    ({"street": "Calle Gondomar 1", "city": "Córdoba", "state": "CO", "zip": "14001"}, "ES", "ES"),
    ({"street": "Unter den Linden 77", "city": "Berlin", "state": "DE"}, "DE", "DE"),
    (line("Calle Larios 1, Málaga MA 29005"), "ES", "ES"), (line("Unter den Linden 77, Berlin, DE"), "DE", "DE"),
    (line("Republic Street 1, Valletta, MT"), "MT", "MT"), (line("Rue du Seyon 1, Neuchâtel NE"), "CH", "CH"),
    (line("Hauptstraße 5 10115 Berlin DE"), "DE", "DE"), (line("Unter den Linden 77 10117 Berlin, DE"), "DE", "DE"),
    (line("Unter den Linden 77, Berlin, DE 10117"), "DE", "DE"),
    (line("Triq ir-Repubblika 1 Valletta VLT 1117 MT"), "MT", "MT"), (line("Rruga e Durresit 10, Tirane, AL"), "AL", "AL"),
    (line("Bulevardi Svetog Petra Cetinjskog 1, Podgorica, ME"), "ME", "ME"),
    (line("Bd. Stefan cel Mare 1, Chisinau, MD"), "MD", "MD"), (line("Bundesplatz 3, 3011, Bern, BE"), "CH", "CH"),
    (line("Bundesplatz 3, 3011 Bern BE"), "CH", "CH"),
    # a US line in a German batch stays American
    (line("123 Main St, Wilmington, DE"), "DE", "US"), (line("1000 N West St, Wilmington, DE 19801"), "DE", "US"),
    ({"street": "100 Main St", "city": "Wilmington", "state": "DE", "zip": "19801"}, "DE", "US"),
    ({"street": "Damrak 1", "city": "Amsterdam", "state": "NH", "zip": "03101"}, "NL", "US"),
    (line("Via Roma 1, Cagliari CA 09123"), "DE", "US"),
])
def test_r3_9_batch_default_and_its_own_codes(req, default, code):
    assert route(**req, default_country=default) == code


# [5] / [42]: Chinese and Taiwanese lines are not Japanese; Japanese lines with a room or name after them are
@pytest.mark.parametrize("text", [
    "北京市海淀区中关村1号", "北京市朝阳区三里屯1号", "上海市浦东新区陆家嘴1号", "广州市天河区珠江新城1号", "杭州市西湖区龙井村1号",
    "重庆市渝中区解放碑1号", "厦门市思明区鼓浪屿1号", "长沙市岳麓区1号", "台北市大安區忠孝東路四段1號", "新北市板橋區文化路一段1號",
    "台中市西屯區台灣大道三段1號", "台北市信義區信義路五段7號", "成都市武侯区人民南路四段1号", "纽约市皇后区法拉盛缅街41-60号",
    "广州市天河区体育西1号", "上海市浦东新区张江1-1-1", "广州市天河区体育西1-1", "台北市中山區1號", "纽约市皇后区法拉盛缅街41 号",
])
def test_r3_5_chinese_lines_are_not_japanese(text):
    assert route(**line(text)) == "US"


def test_r3_5_structured_chinese_row():
    assert route(street="中关村1号", city="北京市海淀区") == "US"


@pytest.mark.parametrize("text", [
    "京都市北区金閣寺町1 101号室", "京都市左京区吉田本町36 京都大学", "京都市北区金閣寺町1 603-8361", "京都市北区金閣寺町1 田中様",
    "京都市中京区上本能寺前町488 1203", "道後湯之町5", "箱根町湯本256", "千代田区九段南1-2-1", "福知山市長田段1-2-3",
    "上川郡美瑛町新区画12", "京都市北区金閣寺町1", "大阪市北区梅田3丁目1番1号",
    # [11] romaji with "St." / "Ave" that is not a US street
    "St. Luke's International Hospital, 9-1 Akashicho, Chuo City, Tokyo 104-8560", "Omotesando Ave, Shibuya, Tokyo 150-0001",
])
def test_r3_5_japanese_lines(text):
    assert route(**line(text)) == "JP"


@pytest.mark.parametrize("text", ["Sushi Nara, 123 Main St, Springfield, Tel 555-1234", "7 Tokyo Ave 123-4567",
                                  "150 Nara Rd 555-1234"])
def test_r3_11_us_streets_with_japanese_words_stay_us(text):
    assert route(**line(text)) == "US"


# [6] / [56]: 'postcode Town CC' in one part
@pytest.mark.parametrize("text, code", [
    ("10 Rue de Rivoli, 75001 Paris FR", "FR"), ("10 Rue de Rivoli, 75001 Paris FRA", "FR"),
    ("Unter den Linden 77, 10117 Berlin DEU", "DE"), ("10 Downing Street, London SW1A 2AA GB", "GB"),
    ("Damrak 1, 1012 LG Amsterdam NL", "NL"), ("Via Roma 1, 00184 Roma IT", "IT"), ("Calle Mayor 1, 28013 Madrid ES", "ES"),
    ("Stephansplatz 1, 1010 Wien AT", "AT"), ("Bahnhofstrasse 1, 8001 Zürich CH", "CH"),
    ("ul. Marszałkowska 99A, 00-693 Warszawa PL", "PL"), ("Laugavegur 1, 101 Reykjavík IS", "IS"),
    ("42 Place Guillaume II, L-1648 Luxembourg LU", "LU"), ("1 Avenue Princesse Grace, 98000 Monaco MC", "MC"),
    ("Grote Markt 1, 2000 Antwerpen BE", "BE"), ("Rådhuspladsen 1, 1550 København DK", "DK"),
    ("Karl Johans gate 22, 0026 Oslo NO", "NO"), ("Drottninggatan 1, 111 51 Stockholm SE", "SE"),
    ("Mannerheimintie 30, 00100 Helsinki FI", "FI"), ("Rua Augusta 1, 1100-048 Lisboa PT", "PT"),
    ("Ermou 10, 105 63 Athens GR", "GR"), ("Mariánské náměstí 2, 110 00 Praha CZ", "CZ"),
    ("1 Grafton Street, Dublin 2 D02 X285 IE", "IE"), ("Via Roma 10, 20121 Milano MI IT", "IT"),
])
def test_r3_6_code_after_postcode_and_town(text, code):
    assert route(**line(text)) == code


def test_r3_56_the_default_keeps_its_province_codes():
    assert route(**line("Via dei Calzaiuoli 7, 50122 Firenze FI", default_country="IT")) == "IT"
    assert route(**line("Kapellplatz 1, 6004 Luzern LU", default_country="CH")) == "CH"
    assert route(**line("10 Rue de Rivoli, 75001 Paris FR", default_country="DE")) == "FR"


# [7] / [60]: 'Street Town, Country' in two parts; the US-town rule needs a US street line before the name
@pytest.mark.parametrize("text, code", [
    ("10 Rue de Rivoli Paris, France", "FR"), ("Damrak 1 Amsterdam, Holland", "NL"), ("Ermou 10 Athens, Greece", "GR"),
    ("Bahnhofstrasse 1 Zürich, Switzerland", "CH"), ("ul. Marszałkowska 99A Warszawa, Poland", "PL"),
    ("Drottninggatan 1 Stockholm, Sweden", "SE"), ("Mannerheimintie 30 Helsinki, Finland", "FI"),
    ("Karl Johans gate 22 Oslo, Norway", "NO"), ("Grote Markt 1 Antwerpen, Belgium", "BE"),
    ("Republic Street 1 Valletta, Malta", "MT"), ("Makedonija 1 Skopje, Macedonia", "MK"),
    ("350 Bay Street Toronto ON M5H 2S6, Canada", "422"), ("Rua Augusta 1, Brazil", "422"),
    ("270 S River Ave, Holland", "US"), ("300 N Coal St, Mexico", "US"), ("100 Main St, Brazil", "US"),
    ("123 Main St Apt 4, Holland", "US"),
])
def test_r3_7_street_town_country(text, code):
    assert route(**line(text)) == code


# [8]: the country in the state cell or after the city, with a 5-digit postcode
@pytest.mark.parametrize("req, code", [
    ({"street": "10 Rue de Rivoli", "city": "Paris", "state": "France", "zip": "75001"}, "FR"),
    ({"street": "Via Roma 10", "city": "Milano", "state": "Italy", "zip": "20121"}, "IT"),
    ({"street": "Mannerheimintie 1", "city": "Helsinki", "state": "Finland", "zip": "00100"}, "FI"),
    ({"street": "Drottninggatan 1", "city": "Stockholm", "state": "Sweden", "zip": "11151"}, "SE"),
    ({"street": "Ermou 10", "city": "Athens", "state": "Greece", "zip": "10563"}, "GR"),
    ({"street": "10 Rue de Rivoli", "city": "Paris, France", "zip": "75001"}, "FR"),
    ({"street": "Via Roma 10", "city": "Milano, Italy", "zip": "20121"}, "IT"),
    ({"street": "Via Roma 10", "city": "20121 Milano MI, Italy"}, "IT"),
])
def test_r3_8_country_in_a_structured_cell(req, code):
    assert route(**req) == code


# [10] / [50]: a US town named like a country, then its ZIP
@pytest.mark.parametrize("text", ["1 Main St, Greece, 14612", "1 Main St, Italy, 76651", "1 Main St, Sweden, 04040",
                                  "1 Main St, Finland, 55603", "1 Main St, Greece, 14616"])
def test_r3_10_us_town_named_like_a_country_then_zip(text):
    assert route(**line(text)) == "US"


# [59]: an unsupported default only matters when it is used
def test_r3_59_unsupported_default_only_when_used():
    assert route(**line("Unter den Linden 77, 10117 Berlin, Germany", default_country="CN")) == "DE"
    assert route(**line("Unter den Linden 77, Berlin", default_country="CN")) == "422"


# S03: a Chinese country name first, before a line that ends in a province code
@pytest.mark.parametrize("text", ["意大利 Via Roma 10, 20121 Milano MI", "意大利 米兰 Via Roma 10, 20121 Milano MI"])
def test_r3_s03_chinese_country_name_first(text):
    assert route(**line(text)) == "IT"


# S02: Jersey / Guernsey / Isle of Man written as the UK, by name
@pytest.mark.parametrize("req, code", [
    ({"address": "1 Broad Street, St Helier, Jersey", "country": "GB"}, "JE"),
    ({"address": "1 Smith Street, St Peter Port, Guernsey", "country": "UK"}, "GG"),
    ({"address": "1 Prospect Hill, Douglas, Isle of Man", "country": "GB"}, "IM"),
    ({"address": "1 Broad Street, St Helier, Channel Islands JE2 3RR", "country": "GB"}, "JE"),
    ({"address": "1 Jersey Road, London", "country": "GB"}, "GB"),
])
def test_r3_s02_crown_dependencies_by_name(req, code):
    assert route(**req) == code


# lead: 'Città del Vaticano' is a city-state name like 'Monaco' - the town as well as the country
def test_r3_vatican_city_is_the_town():
    from app.intl.parse import parse_line

    segs, removed = C.strip_country(["Mario Rossi", "Via della Posta 1", "Città del Vaticano"], "VA")
    assert segs == ["Mario Rossi", "Via della Posta 1"] and C.city_state(removed) == "Città del Vaticano"
    assert parse_line("Via della Posta 1, Città del Vaticano", C.COUNTRIES["VA"]).city == "Città del Vaticano"
    assert parse_line("Via della Posta 1, 00120 Città del Vaticano", C.COUNTRIES["VA"]).city == "Città del Vaticano"
