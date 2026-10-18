"""Japan: the final audit's items [19]-[21], [26]. Offline: GSI answers are canned, Japan Post's tables are written into
tmp_path with each reproduction's postcode and the towns next to it."""

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


# (postcode, prefecture, city, town, KEN_ALL_ROME prefecture, city, town)
TOWNS = [
    ("2310005", "神奈川県", "横浜市中区", "本町", "KANAGAWA KEN", "YOKOHAMA SHI NAKA KU", "HONCHO"),
    ("2310006", "神奈川県", "横浜市中区", "南仲通", "KANAGAWA KEN", "YOKOHAMA SHI NAKA KU", "MINAMINAKADORI"),
    ("2310861", "神奈川県", "横浜市中区", "元町", "KANAGAWA KEN", "YOKOHAMA SHI NAKA KU", "MOTOMACHI"),
    ("1300013", "東京都", "墨田区", "錦糸", "TOKYO TO", "SUMIDA KU", "KINSHI"),
    ("1300012", "東京都", "墨田区", "太平", "TOKYO TO", "SUMIDA KU", "TAIHEI"),
    ("1310045", "東京都", "墨田区", "押上", "TOKYO TO", "SUMIDA KU", "OSHIAGE"),
    ("1600022", "東京都", "新宿区", "新宿", "TOKYO TO", "SHINJUKU KU", "SHINJUKU"),
    ("1600023", "東京都", "新宿区", "西新宿（次のビルを除く）", "TOKYO TO", "SHINJUKU KU", "NISHISHINJUKU (TSUGINOBIRUONOZOKU)"),
    ("1050013", "東京都", "港区", "浜松町", "TOKYO TO", "MINATO KU", "HAMAMATSUCHO"),
    ("1080023", "東京都", "港区", "芝浦", "TOKYO TO", "MINATO KU", "SHIBAURA"),
    ("1000004", "東京都", "千代田区", "大手町", "TOKYO TO", "CHIYODA KU", "OTEMACHI"),
    ("1000005", "東京都", "千代田区", "丸の内（次のビルを除く）", "TOKYO TO", "CHIYODA KU", "MARUNOUCHI (TSUGINOBIRUONOZOKU)"),
    ("1000014", "東京都", "千代田区", "永田町", "TOKYO TO", "CHIYODA KU", "NAGATACHO"),
    ("1010051", "東京都", "千代田区", "神田神保町", "TOKYO TO", "CHIYODA KU", "KANDAJIMBOCHO"),
    ("1040045", "東京都", "中央区", "築地", "TOKYO TO", "CHUO KU", "TSUKIJI"),
    ("1040061", "東京都", "中央区", "銀座", "TOKYO TO", "CHUO KU", "GINZA"),
    ("1030027", "東京都", "中央区", "日本橋", "TOKYO TO", "CHUO KU", "NIHOMBASHI"),
    ("1350061", "東京都", "江東区", "豊洲", "TOKYO TO", "KOTO KU", "TOYOSU"),
    ("1540017", "東京都", "世田谷区", "世田谷", "TOKYO TO", "SETAGAYA KU", "SETAGAYA"),
    ("1830055", "東京都", "府中市", "府中町", "TOKYO TO", "FUCHU SHI", "FUCHUCHO"),
    ("1830027", "東京都", "府中市", "本町", "TOKYO TO", "FUCHU SHI", "HONMACHI"),
    ("7350006", "広島県", "安芸郡府中町", "本町", "HIROSHIMA KEN", "AKI GUN FUCHU CHO", "HONMACHI"),
    ("3890111", "長野県", "北佐久郡軽井沢町", "長倉", "NAGANO KEN", "KITASAKU GUN KARUIZAWA MACHI", "NAGAKURA"),
    ("3890102", "長野県", "北佐久郡軽井沢町", "軽井沢", "NAGANO KEN", "KITASAKU GUN KARUIZAWA MACHI", "KARUIZAWA"),
    ("2600026", "千葉県", "千葉市中央区", "千葉港", "CHIBA KEN", "CHIBA SHI CHUO KU", "CHIBAMINATO"),
    ("5300001", "大阪府", "大阪市北区", "梅田", "OSAKA FU", "OSAKA SHI KITA KU", "UMEDA"),
    ("7940041", "愛媛県", "今治市", "松本町", "EHIME KEN", "IMABARI SHI", "MATSUMOTOCHO"),
    ("7991602", "愛媛県", "今治市", "山口", "EHIME KEN", "IMABARI SHI", "YAMAGUCHI"),
    ("9608132", "福島県", "福島市", "東浜町", "FUKUSHIMA KEN", "FUKUSHIMA SHI", "HIGASHIHAMACHO"),
    ("9608202", "福島県", "福島市", "山口", "FUKUSHIMA KEN", "FUKUSHIMA SHI", "YAMAGUCHI"),
    ("7530084", "山口県", "山口市", "諸願小路", "YAMAGUCHI KEN", "YAMAGUCHI SHI", "SHOGANSHOJI"),
    ("6320005", "奈良県", "天理市", "楢町", "NARA KEN", "TENRI SHI", "NARACHO"),
    ("9250616", "石川県", "羽咋市", "四町", "ISHIKAWA KEN", "HAKUI SHI", "SHICHO"),
    ("0983212", "北海道", "天塩郡幌延町", "二条南", "HOKKAIDO", "TESHIO GUN HORONOBE CHO", "2-JOMINAMI"),
    ("0983213", "北海道", "天塩郡幌延町", "三条南", "HOKKAIDO", "TESHIO GUN HORONOBE CHO", "3-JOMINAMI"),
    ("7690204", "香川県", "綾歌郡宇多津町", "浜四番丁", "KAGAWA KEN", "AYAUTA GUN UTAZU CHO", "HAMA4-BANCHO"),
    ("7690208", "香川県", "綾歌郡宇多津町", "浜八番丁", "KAGAWA KEN", "AYAUTA GUN UTAZU CHO", "HAMA8-BANCHO"),
    ("8450001", "佐賀県", "小城市", "小城町", "SAGA KEN", "OGI SHI", "OGIMACHI"),
    ("0070841", "北海道", "札幌市東区", "北四十一条東", "HOKKAIDO", "SAPPORO SHI HIGASHI KU", "KITA41-JOHIGASHI"),
    ("0070848", "北海道", "札幌市東区", "北四十八条東", "HOKKAIDO", "SAPPORO SHI HIGASHI KU", "KITA48-JOHIGASHI"),
    ("0800033", "北海道", "帯広市", "西三条北", "HOKKAIDO", "OBIHIRO SHI", "NISHI3-JOKITA"),
    ("0800037", "北海道", "帯広市", "西七条北", "HOKKAIDO", "OBIHIRO SHI", "NISHI7-JOKITA"),
    ("0740004", "北海道", "深川市", "四条", "HOKKAIDO", "FUKAGAWA SHI", "4-JO"),
    ("0740009", "北海道", "深川市", "九条", "HOKKAIDO", "FUKAGAWA SHI", "9-JO"),
]
BUSINESSES = [("東京都庁", "東京都", "新宿区", "西新宿", "２丁目８－１", "1638001")]


@pytest.fixture
def jp(tmp_path):
    d = tmp_path / "japanpost"
    d.mkdir()
    _zip(d / "utf_ken_all.zip", "utf_ken_all.csv",
         [["00000", pc[:3], pc, "", "", "", pref, city, town, *FLAGS] for pc, pref, city, town, *_ in TOWNS], "utf-8")
    rome = [[pc, pref, city.replace("郡", "郡　").replace("市", "市　", 1) if city.endswith("区") or "郡" in city else city,
             town, pref_r, city_r, town_r] for pc, pref, city, town, pref_r, city_r, town_r in TOWNS]
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


# --------------------------------------------------------------------------------------------- [19]

SPELT = [  # the postcode's town written another way, with its city: read as that town
    ("1-1-1 Honmachi, Naka-ku, Yokohama 231-0005", "神奈川県横浜市中区本町"),
    ("1-1-1 Kinshi-cho, Sumida-ku, Tokyo 130-0013", "東京都墨田区錦糸"),
    ("2-8-1 Nishishinjyuku, Shinjuku-ku, Tokyo 160-0023", "東京都新宿区西新宿"),
    ("1-1-1 Hamamatsu-machi, Minato-ku, Tokyo 105-0013", "東京都港区浜松町"),
    ("1-1-1 Hamamatsu, Minato-ku, Tokyo 105-0013", "東京都港区浜松町"),
    ("1-1-1 Otecho, Chiyoda-ku 100-0004", "東京都千代田区大手町"),
    ("1-2-3 Kanda Jinbo, Chiyoda-ku 101-0051", "東京都千代田区神田神保町"),
    ("1-1 Nagata, Chiyoda-ku 100-0014", "東京都千代田区永田町"),
    ("1-1-1 Tukiji, Chuo-ku 104-0045", "東京都中央区築地"),
    ("1-1-1 Sibaura, Minato-ku 108-0023", "東京都港区芝浦"),
    ("3-1-1 Shinjyuku, Shinjuku-ku 160-0022", "東京都新宿区新宿"),
    ("1-1-1 Kinshicho, Sumida-ku, Tokyo 130-0013", "東京都墨田区錦糸"),
    ("1-1-1 Honmati, Naka-ku, Yokohama 231-0005", "神奈川県横浜市中区本町"),
    ("1-2-3 Fuchu-machi, Fuchu-shi, Tokyo 183-0055", "東京都府中市府中町"),  # the town named like its city, other kind
    ("1-2-3 Syogansyoji, Yamaguchi-shi, Yamaguchi 753-0084", "山口県山口市諸願小路"),
    ("1-2-3 3-jyominami, Teshio-gun, Horonobe-cho, Hokkaido 098-3213", "北海道天塩郡幌延町三条南"),  # its number
    ("1-2-3 Hama4-banmachi, Ayauta-gun, Utazu-cho, Kagawa 769-0204", "香川県綾歌郡宇多津町浜四番丁"),
    ("1-2-3 Ogi, Ogi-shi, Saga 845-0001", "佐賀県小城市小城町"),  # the town named like its city, written twice
]


@pytest.mark.parametrize("text, town", SPELT)
def test_r4_19_town_spelt_differently_with_its_postcode_is_read(jp, text, town):
    prefix, notes = _romaji(jp, text)
    assert prefix == town
    assert any(f"spelt differently in Japan Post's list: read as the postcode's town {town}" in n for n in notes)


@pytest.mark.parametrize("text, query, title", [
    ("1-1-1 Kinshi-cho, Sumida-ku, Tokyo 130-0013", "東京都墨田区錦糸1-1-1", "東京都墨田区錦糸一丁目１番１号"),
    ("2-8-1 Nishishinjyuku, Shinjuku-ku, Tokyo 160-0023", "東京都新宿区西新宿2-8-1", "東京都新宿区西新宿二丁目８番"),
    ("1-1-1 Tukiji, Chuo-ku 104-0045", "東京都中央区築地1-1-1", "東京都中央区築地一丁目１番１号"),
])
def test_r4_19_town_spelt_differently_is_verified_with_its_postcode(jp, text, query, title):
    res = _verify(_Gsi({query: [title]}), jp, text)
    assert (res["status"], res["best"].raw, res["zip_match"]) == ("verified", title, True)


@pytest.mark.parametrize("text, town", [
    # S24: a town that is not the postcode's, however it is spelt, is not replaced by the postcode's
    ("2-8-1 Nishishinjyuku, Shinjuku-ku, Tokyo 160-0022", "東京都新宿区新宿"),
    ("1-2-3 Nihonbasi, Chuo-ku, Tokyo 104-0061", "東京都中央区銀座"),
    ("2-24 Zzyzxcho, Fuchu-shi, Tokyo 183-0055", "東京都府中市府中町"),
    ("2381-1 Nagakurra, Karuizawa-machi, Nagano 389-0111", "長野県北佐久郡軽井沢町長倉"),  # no edit distance
    ("1-1-1 Honmachi, Naka-ku, Yokohama 231-0006", "神奈川県横浜市中区南仲通"),
    ("1-1-1 Kinsi-cho, Sumida-ku, Tokyo 130-0012", "東京都墨田区太平"),
    ("1-1-1 Kinshi-cho, Sumida-ku, Tokyo 131-0045", "東京都墨田区押上"),
    ("1-1-1 Tukiji, Chuo-ku 104-0061", "東京都中央区銀座"),
    ("1-1-1 Sibaura, Minato-ku 105-0013", "東京都港区浜松町"),
    ("1-1-1 Hamamatsu-machi, Minato-ku, Tokyo 108-0023", "東京都港区芝浦"),
    ("1-1-1 Otecho, Chiyoda-ku 100-0005", "東京都千代田区丸の内"),
    ("1-1-1 Marunouti, Chiyoda-ku 100-0004", "東京都千代田区大手町"),
    ("3-1-1 Shinjyuku, Shinjuku-ku 160-0023", "東京都新宿区西新宿"),
    ("1-2-3 Shinjuku-machi, Shinjuku-ku 160-0023", "東京都新宿区西新宿"),
    ("1-1-1 Sumida-cho, Sumida-ku, Tokyo 130-0013", "東京都墨田区錦糸"),
    # the city written with -machi is not the postcode's town of the same name (軽井沢町軽井沢)
    ("1-2-3 Typo, Karuizawa-machi, Nagano 389-0102", "長野県北佐久郡軽井沢町軽井沢"),
    ("1-2-3 Karuizawa-machi, Nagano 389-0102", "長野県北佐久郡軽井沢町軽井沢"),
    # nor a prefecture, a city or a suffix written ('Nara' for 楢町, 'SHI' for 四町), nor a town with another number
    ("1-2-3 Tainosyocho, Tenri-shi, Nara 632-0005", "奈良県天理市楢町"),
    ("1-2-3 Kamaya, Hakui-shi, Ishikawa 925-0616", "石川県羽咋市四町"),
    ("1-2-3 Typo, Ogi, Saga 845-0001", "佐賀県小城市小城町"),
    ("1-2-3 5-jyominami, Teshio-gun, Horonobe-cho, Hokkaido 098-3212", "北海道天塩郡幌延町二条南"),
    ("1-2-3 3-jyominami, Teshio-gun, Horonobe-cho, Hokkaido 098-3212", "北海道天塩郡幌延町二条南"),
    ("1-2-3 Hama4-banmachi, Ayauta-gun, Utazu-cho, Kagawa 769-0208", "香川県綾歌郡宇多津町浜八番丁"),
])
def test_r4_19_another_town_with_the_postcode_is_still_refused(jp, text, town):
    prefix, notes = _romaji(jp, text)
    assert prefix == "" and any(f"is for {town}: check the town" in n for n in notes)
    res = _verify(_Gsi({}), jp, text)
    assert res["status"] == "geocode_failed" and res["zip_match"] is None


@pytest.mark.parametrize("text", ["1-1-1 Tukiji 104-0045", "2-8-1 Nishishinjyuku, Tokyo 160-0023",
                                  "2-8-1 Nishishinjyuku, Shibuya-ku, Tokyo 160-0023"])
def test_r4_19_spelling_without_its_city_is_not_read(jp, text):
    assert _romaji(jp, text)[0] == ""  # as in round 3: only with the postcode's city written


@pytest.mark.xfail(strict=True, reason="港 read 'ko' vs Japan Post's CHIBAMINATO: another reading, not a spelling")
def test_r4_19_chiba_ko_reading(jp):
    assert _romaji(jp, "1-1 Chiba-ko, Chuo-ku, Chiba 260-0026")[0] == "千葉県千葉市中央区千葉港"


def test_r4_19_loose_spelling():
    loose = japan._romaji_loose
    assert loose("TUKIJI") == loose("TSUKIJI") == "TSUKIJI"
    assert loose("NISHISHINJYUKU") == loose("NISHISHINJUKU")
    assert loose("SIBAURA") == loose("SHIBAURA") and loose("HUKUOKA") == loose("FUKUOKA")
    assert loose("HONMACHI") == loose("HONCHO") == loose("HOMCHO") == loose("HONMATI") == "HON"
    assert loose("SYOGANSYOJI") == loose("SHOGANSHOJI") and loose("ZIZO") == loose("JIZO")
    assert loose("SHUKUGAWA") == "SHUKUGAWA" and loose("CHUO") == "CHUO"  # SHU / CHU are not HU
    assert loose("NISHISHINJYUKU") != loose("SHINJUKU") and loose("NIHONBASI") != loose("GINZA")
    assert loose("MACHI") == "MACHI"  # a town named only 町 keeps its name


def test_r4_19_town_found_in_japan_post_is_unchanged(jp):
    prefix, notes = _romaji(jp, "1-1-1 Motomachi, Naka-ku, Yokohama 231-0005")  # another real town: the postcode check
    assert prefix == "神奈川県横浜市中区元町" and not any("spelt differently" in n for n in notes)
    prefix, notes = _romaji(jp, "1-1-1 Honcho, Naka-ku, Yokohama 231-0005")
    assert prefix == "神奈川県横浜市中区本町" and not any("spelt differently" in n for n in notes)


@pytest.mark.parametrize("text, town", [
    # a numbered town with a wrong postcode: the number written decides, not the postcode's sibling (romaji_places
    # compares the letters only: KITAJOHIGASHI is every 北N条東). Read as the written town (the postcode check then
    # says zip False) or, when the letters alone match several, refused
    ("1-2-3 Kita48-johigashi, Sapporo-shi, Higashi-ku, Hokkaido 007-0841", ""),  # r4/base: 北四十一条東, zip True
    ("1-2-3 Nishi7-jokita, Obihiro-shi, Hokkaido 080-0033", ""),                  # r4/base: 西三条北, zip True
    ("1-2-3 4-jo, Fukagawa-shi, Hokkaido 074-0009", "北海道深川市四条"),            # r4/base: 九条, zip True
    # with the right number: the postcode's
    ("1-2-3 Kita41-johigashi, Sapporo-shi, Higashi-ku, Hokkaido 007-0841", "北海道札幌市東区北四十一条東"),
    ("1-2-3 Kita48-johigashi, Sapporo-shi, Higashi-ku, Hokkaido 007-0848", "北海道札幌市東区北四十八条東"),
    ("1-2-3 Nishi3-jokita, Obihiro-shi, Hokkaido 080-0033", "北海道帯広市西三条北"),
    ("1-2-3 9-jo, Fukagawa-shi, Hokkaido 074-0009", "北海道深川市九条"),
    ("1-2-3 Kita 41-jo Higashi, Sapporo-shi, Higashi-ku 007-0841", "北海道札幌市東区北四十一条東"),
])
def test_r4_19_numbered_town_is_not_the_postcodes_sibling(jp, text, town):
    assert _romaji(jp, text)[0] == town


# --------------------------------------------------------------------------------------------- [20]

@pytest.mark.parametrize("text, nums, rest", [
    ("8-1-1203 Nishishinjuku 2-chome, Shinjuku-ku, Tokyo 160-0023", [2, 8, 1], "1203"),
    ("8-1-1203 Nishishinjuku 2-chome, Shinjuku-ku, Tokyo", [2, 8, 1], "1203"),
    ("2-2-1001 Toyosu, Koto-ku, Tokyo 135-0061", [2, 2], "1001"),
    ("2-2-1001 Toyosu, Koto-ku, Tokyo", [2, 2], "1001"),
    ("2381-1-1203 Nagakura, Karuizawa-machi, Kitasaku-gun, Nagano 389-0111", [2381, 1], "1203"),
    ("2-8-1-1203 Nishishinjuku, Shinjuku-ku, Tokyo 160-0023", [2, 8, 1], "1203"),  # a fourth part: _cap, unchanged
    ("2-8-1 Nishishinjuku 2-chome, Shinjuku-ku, Tokyo 163-8001", [2, 8, 1], ""),
    ("8-1 Nishishinjuku 2-chome, Shinjuku-ku, Tokyo 163-8001", [2, 8, 1], ""),
    ("1-12-99 Dogenzaka, Shibuya-ku, Tokyo 150-0043", [1, 12, 99], ""),  # under 100: a 号
])
def test_r4_20_romaji_room_after_block_and_number(text, nums, rest):
    a = japan.parse_jp(text)
    assert (a.nums, a.rest) == (nums, rest) and not a.warnings


@pytest.mark.parametrize("text", [
    "4-21-27 Setagaya 3-chome, Setagaya-ku, Tokyo 154-0017",
    "8-1-99 Nishishinjuku 2-chome, Shinjuku-ku, Tokyo 160-0023",
])
def test_r4_20_two_different_chome_still_refused(text):
    a = japan.parse_jp(text)
    assert a.nums == [] and any("written twice" in w for w in a.warnings)


@pytest.mark.parametrize("text, query, title, zip_match", [
    ("8-1-1203 Nishishinjuku 2-chome, Shinjuku-ku, Tokyo 160-0023", "東京都新宿区西新宿2丁目8-1", "東京都新宿区西新宿二丁目８番", True),
    ("8-1-1203 Nishishinjuku 2-chome, Shinjuku-ku, Tokyo", "東京都新宿区西新宿2丁目8-1", "東京都新宿区西新宿二丁目８番", None),
    ("2-2-1001 Toyosu, Koto-ku, Tokyo 135-0061", "東京都江東区豊洲2-2", "東京都江東区豊洲二丁目２番", True),
])
def test_r4_20_romaji_room_is_verified_at_the_block(jp, text, query, title, zip_match):
    res = _verify(_Gsi({query: [title]}), jp, text)
    assert (res["status"], res["level"], res["best"].raw, res["zip_match"]) == ("verified", "block", title, zip_match)
    assert res["jp"].rest.startswith(("1203", "1001"))


# --------------------------------------------------------------------------------------------- [21]

@pytest.mark.parametrize("text, query, title", [
    ("2-8-1 Nishishinjuku, Shinjuku-ku, Tokyo 163-8001 Taro Yamaguchi", "東京都新宿区西新宿2-8-1", "東京都新宿区西新宿二丁目８番"),
    ("1-1-2 Oshiage, Sumida-ku, Tokyo 131-0045 Kenji Fukushima", "東京都墨田区押上1-1-2", "東京都墨田区押上一丁目１番２号"),
    ("1-1-2 Oshiage, Sumida-ku, Tokyo 131-0045 Wang Fang", "東京都墨田区押上1-1-2", "東京都墨田区押上一丁目１番２号"),
    ("1-1-2 Oshiage, Sumida-ku, Tokyo, 131-0045, Kenji Fukushima", "東京都墨田区押上1-1-2", "東京都墨田区押上一丁目１番２号"),
    ("1-1-2 Oshiage, Sumida-ku, Tokyo ZIP 131-0045 Kenji Fukushima", "東京都墨田区押上1-1-2", "東京都墨田区押上一丁目１番２号"),
    ("3-1-1 Umeda, Kita-ku, Osaka 530-0001 Hanako Chiba", "大阪府大阪市北区梅田3-1-1", "大阪府大阪市北区梅田三丁目１番"),
    ("1-2-3 Higashihamacho, Fukushima 960-8132 Hanako Yamaguchi", "福島県福島市東浜町1-2-3", "福島県福島市東浜町１番２号"),
    ("1-2-3 Matsumotocho, Imabari-shi, Ehime 794-0041 Taro Yamaguchi", "愛媛県今治市松本町1-2-3", "愛媛県今治市松本町一丁目２番"),
])
def test_r4_21_prefecture_named_person_after_the_postcode(jp, text, query, title):
    res = _verify(_Gsi({query: [title]}), jp, text)
    assert (res["status"], res["best"].raw, res["zip_match"]) == ("verified", title, True)


def test_r4_21_one_word_after_the_postcode_is_still_the_prefecture(jp):
    # 'Fuchu-shi 183-0027 Hiroshima': the prefecture written after the postcode (Hiroshima has a 府中市 too)
    assert _romaji(jp, "1-2-3 Honmachi, Fuchu-shi 183-0027 Hiroshima")[0] == ""
    assert _romaji(jp, "1-2-3 Honmachi, Fuchu-shi 183-0027")[0] == "東京都府中市本町"
    assert _romaji(jp, "1-1-1 Honmachi, Fuchu-cho 735-0006 Hiroshima")[0] == "広島県安芸郡府中町本町"


@pytest.mark.parametrize("text, skip", [
    ("2-8-1 Nishishinjuku, Shinjuku-ku, Tokyo 163-8001 Taro Yamaguchi", range(4, 6)),
    ("1-1-2 Oshiage, Sumida-ku, Tokyo ZIP 131-0045 Kenji Fukushima", range(5, 7)),  # the label is not the name
    ("2-8-1 Nishishinjuku, Shinjuku-ku, Tokyo 163-8001", range(0)),
    ("2-8-1 Nishishinjuku, Shinjuku-ku, Tokyo 163-8001 Japan", range(0)),
    ("2-8-1 Nishishinjuku, Shinjuku-ku 163-8001 Tokyo-to", range(0)),
    ("2-8-1 Nishishinjuku, Shinjuku-ku 163-8001 Yamaguchi", range(0)),
    ("2-8-1 Nishishinjuku 160-0023, Shinjuku-ku, Tokyo", range(0)),
    ("〒163-8001 Tokyo-to Shinjuku-ku Nishishinjuku 2-8-1", range(0)),
    ("2-8-1 Nishishinjuku, Shinjuku-ku, Tokyo 163-8001 Taro Yamaguchi 090-1234-5678", range(0)),
])
def test_r4_21_name_after_postcode(text, skip):
    assert japan._name_after_postcode(japan.parse_jp(text)) == skip


# --------------------------------------------------------------------------------------------- [26]

def test_r4_26_romaji_city_indexed_once(jp):
    assert jp.romaji_city("Shinjuku-ku") == "東京都新宿区"
    index = jp._cities
    assert index[0] is jp.by_pc
    assert jp.romaji_city("Karuizawa-machi") == "長野県北佐久郡軽井沢町"
    assert jp.romaji_city("Kitasaku-gun Karuizawa-machi") == "長野県北佐久郡軽井沢町"
    assert jp.romaji_city("Kita-ku") == "大阪府大阪市北区"   # the only 北区 in these tables
    assert jp.romaji_city("Fuchu-cho") == "広島県安芸郡府中町" and jp.romaji_city("Fuchu-shi") == "東京都府中市"
    assert jp.romaji_city("Nonexistent-shi") == "" and jp.romaji_city("Shinjuku") == ""  # no kind written
    assert jp._cities is index  # not rebuilt
    jp.by_pc = dict(jp.by_pc)  # a table reload: rebuilt
    jp.by_pc.pop("1600022"), jp.by_pc.pop("1600023")
    jp.by_pc.pop("1638001", None)
    assert jp.romaji_city("Shinjuku-ku") == "" and jp._cities[0] is jp.by_pc
