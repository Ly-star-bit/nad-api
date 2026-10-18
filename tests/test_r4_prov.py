"""Round-4 provider repairs, offline: the final audit's reproductions with canned upstream answers shaped like the live
ones (In-ADS, PDOK) and the no-number rule for a rural marker word."""

import asyncio

import pytest

from app.intl import core, europe
from app.intl import countries as C
from app.intl.match import decide
from app.intl.parse import parse_line
from app.intl.places import same_city

CC = C.COUNTRIES


def _a(cc, text):
    return parse_line(text, CC[cc])


def _decide(a, lk):
    return decide(lk.view or a, lk.cands, authority="official", dataset="In-ADS", unit_support=lk.unit_support,
                  street_found=lk.street_found, area_empty=lk.area_empty, incomplete=lk.incomplete)


class _Get:
    """ctx.get with canned answers: answer(service, params) -> JSON."""

    def __init__(self, answer):
        self.answer = answer

    async def get(self, service, url, params, *, expect=dict):
        return self.answer(service, params)


# ----------------------------------------------------------------------------------------------- [16]

def _inads(street, num, settlement, municipality, pc):
    return {"liikVal": "EHITISHOONE", "liikluspind": street, "aadresstekst": f"{street} {num}", "aadress_nr": num,
            "kort_nr": "", "asustusyksus": settlement, "omavalitsus": municipality, "sihtnumber": pc,
            "kvaliteet": "tapne_nr", "adr_id": f"{pc}{num}{settlement}"}


# the live In-ADS answers (now in r3/cache), cut to the records that matter
_EE = {
    "Tartu mnt 1, Tartu": [_inads("Tartu mnt", "1", "Kärevere küla", "Tartu vald", "60603"),
                           _inads("Tartu mnt", "1", "Rõngu alevik", "Elva vald", "61001")],
    "Viljandi mnt 1, Viljandi": [_inads("Viljandi mnt", "1", "Kolga-Jaani alevik", "Viljandi vald", "70301"),
                                 _inads("Viljandi mnt", "1", "Abja-Paluoja linn", "Mulgi vald", "69403")],
    "Võru 1, Võru": [_inads("Võru", "1", "Võrumõisa küla", "Võru vald", "65201"),
                     _inads("Võru", "1", "Vastseliina alevik", "Võru vald", "65505")],
    "Tartu mnt 1, 51004 Tartu": [_inads("Tartu mnt", "1", "Kärevere küla", "Tartu vald", "60603")],
    "Kooli 1, Tartu": [_inads("Kooli tn", "1", "Tartu linn", "Tartu linn", "50409"),
                       _inads("Kooli tee", "1", "Ilmatsalu alevik", "Tartu linn", "61401"),
                       _inads("Kooli tn", "1", "Kõrveküla alevik", "Tartu vald", "60512")],
    "Kooli 1, Viljandi": [_inads("Kooli tn", "1", "", "Viljandi linn", "71004"),
                          _inads("Kooli", "1", "Ramsi alevik", "Viljandi vald", "69602")],
    "Kooli 1, Võru": [_inads("Kooli tn", "1", "", "Võru linn", "65606"),
                      _inads("Kooli", "1", "Väimela alevik", "Võru vald", "65201")],
    "Narva mnt 7, Jõhvi": [_inads("Narva mnt", "7", "Voka alevik", "Jõhvi vald", "41701")],
}


def _ee(text):
    a = _a("EE", text)
    lk = asyncio.run(europe.ee_inads(_Get(lambda s, p: {"addresses": _EE[text]}), a))
    return lk, _decide(a, lk)


@pytest.mark.parametrize("municipality,settlement,alt", [
    ("Tartu vald", "Kärevere küla", ""), ("Viljandi vald", "Kolga-Jaani alevik", ""),   # a parish around a town
    ("Võru vald", "Väimela alevik", ""), ("Rakvere vald", "Sõmeru alevik", ""),
    ("Jõhvi vald", "Voka alevik", "Jõhvi"), ("Elva vald", "Rõngu alevik", "Elva"),       # r3 [31]: Jõhvi is Jõhvi vald
    ("Võru linn", "", "Võru"), ("Narva linn", "", "Narva"),                               # a town's own record
    ("Tartu linn", "Ilmatsalu alevik", ""), ("Narva-Jõesuu linn", "Olgina alevik", ""),  # another settlement in it
])
def test_r4_16_estonian_municipality_name_only_where_it_is_the_place(municipality, settlement, alt):
    c = europe.ee_cands({"addresses": [_inads("Kooli", "1", settlement, municipality, "00000")]})[0]
    bare = municipality.rsplit(" ", 1)[0]
    assert c["city"] == municipality and (bare in c["city_alt"]) == bool(alt)
    # the settlement itself is still the town: "Tartu" for Tartu linn in Tartu linn, "Voka" for Voka alevik
    if settlement:
        assert same_city(settlement.rsplit(" ", 1)[0], c["city_alt"][1], "EE")


# a town written alone is the linn: a record only in the same-named vald is another place
_TWIN = ["Tartu mnt 1, Tartu", "Viljandi mnt 1, Viljandi", "Võru 1, Võru"]


@pytest.mark.parametrize("text", _TWIN)
def test_r4_16_town_is_not_the_same_named_parish(text):
    lk, d = _ee(text)
    town = _a("EE", text).city
    assert not any(same_city(town, x, "EE") for c in lk.cands for x in c["city_alt"])  # the city_alt path is closed
    if same_city(town, f"{town} vald", "EE") is False:  # places.py: 'vald' is a kind, not a namesake's qualifier
        assert d["status"] == "not_found" and any("exists only in other places" in n for n in d["notes"]), d
    else:  # until then the only way left is the namesake rule, which says so
        assert d["status"] in ("verified", "not_covered"), d
        assert d["status"] == "not_covered" or any("taken as" in n for n in d["notes"]), d
        assert "vald" in str(d["best_match"] and d["best_match"]["formatted"]) or d["status"] == "not_covered"


@pytest.mark.parametrize("text,formatted", [
    ("Narva mnt 7, Jõhvi", "Narva mnt 7, Voka alevik, 41701 Jõhvi vald"),   # r3 [31] still holds
    ("Kooli 1, Tartu", "Kooli tn 1, Tartu linn, 50409 Tartu linn"),         # not Ilmatsalu nor a Tartu vald village
    ("Kooli 1, Viljandi", "Kooli tn 1, 71004 Viljandi linn"),
    ("Kooli 1, Võru", "Kooli tn 1, 65606 Võru linn"),
])
def test_r4_16_town_and_johvi_still_verify(text, formatted):
    _, d = _ee(text)
    assert d["status"] == "verified" and d["best_match"]["formatted"] == formatted, d


def test_r4_16_postcode_of_the_town_is_not_found():
    _, d = _ee("Tartu mnt 1, 51004 Tartu")
    assert d["status"] == "not_found", d


# ----------------------------------------------------------------------------------------------- [17]

def _kinker(*units, street="Kinkerstraat", number=2, pc="1053DT"):
    return {"response": {"numFound": len(units), "docs": [
        {"type": "adres", "id": f"{number}{u}", "weergavenaam": f"{street} {number}{'-' + u if u else ''}, {pc} Amsterdam",
         "straatnaam": street, "huisnummer": number, "huisletter": u if u.isalpha() and len(u) == 1 and u != "H" else None,
         "huisnummertoevoeging": u if u and not (u.isalpha() and len(u) == 1 and u != "H") else None, "postcode": pc,
         "woonplaatsnaam": "Amsterdam", "centroide_ll": "POINT(4.87 52.37)"} for u in units]}}


def _nl(text, answer):
    a = _a("NL", text)
    lk = asyncio.run(europe.nl_pdok(_Get(lambda s, p: answer), a))
    return lk, decide(lk.view or a, lk.cands, authority="official", dataset="BAG", unit_support=lk.unit_support)


@pytest.mark.parametrize("text", ["Kinkerstraat 2-IV, 1053 DT Amsterdam", "Kinkerstraat 2-V, 1053 DT Amsterdam",
                                  "Kinkerstraat 2-IX, Amsterdam", "Kinkerstraat 2-9, Amsterdam"])
def test_r4_17_floor_missing_where_floors_are_additions_is_unit_not_found(text):
    lk, d = _nl(text, _kinker("1", "2", "3", "H"))
    assert d["status"] == "unit_not_found", d
    assert any("units there: 1, 2, 3, H" in n for n in d["notes"]) and not lk.notes


@pytest.mark.parametrize("text,formatted", [("Kinkerstraat 2-III, 1053 DT Amsterdam", "Kinkerstraat 2-3, 1053DT Amsterdam"),
                                            ("Kinkerstraat 2-II, 1053 DT Amsterdam", "Kinkerstraat 2-2, 1053DT Amsterdam"),
                                            ("Kinkerstraat 2-I, 1053 DT Amsterdam", "Kinkerstraat 2-1, 1053DT Amsterdam")])
def test_r4_17_floor_that_is_an_addition_still_verifies(text, formatted):
    _, d = _nl(text, _kinker("1", "2", "3", "H"))
    assert d["status"] == "verified" and d["match_level"] == "unit" and d["best_match"]["formatted"] == formatted, d


@pytest.mark.parametrize("units", [("",), ("", "A", "B", "C")])   # no additions; house letters (Eerste Helmersstraat 10)
@pytest.mark.parametrize("floor", ["II", "IX"])
def test_r4_17_floor_where_bag_records_no_floors_verifies_the_building(units, floor):
    lk, d = _nl(f"Eerste Helmersstraat 10-{floor}, 1054 DG Amsterdam",
                _kinker(*units, street="Eerste Helmersstraat", number=10, pc="1054DG"))
    assert d["status"] == "verified" and d["best_match"]["formatted"] == "Eerste Helmersstraat 10, 1054DG Amsterdam", d
    assert f"floor {floor} is not a BAG addition; the building exists" in lk.notes


def test_r4_17_floor_missing_beside_one_floor_addition_is_unit_not_found():
    # moved from test_r3_r59: number 2 and 2-1 in BAG, so floor III would be 2-3
    _, d = _nl("Kinkerstraat 2-III, 1053 DT Amsterdam", _kinker("", "1"))
    assert d["status"] == "unit_not_found", d


# ----------------------------------------------------------------------------------------------- [18]

class _NoUpstream:
    ov = object()

    async def get(self, *args, **kw):
        raise AssertionError("no source is asked without a house number")


def _verify(line):
    cc, text = line.split(" | ", 1)
    return asyncio.run(core.verify_intl(_NoUpstream(), None, cc, address=text))


# the marker word alone is no place name ('Hof' is a town), nor are the other markers alone (as in r3/base)
@pytest.mark.parametrize("line", ["DE | Hof, 95028", "DE | Gut, 95028", "DE | Hof, 95028 Hof", "AT | Gut, 8010 Graz",
                                  "CH | Hof, 8000 Zürich", "DE | Gutshof, 73230 Kirchheim", "DE | Einzelhof, 73230 Kirchheim",
                                  "FR | Hameau, 24260 Le Bugue", "IT | Località, 57121 Livorno", "ES | Lugar, 15800 Melide",
                                  "DE | Hofstraße, 80331 München", "DE | Gutenbergstraße, 10587 Berlin",
                                  "FR | Le Bourgneuf, 24260 Le Bugue"])
def test_r4_18_bare_rural_marker_is_invalid_input(line):
    out = _verify(line)
    assert out["status"] == "invalid_input" and "the address has no house number" in out["notes"], out


@pytest.mark.parametrize("line", ["DE | Hof Wendlingen, 73230 Kirchheim", "DE | Gut Hohenholz, 50189 Elsdorf",
                                  "DE | Gutshof Wendlingen, 73230 Kirchheim", "FR | Le Bourg, 24260 Le Bugue",
                                  "FR | La Grange, 24260 Le Bugue", "FR | Les Granges, 24260 Le Bugue",
                                  "IT | Loc. Pian di Rota, 57121 Livorno", "IT | Loc.Pian di Rota, 57121 Livorno"])
def test_r4_18_named_rural_place_stays_not_covered(line):
    assert _verify(line)["status"] == "not_covered"


# ---------------------------------------------------------------------- IS: a postcode with no known town

def _hms(*rows):
    return {"features": [{"properties": {"HEITI_NF": street, "HEITI_TGF": street, "HUSNR": n, "BOKST": None, "POSTNR": pc,
                                         "HUSMERKING": str(n), "HEINUM": f"{pc}{n}", "SERHEITI": None,
                                         "N_HNIT_WGS84": 64.1, "E_HNIT_WGS84": -21.9}, "geometry": None}
                          for street, n, pc in rows]}


def _is_answer(service, params):
    """The live HMS answers, cut down: Laugavegur 1 is in 101 Reykjavík and 560 Varmahlíð;
    Aðalstræti 20 only in 400 and 415, the street also in 101."""
    cql = params["CQL_FILTER"]
    if "Laugavegur" in cql:
        rows = [("Laugavegur", 1, 101), ("Laugavegur", 1, 560)]
        return _hms(*[r for r in rows if f"POSTNR={r[2]}" in cql or "POSTNR=" not in cql])
    if "HUSNR=20" in cql:
        return _hms() if "POSTNR=" in cql else _hms(("Aðalstræti", 20, 400), ("Aðalstræti", 20, 415))
    return _hms(("Aðalstræti", 16, 101), ("Aðalstræti", 9, 101))


def _is(text):
    a = _a("IS", text)
    lk = asyncio.run(europe.is_hms(_Get(_is_answer), a))
    return decide(lk.view or a, lk.cands, authority="official", dataset="HMS", unit_support=lk.unit_support,
                  street_found=lk.street_found)


@pytest.mark.parametrize("text", ["Laugavegur 1, Akureyri", "Laugavegur 1, Varmahlíð"])
def test_r4_is_town_without_postcode_is_not_a_record_of_unknown_town(text, monkeypatch):
    from app.intl import europe
    monkeypatch.delitem(europe._IS_TOWNS, 560)  # a postcode the table doesn't know
    d = _is(text)  # was verified on 'Laugavegur 1, 560'
    assert d["status"] == "not_found" and any("exists only in other places" in n for n in d["notes"]), d


@pytest.mark.parametrize("text,status,formatted", [
    ("Laugavegur 1, Reykjavík", "verified", "Laugavegur 1, 101"),
    ("Laugavegur 1, 560 Varmahlíð", "verified", "Laugavegur 1, 560"),   # the postcode says which
    ("Laugavegur 1, Varmahlíð", "verified", "Laugavegur 1, 560"),       # 560 is Varmahlíð in _IS_TOWNS
    ("Laugavegur 1, Akureyri", "not_found", None),
    ("Laugavegur 1, 560", "verified", "Laugavegur 1, 560"),
    ("Aðalstræti 20, 101 Reykjavík", "street_only", None),
])
def test_r4_is_postcode_or_known_town_unchanged(text, status, formatted):
    d = _is(text)
    assert d["status"] == status and (d["best_match"] or {}).get("formatted") == formatted, d
