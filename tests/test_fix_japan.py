"""Japan: the round-2 audit's regressions (R35-R53) and the small open items (S24-S39). Offline: GSI answers are
canned, Japan Post's tables are written into tmp_path (one test reads the real ones when they are on disk)."""

import asyncio
import csv
import io
import zipfile
from pathlib import Path

import pytest

from app import config
from app.intl import japan
from app.intl.countries import JP_PREFECTURES

FLAGS = ["0"] * 6


def _zip(path, name, rows, encoding):
    buf = io.StringIO()
    csv.writer(buf).writerows(rows)
    with zipfile.ZipFile(path, "w") as z:
        z.writestr(name, buf.getvalue().encode(encoding))


def _utf(pc, pref, city, town):
    return ["00000", pc[:3], pc, "", "", "", pref, city, town, *FLAGS]


@pytest.fixture
def jp(tmp_path):
    """A small Japan Post: same-named towns in several places, a 郡 town, KEN_ALL_ROME's split names."""
    d = tmp_path / "japanpost"
    d.mkdir()
    _zip(d / "utf_ken_all.zip", "utf_ken_all.csv", [
        _utf("1830027", "東京都", "府中市", "本町"), _utf("7350006", "広島県", "安芸郡府中町", "本町"),
        _utf("1830055", "東京都", "府中市", "府中町"), _utf("9608041", "福島県", "福島市", "大町"),
        _utf("1600023", "東京都", "新宿区", "西新宿（次のビルを除く）"), _utf("1540023", "東京都", "世田谷区", "若林"),
        _utf("3890111", "長野県", "北佐久郡軽井沢町", "長倉"), _utf("3890102", "長野県", "北佐久郡軽井沢町", "軽井沢"),
        _utf("6020866", "京都府", "京都市上京区", "真町"), _utf("6008001", "京都府", "京都市下京区", "真町"),
        _utf("0660046", "北海道", "千歳市", "真町"), _utf("3700054", "群馬県", "高崎市", "真町"),
        _utf("0640951", "北海道", "札幌市中央区", "宮の森一条"), _utf("0640958", "北海道", "札幌市中央区", "宮の森"),
        _utf("1060032", "東京都", "港区", "六本木（次のビルを除く）"), _utf("1066108", "東京都", "港区", "六本木六本木ヒルズ森タワー（８階）"),
        _utf("1440052", "東京都", "大田区", "蒲田"), _utf("4440819", "愛知県", "岡崎市", "岡崎駅前"),
        _utf("4440011", "愛知県", "岡崎市", "欠町"),
    ], "utf-8")
    _zip(d / "KEN_ALL_ROME.zip", "KEN_ALL_ROME.CSV", [
        ["1830027", "東京都", "府中市", "本町", "TOKYO TO", "FUCHU SHI", "HOMMACHI"],
        ["7350006", "広島県", "安芸郡　府中町", "本町", "HIROSHIMA KEN", "AKI GUN FUCHU CHO", "HOMMACHI"],
        ["1830055", "東京都", "府中市", "府中町", "TOKYO TO", "FUCHU SHI", "FUCHUCHO"],
        ["9608041", "福島県", "福島市", "大町", "FUKUSHIMA KEN", "FUKUSHIMA SHI", "OMACHI"],
        ["1600023", "東京都", "新宿区", "西新宿（次のビルを除く）", "TOKYO TO", "SHINJUKU KU", "NISHISHINJUKU (TSUGINOBIRUONOZOKU)"],
        ["1540023", "東京都", "世田谷区", "若林", "TOKYO TO", "SETAGAYA KU", "WAKABAYASHI"],
        ["3890111", "長野県", "北佐久郡　軽井沢町", "長倉", "NAGANO KEN", "KITASAKU GUN KARUIZAWA MACHI", "NAGAKURA"],
        ["3890102", "長野県", "北佐久郡　軽井沢町", "軽井沢", "NAGANO KEN", "KITASAKU GUN KARUIZAWA MACHI", "KARUIZAWA"],
        ["6020866", "京都府", "京都市　上京区", "真町", "KYOTO FU", "KYOTO SHI KAMIGYO KU", "SHINCHO"],
        ["6008001", "京都府", "京都市　下京区", "真町", "KYOTO FU", "KYOTO SHI SHIMOGYO KU", "SHINCHO"],
        ["0660046", "北海道", "千歳市", "真町", "HOKKAIDO", "CHITOSE SHI", "SHINCHO"],
        ["3700054", "群馬県", "高崎市", "真町", "GUMMA KEN", "TAKASAKI SHI", "SHINCHO"],
        ["0640951", "北海道", "札幌市　中央区", "宮の森　一条", "HOKKAIDO", "SAPPORO SHI CHUO KU", "MIYANOMORI 1-JO"],
        ["0640958", "北海道", "札幌市　中央区", "宮の森", "HOKKAIDO", "SAPPORO SHI CHUO KU", "MIYANOMORI"],
        ["1060032", "東京都", "港区", "六本木　（次のビルを除く）", "TOKYO TO", "MINATO KU", "ROPPONGI (TSUGINOBIRUONOZOKU)"],
        ["1066108", "東京都", "港区", "六本木　六本木ヒルズ森タワー（８階", "TOKYO TO", "MINATO KU", "ROPPONGI ROPPONGIHIRUZUMORITAWA(8-K"],
        ["1440052", "東京都", "大田区", "蒲田", "TOKYO TO", "OTA KU", "KAMATA"],
        ["4440011", "愛知県", "岡崎市", "欠町", "AICHI KEN", "OKAZAKI SHI", "KAKEMACHI"],
    ], "cp932")
    _zip(d / "jigyosyo.zip", "JIGYOSYO.CSV", [], "cp932")
    t = japan.JapanPost(None, tmp_path)
    t.by_pc, t.by_place, t.by_town, t.by_romaji = t._read()
    return t


class _Gsi:
    """GSI's address search, canned: query -> titles (anything else: no answer)."""

    def __init__(self, answers):
        self.answers, self.asked = answers, []

    async def get(self, service, url, params, expect=dict):
        self.asked.append(params["q"])
        return [{"geometry": {"coordinates": [139.0, 35.0]}, "properties": {"title": t}}
                for t in self.answers.get(params["q"], [])]


def _verify(gsi, jp, text, **kw):
    return asyncio.run(japan.verify_jp(gsi, jp, text, **kw))


def _romaji(jp, text):
    a, notes = japan.parse_jp(text), []
    japan._from_romaji(jp, a, notes)
    return a.prefix, notes


# --------------------------------------------------------------------------------------------- R35 / S25

def test_r35_kyoto_street_name_keeps_the_ward():
    a = japan.parse_jp("京都府京都市上京区烏丸通下立売上る真町52")
    assert (a.prefix, a.nums, a.read_as) == ("京都府京都市上京区真町", [52], "烏丸通下立売上る")
    a = japan.parse_jp("京都府京都市東山区大和大路通四条下る大和町5")
    assert (a.prefix, a.nums) == ("京都府京都市東山区大和町", [5])
    a = japan.parse_jp("寺町通御池上る上本能寺前町488")  # no city written: the street still goes
    assert (a.prefix, a.read_as) == ("上本能寺前町", "寺町通御池上る")
    assert japan._business_town("寺町通御池上る上本能寺前町４８８番地") == ("上本能寺前町", None)


def test_r35_ward_searched_and_never_verified_in_another_ward(jp):
    gsi = _Gsi({"京都府京都市上京区真町52": ["京都府京都市上京区真町"], "真町52": ["京都府京都市下京区真町５２番地"]})
    res = _verify(gsi, jp, "京都府京都市上京区烏丸通下立売上る真町52")
    assert gsi.asked[0] == "京都府京都市上京区真町52"
    assert res["status"] == "not_covered" and "下京区" not in res["best"].raw


def test_r35_s25_a_town_in_several_places_is_refused(jp):
    gsi = _Gsi({"真町52": ["京都府京都市下京区真町５２番地"], "金吹町452": ["京都府京都市中京区金吹町４５２番地"]})
    for text in ("真町52", "烏丸通下立売上る真町52", "京都市烏丸通下立売上る真町52"):
        res = _verify(gsi, jp, text)
        assert res["status"] == "geocode_failed" and any("places" in n for n in res["notes"]), text
    assert gsi.asked == []  # refused before asking GSI, which would answer for one of them
    # the ward, the state / city fields or the postcode pick one
    gsi = _Gsi({"京都府京都市上京区真町52": ["京都府京都市上京区真町５２番地"]})
    assert _verify(gsi, jp, "上京区真町52")["status"] == "verified"
    assert _verify(gsi, jp, "真町52", city="上京区")["status"] == "verified"
    assert _verify(gsi, jp, "真町52", postcode="602-0866")["status"] == "verified"


# --------------------------------------------------------------------------------------- R37 / R44 / R51

@pytest.mark.parametrize("state", ["東京都", "東京", "Tokyo", "TOKYO-TO", "Tōkyō", "Tokyo Metropolis", "JP-13", "13", "东京都"])
def test_r37_r44_r51_state_read_as_a_prefecture(state):
    assert japan.prefecture(state) == "東京都"


def test_r37_r44_prefecture_names():
    assert [japan.prefecture(x) for x in ("Hokkaido", "Kyoto-fu", "京都", "Osaka", "Gumma", "JP-47", "1", "大阪府")] == [
        "北海道", "京都府", "京都府", "大阪府", "群馬県", "沖縄県", "北海道", "大阪府"]
    assert [japan.prefecture(x) for x in ("Narnia", "Shinjuku-ku", "48", "", None)] == ["", "", "", "", ""]


@pytest.mark.parametrize("kw", [
    dict(state="Tokyo"), dict(state="JP-13"), dict(state="13"), dict(state="TOKYO-TO"), dict(state="东京都"),
    dict(state="Tokyo", postcode="160-0023"), dict(state="Narnia"), dict(city="Shinjuku"), dict(city="Shinjuku-ku", state="Tokyo"),
])
def test_r37_r44_r51_state_and_city_fields_never_reject_the_address(kw):
    gsi = _Gsi({"東京都新宿区西新宿2-8-1": ["東京都新宿区西新宿二丁目８番"], "西新宿2-8-1": ["東京都新宿区西新宿二丁目８番"]})
    for text in ("東京都新宿区西新宿2-8-1", "西新宿2-8-1, 新宿区"):
        res = _verify(gsi, None, text, **kw)
        assert (res["status"], res["level"]) == ("verified", "block"), (text, kw, res["notes"])


def test_r37_r44_unreadable_or_contradicting_fields_get_a_note():
    gsi = _Gsi({"西新宿2-8-1": ["東京都新宿区西新宿二丁目８番"]})
    res = _verify(gsi, None, "西新宿2-8-1, 新宿区", state="Narnia", postcode="160-0023")
    assert res["status"] == "verified" and any("'Narnia' is not a Japanese place name" in n for n in res["notes"])
    res = _verify(gsi, None, "西新宿2-8-1, 新宿区", state="大阪府")  # a preference, not a filter
    assert res["status"] == "verified" and any("大阪府 (state / city field) is not where" in n for n in res["notes"])
    # the field still picks between places GSI offers
    gsi = _Gsi({"本町1-1-1": ["大阪府大阪市中央区本町一丁目１番１号", "東京都中野区本町一丁目１番１号"]})
    res = _verify(gsi, None, "本町1-1-1", state="Osaka")
    assert res["status"] == "verified" and res["best"].raw.startswith("大阪府")
    # a town written in the address itself stays a filter: 西新宿 is not in 渋谷区
    gsi = _Gsi({"西新宿2-8-1": ["東京都新宿区西新宿二丁目８番"]})
    assert _verify(gsi, None, "西新宿2-8-1, 渋谷区, 東京都")["status"] == "geocode_failed"


def test_r37_state_fields_pick_the_town_with_japan_post(jp):
    gsi = _Gsi({"東京都新宿区西新宿2-8-1": ["東京都新宿区西新宿二丁目８番"]})
    res = _verify(gsi, jp, "西新宿2-8-1, 新宿区", state="Tokyo", postcode="160-0023")
    assert (res["status"], res["zip_match"]) == ("verified", True) and gsi.asked[0] == "東京都新宿区西新宿2-8-1"


# --------------------------------------------------------------------------------------------- R38

def test_r38_romaji_town_in_a_gun_without_the_district(jp):
    assert _romaji(jp, "2381-1 Nagakura, Karuizawa-machi, Nagano 389-0111")[0] == "長野県北佐久郡軽井沢町長倉"
    assert _romaji(jp, "2381-1 Nagakura, Karuizawa-machi, Nagano")[0] == "長野県北佐久郡軽井沢町長倉"
    assert _romaji(jp, "2381-1 Nagakura, Karuizawa-machi, Kitasaku-gun, Nagano")[0] == "長野県北佐久郡軽井沢町長倉"
    row = jp.rows("3890111")[0]
    assert (row.city_r, row.gun_r) == ("KARUIZAWA", "KITASAKU")
    # a misspelt town is not replaced by the postcode's town (round 3, S24: a wrong postcode would verify another
    # town); the note names the postcode's town
    prefix, notes = _romaji(jp, "2381-1 Nagakurra, Karuizawa-machi, Nagano 389-0111")
    assert prefix == "" and any("is for 長野県北佐久郡軽井沢町長倉: check the town" in n for n in notes)


# --------------------------------------------------------------------------------------- R39 / R52

@pytest.mark.parametrize("text, nums, rest", [
    ("長野県北佐久郡軽井沢町長倉2381-1-1203", [2381, 1], "1203"),  # round 3 (R39): a 3+ digit third group is the room
    ("京都府京都市下京区東塩小路町721-1 1203", [721, 1], "1203"),
    ("東京都江東区豊洲2-2-1001", [2, 2], "1001"),
    ("北海道札幌市中央区北1条西2丁目1-1 1234", [2, 1, 1], "1234"),
    ("北海道札幌市中央区北1条西2丁目1-1 1203号室", [2, 1, 1], "1203号室"),
    ("東京都千代田区丸の内1-9 1203", [1, 9], "1203"),
    ("沖縄県那覇市泉崎1-2 0123", [1, 2], "0123"),
    ("高知県高知市福井町2510 780-0965", [2510], ""),
])
def test_r39_r52_house_numbers_are_not_phone_numbers(text, nums, rest):
    a = japan.parse_jp(text)
    assert (a.nums, a.rest) == (nums, rest) and not a.read_as


@pytest.mark.parametrize("text", [
    "東京都新宿区西新宿2-8-1 03-5321-1111", "東京都新宿区西新宿2-8-1 TEL 03 5321 1111", "東京都新宿区西新宿2-8-1 電話：090 1234 5678",
    "東京都新宿区西新宿2-8-1 +81 3 5321 1111", "東京都新宿区西新宿2-8-1 山田太郎様 090-1234-5678", "03-5321-1111 東京都新宿区西新宿2-8-1",
    "收件人：李四 电话：+81 90-1111-2222 地址：東京都新宿区西新宿2-8-1",
])
def test_r39_r52_phone_numbers_still_removed(text):
    a = japan.parse_jp(text)
    assert (a.prefix, a.nums, a.postcode) == ("東京都新宿区西新宿", [2, 8, 1], "") and a.read_as


def test_r52_block_with_a_room_number_verifies():
    gsi = _Gsi({"東京都千代田区丸の内1-9": ["東京都千代田区丸の内一丁目９番"]})
    res = _verify(gsi, None, "東京都千代田区丸の内1-9 1203")
    assert (res["status"], res["level"], res["jp"].rest) == ("verified", "block", "1203")


# --------------------------------------------------------------------------------------- R40 / R45 / S31

@pytest.mark.parametrize("text, prefix, nums", [
    ("東京都千代田区千代田一番一号", "東京都千代田区千代田", [1, 1]),
    ("北海道虻田郡ニセコ町字曽我八八八番地", "北海道虻田郡ニセコ町字曽我", [888]),
    ("京都府京都市東山区祇園町北側六二五番地", "京都府京都市東山区祇園町北側", [625]),
    ("長野県北佐久郡軽井沢町大字長倉字鶴溜二一四七番地", "長野県北佐久郡軽井沢町大字長倉字鶴溜", [2147]),
    ("福島県耶麻郡北塩原村大字桧原字剣ケ峯一〇九三番地", "福島県耶麻郡北塩原村大字桧原字剣ケ峯", [1093]),
    # numeral + 番 in a town's name stays (Japan Post's list)
    ("愛知県名古屋市熱田区一番1丁目1-1", "愛知県名古屋市熱田区一番", [1, 1, 1]),
    ("東京都港区麻布十番2-3-1", "東京都港区麻布十番", [2, 3, 1]),
    ("東京都千代田区一番町1", "東京都千代田区一番町", [1]),
    ("福島県河沼郡会津坂下町市中三番甲123", "福島県河沼郡会津坂下町市中三番甲", [123]),
    ("愛知県清須市春日一番割45", "愛知県清須市春日一番割", [45]),
])
def test_r40_r45_s31_kanji_numerals_before_ban(text, prefix, nums):
    a = japan.parse_jp(text)
    assert (a.prefix, a.nums) == (prefix, nums)


def test_r40_verifies_the_block():
    gsi = _Gsi({"東京都千代田区千代田1-1": ["東京都千代田区千代田１番"]})
    assert _verify(gsi, None, "東京都千代田区千代田一番一号")["status"] == "verified"


# --------------------------------------------------------------------------------------------- R42

def test_r42_written_prefecture_filters_romaji_towns(jp):
    places = lambda text: [r.place for r in jp.romaji_places(text)]  # noqa: E731
    assert places("1-1-1 Honmachi, Fuchu-cho, Hiroshima") == ["広島県安芸郡府中町本町"]
    assert places("1-1-1 Honmachi, Fuchu-cho") == ["広島県安芸郡府中町本町"]  # 府中町, not 府中市
    assert places("1-1-1 Honmachi, Fuchu-shi, Tokyo") == ["東京都府中市本町"]
    assert places("1-1 Omachi, Kitakata-shi, Fukushima") == []  # 大町 is in 福島市, not 喜多方市
    assert places("1-1 Omachi, Fukushima-shi, Fukushima") == ["福島県福島市大町"]
    assert places("2-8-1 Nishishinjuku, Shinjuku-ku, Osaka") == []
    assert japan._PREF_ROMAJI["北海道"] == "HOKKAIDO" and len(japan._PREF_ROMAJI) == 47
    assert all(japan.romaji_norm(v) == v for v in japan._PREF_ROMAJI.values())


def test_r42_hiroshima_address_with_its_postcode(jp):
    prefix, notes = _romaji(jp, "1-1-1 Honmachi, Fuchu-cho, Hiroshima 735-0006")
    assert prefix == "広島県安芸郡府中町本町"
    gsi = _Gsi({"広島県安芸郡府中町本町1-1-1": ["広島県府中町本町一丁目１番１号"]})
    res = _verify(gsi, jp, "1-1-1 Honmachi, Fuchu-cho, Hiroshima 735-0006")
    assert res["status"] == "verified" and res["best"].raw.startswith("広島県") and res["zip_match"] is True
    # a state field works for romaji addresses too, in any form
    for state in ("Hiroshima", "JP-34", "広島県"):
        assert _verify(gsi, jp, "1-1-1 Honmachi, Fuchu", state=state)["best"].raw.startswith("広島県"), state
    assert _verify(gsi, jp, "1-1-1 Honmachi, Fuchu")["status"] == "geocode_failed"  # 府中市 or 府中町: can't tell
    assert _verify(gsi, jp, "1-1-1 Honmachi, Fuchu-shi", state="Hiroshima")["status"] == "geocode_failed"


def test_r42_town_and_postcode_in_different_prefectures(jp):
    # the town names its prefecture: the town stays (the postcode check reports the code)
    assert _romaji(jp, "1-2-3 Wakabayashi, Setagaya-ku, Tokyo 735-0006")[0] == "東京都世田谷区若林"
    assert _romaji(jp, "1-2-3 Wakabayashi, Setagaya-ku, Tokyo 160-0023")[0] == "東京都世田谷区若林"
    # nothing says which of the two is right
    prefix, notes = _romaji(jp, "1-2-3 Wakabayashi, Setagaya-ku 735-0006")
    assert prefix == "" and any("different prefectures" in n for n in notes)


# --------------------------------------------------------------------------------------------- R43

def test_r43_romaji_names_split_by_an_ideographic_space(jp):
    assert [(r.town, r.town_r) for r in jp.rows("0640951")] == [("宮の森一条", "MIYANOMORIJO")]
    assert [(r.town, r.town_r, r.business) for r in jp.rows("1066108")] == [("六本木", "ROPPONGI", "六本木六本木ヒルズ森タワー")]
    a = japan.parse_jp("Miyanomori 1-jo 10-chome 3-1, Chuo-ku, Sapporo 064-0951")
    japan._from_romaji(jp, a, [])
    assert (a.prefix, a.nums) == ("北海道札幌市中央区宮の森一条", [10, 3, 1])


@pytest.mark.skipif(not (Path(config.DATA_DIR) / "japanpost" / "KEN_ALL_ROME.zip").exists(), reason="no Japan Post files")
def test_r43_most_towns_have_their_romaji():
    t = japan.JapanPost(None, Path(config.DATA_DIR))
    by_pc = t._read()[0]
    towns = [r for rows in by_pc.values() for r in rows if r.town and not r.business]
    assert sum(1 for r in towns if r.town_r) > 0.99 * len(towns)
    assert sum(1 for r in towns if r.city_r) > 0.999 * len(towns)
    prefs = {r[1]: r[4].split()[0] for r in japan._csv_rows(Path(config.DATA_DIR) / "japanpost" / "KEN_ALL_ROME.zip", "cp932")}
    assert {k: japan.romaji_norm(v) for k, v in prefs.items()} == japan._PREF_ROMAJI


# --------------------------------------------------------------------------------------- R46 / S38

def test_r46_seven_digits_at_the_end_are_the_postcode():
    assert japan.parse_jp("東京都新宿区西新宿2-8-1, 1638001").postcode == "1638001"
    assert japan.parse_jp("2-8-1 Nishishinjuku, Shinjuku-ku, Tokyo, 1638001").postcode == "1638001"
    assert japan.parse_jp("1638001 東京都新宿区西新宿2-8-1").postcode == "1638001"


def test_s38_parcel_number_is_not_a_postcode():
    a = japan.parse_jp("茨城県水戸市笠原町978-1234")
    assert (a.postcode, a.nums) == ("", [978, 1234])
    for text in ("東京都新宿区西新宿2-8-1 163-8001", "163-8001 東京都新宿区西新宿2-8-1", "東京都新宿区西新宿2-8-1（163-8001）",
                 "2-8-1 Nishishinjuku, Shinjuku-ku, Tokyo 163-8001"):
        assert japan.parse_jp(text).postcode == "1638001", text


@pytest.mark.parametrize("text, nums", [
    ("1-1 Kita 1 Jonishi 2 Chome, Chuo Ward, Sapporo, Hokkaido 060-0001", [2, 1, 1]),
    ("8-1 Nishishinjuku 2-chome, Shinjuku-ku, Tokyo", [2, 8, 1]),
    ("2-1 Kasumigaseki 1-chome, Chiyoda-ku, Tokyo 100-8916", [1, 2, 1]),
    ("294 Kiyomizu 1-chome, Higashiyama-ku, Kyoto", [1, 294]),
])
def test_s38_romaji_chome_written_apart(text, nums):
    a = japan.parse_jp(text)
    assert (a.nums, a.chome) == (nums, True)
    a = japan.parse_jp("Nishishinjuku 2-chome 8-1, Shinjuku-ku")  # written together, as before
    assert a.nums == [2, 8, 1]


# --------------------------------------------------------------------------------------------- R53

@pytest.mark.parametrize("text, prefix, nums", [
    ("新宿区西新宿2-8-1 東京都庁第一本庁舎 32階", "新宿区西新宿", [2, 8, 1]),
    ("札幌市北区北8条西5丁目1 北海道大学 2号館", "札幌市北区北8条西", [5, 1]),
    ("大阪市中央区大手前2丁目1-22 大阪府庁本館 5階", "大阪市中央区大手前", [2, 1, 22]),
    # a name or label before the prefecture still goes
    ("山田太郎様 東京都新宿区西新宿2-8-1", "東京都新宿区西新宿", [2, 8, 1]),
    ("六本木ヒルズ森タワー 東京都港区六本木6-10-1", "東京都港区六本木", [6, 10, 1]),
    ("中村太郎 東京都新宿区西新宿2-8-1", "東京都新宿区西新宿", [2, 8, 1]),
    ("3F 東京都新宿区西新宿2-8-1", "東京都新宿区西新宿", [2, 8, 1]),
])
def test_r53_prefecture_in_a_building_name(text, prefix, nums):
    a = japan.parse_jp(text)
    assert (a.prefix, a.nums) == (prefix, nums)


# --------------------------------------------------------------------------------------- optional items

def test_s24_unknown_town_replaced_by_the_postcodes_gets_a_note(jp):
    # round 3: not read as the postcode's town any more (a wrong postcode would verify another town), only named
    prefix, notes = _romaji(jp, "2-24 Zzyzxcho, Fuchu-shi, Tokyo 183-0055")
    assert prefix == "" and any("postcode 183-0055 is for 東京都府中市府中町: check the town" in n for n in notes)
    prefix, notes = _romaji(jp, "1-2-3 Wakabayashi, Setagaya-ku, Tokyo 154-0023")  # a town that is found: no such note
    assert prefix == "東京都世田谷区若林" and not any("postcode's town" in n for n in notes)


def test_s27_unknown_go_in_a_ban_town_without_chome():
    gsi = _Gsi({"大阪府大阪市中央区大阪城1-99": ["大阪府大阪市中央区大阪城１番"],
                "大阪府大阪市中央区大阪城1-1": ["大阪府大阪市中央区大阪城１番１号"]})
    res = _verify(gsi, None, "大阪府大阪市中央区大阪城1-99")
    assert (res["status"], res["level"]) == ("street_only", "street") and gsi.asked[-1] == "大阪府大阪市中央区大阪城1-1"
    assert _verify(gsi, None, "大阪府大阪市中央区大阪城1-1")["status"] == "verified"
    # a parcel area (番地) without 号 data: the branch number still can't be checked, as before
    gsi = _Gsi({"茨城県水戸市笠原町978-6": ["茨城県水戸市笠原町９７８番地"]})
    assert (_verify(gsi, None, "茨城県水戸市笠原町978-6")["level"]) == "block"


def test_s33_passport_oh_spelling(jp):
    assert [japan.romaji_norm(x) for x in ("Ohta", "Ohsaki", "Ohtemachi", "Ohara", "Kohoku", "Satoh")] == [
        "OTA", "OSAKI", "OTEMACHI", "OHARA", "KOHOKU", "SATO"]
    assert _romaji(jp, "1-1-1 Kamata, Ohta-ku, Tokyo")[0] == "東京都大田区蒲田"


def test_s34_kanji_branch_number_after_no():
    a = japan.parse_jp("茨城県水戸市笠原町九七八番地の六")
    assert (a.prefix, a.nums, a.rest) == ("茨城県水戸市笠原町", [978, 6], "")


def test_s35_parcel_missing_in_a_banchi_chome_is_not_covered():
    gsi = _Gsi({"北海道札幌市中央区北1条西1丁目6": ["北海道札幌市中央区北一条西一丁目"],
                "北海道札幌市中央区北一条西1丁目1": ["北海道札幌市中央区北一条西一丁目１番地"]})
    res = _verify(gsi, None, "〒060-8602 北海道札幌市中央区北1条西1丁目6")
    assert res["status"] == "not_covered" and any("numbers parcels (番地)" in n for n in res["notes"])
    # a 住居表示 丁目 without this 番 is still street_only
    gsi = _Gsi({"東京都新宿区西新宿2丁目99-1": ["東京都新宿区西新宿二丁目"], "東京都新宿区西新宿2-99-1": ["東京都新宿区西新宿二丁目"],
                "東京都新宿区西新宿二丁目1": [], "東京都新宿区西新宿2丁目1": ["東京都新宿区西新宿二丁目１番"]})
    assert _verify(gsi, None, "東京都新宿区西新宿2-99-1")["status"] == "street_only"


def test_s39_city_romaji_for_postcodes_missing_from_ken_all_rome(jp):
    row = jp.rows("4440819")[0]
    assert (row.town_r, row.city_r) == ("", "OKAZAKI")
    assert _romaji(jp, "1-1-1 Okazakiekimae, Okazaki-shi, Aichi 444-0819, Japan")[0] == "愛知県岡崎市岡崎駅前"


def test_prefectures_in_jis_order():
    assert JP_PREFECTURES[12] == "東京都" and japan._PREF_ROMAJI[JP_PREFECTURES[33]] == "HIROSHIMA"
