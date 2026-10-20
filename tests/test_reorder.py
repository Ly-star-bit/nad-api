"""Addresses written without commas in another order than the usual one."""

import pytest

from app.address import parse_oneline
from app.intl import japan
from app.intl.countries import COUNTRIES
from app.intl.parse import parse_line


def us(text):
    pa = parse_oneline(text)
    line1 = " ".join(p for p in (pa.house.raw if pa.house else "", pa.street_text) if p)
    return pa.state, pa.city, pa.zip5, line1, pa.unit_raw


@pytest.mark.parametrize("text, unit", [
    ("980 9th St Sacramento CA 95814 Ste 1750", "Ste 1750"),  # the unit after the ZIP
    ("CA 95814 Sacramento 980 9th St", ""),                   # state and ZIP first
    ("CA 95814 Sacramento 980 9th St Ste 1750", "Ste 1750"),
    ("Sacramento 980 9th St CA 95814", ""),                   # the city before the house number
    ("Sacramento 980 9th St Ste 1750 CA 95814", "Ste 1750"),
    ("980 9th St CA 95814 Sacramento", ""),                   # the city after the ZIP
])
def test_us_line_without_commas_in_another_order(text, unit):
    assert us(text) == ("CA", "Sacramento", "95814", "980 9th St", unit)
    assert any(w.startswith("address parts were out of order") for w in parse_oneline(text).warnings)


def test_us_city_of_several_words_before_the_number():
    assert us("North Miami 100 Main St FL 33161") == ("FL", "North Miami", "33161", "100 Main St", "")


@pytest.mark.parametrize("text", [
    "1600 Pennsylvania Ave NW Washington DC 20500",
    "28869 FANNING DR NW MADISON AL 35756",
    "100 Main St Springfield IL 62701",
])
def test_us_line_in_order_is_read_as_before(text):
    assert not any("out of order" in w for w in parse_oneline(text).warnings)


@pytest.mark.parametrize("text, city", [("PO Box 12 Springfield IL 62701", ""),
                                        ("Apt 5 100 Main St Springfield IL 62701", "Springfield")])
def test_us_words_before_the_number_that_are_no_city(text, city):
    assert parse_oneline(text).city == city


def eu(text, cc):
    a = parse_line(text, COUNTRIES[cc])
    return a.city, a.postcode, a.street, a.number, a.unit


@pytest.mark.parametrize("text, cc, want", [
    ("Berlin 10117 Unter den Linden 77", "DE", ("Berlin", "10117", "Unter den Linden", "77", "")),
    ("München 80331 Marienplatz 8", "DE", ("München", "80331", "Marienplatz", "8", "")),
    ("Wien 1010 Rathausplatz 1", "AT", ("Wien", "1010", "Rathausplatz", "1", "")),
    ("Paris 75002 12 rue de la Paix", "FR", ("Paris", "75002", "rue de la Paix", "12", "")),
])
def test_europe_town_and_postcode_before_the_street(text, cc, want):
    assert eu(text, cc) == want


@pytest.mark.parametrize("text, cc", [("Max Mustermann 10117 Unter den Linden 77", "DE"), ("Jan Jansen 1012 LG Damrak 8", "NL")])
def test_europe_name_before_the_postcode_is_no_town(text, cc):
    assert eu(text, cc)[0] != text.split(" 1")[0]


def test_europe_town_of_several_words_before_the_postcode():
    assert eu("Frankfurt am Main 60311 Zeil 1", "DE")[:2] == ("Frankfurt am Main", "60311")
    assert eu("Bad Homburg 61348 Louisenstraße 1", "DE")[:2] == ("Bad Homburg", "61348")


@pytest.mark.parametrize("text, cc, want", [
    ("Unter den Linden 77 10117 Berlin Wohnung 5", "DE", ("Berlin", "10117", "Unter den Linden", "77", "Wohnung 5")),
    ("Unter den Linden 77, 10117 Berlin Wohnung 5", "DE", ("Berlin", "10117", "Unter den Linden", "77", "Wohnung 5")),
    ("Damrak 8 1012 LG Amsterdam 3 hoog", "NL", ("Amsterdam", "1012 LG", "Damrak", "8", "3 hoog")),
    ("12 rue de la Paix 75002 Paris Bât A", "FR", ("Paris", "75002", "rue de la Paix", "12", "Bât A")),
])
def test_europe_flat_after_the_town(text, cc, want):
    assert eu(text, cc) == want


@pytest.mark.parametrize("text, prefix, rest", [
    ("サンハイツ101 東京都世田谷区世田谷4-21-27", "東京都世田谷区世田谷", "サンハイツ101"),
    ("サンハイツ 東京都世田谷区世田谷4-21-27 101号室", "東京都世田谷区世田谷", "サンハイツ 101号室"),
    ("サンハイツ101 世田谷区世田谷4-21-27", "世田谷区世田谷", "サンハイツ101"),  # no prefecture
    ("101号室 大阪市北区梅田3-1-1", "大阪市北区梅田", "101号室"),
    ("5F 大阪府大阪市北区梅田3-1-1", "大阪府大阪市北区梅田", "5F"),
])
def test_japan_building_before_the_address_is_kept(text, prefix, rest):
    a = japan.parse_jp(text)
    assert (a.prefix, a.rest, a.read_as) == (prefix, rest, "")


@pytest.mark.parametrize("text, name", [("山田太郎様 東京都世田谷区世田谷4-21-27", "山田太郎様"),
                                        ("株式会社ABC 東京都世田谷区世田谷4-21-27", "株式会社ABC"),
                                        ("12345 東京都世田谷区世田谷4-21-27", "12345"),  # an order number
                                        ("注文123456 東京都世田谷区世田谷4-21-27", "注文123456"),
                                        ("No.3 東京都世田谷区世田谷4-21-27", "No.3")])
def test_japan_name_before_the_address_is_still_dropped(text, name):
    a = japan.parse_jp(text)
    assert (a.rest, a.read_as) == ("", name)


def test_japan_town_with_its_numbers_first_is_not_a_building():
    a = japan.parse_jp("西新宿2-8-1, 新宿区")
    assert (a.prefix, a.nums, a.rest) == ("西新宿", [2, 8, 1], "")
