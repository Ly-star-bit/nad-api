"""Japan: the round-3 audit's items ([38]-[46], [63], R39, S23-S38). Offline: GSI answers are canned, Japan Post's
tables are written into tmp_path with the competing towns (新宿 next to 西新宿, 渋谷 next to 道玄坂, ...)."""

import asyncio
import csv
import io
import zipfile

import pytest

from app.intl import japan

FLAGS = ["0"] * 6


def _zip(path, name, rows, encoding):
    buf = io.StringIO()
    csv.writer(buf).writerows(rows)
    with zipfile.ZipFile(path, "w") as z:
        z.writestr(name, buf.getvalue().encode(encoding))


# (postcode, prefecture, city, town, KEN_ALL_ROME prefecture, city, town); KEN_ALL_ROME splits 郡 / 政令市 and a
# building's name with an ideographic space
TOWNS = [
    ("1600023", "東京都", "新宿区", "西新宿（次のビルを除く）", "TOKYO TO", "SHINJUKU KU", "NISHISHINJUKU (TSUGINOBIRUONOZOKU)"),
    ("1600022", "東京都", "新宿区", "新宿", "TOKYO TO", "SHINJUKU KU", "SHINJUKU"),
    ("1631030", "東京都", "新宿区", "西新宿新宿パークタワー（地階・階層不明）", "TOKYO TO", "SHINJUKU KU",
     "NISHISHINJUKU SHINJUKUPAKUTAWA(CHIKAI.KAISOFUMEI)"),
    ("1630401", "東京都", "新宿区", "西新宿新宿三井ビル（地階・階層不明）", "TOKYO TO", "SHINJUKU KU",
     "NISHISHINJUKU SHINJUKUMITSUIBIRU(CHIKAI.KAISOFUMEI)"),
    ("3490111", "埼玉県", "蓮田市", "西新宿", "SAITAMA KEN", "HASUDA SHI", "NISHISHINJUKU"),
    ("1500002", "東京都", "渋谷区", "渋谷", "TOKYO TO", "SHIBUYA KU", "SHIBUYA"),
    ("1500043", "東京都", "渋谷区", "道玄坂", "TOKYO TO", "SHIBUYA KU", "DOGENZAKA"),
    ("1530063", "東京都", "目黒区", "目黒", "TOKYO TO", "MEGURO KU", "MEGURO"),
    ("1530064", "東京都", "目黒区", "下目黒", "TOKYO TO", "MEGURO KU", "SHIMOMEGURO"),
    ("1760001", "東京都", "練馬区", "練馬", "TOKYO TO", "NERIMA KU", "NERIMA"),
    ("1760012", "東京都", "練馬区", "豊玉北", "TOKYO TO", "NERIMA KU", "TOYOTAMAKITA"),
    ("1160002", "東京都", "荒川区", "荒川", "TOKYO TO", "ARAKAWA KU", "ARAKAWA"),
    ("1160001", "東京都", "荒川区", "町屋", "TOKYO TO", "ARAKAWA KU", "MACHIYA"),
    ("1000005", "東京都", "千代田区", "丸の内（次のビルを除く）", "TOKYO TO", "CHIYODA KU", "MARUNOUCHI (TSUGINOBIRUONOZOKU)"),
    ("1540017", "東京都", "世田谷区", "世田谷", "TOKYO TO", "SETAGAYA KU", "SETAGAYA"),
    ("1440052", "東京都", "大田区", "蒲田", "TOKYO TO", "OTA KU", "KAMATA"),
    ("1310045", "東京都", "墨田区", "押上", "TOKYO TO", "SUMIDA KU", "OSHIAGE"),
    ("1040061", "東京都", "中央区", "銀座", "TOKYO TO", "CHUO KU", "GINZA"),
    ("1350061", "東京都", "江東区", "豊洲", "TOKYO TO", "KOTO KU", "TOYOSU"),
    ("1830055", "東京都", "府中市", "府中町", "TOKYO TO", "FUCHU SHI", "FUCHUCHO"),
    ("5300001", "大阪府", "大阪市北区", "梅田", "OSAKA FU", "OSAKA SHI KITA KU", "UMEDA"),
    ("5410053", "大阪府", "大阪市中央区", "本町", "OSAKA FU", "OSAKA SHI CHUO KU", "HOMMACHI"),
    ("2310005", "神奈川県", "横浜市中区", "本町", "KANAGAWA KEN", "YOKOHAMA SHI NAKA KU", "HONCHO"),
    ("7300000", "広島県", "広島市中区", "本町", "HIROSHIMA KEN", "HIROSHIMA SHI NAKA KU", "HONMACHI"),
    ("7940041", "愛媛県", "今治市", "松本町", "EHIME KEN", "IMABARI SHI", "MATSUMOTOCHO"),
    ("7991602", "愛媛県", "今治市", "山口", "EHIME KEN", "IMABARI SHI", "YAMAGUCHI"),
    ("9608132", "福島県", "福島市", "東浜町", "FUKUSHIMA KEN", "FUKUSHIMA SHI", "HIGASHIHAMACHO"),
    ("9608202", "福島県", "福島市", "山口", "FUKUSHIMA KEN", "FUKUSHIMA SHI", "YAMAGUCHI"),
    ("2510052", "神奈川県", "藤沢市", "藤沢", "KANAGAWA KEN", "FUJISAWA SHI", "FUJISAWA"),
    ("2510055", "神奈川県", "藤沢市", "南藤沢", "KANAGAWA KEN", "FUJISAWA SHI", "MINAMIFUJISAWA"),
    ("2770005", "千葉県", "柏市", "柏", "CHIBA KEN", "KASHIWA SHI", "KASHIWA"),
    ("2770852", "千葉県", "柏市", "旭町", "CHIBA KEN", "KASHIWA SHI", "ASAHICHO"),
    ("2430001", "神奈川県", "厚木市", "厚木", "KANAGAWA KEN", "ATSUGI SHI", "ATSUGI"),
    ("2430018", "神奈川県", "厚木市", "中町", "KANAGAWA KEN", "ATSUGI SHI", "NAKACHO"),
    ("1730004", "東京都", "板橋区", "板橋", "TOKYO TO", "ITABASHI KU", "ITABASHI"),
    ("1730014", "東京都", "板橋区", "大山東町", "TOKYO TO", "ITABASHI KU", "OYAMAHIGASHICHO"),
    ("1000001", "東京都", "千代田区", "千代田", "TOKYO TO", "CHIYODA KU", "CHIYODA"),
    ("1010065", "東京都", "千代田区", "西神田", "TOKYO TO", "CHIYODA KU", "NISHIKANDA"),
    ("1310031", "東京都", "墨田区", "墨田", "TOKYO TO", "SUMIDA KU", "SUMIDA"),
    ("1300001", "東京都", "墨田区", "吾妻橋", "TOKYO TO", "SUMIDA KU", "AZUMABASHI"),
    ("1500031", "東京都", "渋谷区", "桜丘町", "TOKYO TO", "SHIBUYA KU", "SAKURAGAOKACHO"),
    ("0600001", "北海道", "札幌市中央区", "北一条西", "HOKKAIDO", "SAPPORO SHI CHUO KU", "KITA1-JONISHI"),
    ("0600002", "北海道", "札幌市中央区", "北二条西", "HOKKAIDO", "SAPPORO SHI CHUO KU", "KITA2-JONISHI"),
    ("0600005", "北海道", "札幌市中央区", "北五条西", "HOKKAIDO", "SAPPORO SHI CHUO KU", "KITA5-JONISHI"),
    ("0601234", "北海道", "札幌市北区", "北一条西", "HOKKAIDO", "SAPPORO SHI KITA KU", "KITA1-JONISHI"),
]
# large businesses' own codes (JIGYOSYO: name, prefecture, city, town, number, postcode)
BUSINESSES = [("東京都庁", "東京都", "新宿区", "西新宿", "２丁目８－１", "1638001"),
              ("さっぽろ創生スクエア", "北海道", "札幌市中央区", "北１条西", "１丁目６番地", "0608602")]


@pytest.fixture
def jp(tmp_path):
    d = tmp_path / "japanpost"
    d.mkdir()
    _zip(d / "utf_ken_all.zip", "utf_ken_all.csv",
         [["00000", pc[:3], pc, "", "", "", pref, city, town, *FLAGS] for pc, pref, city, town, *_ in TOWNS], "utf-8")
    rome = []
    for pc, pref, city, town, pref_r, city_r, town_r in TOWNS:
        city = city.replace("市", "市　", 1) if city.count("市") and city.endswith("区") else city
        town = "西新宿　" + town[3:] if town.startswith("西新宿") and "ビル" in town or "タワー" in town else town
        rome.append([pc, pref, city, town, pref_r, city_r, town_r])
    _zip(d / "KEN_ALL_ROME.zip", "KEN_ALL_ROME.CSV", rome, "cp932")
    _zip(d / "jigyosyo.zip", "JIGYOSYO.CSV",
         [["00000", "ｶﾅ", name, pref, city, town, num, pc, "", "", "0", "0", "0"] for name, pref, city, town, num, pc in BUSINESSES],
         "cp932")
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


# --------------------------------------------------------------------------------------------- [43]

BUILDINGS = [
    ("Shinjuku Park Tower 30F, 3-7-1 Nishishinjuku, Shinjuku-ku, Tokyo 163-1030", "東京都新宿区西新宿", [3, 7, 1]),
    ("Shinjuku Park Tower 30F, 3-7-1 Nishishinjuku, Shinjuku-ku, Tokyo", "東京都新宿区西新宿", [3, 7, 1]),
    ("Shinjuku Mitsui Building 1F, 2-1-1 Nishishinjuku, Shinjuku-ku, Tokyo 163-0401", "東京都新宿区西新宿", [2, 1, 1]),
    ("Shibuya Mark City West 10F, 1-12-1 Dogenzaka, Shibuya-ku, Tokyo 150-0043", "東京都渋谷区道玄坂", [1, 12, 1]),
    ("Shibuya Mark City West 10F, 1-12-1 Dogenzaka, Shibuya-ku, Tokyo", "東京都渋谷区道玄坂", [1, 12, 1]),
    ("Meguro Gajoen, 1-8-1 Shimomeguro, Meguro-ku, Tokyo 153-0064", "東京都目黒区下目黒", [1, 8, 1]),
    ("Nerima Station Bldg 3F, 1-17-1 Toyotamakita, Nerima-ku, Tokyo 176-0012", "東京都練馬区豊玉北", [1, 17, 1]),
    ("Arakawa Kumin Kaikan, 1-1-1 Machiya, Arakawa-ku, Tokyo 116-0001", "東京都荒川区町屋", [1, 1, 1]),
    ("Hanako Shibuya, 1-12-1 Dogenzaka, Shibuya-ku, Tokyo 150-0043", "東京都渋谷区道玄坂", [1, 12, 1]),
    ("3-7-1 Nishishinjuku Shinjuku Park Tower 30F, Shinjuku-ku, Tokyo 163-1030", "東京都新宿区西新宿", [3, 7, 1]),
    ("Shinjuku Bldg 5F, 2-8-1 Nishishinjuku, Shinjuku-ku 160-0023", "東京都新宿区西新宿", [2, 8, 1]),
    ("Fujisawa Bldg 3F, 1-2-3 Minamifujisawa, Fujisawa-shi, Kanagawa 251-0055", "神奈川県藤沢市南藤沢", [1, 2, 3]),
    ("Kashiwa Station Bldg, 1-1-1 Asahicho, Kashiwa-shi, Chiba 277-0852", "千葉県柏市旭町", [1, 1, 1]),
    ("Atsugi Bldg, 1-2-3 Nakacho, Atsugi-shi, Kanagawa 243-0018", "神奈川県厚木市中町", [1, 2, 3]),
    ("Itabashi Bldg, 2-66-1 Oyamahigashicho, Itabashi-ku, Tokyo", "東京都板橋区大山東町", [2, 66, 1]),
    ("Chiyoda First Bldg, 3-8-1 Nishikanda, Chiyoda-ku, Tokyo 101-0065", "東京都千代田区西神田", [3, 8, 1]),
    ("Sumida Riverside Hall, 1-23-20 Azumabashi, Sumida-ku, Tokyo 130-0001", "東京都墨田区吾妻橋", [1, 23, 20]),
    ("Shibuya Sakura Stage, 1-1-1 Sakuragaokacho, Shibuya-ku, Tokyo 150-0031", "東京都渋谷区桜丘町", [1, 1, 1]),
    # the town named like its ward is still read when it is the one written
    ("Hanako Yamada, 3-7-1 Shinjuku, Shinjuku-ku, Tokyo 160-0022", "東京都新宿区新宿", [3, 7, 1]),
    ("1-2-3 Shibuya, Shibuya-ku, Tokyo", "東京都渋谷区渋谷", [1, 2, 3]),
]


@pytest.mark.parametrize("text, prefix, nums", BUILDINGS)
def test_r3_43_building_or_name_with_the_ward_name_keeps_the_written_town(jp, text, prefix, nums):
    a, notes = japan.parse_jp(text), []
    japan._from_romaji(jp, a, notes)
    assert (a.prefix, a.nums) == (prefix, nums), notes


def test_r3_43_building_address_verifies_at_the_written_town_with_its_postcode(jp):
    gsi = _Gsi({"東京都新宿区西新宿3-7-1": ["東京都新宿区西新宿三丁目７番１号"],
                "東京都渋谷区道玄坂1-12-1": ["東京都渋谷区道玄坂一丁目１２番１号"]})
    res = _verify(gsi, jp, "Shinjuku Park Tower 30F, 3-7-1 Nishishinjuku, Shinjuku-ku, Tokyo 163-1030")
    assert (res["status"], res["level"], res["zip_match"]) == ("verified", "address", True)
    assert res["best"].raw == "東京都新宿区西新宿三丁目７番１号"
    # structured, the way core joins street, unit (batch.html's address2) and city for JP
    for street, unit, city, pc, want in [
            ("3-7-1 Nishishinjuku", "Shinjuku Park Tower 30F", "Shinjuku-ku", "163-1030", "東京都新宿区西新宿三丁目７番１号"),
            ("1-12-1 Dogenzaka", "Shibuya Mark City West 10F", "Shibuya-ku", "150-0043", "東京都渋谷区道玄坂一丁目１２番１号")]:
        res = _verify(gsi, jp, ", ".join((street, unit, city)), state="Tokyo", postcode=pc)
        assert (res["status"], res["best"].raw, res["zip_match"]) == ("verified", want, True)
    # the postcode only chooses between readings equally good otherwise; a wrong one is reported, not followed
    res = _verify(gsi, jp, "Shinjuku Park Tower 30F, 3-7-1 Nishishinjuku, Shinjuku-ku, Tokyo 160-0022")
    assert res["best"].raw == "東京都新宿区西新宿三丁目７番１号" and res["zip_match"] is False


# --------------------------------------------------------------------------------------------- [44]

@pytest.mark.parametrize("text, prefix", [
    ("Taro Yamaguchi, 2-8-1 Nishi-Shinjuku, Shinjuku-ku 163-8001", "東京都新宿区西新宿"),
    ("Yamaguchi Bldg 3F, 2-8-1 Nishishinjuku, Shinjuku-ku 160-0023", "東京都新宿区西新宿"),
    ("Chiba Building 5F, 1-9-1 Marunouchi, Chiyoda-ku 100-0005", "東京都千代田区丸の内"),
    ("Kenji Fukushima, 4-21-27 Setagaya, Setagaya-ku 154-0017", "東京都世田谷区世田谷"),
    ("c/o Nagano, 1-1-1 Kamata, Ota-ku 144-0052", "東京都大田区蒲田"),
    ("Hanako Ishikawa, 3-1-1 Umeda, Kita-ku, Osaka 530-0001", "大阪府大阪市北区梅田"),
    ("Taro Yamaguchi, 1-2-3 Matsumotocho, Imabari-shi 794-0041", "愛媛県今治市松本町"),
    ("Hanako Yamaguchi, 1-2-3 Higashihamacho, Fukushima 960-8132", "福島県福島市東浜町"),
    ("Taro Miyazaki, 1-2-3 Matsumotocho, Imabari-shi", "愛媛県今治市松本町"),
])
def test_r3_44_name_that_is_a_prefecture_does_not_drop_the_town(jp, text, prefix):
    assert _romaji(jp, text)[0] == prefix


def test_r3_44_prefecture_in_the_prefecture_position_still_excludes(jp):
    places = lambda text: list(dict.fromkeys(r.place for r in jp.romaji_places(text)))  # noqa: E731
    assert places("Taro Yamaguchi, 2-8-1 Nishishinjuku, Shinjuku-ku, Yamaguchi-ken") == []
    assert places("2-8-1 Nishishinjuku, Shinjuku-ku, Osaka") == []
    assert places("2-8-1 Nishishinjuku, Shinjuku-ku, Tokyo") == ["東京都新宿区西新宿"]
    assert japan._pref_written(japan.romaji_tokens("Taro Yamaguchi Umeda Kita ku Osaka")) == {5}
    assert japan._pref_written(japan.romaji_tokens("Tokyo-to Shinjuku-ku Nishishinjuku")) == {0}
    assert japan._pref_written(japan.romaji_tokens("Umeda Kita-ku Osaka-shi")) == set()


def test_r3_44_verifies_with_the_postcode(jp):
    gsi = _Gsi({"東京都新宿区西新宿2-8-1": ["東京都新宿区西新宿二丁目８番"],
                "愛媛県今治市松本町1-2-3": ["愛媛県今治市松本町一丁目２番"]})
    res = _verify(gsi, jp, "Taro Yamaguchi, 2-8-1 Nishi-Shinjuku, Shinjuku-ku 163-8001")
    assert (res["status"], res["level"], res["zip_match"]) == ("verified", "block", True)
    res = _verify(gsi, jp, "Taro Yamaguchi, 1-2-3 Matsumotocho, Imabari-shi 794-0041")
    assert (res["status"], res["best"].raw, res["zip_match"]) == ("verified", "愛媛県今治市松本町一丁目２番", True)


# ---------------------------------------------------------------------------------------------- S24

@pytest.mark.parametrize("text, town", [
    ("2-24 Zzyzxcho, Fuchu-shi, Tokyo 183-0055", "東京都府中市府中町"),
    ("2-8-1 Nishishinjyuku, Shinjuku-ku, Tokyo 160-0022", "東京都新宿区新宿"),
    ("1-2-3 Nihonbasi, Chuo-ku, Tokyo 104-0061", "東京都中央区銀座"),
    ("1-1-1 Nihonbasi, Chuo-ku, Tokyo 104-0061", "東京都中央区銀座"),
])
def test_r3_s24_unknown_town_is_not_replaced_by_the_postcodes(jp, text, town):
    prefix, notes = _romaji(jp, text)
    assert prefix == "" and any(f"is for {town}: check the town" in n for n in notes)
    gsi = _Gsi({f"{town}{n}": [f"{town}二丁目８番１号"] for n in ("2-24", "2-8-1", "1-2-3", "1-1-1")})
    res = _verify(gsi, jp, text)
    assert res["status"] == "geocode_failed" and res["zip_match"] is None and not gsi.asked


def test_r3_s24_town_written_and_its_postcode_agree(jp):
    # no city, but the postcode's town is the one written: read (also [41])
    assert _romaji(jp, "2-8-1 Nishishinjuku, Tokyo 160-0023")[0] == "東京都新宿区西新宿"


# --------------------------------------------------------------------------------------------- [41]

@pytest.mark.parametrize("text, prefix, query, title", [
    ("3-1-1 Umeda, Kita-ku 530-0001", "大阪府大阪市北区梅田", "大阪府大阪市北区梅田3-1-1", "大阪府大阪市北区梅田三丁目１番"),
    ("1-1-2 Oshiage 131-0045", "東京都墨田区押上", "東京都墨田区押上1-1-2", "東京都墨田区押上一丁目１番２号"),
])
def test_r3_41_romaji_with_the_postcode_and_no_city(jp, text, prefix, query, title):
    assert _romaji(jp, text)[0] == prefix
    res = _verify(_Gsi({query: [title]}), jp, text)
    assert (res["status"], res["zip_match"]) == ("verified", True)


def test_r3_41_written_ward_still_contradicts(jp):
    assert _romaji(jp, "3-1-1 Umeda, Chuo-ku 530-0001")[0] == ""  # 梅田 is in 北区


# ---------------------------------------------------------------------------------------- [38] / [46]

@pytest.mark.parametrize("text, pc", [
    ("邮编163-8001 東京都新宿区西新宿2-8-1", "1638001"),
    ("郵便番号163-8001 東京都新宿区西新宿2-8-1", "1638001"),
    ("东京都新宿区西新宿2-8-1 邮编163-8001", "1638001"),
    ("ZIP:163-8001 東京都新宿区西新宿2-8-1", "1638001"),
    ("日本163-8001東京都新宿区西新宿2-8-1", "1638001"),
    ("東京都新宿区西新宿2-8-1【163-8001】", "1638001"),
    ("東京都新宿区西新宿2-8-1/163-8001", "1638001"),
    ("東京都新宿区西新宿2-8-1;163-8001", "1638001"),
    ("邮编163-8001 东京都新宿区西新宿2-8-1", "1638001"),
    ("日本163-8001东京都新宿区西新宿2-8-1", "1638001"),
    ("东京都新宿区西新宿2-8-1 邮编160-0022", "1600022"),
    ("东京都新宿区西新宿2-8-1 邮编100-0005", "1000005"),
    ("郵便番号100-0005 東京都新宿区西新宿2-8-1", "1000005"),
    ("ZIP:100-0005 東京都新宿区西新宿2-8-1", "1000005"),
    ("163-8001東京都新宿区西新宿2-8-1", "1638001"),
    ("〒163-8001 東京都新宿区西新宿2-8-1", "1638001"),
])
def test_r3_46_postcode_after_a_label_or_text(text, pc):
    a = japan.parse_jp(text)
    assert (a.postcode, a.nums, a.rest, a.read_as) == (pc, [2, 8, 1], "", "")
    assert a.prefix.endswith("都新宿区西新宿")


def test_r3_46_parcel_glued_to_the_town_is_not_a_postcode():
    a = japan.parse_jp("茨城県水戸市笠原町978-1234")
    assert (a.postcode, a.prefix, a.nums) == ("", "茨城県水戸市笠原町", [978, 1234])


def test_r3_46_romaji_postcode_in_brackets(jp):
    a = japan.parse_jp("1-1 Kita 1-jo Nishi 2-chome, Chuo-ku, Sapporo [060-0001]")
    assert a.postcode == "0600001"
    japan._from_romaji(jp, a, [])
    assert (a.prefix, a.nums) == ("北海道札幌市中央区北一条西", [2, 1, 1])


@pytest.mark.parametrize("text, where", [
    ("东京都新宿区西新宿2-8-1 邮编100-0005", "100-0005 is for 東京都千代田区丸の内"),
    ("郵便番号100-0005 東京都新宿区西新宿2-8-1", "100-0005 is for 東京都千代田区丸の内"),
    ("ZIP:100-0005 東京都新宿区西新宿2-8-1", "100-0005 is for 東京都千代田区丸の内"),
    ("东京都新宿区西新宿2-8-1 邮编160-0022", "160-0022 is for 東京都新宿区新宿"),
])
def test_r3_38_wrong_postcode_after_a_label_is_reported(jp, text, where):
    gsi = _Gsi({"東京都新宿区西新宿2-8-1": ["東京都新宿区西新宿二丁目８番"]})
    res = _verify(gsi, jp, text)
    assert (res["status"], res["zip_match"]) == ("verified", False)
    assert any(n.startswith(f"postcode {where}") for n in res["notes"])


@pytest.mark.parametrize("text", ["邮编163-8001 东京都新宿区西新宿2-8-1", "日本163-8001东京都新宿区西新宿2-8-1",
                                  "东京都新宿区西新宿2-8-1 邮编163-8001",
                                  "收件人: 张三 电话: 090-1234-5678 地址: 日本东京都新宿区西新宿2-8-1 邮编: 163-8001"])
def test_r3_38_postcode_after_chinese_text_matches(jp, text):
    gsi = _Gsi({"東京都新宿区西新宿2-8-1": ["東京都新宿区西新宿二丁目８番"]})
    res = _verify(gsi, jp, text)
    assert (res["status"], res["zip_match"]) == ("verified", True)


# --------------------------------------------------------------------------------------------- [39]

@pytest.mark.parametrize("text, prefix, nums, name", [
    ("中村太郎様 東京都 新宿区 西新宿2-8-1", "東京都新宿区西新宿", [2, 8, 1], "中村太郎様"),
    ("町田商事 大阪府 大阪市北区梅田3-1-1", "大阪府大阪市北区梅田", [3, 1, 1], "町田商事"),
    ("木村花子 東京都 新宿区西新宿2-8-1", "東京都新宿区西新宿", [2, 8, 1], "木村花子"),
    ("田村 京都府 京都市中京区上本能寺前町488", "京都府京都市中京区上本能寺前町", [488], "田村"),
    ("中村 神奈川県 横浜市中区本町6-50-10", "神奈川県横浜市中区本町", [6, 50, 10], "中村"),
    ("中村太郎 東京都　新宿区西新宿2-8-1", "東京都新宿区西新宿", [2, 8, 1], "中村太郎"),
    ("中村太郎 東京都新宿区西新宿2-8-1", "東京都新宿区西新宿", [2, 8, 1], "中村太郎"),
])
def test_r3_39_name_before_the_prefecture_and_a_space(text, prefix, nums, name):
    a = japan.parse_jp(text)
    assert (a.prefix, a.nums, a.read_as) == (prefix, nums, name)


def test_r3_39_floor_before_the_prefecture_is_the_building():  # the floor of the building, not a name to drop
    a = japan.parse_jp("5F 東京都 新宿区西新宿2-8-1")
    assert (a.prefix, a.nums, a.rest, a.read_as) == ("東京都新宿区西新宿", [2, 8, 1], "5F", "")


def test_r3_39_building_after_the_address_stays():
    a = japan.parse_jp("新宿区西新宿2-8-1 東京都庁第一本庁舎")
    assert (a.prefix, a.rest) == ("新宿区西新宿", "東京都庁第一本庁舎")


# --------------------------------------------------------------------------------------------- [40]

@pytest.mark.parametrize("street, city, query, title", [
    ("本町3-1-1", "大阪府大阪市中央区", "大阪府大阪市中央区本町3-1-1", "大阪府大阪市中央区本町三丁目１番１号"),
    ("本町6-50-10", "神奈川県横浜市中区", "神奈川県横浜市中区本町6-50-10", "神奈川県横浜市中区本町六丁目５０番地"),
    ("北1条西2丁目1-1", "北海道札幌市中央区", "北海道札幌市中央区北一条西2丁目1-1", "北海道札幌市中央区北一条西二丁目１番地"),
])
def test_r3_40_city_cell_with_prefecture_city_and_ward(jp, street, city, query, title):
    text = f"{street}, {city}"  # core joins street, unit and city for JP
    a = japan.parse_jp(text)
    assert (a.hints, a.rest) == ([city], "")
    res = _verify(_Gsi({query: [title]}), jp, text)
    assert res["status"] == "verified" and res["best"].raw == title


# --------------------------------------------------------------------------------------------- [45]

@pytest.mark.parametrize("text, nums", [
    ("4-21-27 Setagaya 4-chome, Setagaya-ku, Tokyo 154-0017", [4, 21, 27]),
    ("2-8-1 Nishishinjuku 2-chome, Shinjuku-ku, Tokyo 163-8001", [2, 8, 1]),
    ("1-9-1 Marunouchi 1-chome, Chiyoda-ku, Tokyo 100-0005", [1, 9, 1]),
    ("8-1 Nishishinjuku 2-chome, Shinjuku-ku, Tokyo 163-8001", [2, 8, 1]),
    ("Nishishinjuku 2-chome 8-1, Shinjuku-ku, Tokyo 163-8001", [2, 8, 1]),
    ("1-1 Kita 1 Jonishi 2 Chome, Chuo Ward, Sapporo, Hokkaido 060-0001", [2, 1, 1]),
])
def test_r3_45_chome_written_twice(text, nums):
    assert japan.parse_jp(text).nums == nums


def test_r3_45_two_different_chome_are_refused():
    a = japan.parse_jp("4-21-27 Setagaya 3-chome, Setagaya-ku, Tokyo 154-0017")
    assert a.nums == [] and any("written twice" in w for w in a.warnings)


# --------------------------------------------------------------------------------------- [63] / R39

@pytest.mark.parametrize("text, read_as", [
    ("東京都新宿区西新宿2-8-1 03 5321 1111", "03 5321 1111"),
    ("東京都新宿区西新宿2-8-1 090 1234 5678", "090 1234 5678"),
])
def test_r3_63_phone_with_spaces_is_not_the_unit(text, read_as):
    a = japan.parse_jp(text)
    assert (a.prefix, a.nums, a.rest, a.read_as) == ("東京都新宿区西新宿", [2, 8, 1], "", read_as)


@pytest.mark.parametrize("text, nums, rest", [
    ("東京都江東区豊洲2-2-1001", [2, 2], "1001"),
    ("長野県北佐久郡軽井沢町長倉2381-1-1203", [2381, 1], "1203"),
    ("東京都江東区豊洲2丁目2-1001", [2, 2], "1001"),
    ("東京都新宿区西新宿2-8-1", [2, 8, 1], ""),         # a 号 stays
    ("東京都新宿区西新宿2丁目8番101号", [2, 8, 101], ""),  # written as 号
    ("京都府京都市下京区東塩小路町721-1 1203", [721, 1], "1203"),
    ("沖縄県那覇市泉崎1-2 0123", [1, 2], "0123"),
])
def test_r3_r39_room_as_the_third_dash_group(text, nums, rest):
    a = japan.parse_jp(text)
    assert (a.nums, a.rest, a.read_as) == (nums, rest, "")


def test_r3_r39_room_does_not_fail_the_block(jp):
    gsi = _Gsi({"東京都江東区豊洲2-2": ["東京都江東区豊洲二丁目２番"], "東京都江東区豊洲2丁目2-1": ["東京都江東区豊洲二丁目２番１号"]})
    res = _verify(gsi, jp, "東京都江東区豊洲2-2-1001")
    assert (res["status"], res["level"], res["jp"].rest) == ("verified", "block", "1001")


# ---------------------------------------------------------------------------------------- S23 / S30

@pytest.mark.parametrize("text, prefix, read_as", [
    ("日本大阪市北区梅田3-1-1", "大阪市北区梅田", ""),
    ("日本国大阪市北区梅田3-1-1", "大阪市北区梅田", ""),
    ("日本新宿区西新宿2-8-1", "新宿区西新宿", ""),
    ("日本 大阪市北区梅田3-1-1", "大阪市北区梅田", ""),
    ("東京都中央区日本橋1-1-1", "東京都中央区日本橋", ""),  # a town that starts with 日本
    ("中村太郎 新宿区西新宿2-8-1", "新宿区西新宿", "中村太郎"),
    ("中村 新宿区西新宿2-8-1", "新宿区西新宿", "中村"),
    ("大阪市 北区梅田3-1-1", "大阪市北区梅田", ""),      # a city before the ward is not a name
    ("北佐久郡 軽井沢町長倉2381", "北佐久郡軽井沢町長倉", ""),
    ("收货人：王先生 地址：日本东京都墨田区押上1-1-2", "东京都墨田区押上", "王先生"),
    ("收货人：王先生 手机：13800138000 地址：日本东京都墨田区押上1-1-2", "东京都墨田区押上", "手机:13800138000; 王先生"),
])
def test_r3_s23_s30_country_name_and_contact_cut(text, prefix, read_as):
    a = japan.parse_jp(text)
    assert (a.prefix, a.read_as) == (prefix, read_as) and a.nums


# ---------------------------------------------------------------------------------------- S28 / S32

def test_r3_s28_sapporo_jo_number_picks_the_town(jp):
    assert _romaji(jp, "Kita 5-jo Nishi 2-chome 5, Chuo-ku, Sapporo")[0] == "北海道札幌市中央区北五条西"
    assert _romaji(jp, "1-1 Kita 1-jo Nishi 2-chome, Chuo-ku, Sapporo")[0] == "北海道札幌市中央区北一条西"
    prefix, notes = _romaji(jp, "1-1 Kitajonishi 2-chome, Chuo-ku, Sapporo")  # no number: still several
    assert prefix == "" and any("several places" in n for n in notes)


def test_r3_s32_business_postcode_written_with_digits(jp):
    assert [(r.town, r.business) for r in jp.rows("0608602")] == [("北1条西", "さっぽろ創生スクエア")]
    a = japan.parse_jp("1-6 Kita 1-jo Nishi 1-chome, Chuo-ku, Sapporo 060-8602")
    japan._from_romaji(jp, a, [])
    assert (a.prefix, a.nums) == ("北海道札幌市中央区北一条西", [1, 1, 6])


# ---------------------------------------------------------------------------------------------- S38

def test_r3_s38_hokkaido_grid_town_keeps_its_go():
    a = japan.parse_jp("北海道富良野市東1線北19号123")
    assert (a.prefix, a.nums) == ("北海道富良野市東1線北19号", [123])


# ---------------------------------------------------------------------------------------------- R44

def test_r3_r44_romaji_city_field(jp):
    assert jp.romaji_city("Shinjuku-ku") == "東京都新宿区"
    assert jp.romaji_city("Chuo-ku") == "" and jp.romaji_city("Shinjuku") == ""  # several wards / no kind
    gsi = _Gsi({"東京都新宿区西新宿2-8-1": ["東京都新宿区西新宿二丁目８番"]})
    for kw in ({"text": "西新宿2-8-1", "city": "Shinjuku-ku"}, {"text": "西新宿2-8-1, Shinjuku-ku"}):
        res = _verify(gsi, jp, **kw)
        assert (res["status"], res["best"].raw) == ("verified", "東京都新宿区西新宿二丁目８番"), kw
    assert _verify(gsi, jp, "西新宿2-8-1")["status"] == "geocode_failed"  # 3 places: still refused
