"""Europe / Japan with the inputs a review turned up: messy order data, wrong towns and postcodes, look-alike streets,
US lines that must stay US. Offline: providers are replaced by canned answers."""

import asyncio
import csv
import io
import zipfile

import pytest
from fastapi import HTTPException

from app.intl import countries as C
from app.intl import core, europe, japan, osm
from app.intl.base import Lookup, Skip
from app.intl.match import decide, num_matches, street_sim
from app.intl.parse import parse_line
from app.intl.places import local_city, same_city
from app.main import _country
from app.models import VerifyRequest
from app.upstream import UpstreamError

CC = C.COUNTRIES


# ------------------------------------------------------------------------------------------- country

@pytest.mark.parametrize("text", [
    "1 Main St, Providence, Rhode Island",                 # not Iceland (Ísland)
    "10 Bay St, Staten Island", "100 Main St, New Britain", "100 Main St, North Wales",
    "350 5th Ave, New York, NY 10118, John Holland",       # a recipient, not the Netherlands
    "123 Mulberry St, New York, NY 10013, Little Italy",
    "100 E 8th St, Holland MI", "1 Main St, Poland, OH 44514",
    "1700 W Washington St, Phoenix, AZ 85007（周末都在家，可放小区门口）",   # a Chinese note, not Japan
    "123 Main St, Springfield, IL 62701 收件人: 王府井区域经理",
    "1700 W Washington St, Phoenix, AZ 85007 (タナカ様)", "Mary・Jane Watson, 20 Ingram St, Forest Hills, NY 11375",
    "123 Main St, Wilmington, DE 19801", "10 Main St, Baltimore, MD", "1 Peachtree St, Atlanta, Georgia",
    "1 Main St, Newark, New Jersey", "1 Main St Apt 4, Brooklyn, NY 11201, Tel 718-555-1234",
])
def test_us_lines_stay_us(text):
    assert C.country_in_text(text) in (None, "US")


@pytest.mark.parametrize("text, code", [
    ("Unter den Linden 77, 10117 Berlin, Germany", "DE"), ("10117 Berlin Germany", "DE"),
    ("11 High Street, Oxford, England,", "GB"), ("11 High Street, Oxford, United Kingdom, +44 1865 000000", "GB"),
    ("Unter den Linden 77, 10117 Berlin, Germany (Max Mustermann)", "DE"),
    ("Germany, 10117 Berlin, Unter den Linden 77", "DE"), ("德国 柏林 10117 Unter den Linden 77", "DE"),
    ("11 High Street, Oxford 英国", "GB"), ("Unter den Linden 77 10117 Berlin 德国", "DE"),
    ("10 Downing Street, London SW1A 2AA UK", "GB"), ("11 High Street, Oxford, U.K.", "GB"),
    ("10 Rue de Rivoli, 75001 Paris, FR", "FR"), ("Damrak 1, 1012 LG Amsterdam, NLD", "NL"),
    ("1 Olympic Way, Belfast BT3 9EP, N. Ireland", "GB"), ("Via Roma 10, 20121 Milano, 義大利", "IT"),
    ("Ermou 1, 105 63 Athens, Ελλάδα", "GR"), ("Keizersgracht 123, Amsterdam, Holland", "NL"),
    ("42 Place Guillaume II, Luxembourg", "LU"), ("1 Main St, Toronto, ON M5V 2T6, Canada", "CA"),
    ("大阪市北区梅田3-1-1", "JP"), ("新宿区西新宿2-8-1", "JP"), ("神奈川县横滨市中区本町6-50-10", "JP"),
    ("3-1-1 Umeda, Kita-ku, Osaka 530-0001", "JP"), ("日本东京都新宿区西新宿2-8-1", "JP"), ("千代田区丸の内1-1-1", "JP"),
])
def test_country_named_in_the_line(text, code):
    assert C.country_in_text(text) == code


def test_country_field_spellings():
    for value, code in [("UK", "GB"), ("u.k.", "GB"), ("EL", "GR"), ("DEU", "DE"), ("JPN", "JP"), ("U.S.A.", "US"),
                        ("America", "US"), ("美國", "US"), ("PR", "US"), ("義大利", "IT"), ("ＤＥ", "DE"), ("BRD", "DE")]:
        assert C.resolve_country(value) == code, value
    assert C.resolve_country("Canada") is None and C.known_country("Canada") == "CA"


def test_given_country_else_the_one_the_address_names():
    assert _country(VerifyRequest(address="Damrak 1, 1012 LG Amsterdam", country="nl")) == "NL"
    assert _country(VerifyRequest(address="Damrak 1, 1012 LG Amsterdam, Netherlands", country="DE")) == "DE"
    assert _country(VerifyRequest(address="Unter den Linden 77, Berlin", country="德国")) == "DE"
    # not given: read from the address, else the US
    assert _country(VerifyRequest(address="〒154-0017 東京都世田谷区世田谷4-21-27")) == "JP"
    assert _country(VerifyRequest(address="Unter den Linden 77, 10117 Berlin, Germany", country=" ")) == "DE"
    assert _country(VerifyRequest(address="350 5th Ave, New York, NY 10118")) == "US"
    assert _country(VerifyRequest(address="Unter den Linden 77, Berlin")) == "US"
    for req in (VerifyRequest(address="1 Main St", country="Narnia"), VerifyRequest(address="1 Main St, Toronto, Canada")):
        with pytest.raises(HTTPException) as e:
            _country(req)
        assert e.value.status_code == 422 and "unsupported country" in e.value.detail


# --------------------------------------------------------------------------------------------- parse

@pytest.mark.parametrize("cc, text, want", [
    ("DE", "Jungfernstieg 10, 1067 Dresden", dict(street="Jungfernstieg", number="10", postcode="01067", city="Dresden")),
    ("CZ", "náměstí Míru 50, Praha 2", dict(street="náměstí Míru", number="50", city="Praha 2")),
    ("IE", "12 Main Street, Dublin 2, Ireland", dict(street="Main Street", number="12", city="Dublin 2")),
    ("ES", "Calle de Alcalá, 45, 28014 Madrid", dict(street="Calle de Alcalá", number="45", city="Madrid")),
    ("FR", "55, rue du Faubourg Saint-Honoré, 75008 Paris", dict(street="rue du Faubourg Saint-Honoré", number="55")),
    ("IT", "Via Garibaldi, 10 - 20121 Milano (MI)", dict(street="Via Garibaldi", number="10", postcode="20121", city="Milano")),
    ("FR", "Rue Inexistante, 2, Paris", dict(street="Rue Inexistante", number="2", city="Paris")),
    ("DE", "Max Mustermann, Hauptstraße 12, 80331, München", dict(street="Hauptstraße", number="12", city="München")),
    ("NL", "Keizersgracht 123 III, 1015 CJ Amsterdam", dict(street="Keizersgracht", number="123")),
    ("AT", "Mariahilfer Straße 1/Top 5, 1060 Wien", dict(street="Mariahilfer Straße", number="1", unit="Top 5")),
    ("FI", "Mannerheimintie 1 A 5, 00100 Helsinki", dict(street="Mannerheimintie", number="1", unit="A 5")),
    ("DK", "Nyhavn 17, 3. th., 1051 København K", dict(street="Nyhavn", number="17", unit="3. th.")),
    ("FR", "50 rue Ste-Catherine, 33000 Bordeaux", dict(street="rue Ste-Catherine", number="50", unit="")),
    ("GB", "4 Top Road, Leeds LS1 1AA", dict(street="Top Road", number="4", unit="")),
    ("GB", "3rd Floor, 10 Downing Street, London SW1A 2AA", dict(street="Downing Street", number="10", unit="3rd Floor")),
    ("DE", "3M Deutschland GmbH, Carl-Schurz-Str. 1, 41453 Neuss", dict(street="Carl-Schurz-Str.", number="1")),
    ("FR", "Jean Dupont, Tél. 06 12 34 56 78, 18 bis rue de la République, 69002 Lyon", dict(street="rue de la République", number="18bis")),
    ("DE", "80331 München, Hauptstraße 12", dict(street="Hauptstraße", number="12", postcode="80331", city="München")),
    ("DE", "Hauptstraße 12；80331 München", dict(street="Hauptstraße", number="12", postcode="80331")),
    ("DE", "Hauptstr.12, 80331 München", dict(street="Hauptstr.", number="12")),
    ("ES", "Calle Mayor nº 5, 28013 Madrid", dict(street="Calle Mayor", number="5")),
    ("GB", "10 Downing Street London SW1A 2AA", dict(street="Downing Street", number="10", city="London")),
    ("DE", "Jungfernstieg 10, 01067 Dresden, Tel. 0351 12345", dict(postcode="01067", city="Dresden")),
    ("FR", "60 boulevard du Général Martial Valin, 75509 Paris Cedex 15", dict(city="Paris", cedex=True)),
    ("LU", "12 rue de la Gare, Luxembourg", dict(street="rue de la Gare", city="Luxembourg")),
    ("GB", "Flat 3, 10 Downing Street, London SW1A 2AA United Kingdom", dict(city="London")),
    ("PL", "ul. Marszałkowska 10, 00001 Warszawa", dict(postcode="00-001", city="Warszawa")),
])
def test_parse_line_real_inputs(cc, text, want):
    a = parse_line(text, CC[cc])
    assert {k: getattr(a, k) for k in want} == want


def test_po_boxes_and_numberless():
    for cc, text in [("DE", "Postfach 12 34, 10115 Berlin"), ("FR", "BP 123, 75001 Paris"), ("NL", "Postbus 123, 1000 AA Amsterdam")]:
        a = parse_line(text, CC[cc])
        assert a.po_box and not a.number, text
    a = parse_line("Calle Mayor s/n, 28013 Madrid", CC["ES"])
    assert (a.street, a.number, a.no_number) == ("Calle Mayor", "", True)


# --------------------------------------------------------------------------------------------- match

@pytest.mark.parametrize("want, have", [
    ("Avenue du Gal Leclerc", "Avenue du Général Leclerc"), ("Avenue Foch", "Avenue Maréchal Foch"),
    ("Av. Diagonal", "AVINGUDA DIAGONAL"), ("Narva maantee", "Narva mnt"), ("Storgt.", "Storgata"),
    ("Prešernova c.", "Prešernova cesta"), ("C.so Buenos Aires", "CORSO BUENOS AIRES"), ("ul. Kościuszki", "Tadeusza Kościuszki"),
    ("ul. Mickiewicza", "Aleja Adama Mickiewicza"), ("Via Giuseppe Verdi", "VIA VERDI GIUSEPPE"), ("Getreidegase", "Getreidegasse"),
    ("Corso Vittorio Emanuele II", "CORSO VITTORIO EMANUELE SECONDO"), ("1e Jan Steenstraat", "Eerste Jan Steenstraat"),
])
def test_same_street_written_differently(want, have):
    assert street_sim(want, have) >= 0.85


@pytest.mark.parametrize("want, have", [
    ("Markt", "Marktstraße"), ("Bahnhofstraße", "Am Bahnhof"), ("Calle Mayor", "TRAVESIA CALLE MAYOR"), ("Wiejska", "Miejska"),
    ("Hauptstraße", "Neue Hauptstraße"), ("Rue Saint-Denis", "Rue du Faubourg Saint-Denis"), ("Am Reinsgraben", "Am Steinsgraben"),
])
def test_different_streets(want, have):
    assert street_sim(want, have) < 0.85


def test_towns_and_exonyms():
    assert same_city("Munich", "München", "DE") and same_city("Paris 11e", "Paris", "FR") and same_city("Helsingfors", "Helsinki", "FI")
    assert not same_city("Wien", "Wiener Neustadt", "AT") and not same_city("Praha 2", "Praha 1", "CZ")
    assert local_city("Vienna", "AT") == "Wien" and local_city("慕尼黑", "DE") == "München"


def _c(number, street, pc, city, unit=None, kind="address"):
    return {"kind": kind, "number": number, "street": street, "postcode": pc, "city": city, "unit": unit,
            "formatted": f"{street} {number}, {pc} {city}", "street_alt": []}


def test_another_town_is_never_verified():
    a = parse_line("Karl Johans gate 22, Bergen", CC["NO"])
    d = decide(a, [_c("22", "Karl Johans gate", "0026", "OSLO")], authority="official", dataset="X")
    assert d["status"] == "not_found" and any("only in other places" in n for n in d["notes"])
    a = parse_line("Storgata 10, 0155 Oslo", CC["NO"])
    d = decide(a, [_c("10A", "Storgata", "0155", "OSLO"), _c("10", "Storgata", "1850", "MYSEN")], authority="official", dataset="X")
    assert d["status"] == "street_only"
    a = parse_line("Unter den Linden 77, 10115 Berlin", CC["DE"])  # a postcode typo in the right town is still the address
    assert decide(a, [_c("77", "Unter den Linden", "10117", "Berlin")], authority="official", dataset="X")["status"] == "verified"


def test_no_town_and_several_matches_is_ambiguous():
    a = parse_line("Bahnhofstrasse 1", CC["CH"])
    d = decide(a, [_c("1", "Bahnhofstrasse", "4125", "Riehen"), _c("1", "Bahnhofstrasse", "8001", "Zürich")],
               authority="official", dataset="X")
    assert d["status"] == "not_covered" and not d["verified"]


def test_exact_street_beats_a_lookalike_with_the_number():
    a = parse_line("Markt 7, 99423 Weimar", CC["DE"])
    d = decide(a, [_c("7", "Marktstraße", "99423", "Weimar"), _c("5", "Markt", "99423", "Weimar")], authority="official", dataset="X")
    assert d["status"] == "street_only"


def test_ranges_letters_and_additions():
    a = parse_line("Markt 48, 52062 Aachen", CC["DE"])
    assert num_matches(a, "48/50") == "range" and num_matches(parse_line("Markt 49, 52062 Aachen", CC["DE"]), "48/50") is None
    a = parse_line("Via Po 10A, 10123 Torino", CC["IT"])  # Overture keeps the letter as the unit
    d = decide(a, [_c("10", "VIA PO", None, "Torino", "A")], authority="overture", dataset="X")
    assert d["status"] == "verified"
    a = parse_line("Eerste Jan Steenstraat 8, 1072NK Amsterdam", CC["NL"])
    d = decide(a, [_c("8", "Eerste Jan Steenstraat", "1072NK", "Amsterdam", u) for u in "ABC"], authority="official",
               dataset="BAG", unit_support=True)
    assert d["status"] == "verified" and any("only with an addition" in n for n in d["notes"])


# ------------------------------------------------------------------------------------------ providers

def test_provider_records():
    es = europe.es_cands([{"type": "portal", "address": "CALLE MAYOR 5 BIS, Betelu", "portalNumber": 5, "extension": "BIS"},
                          {"type": "portal", "address": "CALLE MAYOR 20 22, Azofra", "portalNumber": 20, "extension": "22"}])
    assert [(c["street"], c["number"]) for c in es] == [("CALLE MAYOR", "5BIS"), ("CALLE MAYOR", "20-22")]
    cz = europe.cz_cands({"candidates": [
        {"address": "č.p. 12, 67905 Křtiny", "location": {}, "attributes": {"Type": "AdresniMisto"}},
        {"address": "č.ev. 12, 67905 Křtiny", "location": {}, "attributes": {"Type": "AdresniMisto"}}]})
    assert [(c["street"], c["number"]) for c in cz] == [("Křtiny", "12"), ("Křtiny", "ev.12")]
    pl = europe.pl_cands({"type": "address", "results": {"1": {"city": "Przybysławice", "street": None, "number": "12", "code": "32-046"}}})
    assert (pl[0]["street"], pl[0]["number"]) == ("Przybysławice", "12")
    si = europe.si_cands({"features": [{"properties": {"HS_STEVILKA": 5, "ULICA_NAZIV": "Aljaževa cesta", "NASELJE_NAZIV": "Bled",
                                                       "POSTNI_OKOLIS_SIFRA": 4260, "POSTNI_OKOLIS_NAZIV": "Bled"}}]})
    assert si[0]["street_alt"] == [] and si[0]["city_alt"] == ["Bled"]
    assert europe.is_cands({"features": [{"properties": {"HUSNR": 10, "HEITI_NF": "Laugavegur", "POSTNR": 101}}]})[0]["city"] == "Reykjavík"
    assert europe.fr_ban_cands({"features": [None, "x"]}) == []
    assert europe._cql("100%_x'") == "'100  x'''"


# ---------------------------------------------------------------------------------------------- chain

class _Ctx:
    ov = object()


def _run(monkeypatch, cc, answers, text):
    calls = []

    def fake(key):
        async def provider(ctx, a):
            calls.append(key)
            ans = answers[key]
            if isinstance(ans, Exception):
                raise ans
            return ans
        return provider

    for key in CC[cc].providers:
        monkeypatch.setitem(core.PROVIDERS, key, fake(key))
    out = asyncio.run(core.verify_intl(_Ctx(), None, cc, address=text))
    return out, calls


def test_chain_moves_on_and_keeps_the_best_answer(monkeypatch):
    real = Lookup(cands=[_c("77", "Unter den Linden", "10117", "Berlin")])
    out, calls = _run(monkeypatch, "DE", {"overture": UpstreamError("overture", "down"), "osm": real},
                      "Unter den Linden 77, 10117 Berlin")
    assert out["status"] == "verified" and out["data_source"] == "osm" and calls == ["overture", "osm"]
    out, calls = _run(monkeypatch, "DE", {"overture": KeyError("odd JSON"), "osm": real}, "Unter den Linden 77, 10117 Berlin")
    assert out["status"] == "verified" and any("could not be read" in n for n in out["notes"])
    # an official register that finds nothing at all lets the next source try, and stands if that does no better
    empty, failed = Lookup(), Lookup(status="geocode_failed")
    out, calls = _run(monkeypatch, "AT", {"at_bev": empty, "overture": failed, "osm": failed}, "Hauptplatz 1, 8010 Graz")
    assert out["status"] == "not_found" and out["data_source"] == "at_bev" and calls == ["at_bev", "overture", "osm"]
    out, calls = _run(monkeypatch, "PL", {"pl_gugik": Skip("needs the town"), "overture": real, "osm": failed}, "ul. X 1, Warszawa")
    assert calls == ["pl_gugik", "overture"]


# ---------------------------------------------------------------------------------------------- Japan

@pytest.mark.parametrize("text, prefix, nums", [
    ("本町1-1-1, 中央区, 大阪府", "本町", [1, 1, 1]),
    ("2-8-1 西新宿, 新宿区, 東京都", "西新宿", [2, 8, 1]),
    ("日本橋3-1-1, 中央区, 東京都", "日本橋", [3, 1, 1]),
    ("住所：東京都新宿区西新宿2-8-1", "東京都新宿区西新宿", [2, 8, 1]),
    ("山田太郎様 東京都新宿区西新宿2-8-1", "東京都新宿区西新宿", [2, 8, 1]),
    ("京都府京都市中京区寺町通御池上る上本能寺前町488", "京都府京都市中京区上本能寺前町", [488]),
    ("東京都千代田区一番町1", "東京都千代田区一番町", [1]),
    ("東京都港区麻布十番2-3-1", "東京都港区麻布十番", [2, 3, 1]),
    ("茨城県水戸市笠原町978番地の6", "茨城県水戸市笠原町", [978, 6]),
    ("日本东京都新宿区西新宿2-8-1", "东京都新宿区西新宿", [2, 8, 1]),
])
def test_japanese_input_forms(text, prefix, nums):
    a = japan.parse_jp(text)
    assert (a.prefix, a.nums) == (prefix, nums)


def test_japanese_phone_is_not_the_postcode():
    a = japan.parse_jp("東京都新宿区西新宿2-8-1 山田太郎様 090-1234-5678")
    assert a.postcode == "" and a.nums == [2, 8, 1]
    assert japan.parse_jp("茨城県水戸市笠原町978-1234").postcode == ""


def test_town_names_compared_whole():
    assert not japan.town_matches("上野", "東京都台東区東上野")
    assert not japan.town_matches("新宿", "東京都新宿区西新宿")
    assert japan.town_matches("西新宿", "東京都新宿区西新宿") and japan.town_matches("東京都西新宿", "東京都新宿区西新宿")


def _jp(text, title, chome_town=None, block_has_go=None):
    a = japan.parse_jp(text)
    b = japan.parse_title({"properties": {"title": title}, "geometry": {"coordinates": [139.0, 35.0]}})
    res = {"notes": [], "status": None, "level": None}
    japan._decide(a, b, res, chome_town, block_has_go)
    return res["status"], res["level"]


def test_japanese_verdicts_for_parcels_and_blocks():
    assert _jp("東京都世田谷区世田谷4-21-99", "東京都世田谷区世田谷四丁目２１番", block_has_go=True) == ("street_only", "street")
    assert _jp("北海道札幌市中央区北4条西4丁目1", "北海道札幌市中央区北四条西四丁目１番地") == ("verified", "address")
    assert _jp("茨城県水戸市笠原町978-6", "茨城県水戸市笠原町９７８番地５号") == ("street_only", "street")
    assert _jp("茨城県水戸市笠原町978-6", "茨城県水戸市笠原町９７８番地") == ("verified", "block")


def _zip(path, name, rows, encoding):
    buf = io.StringIO()
    csv.writer(buf).writerows(rows)
    with zipfile.ZipFile(path, "w") as z:
        z.writestr(name, buf.getvalue().encode(encoding))


def test_japan_post_tables(tmp_path):
    d = tmp_path / "japanpost"
    d.mkdir()
    flags = ["0"] * 6
    _zip(d / "utf_ken_all.zip", "utf_ken_all.csv", [
        ["13104", "160", "1600023", "", "", "", "東京都", "新宿区", "西新宿（次のビルを除く）", *flags],
        ["13112", "154", "1540023", "", "", "", "東京都", "世田谷区", "若林", *flags],
        ["01101", "064", "0640820", "", "", "", "北海道", "札幌市中央区", "大通西（２０〜２８丁目）", *flags],
        ["01101", "060", "0600042", "", "", "", "北海道", "札幌市中央区", "大通西（１〜１９丁目）", *flags],
        ["13103", "106", "1066108", "", "", "", "東京都", "港区", "六本木六本木ヒルズ森タワー（８階）", *flags],
        ["13103", "106", "1060032", "", "", "", "東京都", "港区", "六本木", *flags],
    ], "utf-8")
    _zip(d / "KEN_ALL_ROME.zip", "KEN_ALL_ROME.CSV", [
        ["1600023", "東京都", "新宿区", "西新宿（次のビルを除く）", "TOKYO TO", "SHINJUKU KU", "NISHISHINJUKU (TSUGINOBIRUONOZOKU)"],
        ["1540023", "東京都", "世田谷区", "若林", "TOKYO TO", "SETAGAYA KU", "WAKABAYASHI"],
    ], "cp932")
    _zip(d / "jigyosyo.zip", "JIGYOSYO.CSV", [
        ["13104", "ﾄｳｷﾖｳﾄﾁﾖｳ", "東京都庁", "東京都", "新宿区", "西新宿", "２丁目８－１", "1638001", "16001", "0", "0", "0", "0"],
    ], "cp932")
    jp = japan.JapanPost(None, tmp_path)
    jp.by_pc, jp.by_place, jp.by_town, jp.by_romaji = jp._read()
    assert [r.place for r in jp.romaji_places("1-2-3 Wakabayashi, Setagaya-ku, Tokyo")] == ["東京都世田谷区若林"]
    assert jp.romaji_places("1-2-3 Wakabayashi, Shinjuku-ku, Tokyo") == []           # the town's city isn't written
    assert jp.postcodes_for("北海道札幌市中央区大通西", 5) == ["0600042"]
    assert [(r.place, r.chome) for r in jp.rows("1638001")] == [("東京都新宿区西新宿", (2, 2))]
    assert [r.place for r in jp.rows("1066108")] == ["東京都港区六本木"]                # a building's own code
    # a romaji address with another town's postcode stays in the town it names
    a = japan.parse_jp("1-2-3 Wakabayashi, Setagaya-ku, Tokyo 160-0023")
    japan._from_romaji(jp, a, [])
    assert a.prefix == "東京都世田谷区若林"
