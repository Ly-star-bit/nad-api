"""`parsed` carries country / state / city / zip / address1 (number + street) / address2 (unit) for every country."""

import asyncio
from pathlib import Path

from app.intl import Ctx, core, japan
from app.intl.base import Lookup
from app.intl.countries import COUNTRIES
from app.intl.parse import parse_line
from app.upstream import nad_record
from app.verify import record_split
from test_verify import FakeUpstream, nad, run

SIX = ["country", "state", "city", "zip", "address1", "address2"]


def six(parsed):
    return [parsed[k] for k in SIX]


def test_us_line_is_split_into_the_six_fields():
    r = run(FakeUpstream(street=[nad(1700)]), "1716 W Washington St Apt 2, Phoenix, AZ 85007")  # not in NAD
    assert r["status"] == "street_only"
    assert six(r["parsed"]) == ["US", "AZ", "Phoenix", "85007", "1716 W Washington St", "Apt 2"]
    assert r["parsed"]["house_number"] == "1716" and r["parsed"]["street"] == "W Washington St"


def test_verified_address_is_split_as_the_record_has_it():
    r = run(FakeUpstream(street=[nad(1700)]), "1700 W Washington St, Phoen, AZ 85007")
    assert r["status"] == "verified" and r["input"] == "1700 W Washington St, Phoen, AZ 85007"
    assert six(r["parsed"]) == ["US", "AZ", "Phoenix", "85007", "1700 West Washington Street", None]
    assert r["parsed"]["street"] == "West Washington Street"


def test_verified_building_keeps_the_unit_written():
    # the building is in NAD, the unit isn't recorded: the unit stays as written
    r = run(FakeUpstream(street=[nad(1700)]), "1700 W Washington St Apt 2, Phoenix, AZ 85007")
    assert six(r["parsed"])[4:] == ["1700 West Washington Street", "Apt 2"]
    # a unit matched in NAD is the record's
    rec = nad_record({"Add_Number": 1700, "StNam_Full": "West Washington Street", "Unit": "Apt 2", "Post_City": "Phoenix",
                      "State": "AZ", "Zip_Code": "85007"})
    assert record_split(rec, "unit", {"address2": "#2"})["address2"] == "Apt 2"

def _europe(text, cc):
    a = parse_line(text, COUNTRIES[cc])
    return asyncio.run(core._europe(Ctx(FakeUpstream(), None), a, (), 5))["parsed"]  # no sources: only the reading


def test_europe_keeps_the_countrys_order_of_number_and_street():
    assert six(_europe("Unter den Linden 77, 10117 Berlin", "DE")) == [
        "DE", None, "Berlin", "10117", "Unter den Linden 77", None]
    assert six(_europe("8 Boulevard du Port, 80000 Amiens", "FR")) == [
        "FR", None, "Amiens", "80000", "8 Boulevard du Port", None]


def _table(*cities):
    t = japan.JapanPost(None, Path("."))
    t.by_pc = {f"{i:07d}": [japan.PostRow(f"{i:07d}", pref, city, "")] for i, (pref, city) in enumerate(cities)}
    return t


JP = _table(("東京都", "世田谷区"), ("大阪府", "大阪市中央区"), ("大阪府", "大阪市北区"), ("東京都", "中央区"),
            ("長野県", "北佐久郡軽井沢町"), ("三重県", "四日市市"), ("東京都", "府中市"), ("広島県", "府中市"))


def _jp(text, jp=JP):
    a = japan.parse_jp(text)
    return japan.split_place(jp, a), a


def test_japan_prefecture_and_city_come_off_the_place():
    (pref, city, town), a = _jp("〒154-0017 東京都世田谷区世田谷4-21-27 サンハイツ101")
    assert (pref, city, town, a.rest) == ("東京都", "世田谷区", "世田谷", "サンハイツ101")


def test_japan_city_names_with_city_words_inside():
    assert _jp("三重県四日市市諏訪町1-5")[0] == ("三重県", "四日市市", "諏訪町")
    assert _jp("大阪府大阪市中央区本町3-1-1")[0] == ("大阪府", "大阪市中央区", "本町")
    assert _jp("大阪府大阪市本町3-1-1")[0] == ("大阪府", "大阪市", "本町")  # the ward left out
    assert _jp("長野県軽井沢町軽井沢1-1")[0] == ("長野県", "軽井沢町", "軽井沢")  # the 郡 left out


def test_japan_city_without_prefecture_only_when_one_prefecture_has_it():
    assert _jp("世田谷区世田谷4-21-27")[0] == ("", "世田谷区", "世田谷")
    assert _jp("府中市宮町1-1")[0] == ("", "", "府中市宮町")  # 東京都 and 広島県 both have one


def test_japan_without_the_table_keeps_the_city_with_the_town():
    assert _jp("東京都世田谷区世田谷4-21-27", None)[0] == ("東京都", "", "世田谷区世田谷")


def test_japan_place_written_after_the_numbers():
    assert _jp("本町1-1-1, 中央区, 大阪府")[0] == ("大阪府", "中央区", "本町")


def test_verified_european_address_is_split_as_the_record_has_it(monkeypatch):
    rec = {"kind": "address", "number": "77", "street": "Unter den Linden", "street_alt": [], "postcode": "10117",
           "city": "Berlin", "unit": None, "formatted": "Unter den Linden 77, 10117 Berlin", "lat": 52.5, "lon": 13.4}

    async def provider(ctx, a):
        return Lookup(cands=[rec])
    monkeypatch.setitem(core.PROVIDERS, "overture", provider)
    out = asyncio.run(core.verify_intl(Ctx(FakeUpstream(), object()), None, "DE", address="unter den linden 77, 10117 berlin"))
    assert out["status"] == "verified"
    assert six(out["parsed"]) == ["DE", None, "Berlin", "10117", "Unter den Linden 77", None]


def test_european_record_town_only_when_it_is_the_town_written(monkeypatch):
    # Overture names Berlin's district; the record has no house letter and no space in the postcode
    rec = {"kind": "address", "number": "77", "street": "Unter den Linden", "street_alt": [], "postcode": "10117",
           "city": "Mitte", "unit": None, "formatted": "Unter den Linden 77, 10117 Mitte", "lat": 52.5, "lon": 13.4,
           "area_city": "Berlin"}

    async def provider(ctx, a):
        return Lookup(cands=[rec])
    monkeypatch.setitem(core.PROVIDERS, "overture", provider)
    out = asyncio.run(core.verify_intl(Ctx(FakeUpstream(), object()), None, "DE", address="unter den linden 77, 10117 Berlin"))
    assert out["status"] == "verified"
    assert six(out["parsed"]) == ["DE", None, "Berlin", "10117", "Unter den Linden 77", None]


def test_european_mistyped_postcode_is_the_records(monkeypatch):
    rec = {"kind": "address", "number": "9", "street": "náměstí Míru", "street_alt": [], "postcode": "12000",
           "city": "Praha 2", "unit": None, "formatted": "náměstí Míru 820/9, Vinohrady, 12000 Praha 2", "lat": 50.07,
           "lon": 14.44}

    async def provider(ctx, a):
        return Lookup(cands=[rec])
    monkeypatch.setitem(core.PROVIDERS, "overture", provider)
    out = asyncio.run(core.verify_intl(Ctx(FakeUpstream(), object()), None, "CZ", address="náměstí Míru 9, 110 00 Praha 2"))
    assert out["status"] == "verified" and out["best_match"]["match"]["zip_match"] is False
    assert out["parsed"]["zip"] == "12000" and out["parsed"]["city"] == "Praha 2"
