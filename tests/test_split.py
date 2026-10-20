"""`parsed` carries country / state / city / zip / address1 (number + street) / address2 (unit) for every country."""

import asyncio
from pathlib import Path

from app.intl import Ctx, core, japan
from app.intl.countries import COUNTRIES
from app.intl.parse import parse_line
from test_verify import FakeUpstream, nad, run

SIX = ["country", "state", "city", "zip", "address1", "address2"]


def six(parsed):
    return [parsed[k] for k in SIX]


def test_us_line_is_split_into_the_six_fields():
    r = run(FakeUpstream(street=[nad(1700)]), "1700 W Washington St Apt 2, Phoenix, AZ 85007")
    assert six(r["parsed"]) == ["US", "AZ", "Phoenix", "85007", "1700 W Washington St", "Apt 2"]
    assert r["parsed"]["house_number"] == "1700" and r["parsed"]["street"] == "W Washington St"


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
