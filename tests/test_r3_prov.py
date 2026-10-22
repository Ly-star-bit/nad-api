"""Round-3 provider repairs, offline: the audit's reproductions with canned upstream answers shaped like the live ones
(Overture's DuckDB task, Kartverket, HMS, GURS, In-ADS, RÚIAN, PDOK, VZD)."""

import asyncio

import pytest

from app import overture as ovmod
from app.intl import core, europe
from app.intl import countries as C
from app.intl.base import Ctx
from app.intl.match import decide
from app.intl.parse import parse_line
from app.upstream import TTLCache, UpstreamError

CC = C.COUNTRIES


def _a(cc, text):
    return parse_line(text, CC[cc])


def _decide(a, lk):
    return decide(lk.view or a, lk.cands, authority="official", dataset="X", unit_support=lk.unit_support,
                  street_found=lk.street_found, area_empty=lk.area_empty, incomplete=lk.incomplete)


class _Get:
    """ctx.get with canned answers: answer(service, params) -> JSON; the params asked are kept."""

    def __init__(self, answer):
        self.answer, self.asked = answer, []

    async def get(self, service, url, params, *, expect=dict):
        self.asked.append(params)
        return self.answer(service, params)


# ------------------------------------------------------------------------------------------- [36], [55]

def _client(monkeypatch, fail=lambda bbox, number: "query timed out after 20s"):
    client, runs = ovmod.OvertureClient(None), []

    async def release(refresh=False):
        return "2026-09-23.1"

    async def run(release, bbox, number, limit, timeout, any_part=False, theme="addresses"):
        runs.append((bbox["xmin"], number))
        await asyncio.sleep(0.01)
        if msg := fail(bbox, number):
            raise UpstreamError("overture", msg)
        return []

    monkeypatch.setattr(client, "release", release)
    monkeypatch.setattr(client, "_run", run)
    return client, runs


_US = {"xmin": -122.1, "ymin": 37.4, "xmax": -122.0, "ymax": 37.5}
_US2 = {"xmin": -112.1, "ymin": 33.4, "xmax": -112.0, "ymax": 33.5}


async def _ask(q, box, number):
    try:
        return await q(box, number=number)
    except UpstreamError as exc:
        return exc.message


def test_r3_36_one_shared_failed_query_counts_once(monkeypatch):
    client, runs = _client(monkeypatch, fail=lambda bbox, number: "query timed out after 20s" if number == 108 else None)

    async def main():
        # three rows of a batch on one street share ONE DuckDB query that times out once
        out = await asyncio.gather(*(_ask(client.query, _US, 108) for _ in range(3)))
        return out, client._fails, await _ask(client.query, _US2, 200)

    out, fails, other = asyncio.run(main())
    assert out == ["query timed out after 20s"] * 3 and fails == 1
    assert other == [] and runs == [(-122.1, 108), (-112.1, 200)]  # the next US address is still asked
    assert client._fails == 0  # ... and its answer clears the count


def test_r3_55_duplicate_rows_need_three_failed_queries_and_intl_has_its_own_breaker(monkeypatch):
    client, runs = _client(monkeypatch)
    up = type("Up", (), {"cache": TTLCache(60, 100)})()
    ctx = Ctx(up, client)

    async def main():
        # 2 failed queries shared by 6 rows: still open
        for n in (1, 2):
            await asyncio.gather(*(_ask(ctx.ov.query, _US, n) for _ in range(3)))
        second = up.intl_breaker["overture"][0]
        third = await _ask(ctx.ov.query, _US, 3)
        refused = await _ask(ctx.ov.query, _US, 4)  # the European lookups' breaker is open now ...
        us = await _ask(client.query, _US2, 5)       # ... the US fallback's is not
        return second, third, refused, us

    second, third, refused, us = asyncio.run(main())
    assert second == 2 and third.endswith("timed out after 20s") and refused.startswith("not asked")
    assert us.endswith("timed out after 20s") and client._fails == 1
    assert [n for _, n in runs] == [1, 2, 3, 5]


def test_r3_36_an_error_that_comes_back_at_once_does_not_open_the_breaker(monkeypatch):
    client, runs = _client(monkeypatch, fail=lambda bbox, number: "IO Error: HTTP 500 (Internal Server Error)")
    for n in range(4):
        with pytest.raises(UpstreamError, match="HTTP 500"):
            asyncio.run(client.query(_US, number=n))
    assert len(runs) == 4 and client._fails == 0


# ------------------------------------------------------------------------------------------- [19], [61]

_RURAL = ["IT | Località Pian di Rota, 57121 Livorno", "FR | Le Bourg, 24260 Le Bugue", "FR | La Grange Neuve, 24000 Périgueux",
          "ES | Lugar de Outeiro, 15800 Melide", "PT | Quinta da Marinha, 2750-715 Cascais", "DE | Gut Wendlingen, 73230 Kirchheim"]
_RURAL_61 = ["IT | Località Valle, 53024 Montalcino SI", "ES | Diseminado Los Llanos, 30880 Águilas",
             "PT | Lugar de Cima, 4900-123 Viana do Castelo", "FR | Le Bourg, 24260 Le Bugue", "DE | Gut Hohenholz, 50189 Elsdorf"]
_RURAL_MORE = ["FR | Lieu-dit Les Granges, 24260 Le Bugue", "IT | Frazione San Carlo, 10010 Torino",
               "IT | Loc. Pian di Rota, 57121 Livorno", "IT | Contrada Fiumara, 98100 Messina",
               "IT | Podere Le Lame, 53024 Montalcino", "ES | Paraje La Hoya, 30880 Águilas", "ES | Partida Alted, 03203 Elche",
               "PT | Casal do Marco, 2840-001 Seixal", "FR | Hameau de la Croix, 24260 Le Bugue",
               "FR | Domaine de la Rivière, 24260 Le Bugue", "DE | Hof Wendlingen, 73230 Kirchheim",
               "DE | Gutshof Wendlingen, 73230 Kirchheim"]
# a street with its number left out stays invalid_input
_STREETS = ["DE | Hauptstraße, 80331 München", "DE | Hofstraße, 80331 München", "FR | Rue du Bourg, 24260 Le Bugue",
            "DE | Gutleutstraße, 60329 Frankfurt", "IT | Via Roma, 00184 Roma", "FR | Le Bourgneuf, 24260 Le Bugue"]


class _NoUpstream:
    ov = object()

    async def get(self, *args, **kw):
        raise AssertionError("no source is asked without a house number")


def _verify(line):
    cc, text = line.split(" | ", 1)
    return asyncio.run(core.verify_intl(_NoUpstream(), None, cc, address=text))


@pytest.mark.parametrize("line", _RURAL)
def test_r3_19_rural_place_without_number_is_not_covered(line):
    out = _verify(line)
    assert out["status"] == "not_covered", out
    assert any("without a house number" in n for n in out["notes"])


@pytest.mark.parametrize("line", _RURAL_61 + _RURAL_MORE)
def test_r3_61_rural_place_without_number_is_not_covered(line):
    assert _verify(line)["status"] == "not_covered"


@pytest.mark.parametrize("line", _STREETS)
def test_r3_19_street_without_number_stays_invalid_input(line):
    out = _verify(line)
    assert out["status"] == "invalid_input" and "the address has no house number" in out["notes"]


# ----------------------------------------------------------------------------------------------- [31]

def test_r3_31_estonian_municipality_without_vald():
    rec = {"liikVal": "EHITISHOONE", "liikluspind": "Narva mnt", "aadresstekst": "Narva mnt 7", "aadress_nr": "7",
           "kort_nr": "", "asustusyksus": "Voka alevik", "omavalitsus": "Jõhvi vald", "sihtnumber": "41701",
           "kvaliteet": "tapne_nr", "adr_id": "1"}
    a = _a("EE", "Narva mnt 7, Jõhvi")
    lk = asyncio.run(europe.ee_inads(_Get(lambda s, p: {"addresses": [rec]}), a))
    d = _decide(a, lk)
    assert d["status"] == "verified" and d["best_match"]["formatted"] == "Narva mnt 7, Voka alevik, 41701 Jõhvi vald"
    assert not any("other places" in n for n in d["notes"])
    # a town named after neither the settlement nor the municipality is still another place
    assert decide(_a("EE", "Narva mnt 7, Tartu"), lk.cands, authority="official", dataset="In-ADS")["status"] == "not_found"


# ------------------------------------------------------------------------------------------------ S15

def _ruian(*addrs):
    return {"candidates": [{"address": x, "location": {"x": 14.42, "y": 50.08}, "attributes": {"Type": "AdresniMisto"}}
                           for x in addrs]}


def test_r3_s15_czech_street_without_the_wrong_psc():
    wenceslas = [f"Václavské náměstí {n}, Nové Město, 11000 Praha 1" for n in ("841/3", "781/20", "791/32", "821/39")]

    def answer(service, p):
        q = p["SingleLine"]
        if q == "Václavské náměstí, Praha 1":
            return _ruian(*wenceslas, "Václavské náměstí 846/1, Nové Město, 11000 Praha 1")
        return _ruian(*wenceslas)  # the number queries and the street with 12000: no 846/1

    a = _a("CZ", "Václavské náměstí 1, 120 00 Praha 1")
    get = _Get(answer)
    d = _decide(a, asyncio.run(europe.cz_ruian(get, a)))
    assert [p["SingleLine"] for p in get.asked] == ["Václavské náměstí 1, 12000 Praha 1", "Václavské náměstí 1, Praha 1",
                                                    "Václavské náměstí, 12000 Praha 1", "Václavské náměstí, Praha 1"]
    assert d["status"] == "verified" and d["best_match"]["formatted"].startswith("Václavské náměstí 846/1, ")
    assert any("postcode differs" in n for n in d["notes"])
    # no town: the street is not asked for nationwide
    get = _Get(lambda s, p: _ruian(*wenceslas))
    asyncio.run(europe.cz_ruian(get, _a("CZ", "Václavské náměstí 1, 120 00")))
    assert [p["SingleLine"] for p in get.asked] == ["Václavské náměstí 1, 12000", "Václavské náměstí 1",
                                                    "Václavské náměstí, 12000"]
    # found with the PSČ: not asked again
    get = _Get(lambda s, p: _ruian("Václavské náměstí 846/1, Nové Město, 11000 Praha 1"))
    asyncio.run(europe.cz_ruian(get, _a("CZ", "Václavské náměstí 1, 110 00 Praha 1")))
    assert len(get.asked) == 1


# ---------------------------------------------------------------------------------------------- S10 (b)

def _kv(rows):
    return {"adresser": [{"adressenavn": st, "adressetekst": f"{st} {n}", "nummer": int(n.rstrip("AB")), "bokstav": n[-1].strip("0123456789"),
                          "postnummer": pc, "poststed": town, "kommunenavn": muni, "adressekode": i,
                          "bruksenhetsnummer": [], "representasjonspunkt": {"lat": 60.0, "lon": 10.0}}
                         for i, (st, n, pc, town, muni) in enumerate(rows)]}


def _no_answer(service, p):
    st, n, pc, town = p.get("adressenavn"), p.get("nummer"), p.get("postnummer"), p.get("poststed")
    if st == "Storgata":
        if n and not pc and not town:  # nationwide: Storgata 11 in five other towns, not in Tromsø
            return _kv([("Storgata", "11", "2335", "STANGE", "STANGE"), ("Storgata", "11", "8300", "SVOLVÆR", "VÅGAN")])
        if not n and pc == "9008":
            return _kv([("Storgata", x, "9008", "TROMSØ", "TROMSØ") for x in ("1", "10", "12")])
    if st == "Drammensveien":
        if n and not pc and not town:
            return _kv([("Drammensveien", "30", "1808", "ASKIM", "INDRE ØSTFOLD")])
        if not n and town == "Oslo":  # the street is in Oslo, but not with postcode 0255
            return _kv([("Drammensveien", "103", "0273", "OSLO", "OSLO")])
    return {"adresser": []}


def _gis(features):
    return {"type": "FeatureCollection", "features": [{"type": "Feature", "geometry": {"type": "Point", "coordinates": [0, 0]},
                                                       "properties": p} for p in features]}


def _is_answer(service, p):
    q = p["CQL_FILTER"]
    rec = lambda n, pc: {"HEITI_NF": "Aðalstræti", "HEITI_TGF": "Aðalstræti", "HUSNR": n, "POSTNR": pc, "HEINUM": n * 1000 + pc}  # noqa: E731
    if "HUSNR=20" in q:
        return _gis([] if "POSTNR=101" in q else [rec(20, 400), rec(20, 415)])
    return _gis([rec(n, 101) for n in (10, 12, 16)] if "POSTNR=101" in q else [])


def _si_answer(service, p):
    q = p["filter"]
    rec = lambda n, pc, town: {"ULICA_NAZIV": "Prešernova cesta", "NASELJE_NAZIV": town, "HS_STEVILKA": n,  # noqa: E731
                               "POSTNI_OKOLIS_SIFRA": pc, "POSTNI_OKOLIS_NAZIV": town, "EID_HISNA_STEVILKA": f"{pc}{n}"}
    if "HS_STEVILKA=1" in q:
        return _gis([] if "8270" in q else [rec(1, 8250, "Brežice"), rec(1, 1230, "Domžale")])
    return _gis([rec(n, 8270, "Krško") for n in (4, 7, 9)] if "8270" in q else [])


@pytest.mark.parametrize("cc,text,provider,answer", [
    ("NO", "Storgata 11, 9008 Tromsø", europe.no_kartverket, _no_answer),
    ("NO", "Drammensveien 30, 0255 Oslo", europe.no_kartverket, _no_answer),
    ("IS", "Aðalstræti 20, 101 Reykjavík", europe.is_hms, _is_answer),
    ("SI", "Prešernova cesta 1, 8270 Krško", europe.si_gurs, _si_answer),
])
def test_r3_s10_number_only_in_other_towns_but_street_in_the_town_is_street_only(cc, text, provider, answer):
    a = _a(cc, text)
    d = _decide(a, asyncio.run(provider(_Get(answer), a)))
    assert d["status"] == "street_only" and d["best_match"] is None, d
    # never the same number in another town (8ed3bec verified Storgata 11, 2335 STANGE for Tromsø)
    assert all(a.city.casefold() in s["formatted"].casefold() or a.postcode in s["formatted"] for s in d["suggestions"])


def test_r3_s10_street_not_in_the_town_either_stays_not_found():
    a = _a("NO", "Storgata 11, 0255 Oslo")
    d = _decide(a, asyncio.run(europe.no_kartverket(_Get(_no_answer), a)))
    assert d["status"] == "not_found" and any("other places" in n for n in d["notes"])


# ------------------------------------------------------------------------------------------------ S12

def test_r3_s12_kartverket_fuzzy_retry_stays_in_the_postcode():
    def answer(service, p):
        if p.get("sok") and p.get("postnummer") == "0155":
            return _kv([("Storgata", "11", "0155", "OSLO", "OSLO")])
        if p.get("sok"):
            return _kv([("Storgata", "11", "2335", "STANGE", "STANGE")])
        return {"adresser": []}

    a = _a("NO", "Storgt. 11, 0155 Oslo")
    get = _Get(answer)
    d = _decide(a, asyncio.run(europe.no_kartverket(get, a)))
    assert d["status"] == "verified" and d["best_match"]["formatted"] == "Storgata 11, 0155 OSLO"
    assert [p.get("postnummer") for p in get.asked if p.get("sok")] == ["0155"]  # the nationwide one isn't needed


def test_r3_s12_kartverket_fuzzy_retry_in_the_town_keeps_its_street():
    def answer(service, p):
        if p.get("sok") and p.get("poststed") == "Oslo":  # Oslo's Storgata 10 is 10A and 10B
            return _kv([("Storgata", "10A", "0155", "OSLO", "OSLO"), ("Storgata", "10B", "0155", "OSLO", "OSLO")])
        if p.get("sok") and not p.get("postnummer"):
            return _kv([("Storgata", "10", "1850", "MYSEN", "INDRE ØSTFOLD")])
        return {"adresser": []}

    a = _a("NO", "Storgt. 10, 0184 Oslo")
    get = _Get(answer)
    d = _decide(a, asyncio.run(europe.no_kartverket(get, a)))
    assert d["status"] == "street_only" and d["suggestions"][0]["formatted"] == "Storgata 10A, 0155 OSLO"
    assert [(p.get("postnummer"), p.get("poststed")) for p in get.asked if p.get("sok")] == [("0184", None), (None, "Oslo"),
                                                                                              (None, None)]


# ------------------------------------------------------------------------------------------------ R59

def _pdok(*docs):
    return {"response": {"numFound": len(docs), "docs": [
        {"type": "adres", "id": f"{n}{u}", "weergavenaam": f"Damrak {n}{'-' + u if u else ''}, 1012LG Amsterdam",
         "straatnaam": "Damrak", "huisnummer": n, "huisnummertoevoeging": u or None, "postcode": "1012LG",
         "woonplaatsnaam": "Amsterdam", "centroide_ll": "POINT(4.89 52.37)"} for n, u in docs]}}


@pytest.mark.parametrize("text", ["Damrak 1 hs, 1012 LG Amsterdam", "Damrak 1-hs, 1012 LG Amsterdam"])
def test_r3_r59_dutch_hs_is_the_ground_floor_not_an_addition(text):
    a = _a("NL", text)
    lk = asyncio.run(europe.nl_pdok(_Get(lambda s, p: _pdok((1, ""))), a))
    d = _decide(a, lk)
    assert d["status"] == "verified" and d["match_level"] == "address" and d["best_match"]["formatted"] == "Damrak 1, 1012LG Amsterdam"
    assert any("ground floor" in n for n in lk.notes)


def test_r3_r59_hs_is_the_addition_h_and_other_additions_are_still_checked():
    a = _a("NL", "Damrak 1 hs, 1012 LG Amsterdam")
    lk = asyncio.run(europe.nl_pdok(_Get(lambda s, p: _pdok((1, "H"), (1, "1"), (1, "2"))), a))
    d = _decide(a, lk)
    assert d["status"] == "verified" and d["best_match"]["formatted"] == "Damrak 1-H, 1012LG Amsterdam"
    for text in ("Damrak 1-2, 1012 LG Amsterdam", "Damrak 1 H, 1012 LG Amsterdam"):  # one that BAG doesn't have
        a = _a("NL", text)
        assert _decide(a, asyncio.run(europe.nl_pdok(_Get(lambda s, p: _pdok((1, ""))), a)))["status"] == "unit_not_found"


# ------------------------------------------------------------------------------------------------ S19

def test_r3_s19_latvian_skip_note_blames_the_spelling_only_when_it_can_be():
    for text, note in (("Brīvības iela 40, Jelgava", "no street 'Brīvības iela' in Jelgava in the register"),
                       ("Brivibas iela 40, Riga", "it needs the Latvian spelling")):
        with pytest.raises(europe.Skip) as exc:
            asyncio.run(europe.lv_vzd(_Get(lambda s, p: {"result": {"records": []}}), _a("LV", text)))
        assert note in str(exc.value) and ("spelling" in str(exc.value)) == ("Brivibas" in text)


# ------------------------------------------------------------------------- R59 follow-up: Roman floors

def _steen(*units):
    return {"response": {"numFound": len(units), "docs": [
        {"type": "adres", "id": f"2{u}", "weergavenaam": f"Eerste Jan Steenstraat 2{'-' + u if u else ''}, 1072NK Amsterdam",
         "straatnaam": "Eerste Jan Steenstraat", "huisnummer": 2, "huisnummertoevoeging": u or None, "postcode": "1072NK",
         "woonplaatsnaam": "Amsterdam", "centroide_ll": "POINT(4.887 52.353)"} for u in units]}}


def _floor(text, unit):
    """The address as the parser reads it now: number 2, unit 'III' (a Roman floor)."""
    a = _a("NL", text)
    a.number, a.unit = "2", unit
    return a


@pytest.mark.parametrize("units,unit,formatted,note", [
    (("", "1", "2", "3"), "III", "Eerste Jan Steenstraat 2-3, 1072NK Amsterdam", "floor III read as BAG's addition 3"),
    # (("", "1"), "III"): the building's floors are additions and III isn't one: unit_not_found since r4 [17]
    (("",), "IV", "Eerste Jan Steenstraat 2, 1072NK Amsterdam", "floor IV is not a BAG addition; the building exists"),
    (("", "III"), "III", "Eerste Jan Steenstraat 2-III, 1072NK Amsterdam", None),  # BAG's own addition stands
])
def test_r3_r59_roman_floor_is_the_arabic_addition_or_the_building(units, unit, formatted, note):
    a = _floor("Eerste Jan Steenstraat 2-III, 1072 NK Amsterdam", unit)
    lk = asyncio.run(europe.nl_pdok(_Get(lambda s, p: _steen(*units)), a))
    d = _decide(a, lk)
    assert d["status"] == "verified" and d["best_match"]["formatted"] == formatted, d
    assert (note in lk.notes) if note else not lk.notes


def test_r3_r59_roman_floor_parsed_from_the_number_and_arabic_units_still_checked():
    a = _a("NL", "Eerste Jan Steenstraat 2-III, 1072 NK Amsterdam")  # this copy's parse: number '2-III'
    d = _decide(a, asyncio.run(europe.nl_pdok(_Get(lambda s, p: _steen("", "2", "3")), a)))
    assert d["status"] == "verified" and d["best_match"]["formatted"] == "Eerste Jan Steenstraat 2-3, 1072NK Amsterdam"
    for text, units in (("Eerste Jan Steenstraat 2-5, 1072 NK Amsterdam", ("", "1", "2")),  # a real missing addition
                        ("Damrak 1-2, 1012 LG Amsterdam", None)):
        a = _a("NL", text)
        answer = (lambda s, p: _steen(*units)) if units else (lambda s, p: _pdok((1, "")))
        assert _decide(a, asyncio.run(europe.nl_pdok(_Get(answer), a)))["status"] == "unit_not_found"
    # no record of the building on this street: nothing to fall back to
    a = _floor("Eerste Jan Steenstraat 2-III, 1072 NK Amsterdam", "III")
    assert _decide(a, asyncio.run(europe.nl_pdok(_Get(lambda s, p: {"response": {"numFound": 0, "docs": []}}), a)))["status"] != "verified"
