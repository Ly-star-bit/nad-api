"""Round-4 routing repairs: which pipeline a request goes to (app.main._country). Offline; the numbers are the final
audit's indexes (items_route.md)."""

import pytest
from fastapi import HTTPException

from app.intl import countries as C
from app.main import _country
from app.models import VerifyRequest
from batch_line import as_request


def route(**req) -> str:
    try:
        return _country(VerifyRequest(**as_request(req)))
    except HTTPException as e:
        assert e.status_code == 422
        return "422"


def line(text, **kw) -> dict:
    return {"address": text, **kw}


def cell(street, city, state, zip_, **kw) -> dict:
    return {"street": street, "city": city, "state": state, "zip": zip_, **kw}


# [1]: a unit part or a street without a USPS type word never makes a trailing AL/MD/DE/ME/MT a country
@pytest.mark.parametrize("text", [
    "1200 Pike Plaza, Dept 4500, Birmingham, AL", "1200 Pike Plaza, Dock 1234, Birmingham, AL",
    "1200 Pike Plaza, Dept 4500, Baltimore, MD", "1200 Pike Plaza, Dock 1234, Baltimore, MD",
    "12345 Coastal Broadway, Gate 12345, Lewes, DE", "12345 Coastal Broadway, Gate 12345, Portland, ME",
    "1200 Pike Plaza, Space 1234, Birmingham, AL", "1200 Pike Plaza, Pier 1234, Birmingham, AL",
    "1200 Pike Plaza, Office 1234, Birmingham, AL", "1200 Pike Plaza, Mailstop 1234, Birmingham, AL",
    "1200 Pike Plaza, Door 1234, Birmingham, AL", "100 Last Chance Gulch, Bay 1234, Helena, MT",
    "John Smith, 1234 Oak Loop, Birmingham, AL", "Acme Inc\n1234 Oak Loop\nBirmingham AL", "100 Main St, PMB 123, Helena, MT",
    "John Smith, 12345 Coastal Broadway, Lewes, DE", "John Smith, 12345 Coastal Broadway, Portland, ME",
    "Jane Doe, 12345 Coastal Plaza Apt 4, Lewes, DE", "1234 Oak Loop, Spc 12, Portland, ME",
    "1234 Broadway, PMB 123, Helena, MT", "100 Broadway, Spc 12, Billings, MT", "Bob Lee, 1200 Pike Plaza, Birmingham, AL",
    "John Smith, 1234 Broadway, Birmingham, AL", "Acme Inc, 2100 Broadway, Baltimore, MD",
    "John, 1234 Spring Hollow, Birmingham, AL", "Recv 2, 4400 Cove Pt, Annapolis, MD",
    "Via Garibaldi 1, 98122 Messina ME",  # no Montenegrin postcode (81xxx-85xxx): Messina, or Maine - not Montenegro
])
def test_r4_1_us_units_and_streets_stay_us(text):
    assert route(**line(text)) == "US"


@pytest.mark.parametrize("text, code", [
    ("Hauptstraße 5, 80331 München, DE", "DE"), ("Hauptstraße 5, 80331, München, DE", "DE"),
    ("Hauptstraße 5, 80331 München, Bayern, DE", "DE"), ("Max Mustermann, Hauptstraße 5, 80331 München, DE", "DE"),
    ("Hauptstraße 5\n80331 München\nDE", "DE"), ("Unter den Linden 77, Berlin 10117, DE", "DE"),
    ("Bulevar Svetog Petra Cetinjskog 1, 81000 Podgorica, ME", "ME"), ("Njegoševa 1, 85000 Bar, ME", "ME"),
    ("Republic Street 1, Valletta VLT 1117, MT", "MT"), ("Rruga e Durresit 10, 1001 Tirane, AL", "AL"),
    ("Bd. Stefan cel Mare 1, MD-2001 Chisinau, MD", "MD"),
])
def test_r4_1_the_country_code_after_its_own_postcode_still_counts(text, code):
    assert route(**line(text)) == code


# [22]: the joined batch row 'street, postcode, town, province, country': the written country, after its postcode
@pytest.mark.parametrize("text, code", [
    ("Via Roma 10, 20121, Milano, MI, IT", "IT"), ("Damrak 1, 1012 LG, Amsterdam, NH, NL", "NL"),
    ("Calle Larios 1, 29005, Málaga, MA, ES", "ES"), ("Via Roma 10, 20121 Milano, MI, IT", "IT"),
    ("Rue du Seyon 1, 2000, Neuchâtel, NE, CH", "CH"),
    # no postcode of that country, a ZIP of that state, or a US street: the US
    ("100 Main St, Boston, MA, IT", "US"), ("Acme, 02110, Boston, MA, IT", "US"), ("PO Box 12, 20121, Boston, MA, IT", "US"),
    ("1 Main St, 20121, Detroit, MI, IT", "US"), ("Via Roma 10, Boston, MA, IT", "US"), ("Damrak 1, Amsterdam, NH, NL", "US"),
])
def test_r4_22_named_country_after_a_province(text, code):
    assert route(**line(text)) == code


# [2]: a US town named like a country in the state cell, next to a US ZIP
@pytest.mark.parametrize("req, code", [
    (cell("1 Main St", "Springfield", "Holland", "49423"), "US"), (cell("1 Main St", "Springfield", "Greece", "14612"), "US"),
    (cell("1 Main St", "Springfield", "Poland", "44514"), "US"), (cell("1 Main St", "Springfield", "Wales", "04280"), "US"),
    (cell("100 Broadway", "", "Holland", "49423"), "US"), (cell("100 Broadway", "", "Poland", "04274"), "US"),
    (cell("100 Broadway", "", "Wales", "53183"), "US"), (cell("100 Broadway", "", "Greece", "14612"), "US"),
    (cell("100 Broadway", "Rochester", "Greece", "14612"), "US"), (cell("100 Broadway", "", "Sweden", "11151"), "US"),
    # a European street and that country's postcode: the country
    (cell("Damrak 1", "Amsterdam", "Holland", "1012 LG"), "NL"), (cell("ul. Marszałkowska 1", "Warszawa", "Poland", "00-001"), "PL"),
])
def test_r4_2_country_like_town_in_the_state_cell(req, code):
    assert route(**req) == code


# [3]: a Japanese place word in a US line with a phone number or a US street that is not written number-first
@pytest.mark.parametrize("text, code", [
    ("Tokyo Sushi, Main St, New York, Tel 555-1234", "US"), ("Nara Restaurant, Main Street Plaza, Tel 555-1234", "US"),
    ("Kyoto Kitchen, Ste 5 100 Main St, Springfield 555-1234", "US"), ("Osaka Grill, #5 100 Main St, Springfield, 555-1234", "US"),
    ("Fukuoka Ltd, 100 Avenue of the Americas, New York 555-1234", "US"), ("Tokyo Sushi, Main St, Ph: 555-1234", "US"),
    ("St. Luke's International Hospital, 9-1 Akashicho, Chuo City, Tokyo 104-8560", "JP"),
    ("Omotesando Ave, Shibuya, Tokyo 150-0001", "JP"), ("Shibuya Scramble Square, 2-24-12 Shibuya, Tokyo 150-6101", "JP"),
    ("Grand Hyatt Tokyo, 6-10-3 Roppongi, Tokyo 106-0032 Tel 03-4333-1234", "JP"),
])
def test_r4_3_japanese_word_and_phone_in_a_us_line(text, code):
    assert route(**line(text)) == code


# [4] / [5] / R41: Chinese and Taiwanese lines in cities that were not on the list
@pytest.mark.parametrize("text", [
    "南宁市青秀区东葛1", "南宁市青秀区东葛1 张三", "惠州市惠城区江北1 张三 13800138000", "石家庄市长安区1 张三", "泉州市丰泽区1 李四",
    "洛阳市西工区1 101", "汕头市金平区1 王五", "南昌市东湖区1 101室", "花蓮市中正1 王先生", "彰化市中山1 101", "中山市石岐区1",
    "海口市龙华区1", "宜蘭市中山1", "屏東市自由1", "西門町1", "台北西門町1", "贵阳市南明区花果1-2", "太原市小店区1-2",
    "加州洛杉矶市1-2", "张三 北京市海淀区中关村1",
])
def test_r4_4_chinese_lines_are_not_japan(text):
    assert route(**line(text)) in ("US", "422")


# [5]: without a default, a province code after its own postcode and a street of that country is not a third country
@pytest.mark.parametrize("text, code", [
    ("Via Roma 1, 50122 Firenze FI", "IT"), ("Via Roma 1, 03100 Frosinone FR", "IT"), ("Via Roma 1, 58100 Grosseto GR", "IT"),
    ("Via Sparano 1, 70121 Bari BA", "IT"), ("Via Roma 1, 88100 Catanzaro CZ", "IT"), ("Via Roma 1, 04100 Latina LT", "IT"),
    ("Calle Sierpes 1, 41004 Sevilla SE", "ES"), ("Pilatusstrasse 1, 6003 Luzern LU", "CH"),
    # the country itself
    ("Mannerheimintie 1, 00100 Helsinki FI", "FI"), ("Hallituskatu 1, 50100 Mikkeli FI", "FI"),
    ("10 Rue de Rivoli, 75001 Paris FR", "FR"), ("Ermou 10, 10563 Athens GR", "GR"), ("Ferhadija 1, 71000 Sarajevo BA", "BA"),
    ("Gedimino pr. 1, 01103 Vilnius LT", "LT"), ("1 Rue du Fort, L-1648 Luxembourg LU", "LU"),
    ("Rue Royale 1, 4900 Spa BE", "BE"), ("Drottninggatan 1, 111 51 Stockholm SE", "SE"),
])
def test_r4_5_province_code_without_a_default(text, code):
    assert route(**line(text)) == code


# R02 / R36: a Japanese town with a room or name after it, a postcode after a comma, a name first; Sapporo's 条
@pytest.mark.parametrize("text", [
    "道後湯之町5 101号室", "箱根町湯本256 田中様", "京都市左京区銀閣寺町2, 606-8402", "京都市左京区銀閣寺町2、606-8402",
    "山田太郎 京都市北区金閣寺町1", "山田太郎 京都市中京区上本能寺前町488", "中山 京都市北区金閣寺町1", "静岡市葵区西門町1",
    "札幌市中央区北1条西2", "札幌市中央区北1条西2-1", "道後湯之町5", "箱根町湯本256-1",
])
def test_r4_r02_r36_japanese_town_lines(text):
    assert route(**line(text)) == "JP"


def test_r4_province_postcode_table():
    assert C._province_postcode("IT", "FI", "50122 Firenze") and not C._province_postcode("IT", "FI", "00100 Helsinki")
    assert C._province_postcode("CH", "BE", "3011 Bern") and not C._province_postcode("CH", "BE", "1000 Bruxelles")
    assert C._province_postcode("IT", "MI", "20121 Milano")  # not in the table: no say


# lead follow-up: in a one-part line the number after the name is the house number, not a postcode
@pytest.mark.parametrize("text", [
    "John Smith 1234 Oak Loop Birmingham AL", "Acme 12345 Coastal Broadway Lewes DE", "Acme Inc 2100 Charles Center Baltimore MD",
    "Jane Doe 12345 Coastal Plaza Portland ME", "Bob Lee 1234 Last Chance Gulch Helena MT", "Jane Doe 4400 Cove Pt Annapolis MD",
    "John Smith 1234 Oak Loop Spc 12 Birmingham AL", "Acme Corp 12345 Coastal Broadway Gate 5 Lewes DE",
    "John Smith 2100 Monument Row Richmond VA", "Acme 1200 Pike Plaza Dept 4500 Birmingham AL",
    "John Smith 12345 Coastal Broadway Ste 100 Lewes DE", "Acme LLC 1234 Oak Loop Bangor ME",
])
def test_r4_one_part_name_first_us_lines_stay_us(text):
    assert route(**line(text)) == "US"


@pytest.mark.parametrize("text, code", [
    ("Hauptstraße 5 80331 München DE", "DE"), ("Hauptstraße 5 10115 Berlin DE", "DE"),
    ("Max Mustermann Hauptstraße 5 80331 München DE", "DE"), ("Bulevar Svetog Petra Cetinjskog 1 81000 Podgorica ME", "ME"),
    ("Rruga e Durresit 10 1001 Tirane AL", "AL"), ("Strada Puskin 1 MD-2001 Chisinau MD", "MD"),
    ("Triq ir-Repubblika 1 Valletta VLT 1117 MT", "MT"),
])
def test_r4_one_part_postcode_after_the_house_number(text, code):
    assert route(**line(text)) == code
