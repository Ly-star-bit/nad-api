"""Live end-to-end check against a running server (hits the real upstream services).

usage: python scripts/smoke_test.py [base_url]   (default http://127.0.0.1:8000)
"""

import sys
import time

import httpx

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000").rstrip("/")

# (request body, expected status, expected data_source)
CASES = [
    ({"address": "1700 W Washington St, Phoenix, AZ 85007"}, "verified", "nad"),
    ({"address": "350 5th Ave, New York, NY 10118"}, "verified", "nad"),
    ({"address": "980 9th St Ste 1750, Sacramento, CA 95814"}, "verified", "nad"),
    ({"address": "980 9th St Ste 9999, Sacramento, CA 95814"}, "unit_not_found", "nad"),
    ({"address": "11 W Washington St Suite 240, Phoenix, AZ 85003"}, "verified", "nad"),
    ({"address": "77 Fair Dr, Costa Mesa, CA 92626"}, "verified", "nad"),
    ({"address": "13600 W Lake View Rd, Raymond, NE 68428"}, "verified", "nad"),
    ({"address": "1716 W Washington St, Phoenix, AZ 85007"}, "street_only", "nad"),
    # Counties missing from NAD -> answered by the Overture fallback (county data via OpenAddresses)
    ({"address": "1600 Amphitheatre Pkwy, Mountain View, CA 94043"}, "verified", "overture"),
    ({"address": "1 Civic Center Plaza, Irvine, CA 92606"}, "verified", "overture"),
    ({"address": "108 Bryant St Unit 32, Mountain View, CA 94041"}, "verified", "overture"),
    ({"address": "200 N Spring St, Los Angeles, CA 90012"}, "verified", "overture"),  # Census says S Spring
    ({"address": "1 Dr Carlton B Goodlett Pl, San Francisco, CA 94102"}, "verified", "overture"),
    ({"address": "123 Fakestreet Blvd, Phoenix AZ 85007"}, "geocode_failed", "nad"),  # NAD has no such street in 85007
    ({"address": "106 Quivira Rd, Marquette, KS 67464"}, "verified", "nad"),
    ({"address": "asdkjh qwe zzz"}, "invalid_input", None),
]

# Europe and Japan: one real and one wrong address per country's source. Run this on the production server too:
# several registers answer only some countries' IP addresses.
INTL = [
    ({"address": "18 bis rue de la République, 69002 Lyon", "country": "FR"}, "verified", "fr_ban"),
    ({"address": "9999 rue de Rivoli, 75004 Paris", "country": "FR"}, "street_only", "fr_ban"),
    ({"address": "Damrak 8-H, 1012 LG Amsterdam", "country": "NL"}, "verified", "nl_pdok"),
    ({"address": "Museumstraat 9999, 1071XX Amsterdam", "country": "NL"}, "street_only", "nl_pdok"),
    ({"address": "Grote Markt 1, 2000 Antwerpen", "country": "BE"}, "verified", "be_register"),
    ({"address": "Place du Marché 2, 4000 Liège", "country": "BE"}, "verified", "be_register"),
    ({"address": "Grand-Place 1, 1000 Bruxelles", "country": "BE"}, "verified", "overture"),
    ({"address": "42 Place Guillaume II, L-1648 Luxembourg", "country": "LU"}, "verified", "lu_geoportail"),
    ({"address": "Bundesplatz 3, 3011 Bern", "country": "CH"}, "verified", "ch_geoadmin"),
    ({"address": "Bundesplatz 9999, 3011 Bern", "country": "CH"}, "street_only", "ch_geoadmin"),
    ({"address": "Rathausplatz 1, 1010 Wien", "country": "AT"}, "verified", "at_bev"),
    ({"address": "Plaza de Cibeles 1, 28014 Madrid", "country": "ES"}, "verified", "es_cartociudad"),
    ({"address": "Karl Johans gate 22, 0026 Oslo", "country": "NO"}, "verified", "no_kartverket"),
    ({"address": "Mannerheimintie 30, 00100 Helsinki", "country": "FI"}, "verified", "fi_ryhti"),
    ({"address": "Kirkjustræti 14, 101 Reykjavík", "country": "IS"}, "verified", "is_hms"),
    ({"address": "Raekoja plats 1, 10146 Tallinn", "country": "EE"}, "verified", "ee_inads"),
    ({"address": "Rātslaukums 1, Rīga, LV-1050", "country": "LV"}, "verified", "lv_vzd"),
    ({"address": "ul. Marszałkowska 99A, 00-693 Warszawa", "country": "PL"}, "verified", "pl_gugik"),
    ({"address": "Mariánské náměstí 2, 110 00 Praha", "country": "CZ"}, "verified", "cz_ruian"),
    ({"address": "Mestni trg 1, 1000 Ljubljana", "country": "SI"}, "verified", "si_gurs"),
    ({"address": "Unter den Linden 77, 10117 Berlin, Germany"}, "verified", "overture"),
    ({"address": "Unter den Linden 977, 10117 Berlin, Germany"}, "street_only", "overture"),
    ({"address": "Via XX Settembre 97, 00187 Roma", "country": "IT"}, "verified", "overture"),
    ({"address": "Marienplatz 8, 80331 München", "country": "DE"}, "verified", "osm"),  # Bavaria: not in Overture
    ({"address": "10 Downing Street, London SW1A 2AA", "country": "GB"}, "verified", "osm"),
    ({"address": "東京都世田谷区世田谷4-21-27"}, "verified", "jp_gsi"),
    ({"address": "東京都新宿区西新宿2-99-1"}, "street_only", "jp_gsi"),
    ({"address": "2-8-1 Nishi-Shinjuku, Shinjuku-ku, Tokyo 163-8001, Japan"}, "verified", "jp_gsi"),
]
# Inputs a review turned up: each was answered wrongly once.
REGRESSIONS = [
    ({"address": "25 Dorrance St, Providence, Rhode Island"}, "verified", "nad"),  # not Iceland
    ({"address": "1700 W Washington St, Phoenix, AZ 85007（周末都在家，可放小区门口）"}, "verified", "nad"),  # not Japan
    ({"address": "Karl Johans gate 22, Bergen", "country": "NO"}, "not_found", "no_kartverket"),  # the number is in Oslo
    ({"address": "náměstí Míru 9, Praha 2", "country": "CZ"}, "verified", "cz_ruian"),  # a district, not a postcode
    ({"address": "Calle Mayor 5, 28013 Madrid", "country": "ES"}, "verified", "es_cartociudad"),  # really 28012
    ({"address": "Stephansplatz 1, 1010 Vienna", "country": "AT"}, "verified", "at_bev"),  # English town name
    ({"address": "Przybysławice 12, 32-046 Przybysławice", "country": "PL"}, "verified", "pl_gugik"),  # a village without streets
    ({"address": "Markt 7, 99423 Weimar", "country": "DE"}, "street_only", "overture"),  # not Marktstraße 7
    ({"address": "1-2-3 Wakabayashi, Setagaya-ku, Tokyo 160-0023, Japan"}, "verified", "jp_gsi"),  # postcode of 西新宿
    ({"address": "本町1-1-1, 中央区, 大阪府"}, "verified", "jp_gsi"),  # not the first 本町 in Japan
    ({"address": "中央1-1-1", "country": "JP"}, "geocode_failed", "jp_gsi"),  # dozens of 中央 towns
    ({"address": "Unter den Linden 77, Berlin Germany"}, "verified", "overture"),  # the country after the town, no postcode
    ({"address": "京都市北区金閣寺町1"}, "verified", "jp_gsi"),  # Kyoto without the prefecture
]
CASES += INTL + REGRESSIONS

failed = 0
with httpx.Client(timeout=90) as c:
    for body, expected, source in CASES:
        t = time.perf_counter()
        r = c.post(f"{BASE}/v1/verify", json=body)
        ms = (time.perf_counter() - t) * 1000
        d = r.json()
        ok = r.status_code == 200 and d.get("status") == expected and d.get("data_source") == source
        failed += not ok
        best = (d.get("best_match") or {}).get("formatted", "")
        label = body["address"] + (f" [{body['country']}]" if body.get("country") else "")
        got = f"{d.get('status', r.status_code)}/{d.get('data_source')}"
        print(f"{'PASS' if ok else 'FAIL'} {ms:7.0f} ms  {got:<24} {label}  ->  {best}")
print(f"\n{len(CASES) - failed}/{len(CASES)} passed")
sys.exit(1 if failed else 0)
