"""Europe / Japan: parsing, matching and reading each provider's answer (no network: the JSON is trimmed from
real responses)."""

from fastapi.testclient import TestClient

from app.intl import countries as C
from app.intl import europe, japan, osm
from app.intl.match import decide, norm_num, norm_postcode, street_sim
from app.intl.parse import parse_line
from app.main import app

DE, FR, NL, GB, AT, PL, ES = (C.COUNTRIES[k] for k in ("DE", "FR", "NL", "GB", "AT", "PL", "ES"))


# --------------------------------------------------------------------------------------- country

def test_country_names_and_codes():
    assert C.resolve_country("de") == "DE"
    assert C.resolve_country("Deutschland") == "DE"
    assert C.resolve_country("德国") == "DE"
    assert C.resolve_country("United Kingdom") == "GB"
    assert C.resolve_country("US") == "US"
    assert C.resolve_country("Narnia") is None


def test_country_in_address_line_never_takes_us_states():
    assert C.country_in_text("Unter den Linden 77, 10117 Berlin, Germany") == "DE"
    assert C.country_in_text("10117 Berlin Germany") == "DE"
    assert C.country_in_text("東京都新宿区西新宿2-8-1") == "JP"
    assert C.country_in_text("123 Main St, Wilmington, DE 19801") is None   # Delaware, not Germany
    assert C.country_in_text("1 Peachtree St, Atlanta, Georgia") is None
    assert C.country_in_text("10 Main St, Baltimore, MD") is None           # Maryland, not Moldova


# ----------------------------------------------------------------------------------------- parse

def test_parse_number_after_street():
    a = parse_line("Hauptstraße 12a, Whg 5, 80331 München, Germany", DE)
    assert (a.street, a.number, a.unit, a.postcode, a.city) == ("Hauptstraße", "12a", "Whg 5", "80331", "München")
    a = parse_line("Unter den Linden 77 10117 Berlin", DE)
    assert (a.street, a.number, a.postcode, a.city) == ("Unter den Linden", "77", "10117", "Berlin")


def test_parse_number_first_and_extras():
    a = parse_line("Hôtel de Ville, 1 Place de la Comédie, 69001 Lyon", FR)
    assert (a.street, a.number, a.postcode, a.extra) == ("Place de la Comédie", "1", "69001", ["Hôtel de Ville"])
    a = parse_line("18 bis rue de la République, 69002 Lyon", FR)
    assert a.number == "18bis"
    a = parse_line("Flat 3, 10 Downing Street, London SW1A 2AA", GB)
    assert (a.street, a.number, a.unit, a.postcode, a.city) == ("Downing Street", "10", "Flat 3", "SW1A 2AA", "London")


def test_parse_separators_and_floors():
    assert parse_line("Damrak 8-H, 1012 LG Amsterdam", NL).number == "8-H"
    a = parse_line("Hauptstraße 12/3/4, 1010 Wien", AT)
    assert (a.number_base, a.number_ext) == ("12", "3/4")
    assert parse_line("ul. Wiejska 4/6/8, 00-902 Warszawa", PL).postcode == "00-902"
    a = parse_line("Calle de Alcalá 45, 3º B, 28014 Madrid", ES)
    assert (a.number, a.unit, a.postcode) == ("45", "3o B", "28014")
    assert parse_line("Piazza del Duomo, 20122 Milano", C.COUNTRIES["IT"]).number == ""


# ----------------------------------------------------------------------------------------- match

def test_street_similarity():
    assert street_sim("Wiejska", "Nowowiejska") < 0.85            # a different street
    assert street_sim("ul. Wiejska", "Wiejska") == 1.0
    assert street_sim("Hauptstr.", "Hauptstraße") == 1.0
    assert street_sim("rue de la Paix", "Avenue de la Paix") < 0.85   # same name, different type
    assert street_sim("Via XX Settembre", "VIA VENTI SETTEMBRE") == 1.0
    assert street_sim("Via XX Settembre", "Via XXI Settembre") < 0.85
    assert street_sim("St Mary Axe", "Saint Mary Axe") == 1.0
    assert street_sim("Downing St", "Downing Street") == 1.0
    assert street_sim("Karl Johans gt", "Karl Johans gate") == 1.0


def test_number_and_postcode_normalisation():
    assert [norm_num(x) for x in ("18 bis", "8-H", "011", "26 A", "12/3")] == ["18bis", "8h", "11", "26a", "12/3"]
    assert [norm_postcode(x) for x in ("LV-1050", "1012 lg", "SW1A 2AA", "00-110")] == ["1050", "1012LG", "SW1A2AA", "00110"]


def _addr(number, street="Unter den Linden", postcode="10117", unit=None):
    return {"kind": "address", "number": number, "street": street, "postcode": postcode, "unit": unit, "city": "Berlin",
            "formatted": f"{street} {number}", "street_alt": []}


def test_decide_verified_and_street_only():
    a = parse_line("Unter den Linden 77, 10117 Berlin", DE)
    d = decide(a, [_addr("76"), _addr("77")], authority="official", dataset="X")
    assert d["status"] == "verified" and d["best_match"]["number"] == "77"
    d = decide(parse_line("Unter den Linden 977, 10117 Berlin", DE), [_addr("76"), _addr("78")], authority="official", dataset="X")
    assert d["status"] == "street_only" and [s["number"] for s in d["suggestions"]] == ["78", "76"]


def test_decide_wrong_postcode_still_verifies_with_flag():
    a = parse_line("Unter den Linden 77, 10115 Berlin", DE)
    d = decide(a, [_addr("77")], authority="official", dataset="X")
    assert d["status"] == "verified" and d["best_match"]["match"]["zip_match"] is False


def test_decide_osm_miss_is_not_evidence():
    a = parse_line("250 Downing Street, London SW1A 2AA", GB)
    d = decide(a, [_addr("10", "Downing Street", "SW1A 2AA")], authority="osm", dataset="OSM")
    assert d["status"] == "not_covered"


def test_decide_units():
    a = parse_line("Damrak 8-Z, 1012LG Amsterdam", NL)
    a.number, a.unit = "8", "Z"
    cands = [_addr("8", "Damrak", "1012LG", "1"), _addr("8", "Damrak", "1012LG", "H")]
    assert decide(a, cands, authority="official", dataset="BAG", unit_support=True)["status"] == "unit_not_found"
    a.unit = "H"
    d = decide(a, cands, authority="official", dataset="BAG", unit_support=True)
    assert (d["status"], d["match_level"]) == ("verified", "unit")


# ------------------------------------------------------------------------------------- providers

def test_ban_types():
    data = {"features": [
        {"properties": {"type": "housenumber", "housenumber": "18bis", "street": "Rue de la République", "postcode": "69002",
                        "city": "Lyon", "label": "18bis Rue de la République 69002 Lyon", "id": "x"},
         "geometry": {"coordinates": [4.83, 45.76]}},
        {"properties": {"type": "street", "name": "Rue de Rivoli", "postcode": "75004", "city": "Paris"},
         "geometry": {"coordinates": [2.35, 48.85]}},
        {"properties": {"type": "municipality", "name": "Paris"}, "geometry": {"coordinates": [2.3, 48.8]}}]}
    cands = europe.fr_ban_cands(data)
    assert [(c["kind"], c["number"]) for c in cands] == [("address", "18bis"), ("street", None)]


def test_pdok_letter_and_addition_are_the_unit():
    data = {"response": {"docs": [{"type": "adres", "straatnaam": "Damrak", "huisnummer": 8, "huisnummertoevoeging": "H",
                                   "postcode": "1012LG", "woonplaatsnaam": "Amsterdam", "id": "a",
                                   "centroide_ll": "POINT(4.89767 52.37689)", "weergavenaam": "Damrak 8-H"}]}}
    c = europe.nl_cands(data)[0]
    assert (c["number"], c["unit"], c["lat"], c["lon"]) == ("8", "H", 52.37689, 4.89767)
    a = parse_line("Damrak 8-H, 1012 LG Amsterdam", NL)
    assert europe.nl_split(a) == (8, "H")


def test_swiss_label():
    data = {"results": [{"attrs": {"label": "Stadthausquai 17a <b>8001 Zürich</b>", "lat": 47.37, "lon": 8.54}}]}
    c = europe.ch_cands(data)[0]
    assert (c["street"], c["number"], c["postcode"], c["city"]) == ("Stadthausquai", "17a", "8001", "Zürich")


def test_austria_street_polygon_and_empty_answer():
    data = {"data": {"features": [
        {"properties": {"objectType": "12", "strasse": "Getreidegasse", "plz": "5020", "ort": "Salzburg"},
         "geometry": {"type": "Polygon", "coordinates": [[[13.04, 47.80], [13.05, 47.80], [13.05, 47.81]]]}}]}}
    assert [(c["kind"], c["lat"]) for c in europe.at_cands(data)] == [("street", None)]
    assert europe.at_cands({"data": {"type": "FeatureCollection"}}) == []


def test_wallonia_score_digits():
    def c(score, house):
        return {"score": score, "city": {"name": "Liège"}, "zone": {"ident": "4000"}, "street": {"name": "Place du Marché"},
                "house": {"name": house, "geometry": {"coordinates": [5.57, 50.64]}} if house else {}}
    kinds = [(x["kind"], x["number"]) for x in europe.be_wa_cands({"candidates": [c(111, "2"), c(113, "4"), c(110, None)]})]
    assert kinds == [("address", "2"), ("address", "4"), ("street", None)]


def test_czech_numbers_in_both_forms():
    data = {"candidates": [{"address": "Plzeňská 151/1a, Nová Ves, 70900 Ostrava", "location": {"x": 18.2, "y": 49.8},
                            "attributes": {"Type": "AdresniMisto"}}]}
    assert {c["number"] for c in europe.cz_cands(data)} == {"1a", "151", "151/1a"}


def test_latvia_skips_named_houses():
    data = {"result": {"records": [
        {"STD": "Rātslaukums 1, Rīga, LV-1050", "NOSAUKUMS": "1", "ATRIB": "LV-1050", "DD_N": 56.9, "DD_E": 24.1, "KODS": 1},
        {"STD": "Kalnieši, Ādažu pag., Ādažu nov., LV-2164", "NOSAUKUMS": "Kalnieši", "ATRIB": "LV-2164", "KODS": 2}]}}
    c = europe.lv_cands(data)
    assert [(x["street"], x["number"], x["city"]) for x in c] == [("Rātslaukums", "1", "Rīga")]


def test_finland_range_contains_the_number():
    a = parse_line("Pohjoisesplanadi 12, Helsinki", C.COUNTRIES["FI"])
    data = {"features": [{"geometry": {"type": "Point", "coordinates": [24.95, 60.16]}, "properties": {
        "address_fin": "Pohjoisesplanadi 11-13", "address_name_fin": "Pohjoisesplanadi", "number_part_of_address_number": 11,
        "number_part_of_address_number2": 13, "postal_code": "00170", "postal_office_fin": "HELSINKI"}}]}
    assert europe.fi_cands(data, a)[0]["number"] == "12"


def test_photon_interpolation_counts_as_street():
    feats = [{"properties": {"type": "house", "osm_type": "W", "osm_key": "place", "osm_value": "house_number",
                             "housenumber": "17", "street": "Bedford Road", "countrycode": "GB"},
              "geometry": {"coordinates": [-0.13, 51.46]}},
             {"properties": {"type": "house", "osm_type": "N", "osm_key": "building", "osm_value": "yes", "housenumber": "10",
                             "street": "Downing Street", "postcode": "SW1A 2AA", "city": "London", "countrycode": "GB"},
              "geometry": {"coordinates": [-0.1276, 51.5034]}}]
    cands = osm.photon_cands(feats, number_first=True)
    assert [(c["kind"], c["number"]) for c in cands] == [("street", None), ("address", "10")]
    assert cands[1]["formatted"] == "10 Downing Street, SW1A 2AA London"


# ----------------------------------------------------------------------------------------- Japan

def test_japanese_numbers():
    a = japan.parse_jp("〒163-8001 東京都新宿区西新宿二丁目8番1号 東京都庁")
    assert (a.prefix, a.nums, a.chome, a.rest, a.postcode) == ("東京都新宿区西新宿", [2, 8, 1], True, "東京都庁", "1638001")
    a = japan.parse_jp("東京都港区芝公園4-2-8-101")
    assert (a.nums, a.rest) == ([4, 2, 8], "101")
    a = japan.parse_jp("北海道札幌市中央区北1条西2丁目1-1")
    assert (a.prefix, a.nums) == ("北海道札幌市中央区北1条西", [2, 1, 1])
    a = japan.parse_jp("Apt 302, 1-2-3 Wakabayashi, Setagaya-ku, Tokyo 154-0023")
    assert (a.romaji, a.nums, a.rest, a.postcode) == (True, [1, 2, 3], "Apt 302", "1540023")


def test_gsi_titles_and_places():
    t = japan.parse_title({"properties": {"title": "東京都新宿区西新宿二丁目８番"}, "geometry": {"coordinates": [139.69, 35.69]}})
    assert (t.town, t.chome, t.ban, t.go, t.level) == ("東京都新宿区西新宿", 2, 8, None, "ban")
    t = japan.parse_title({"properties": {"title": "茨城県水戸市笠原町９７８番地"}, "geometry": {"coordinates": [140.4, 36.3]}})
    assert (t.ban, t.banchi, t.level) == (978, True, "ban")
    assert japan.town_matches("新宿区西新宿", "東京都新宿区西新宿")
    assert japan.town_matches("北海道虻田郡ニセコ町字富士見", "北海道ニセコ町富士見")
    assert not japan.town_matches("東新宿", "東京都新宿区東五軒町")
    assert japan._clean_town("西新宿（次のビルを除く）") == "西新宿"
    assert japan._clean_town("以下に掲載がない場合") == ""


def _jp(text, title, chome_town=None):
    a = japan.parse_jp(text)
    b = japan.parse_title({"properties": {"title": title}, "geometry": {"coordinates": [139.0, 35.0]}})
    res = {"notes": [], "status": None, "level": None}
    japan._decide(a, b, res, chome_town)
    return res["status"], res["level"]


def test_gsi_verdicts():
    assert _jp("東京都世田谷区世田谷4-21-27", "東京都世田谷区世田谷四丁目２１番２７号") == ("verified", "address")
    assert _jp("東京都新宿区西新宿2-8-1", "東京都新宿区西新宿二丁目８番") == ("verified", "block")
    assert _jp("東京都新宿区西新宿2-99-1", "東京都新宿区西新宿二丁目") == ("street_only", "street")
    assert _jp("東京都新宿区西新宿9-1-1", "東京都新宿区西新宿", chome_town=True) == ("not_found", None)
    assert _jp("茨城県水戸市笠原町9999", "茨城県水戸市笠原町", chome_town=False) == ("not_covered", None)
    assert _jp("茨城県水戸市笠原町978-6", "茨城県水戸市笠原町９７８番地") == ("verified", "block")


# --------------------------------------------------------------------------------------------- API

def test_countries_endpoint_and_unknown_country():
    client = TestClient(app)
    codes = [c["code"] for c in client.get("/countries").json()]
    assert codes[:2] == ["US", "JP"] and {"DE", "FR", "GB", "NL"} <= set(codes)
    r = client.get("/v1/verify", params={"address": "1 Main St", "country": "Narnia"})
    assert r.status_code == 422 and "unsupported country" in r.json()["detail"]
