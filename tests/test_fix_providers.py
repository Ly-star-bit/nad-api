"""Round-2 provider repairs, offline: each provider gets canned upstream answers shaped like the live ones (BAN, In-ADS,
RÚIAN, Ryhti, geo.admin, Kartverket, Photon + Overture rows), and the verdict is checked end to end."""

import asyncio
import time

import pytest

from app import overture as ovmod
from app.intl import core, europe, osm
from app.intl import countries as C
from app.intl.base import Ctx, Lookup
from app.intl.match import decide
from app.intl.parse import IntlAddress, parse_line
from app.upstream import TTLCache, Upstream, UpstreamError

CC = C.COUNTRIES


def _decide(a, lk, authority="official", dataset="X"):
    return decide(lk.view or a, lk.cands, authority=authority, dataset=dataset, unit_support=lk.unit_support,
                  street_found=lk.street_found, area_empty=lk.area_empty, incomplete=lk.incomplete)


class _Get:
    """ctx.get with canned answers: answer(service, params) -> JSON; the params asked are kept."""

    def __init__(self, answer, ov=None):
        self.answer, self.asked, self.ov = answer, [], ov

    async def get(self, service, url, params, *, expect=dict):
        self.asked.append(params)
        return self.answer(service, params)


def _a(cc, text):
    return parse_line(text, CC[cc])


# ----------------------------------------------------------------------------------------------- R27

class _Ov:
    ov = object()


def _chain(monkeypatch, cc, answers, text):
    for key in CC[cc].providers:
        async def provider(ctx, a, key=key):
            return answers[key]
        monkeypatch.setitem(core.PROVIDERS, key, provider)
    return asyncio.run(core.verify_intl(_Ov(), None, cc, address=text))


def _rec(number, street, pc, city, **kw):
    return {"kind": "address", "number": number, "street": street, "postcode": pc, "city": city, "unit": None,
            "formatted": f"{street} {number}, {pc} {city}", "street_alt": [], "city_alt": [], **kw}


def test_r27_register_found_nothing_and_a_similar_street_elsewhere_does_not_verify(monkeypatch):
    # Gent has no Grote Markt; Overture's Groentenmarkt 1 (street_sim 0.87) is another square
    similar = Lookup(cands=[_rec("1", "Groentenmarkt", "9000", "Gent")])
    out = _chain(monkeypatch, "BE", {"be_register": Lookup(), "overture": similar, "osm": Lookup(status="geocode_failed")},
                 "Grote Markt 1, 9000 Gent")
    assert out["status"] == "not_found" and not out["verified"] and out["data_source"] == "be_register"
    assert [c["formatted"] for c in out["candidates"]] == ["Groentenmarkt 1, 9000 Gent"]
    assert any("similar street name" in n for n in out["notes"]) and out["fallback"] is None
    # the exact name in the next source is still taken (a spelling the register can't read)
    exact = Lookup(cands=[_rec("1", "Grote Markt", "9000", "Gent")])
    out = _chain(monkeypatch, "BE", {"be_register": Lookup(), "overture": exact, "osm": Lookup()}, "Grote Markt 1, 9000 Gent")
    assert out["status"] == "verified" and out["data_source"] == "overture"
    # a register that answered with records keeps the old flow: a similar street there is the register's call
    out = _chain(monkeypatch, "BE", {"be_register": Lookup(cands=[_rec("1", "Groentenmarkt", "9000", "Gent")]),
                                     "overture": exact, "osm": Lookup()}, "Grote Markt 1, 9000 Gent")
    assert out["data_source"] == "be_register"


# ----------------------------------------------------------------------------------------------- R14

@pytest.mark.parametrize("cc, text, status", [
    ("DE", "Hauptstraße, 80331 München", "invalid_input"),        # a forgotten number, as at 8ed3bec
    ("NO", "Storgata, 8300 Svolvær", "invalid_input"),
    ("GB", "High Street, Oxford OX1 4AA", "invalid_input"),
    ("ES", "Calle Mayor s/n, 28013 Madrid", "not_covered"),       # s/n: no number on purpose
    ("GB", "Rose Cottage, Little Snoring, Fakenham NR21 0AA", "not_covered"),
    ("GB", "Rose Cottage, High Street, Little Snoring NR21 0AA", "not_covered"),
    ("LV", "Ozoli, Ādažu pagasts, LV-2164", "not_covered"),       # a named farm
    ("EE", "Mäe talu, Kõrve küla, 76401 Saue vald", "not_covered"),
    ("FR", "Lieu-dit Le Bourg, 12345 Village", "not_covered"),
    ("DE", "Postfach 12 34, 10115 Berlin", "invalid_input"),
])
def test_r14_numberless_addresses(cc, text, status):
    out = asyncio.run(core.verify_intl(_Ov(), None, cc, address=text))
    assert out["status"] == status


# ----------------------------------------------------------------------------------------------- R62

class _Up:
    def __init__(self, fail=True):
        self.cache, self.fail, self.calls = TTLCache(3600, 100), fail, 0
        self._key = Upstream._key

    def forget(self, url, params):
        self.cache.pop(self._key(url, params))

    async def _get_json(self, service, url, params):
        hit = self.cache.get(self._key(url, params))
        if hit is not None:
            return hit
        self.calls += 1
        await asyncio.sleep(0.01)
        if self.fail:
            raise UpstreamError(service, "timed out after 25s")
        return {"ok": params}


def test_r62_breaker_counts_each_failure_and_only_a_real_answer_resets_it():
    up = _Up()
    ctx = Ctx(up, None)

    async def wave():
        return await asyncio.gather(*(ctx.get("x", "u", {"q": i}) for i in range(4)), return_exceptions=True)

    asyncio.run(wave())  # four concurrent failures: one wave opens it
    fails, until = up.intl_breaker["x"]
    assert fails == 4 and until > time.monotonic()
    with pytest.raises(UpstreamError, match="not asked"):
        asyncio.run(ctx.get("x", "u", {"q": 9}))
    assert up.calls == 4
    # a cached answer says nothing about the service now; a real one closes the breaker
    up.intl_breaker["x"], up.fail = [2, 0.0], False
    up.cache.set(Upstream._key("u", {"q": "old"}), {"cached": 1})
    assert asyncio.run(ctx.get("x", "u", {"q": "old"})) == {"cached": 1} and up.intl_breaker["x"] == [2, 0.0]
    assert asyncio.run(ctx.get("x", "u", {"q": "new"})) == {"ok": {"q": "new"}} and "x" not in up.intl_breaker


# ------------------------------------------------------------------------------------- Overture / Photon

def _feat(typ, lat, lon, **p):
    p = {"osm_type": "W", "osm_id": hash((typ, lat, lon)) % 10**6, "type": typ, "countrycode": p.pop("cc", "DE"), **p}
    return {"type": "Feature", "geometry": {"type": "Point", "coordinates": [lon, lat]}, "properties": p}


def _row(number, street, lat, lon, pc=None, city="Köln", unit=None, uid=None):
    return {"uuid": uid or f"{street}-{number}-{lat}", "address_number": number, "street": street, "postcode": pc,
            "city": city, "subaddress": {"unit": unit}, "lat": lat, "lon": lon, "state": None, "source": "overture:x",
            "license": "CC0", "updated": None}


class _Overture:
    def __init__(self, by_number, nearby):
        self.by_number, self.nearby, self.asked = by_number, nearby, []

    async def query(self, bbox, number=None, limit=5000, timeout=None, any_part=False):
        self.asked.append((number, limit, any_part))
        if number is None:
            return self.nearby
        return [r for r in self.by_number if bbox["xmin"] <= r["lon"] <= bbox["xmax"] and bbox["ymin"] <= r["lat"] <= bbox["ymax"]]


def _overture(monkeypatch, a, feats, by_number, nearby):
    async def photon(ctx, a, **kw):
        return feats
    monkeypatch.setattr(osm, "photon", photon)
    ov = _Overture(by_number, nearby)
    lk = asyncio.run(osm.overture(_Get(lambda *x: {}, ov=ov), a))
    return lk, _decide(a, lk, "overture", "Overture Maps"), ov


# Köln-Westhoven's Mainstraße as Photon has it (street segments, postcode 51149) and Overture's coded rows
_WESTHOVEN = [_feat("street", 50.899067, 7.0196833, name="Mainstraße", postcode="51149", city="Köln", district="Westhoven",
                    extent=[7.0194555, 50.8992101, 7.0198942, 50.8989595]),
              _feat("street", 50.8978054, 7.0212839, name="Mainstraße", postcode="51149", city="Köln", district="Westhoven",
                    extent=[7.0194555, 50.8989595, 7.0232062, 50.896775])]
_WV = [_row(n, "Mainstr. Wv", 50.8985 - i * 0.0002, 7.0200 + i * 0.0003) for i, n in enumerate(("16", "18", "20"))]


def test_r32_koeln_namesake_in_another_district_is_another_street(monkeypatch):
    a = _a("DE", "Mainstraße 17, 51149 Köln")
    ro17 = _row("17", "Mainstr. Ro", 50.88656, 6.99891)  # Rodenkirchen's Mainstraße, 2 km away
    lk, d, ov = _overture(monkeypatch, a, _WESTHOVEN, [ro17, _row("17", "Neckarstr. Wv", 50.89769, 7.02001)], _WV)
    assert d["status"] == "street_only" and not d["verified"]
    other = next(c for c in lk.cands if c["uuid"] == ro17["uuid"])
    assert other["street"] is None and other["formatted"].startswith("Mainstr. Ro 17")
    assert {s["number"] for s in d["suggestions"]} <= {"16", "18", "20"}
    assert all(c.get("area_postcode") is None for c in lk.cands)  # Photon located the street, not the house
    # Westhoven's own 17 verifies; so does a far number on the same district's street
    lk, d, _ = _overture(monkeypatch, a, _WESTHOVEN, [ro17, _row("17", "Mainstr. Wv", 50.8984, 7.0203)], _WV)
    assert d["status"] == "verified" and d["best_match"]["street"] == "Mainstr."
    far = _row("17", "Mainstr. Wv", 50.9050, 7.0300)  # 1 km along: away from the segments Photon gave
    lk, d, _ = _overture(monkeypatch, a, _WESTHOVEN, [far], _WV)
    assert d["status"] == "verified"
    # no rows of the street near the located point: a coded namesake away from it is not taken
    lk, d, _ = _overture(monkeypatch, a, _WESTHOVEN, [ro17], [])
    assert not d["verified"]


def test_r32_district_codes_split_but_allotment_letters_kept():
    assert osm._district("Mainstr. Ro", "DE") == ("Mainstr.", "Ro")
    assert osm._district("Paulstraße", "DE") == ("Paulstraße", "")
    assert osm._district("Kalscheurer Weg,Weg S", "DE") == ("Kalscheurer Weg,Weg S", "")
    assert osm._district("VIA PO", "IT") == ("VIA PO", None)


def test_r20_area_postcode_only_from_the_located_house():
    origin = (41.90, 12.48)
    area = {"area_postcode": "00186", "area_city": "Roma"}
    near = osm.overture_cand(_row("336", "VIA DEL CORSO", 41.9003, 12.48, city=None), origin, "IT", area)
    away = osm.overture_cand(_row("336", "VIA DEL CORSO", 41.9055, 12.48, city=None), origin, "IT", area)
    assert near["area_postcode"] == "00186" and near["area_city"] == "Roma"  # 33 m
    assert away["area_postcode"] is None and away["area_city"] is None    # 610 m


def test_r20_street_postcode_not_borrowed(monkeypatch):
    # Via del Corso: Photon locates street segments with 00187; number 336 is in 00186. No postcode verdict then.
    a = _a("IT", "Via del Corso 336, 00186 Roma")
    feats = [_feat("street", 41.9035, 12.4800, name="Via del Corso", postcode="00187", city="Roma", cc="IT",
                   extent=[12.4795, 41.9050, 12.4805, 41.9020])]
    lk, d, _ = _overture(monkeypatch, a, feats, [_row("336", "VIA DEL CORSO", 41.8990, 12.4810, city="Roma")], [])
    assert d["status"] == "verified" and d["best_match"]["match"]["zip_match"] is None
    assert not any("postcode differs" in n for n in d["notes"]) and d["confidence"] == 0.9


def test_r20_a_borrowed_house_postcode_is_named_as_openstreetmap():
    a = _a("IT", "Via del Corso 305, 00186 Roma")
    c = {**_rec("305", "VIA DEL CORSO", None, "Roma"), "area_postcode": "00187", "city_loose": True}
    out = core._apply(core._base("IT", a.raw), "overture", Lookup(cands=[c]), a, 5)
    assert out["status"] == "verified" and "postcode differs: input 00186 vs OpenStreetMap 00187" in out["notes"]


def test_r55_area_city_never_overrides_a_rows_own_town_and_no_county():
    origin = (49.14, 9.28)
    area = {"area_postcode": None, "area_city": "Erlenbach"}
    own = osm.overture_cand(_row("5", "Hauptstraße", 49.1401, 9.28, pc="74189", city="Weinsberg"), origin, "DE", area)
    none = osm.overture_cand(_row("5", "Hauptstraße", 49.1401, 9.28, city=None), origin, "DE", area)
    assert none["area_city"] == "Erlenbach"
    # on a row with a town of its own, Photon's town can only show that the input's is another, never confirm it
    from app.intl.match import score
    assert score(_a("DE", "Hauptstraße 5, 74235 Erlenbach"), own)["match"]["place"] is False
    assert score(_a("DE", "Hauptstraße 5, Erlenbach"), own)["match"]["place"] is not True
    stockport = _feat("house", 53.41, -2.16, housenumber="12", street="High Street", postcode="SK1 1AA", city="Stockport",
                      county="Greater Manchester", cc="GB")
    assert osm._towns(stockport["properties"]) == ["Stockport"]
    assert osm.place_ok(stockport, _a("GB", "12 High Street, Manchester")) is False


def test_s20_slovak_number_parts(monkeypatch):
    feats = [_feat("street", 48.1480, 17.1100, name="Obchodná", postcode="811 06", city="Bratislava", cc="SK",
                   extent=[17.1050, 48.1490, 17.1150, 48.1470])]
    rows = [_row("553/25", "Obchodná", 48.1482, 17.1102, pc="81106", city="Bratislava-Staré Mesto"),
            _row("426/24", "Obchodná", 48.1483, 17.1104, pc="81106", city="Bratislava-Staré Mesto")]
    for text, best in (("Obchodná 25, 811 06 Bratislava", "25"), ("Obchodná 553, 811 06 Bratislava", "553"),
                       ("Obchodná 553/25, 811 06 Bratislava", "553/25")):
        lk, d, ov = _overture(monkeypatch, _a("SK", text), feats, rows, rows)
        assert d["status"] == "verified" and d["best_match"]["number"] == best, text
        assert d["best_match"]["formatted"].startswith("Obchodná 553/25")
    lk, d, _ = _overture(monkeypatch, _a("SK", "Obchodná 999, 811 06 Bratislava"), feats, [], rows)
    assert d["status"] == "street_only" and len(d["suggestions"]) == len({s["formatted"] for s in d["suggestions"]}) == 2


# ---------------------------------------------------------------------------------------------- europe

def _ban(label, street, number, pc, city, oldcity=None):
    return {"type": "Feature", "geometry": {"type": "Point", "coordinates": [3.06, 50.63]},
            "properties": {"label": label, "type": "housenumber", "housenumber": number, "street": street, "postcode": pc,
                           "city": city, "oldcity": oldcity, "id": label}}


def test_r18_ban_commune_suffix():
    data = {"features": [_ban("10 Rue Léon Gambetta (Lille) 59000 Lille", "Rue Léon Gambetta (Lille)", "10", "59000", "Lille", "Lille"),
                         _ban("10 Rue Léon Gambetta (Hellemmes) 59260 Lille", "Rue Léon Gambetta (Hellemmes)", "10", "59260",
                              "Lille", "Hellemmes")]}
    cands = europe.fr_ban_cands(data)
    assert [(c["street"], c["city"], c["street_alt"]) for c in cands] == [
        ("Rue Léon Gambetta", "Lille", ["Rue Léon Gambetta (Lille)"]),
        ("Rue Léon Gambetta", "Hellemmes", ["Rue Léon Gambetta (Hellemmes)"])]
    d = decide(_a("FR", "10 Rue Léon Gambetta, 59000 Lille"), cands, authority="official", dataset="BAN")
    assert d["status"] == "verified" and d["best_match"]["postcode"] == "59000" and d["confidence"] == 0.97
    d = decide(_a("FR", "10 Rue Léon Gambetta, 59260 Hellemmes"), cands, authority="official", dataset="BAN")
    assert d["status"] == "verified" and d["best_match"]["postcode"] == "59260"
    # Lomme's Rue Jean Jaurès (59160) is not Lille's: no 5 in 59000 -> not verified
    lomme = europe.fr_ban_cands({"features": [_ban("5 Rue Jean Jaurés (Lomme) 59160 Lille", "Rue Jean Jaurés (Lomme)", "5",
                                                   "59160", "Lille", "Lomme")]})
    assert not decide(_a("FR", "5 Rue Jean Jaurès, 59000 Lille"), lomme, authority="official", dataset="BAN")["verified"]
    # a bracket that is not the commune stays part of the name
    assert europe.fr_ban_cands({"features": [_ban("1 Rue X (Prolongée) 1 A", "Rue X (Prolongée)", "1", "01000", "A")]})[0][
        "street"] == "Rue X (Prolongée)"


def _inads(nr, settlement, municipality, pc, flat=""):
    return {"liikVal": "EHITISHOONE", "liikluspind": "Narva mnt", "aadresstekst": "Narva mnt 7", "aadress_nr": nr,
            "kort_nr": flat, "asustusyksus": settlement, "omavalitsus": municipality, "sihtnumber": pc,
            "kvaliteet": "tapne_nr", "adr_id": f"{settlement}{flat}"}


def test_r19_estonian_settlement_counts_as_the_town():
    cands = europe.ee_cands({"addresses": [_inads("7", "Voka alevik", "Jõhvi vald", "41701")]})
    assert cands[0]["city_alt"] == ["Voka alevik", "Voka", "Jõhvi"]  # r3 [31]: the municipality without 'vald' too
    for text in ("Narva mnt 7, Voka", "Narva mnt 7, Voka alevik, Toila vald"):
        assert decide(_a("EE", text), cands, authority="official", dataset="In-ADS")["status"] == "verified", text
    assert decide(_a("EE", "Narva mnt 7, Tartu"), cands, authority="official", dataset="In-ADS")["status"] == "not_found"
    # two settlements of that name with the address: the settlement doesn't say which
    two = {"addresses": [_inads("7", "Voka alevik", "Jõhvi vald", "41701"), _inads("7", "Voka küla", "Saue vald", "76401")]}
    a = _a("EE", "Narva mnt 7, Voka")
    assert not _decide(a, asyncio.run(europe.ee_inads(_Get(lambda s, p: two), a)))["verified"]


def _kartverket(*towns):
    rows = [{"adressenavn": "Storgata", "nummer": 11, "postnummer": pc, "poststed": town, "kommunenavn": "VÅGAN",
             "adressetekst": "Storgata 11", "adressekode": i} for i, (pc, town) in enumerate(towns)]
    return lambda s, p: {"adresser": [] if "poststed" in p else rows}


def test_r19_norwegian_municipality_counts_as_the_town_when_it_says_which():
    a = _a("NO", "Storgata 11, Vågan")
    lk = asyncio.run(europe.no_kartverket(_Get(_kartverket(("8300", "SVOLVÆR"))), a))
    d = _decide(a, lk)
    assert d["status"] == "verified" and d["best_match"]["city"] == "SVOLVÆR"
    # Vågan has a Storgata 11 in Svolvær and in Kabelvåg: the municipality doesn't say which
    lk = asyncio.run(europe.no_kartverket(_Get(_kartverket(("8300", "SVOLVÆR"), ("8310", "KABELVÅG"))), a))
    assert not _decide(a, lk)["verified"]


def _ruian(*addrs):
    return {"candidates": [{"address": x, "location": {"x": 16.73, "y": 49.29}, "attributes": {"Type": "AdresniMisto"}}
                           for x in addrs]}


@pytest.mark.parametrize("text, best", [
    ("č.p. 12, 679 05 Křtiny", "č.p. 12, 67905 Křtiny"), ("čp. 12, 679 05 Křtiny", "č.p. 12, 67905 Křtiny"),
    ("č. p. 12, 679 05 Křtiny", "č.p. 12, 67905 Křtiny"), ("Křtiny 12, 679 05 Křtiny", "č.p. 12, 67905 Křtiny"),
    ("č.ev. 12, 679 05 Křtiny", "č.ev. 12, 67905 Křtiny"), ("Křtiny č.ev. 12, 679 05 Křtiny", "č.ev. 12, 67905 Křtiny"),
])
def test_r28_s17_czech_cp_and_cev(text, best):
    get = _Get(lambda s, p: _ruian("č.p. 12, 67905 Křtiny", "č.ev. 12, 67905 Křtiny"))
    a = _a("CZ", text)
    lk = asyncio.run(europe.cz_ruian(get, a))
    d = _decide(a, lk)
    assert d["status"] == "verified" and d["best_match"]["formatted"] == best
    assert "čp." not in get.asked[0]["SingleLine"]  # asked the way the register writes it


def test_r28_cp_and_cev_stay_distinct():
    for text, only in (("č.p. 12, 679 05 Křtiny", "č.ev. 12, 67905 Křtiny"), ("č.ev. 12, 679 05 Křtiny", "č.p. 12, 67905 Křtiny")):
        a = _a("CZ", text)
        lk = asyncio.run(europe.cz_ruian(_Get(lambda s, p, only=only: _ruian(only)), a))
        assert not _decide(a, lk)["verified"], text


def _ryhti(n, n2=None, key="27d08ca7"):
    return {"properties": {"address_key": key, "address_fin": f"Pohjoisesplanadi {n}-{n2}" if n2 else f"Pohjoisesplanadi {n}",
                           "address_name_fin": "Pohjoisesplanadi", "address_name_swe": "Norra Esplanaden",
                           "number_part_of_address_number": n, "number_part_of_address_number2": n2, "postal_code": "00170",
                           "postal_office_fin": "HELSINKI", "postal_office_swe": "HELSINGFORS"}, "geometry": None}


def test_r31_a_range_record_is_not_listed_again():
    street = {"features": [_ryhti(11, 13), _ryhti(5, key="k5"), _ryhti(7, key="k7")]}
    get = _Get(lambda s, p: {"features": [_ryhti(11, 13)]} if "number_part" in p["filter"] else street)
    a = _a("FI", "Pohjoisesplanadi 13, 00170 Helsinki")
    lk = asyncio.run(europe.fi_ryhti(get, a))
    assert [c["uuid"] for c in lk.cands].count("27d08ca7") == 1
    d = _decide(a, lk)
    assert d["status"] == "verified" and d["candidates"] == []


def test_r12_estonian_flat_written_korter_is_asked_as_number_dash_flat():
    def answer(s, p):
        if p["address"].startswith("Narva mnt 7-1"):
            return {"addresses": [_inads("7", "Kesklinna linnaosa", "Tallinn", "10117", flat="1")]}
        return {"addresses": [_inads("7", "Kesklinna linnaosa", "Tallinn", "10117")]}
    get = _Get(answer)
    a = _a("EE", "Narva mnt 7, korter 1, Tallinn")
    lk = asyncio.run(europe.ee_inads(get, a))
    assert get.asked[0]["address"] == "Narva mnt 7-1, Tallinn"
    d = _decide(a, lk)
    assert d["status"] == "verified" and d["match_level"] == "unit"
    a = _a("EE", "Narva mnt 7, korter 99, Tallinn")  # no such flat: the building is still found
    assert _decide(a, asyncio.run(europe.ee_inads(_Get(answer), a)))["status"] == "unit_not_found"


def _vzd(*numbers):
    return {"result": {"records": [{"NOSAUKUMS": n, "STD": f"Brīvības gatve {n}, Rīga, LV-1039", "ATRIB": "LV-1039",
                                    "KODS": i, "DD_N": 56.97, "DD_E": 24.16} for i, n in enumerate(numbers)]}}


def test_s18_latvian_block_is_not_the_letter_house():
    # the parser reads '214 k2' / '214 k-2' as '214k-2' (block 2 of house 214); VZD has the houses 214 and 214K only
    def answer(s, p):
        return _vzd() if "k-2" in p["q"] else _vzd("214", "214K", "216")
    a = IntlAddress(raw="Brīvības gatve 214 k2, Rīga", country="LV", street="Brīvības gatve", number="214k-2", city="Rīga")
    d = _decide(a, asyncio.run(europe.lv_vzd(_Get(answer), a)))
    assert d["status"] == "street_only" and not d["verified"] and "214" in [s["number"] for s in d["suggestions"]]
    a = IntlAddress(raw="Brīvības gatve 386 k2, Rīga", country="LV", street="Brīvības gatve", number="386k-2", city="Rīga")
    d = _decide(a, asyncio.run(europe.lv_vzd(_Get(lambda s, p: _vzd("386 k-2", "386")), a)))
    assert d["status"] == "verified" and d["best_match"]["number"] == "386 k-2"


def _ch(*labels):
    return {"results": [{"attrs": {"label": lab, "lat": 47.37, "lon": 8.54, "featureId": lab}} for lab in labels]}


def test_s15_a_number_on_another_street_does_not_stop_the_retry():
    def answer(s, p):
        if "8002" in p["searchText"]:
            return _ch("Brunaustrasse 10 <b>8002 Zürich</b>", "Gartenstrasse 10 <b>8002 Zürich</b>")
        return _ch("Bahnhofstrasse 10 <b>8001 Zürich</b>")
    get = _Get(answer)
    a = _a("CH", "Bahnhofstrasse 10, 8002 Zürich")
    d = _decide(a, asyncio.run(europe.ch_geoadmin(get, a)))
    assert len(get.asked) == 2 and d["status"] == "verified" and d["best_match"]["postcode"] == "8001"
    assert any("postcode differs" in n for n in d["notes"])
    # Luxembourg's second query leaves the postcode out
    get = _Get(lambda s, p: {"results": []})
    asyncio.run(europe.lu_geoportail(get, _a("LU", "42 Place Guillaume II, 1650 Luxembourg")))
    assert [p["queryString"] for p in get.asked] == ["42 Place Guillaume II 1650 Luxembourg", "42 Place Guillaume II Luxembourg"]


# ---------------------------------------------------------------------------------------------- S42, S45

def test_s42_overture_breaker(monkeypatch):
    client = ovmod.OvertureClient(None)
    calls = []

    async def release(refresh=False):
        return "2026-09-23.1"

    async def run(release, bbox, number, limit, timeout, any_part=False):
        calls.append(number)
        if number != 7:
            raise UpstreamError("overture", "query timed out after 30s")
        return []

    monkeypatch.setattr(client, "release", release)
    monkeypatch.setattr(client, "_run", run)
    box = {"xmin": 0, "ymin": 0, "xmax": 1, "ymax": 1}
    for n in range(3):
        with pytest.raises(UpstreamError, match="timed out"):
            asyncio.run(client.query(box, number=n))
    with pytest.raises(UpstreamError, match="not asked"):
        asyncio.run(client.query(box, number=3))
    assert calls == [0, 1, 2]
    client._closed_until = 0.0  # a minute later: asked again, and a real answer closes it
    assert asyncio.run(client.query(box, number=7)) == [] and client._fails == 0


def test_s45_gb_postcode_data_is_credited(monkeypatch):
    async def uk_postcode(ctx, a, notes):
        return 51.5034, -0.1276

    async def photon(ctx, a, **kw):
        return [_feat("house", 51.5034, -0.1276, housenumber="10", street="Downing Street", postcode="SW1A 2AA",
                      city="London", cc="GB")]
    monkeypatch.setattr(osm, "uk_postcode", uk_postcode)
    monkeypatch.setattr(osm, "photon", photon)
    a = _a("GB", "10 Downing Street, London SW1A 2AA")
    lk = asyncio.run(osm.osm(_Get(lambda *x: {}), a))
    out = core._apply(core._base("GB", a.raw), "osm", lk, a, 5)
    assert out["status"] == "verified" and "postcodes.io" in out["provider"]["attribution"]
    assert out["provider"]["attribution"].startswith("© OpenStreetMap contributors")
    assert "postcodes.io" not in core._provider("osm")["attribution"]
