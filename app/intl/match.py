"""Compare what a provider returned with what was asked, and turn that into a verdict.

Most free geocoders answer *something* for a nonexistent house number - the street, or the nearest real number
on it (Swiss, Spanish and Brussels services, PDOK free text, Photon). So nothing here trusts "a result came back":
every candidate's house number, street and postcode are compared with the input again.
"""

from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher
from typing import Any

from .countries import fold
from .parse import IntlAddress, city_matches

STREET_OK = 0.85  # strict: "Wiejska" vs "Nowowiejska" is 0.78 and must not pass

# Street-type words -> one canonical token. Collisions across languages don't matter: inputs are only ever
# compared with records from the same country.
_TYPES = {
    "str": "str", "strasse": "str", "straat": "str", "strada": "str", "st": "st", "street": "st",
    "pl": "pl", "platz": "pl", "place": "pl", "plac": "pl", "plein": "pl", "plaza": "pl", "piazza": "pl", "pza": "pl",
    "pzza": "pl", "praca": "pl", "placa": "pl", "plass": "pl", "namesti": "pl", "nam": "pl", "namestie": "pl", "torg": "pl",
    "av": "av", "ave": "av", "avenue": "av", "avenida": "av", "avda": "av", "aleja": "al", "al": "al", "aleje": "al",
    "bd": "bd", "bld": "bd", "blvd": "bd", "boulevard": "bd", "bv": "bd",
    "rue": "rue", "r": "rue", "rua": "rue",
    "rd": "rd", "road": "rd", "ln": "ln", "lane": "ln", "laan": "ln", "dr": "dr", "drive": "dr", "ct": "ct", "court": "ct",
    "sq": "sq", "square": "sq", "cres": "cres", "crescent": "cres", "terr": "terr", "terrace": "terr", "gdns": "gdns",
    "gardens": "gdns", "way": "way", "wy": "way", "pk": "pk", "park": "pk", "cl": "cl", "close": "cl", "gr": "gr", "grove": "gr",
    "ch": "ch", "chem": "ch", "chemin": "ch", "rte": "rte", "route": "rte", "imp": "imp", "impasse": "imp",
    "all": "all", "allee": "all", "allees": "all", "quai": "quai", "qu": "quai", "crs": "crs", "cours": "crs",
    "fbg": "fbg", "faubourg": "fbg", "pass": "pass", "passage": "pass", "sente": "sente", "voie": "voie",
    "calle": "calle", "c": "calle", "cl": "calle", "paseo": "paseo", "po": "paseo", "ctra": "ctra", "carretera": "ctra",
    "ronda": "ronda", "rda": "ronda", "travesia": "trav", "trav": "trav", "camino": "camino", "cmno": "camino",
    "via": "via", "v": "via", "viale": "viale", "vle": "viale", "corso": "corso", "cso": "corso", "largo": "largo",
    "lgo": "largo", "vicolo": "vicolo", "vic": "vicolo", "contrada": "contrada", "localita": "loc", "loc": "loc",
    "travessa": "trav", "tv": "trav", "estrada": "ctra", "estr": "ctra", "lg": "largo",
    "ul": "ul", "ulica": "ul", "os": "os", "osiedle": "os", "trida": "trida", "tr": "trida",
    "weg": "weg", "wg": "weg", "gasse": "gasse", "g": "gasse", "steig": "steig", "ring": "ring", "damm": "damm", "ufer": "ufer",
    "gate": "gate", "gt": "gate", "gata": "gata", "gatan": "gata", "gade": "gade", "vej": "vej", "vagen": "vagen",
    "veien": "vei", "vei": "vei", "vn": "vei", "katu": "katu", "tie": "tie", "kuja": "kuja",
    "utca": "utca", "u": "utca", "ut": "ut", "ulice": "ul", "cesta": "cesta",
}
# Type words glued to the name ("Hauptstraße", "Museumstraat", "Drottninggatan", "Mannerheimintie")
_TYPE_SUFFIXES = ("strasse", "straat", "str", "gasse", "allee", "platz", "plein", "laan", "weg", "gatan", "gata",
                  "gade", "vej", "vagen", "veien", "katu", "tie", "kuja", "utca", "ring", "damm", "ufer")
_SAINT = {"saint": "st", "sankt": "st", "san": "st", "santo": "st", "sao": "st", "sainte": "ste", "ste": "ste",
          "santa": "ste"}
_STOP = {"de", "la", "le", "les", "du", "des", "d", "l", "der", "die", "das", "den", "dem", "van", "het", "t", "of",
         "the", "di", "del", "della", "delle", "dei", "degli", "dello", "y", "e", "da", "do", "dos", "el", "los", "las",
         "et", "en", "und", "an", "am", "im", "auf", "zum", "zur", "a", "i", "o"}


_NUMBER_WORDS = {w: i for i, ws in enumerate([
    (), ("uno", "un", "une", "eins"), ("due", "deux", "dos", "zwei"), ("tre", "trois", "tres", "drei"),
    ("quattro", "quatre", "cuatro", "vier"), ("cinque", "cinq", "cinco", "funf"), ("sei", "six", "seis", "sechs"),
    ("sette", "sept", "siete", "sieben"), ("otto", "huit", "ocho", "acht"), ("nove", "neuf", "nueve", "neun"),
    ("dieci", "dix", "diez", "zehn"), ("undici", "onze", "once"), ("dodici", "douze", "doce"), ("tredici", "treize", "trece"),
    ("quattordici", "quatorze", "catorce"), ("quindici", "quinze", "quince"), ("sedici", "seize", "dieciseis"),
    ("diciassette", "diecisiete"), ("diciotto", "dieciocho"), ("diciannove", "diecinueve"), ("venti", "vingt", "veinte"),
    ("ventuno", "veintiuno"), ("ventidue", "veintidos"), ("ventitre", "veintitres"), ("ventiquattro", "veinticuatro"),
    ("venticinque", "veinticinco"), ("ventisei", "veintiseis"), ("ventisette", "veintisiete"), ("ventotto", "veintiocho"),
    ("ventinove", "veintinueve"), ("trenta", "trente", "treinta"), ("trentuno", "treinta y uno"),
]) for w in ws}
_ROMAN = {"i": 1, "v": 5, "x": 10, "l": 50, "c": 100}


def _number_token(tok: str) -> str:
    """Dates in street names are written three ways: 'Via XX Settembre' (the register: 'Via Venti Settembre'),
    'Rue du 4 Septembre' / 'Rue du Quatre-Septembre'. All become digits."""
    if tok in _NUMBER_WORDS:
        return str(_NUMBER_WORDS[tok])
    if len(tok) >= 2 and re.fullmatch(r"[ivxlc]+", tok):
        vals = [_ROMAN[ch] for ch in tok]
        n = sum(-v if i + 1 < len(vals) and v < vals[i + 1] else v for i, v in enumerate(vals))
        if 0 < n < 200:
            return str(n)
    return tok


def street_parts(text: str | None) -> tuple[str, set[str]]:
    """'Rue de la République' -> ('republique', {'rue'}); 'Hauptstr.' -> ('haupt', {'str'})."""
    name: list[str] = []
    types: set[str] = set()
    toks = fold(text).split()
    for i, tok in enumerate(toks):
        if tok in _SAINT or (tok == "st" and i < len(toks) - 1):  # "St Mary Axe" is Saint, "Downing St" Street
            name.append(_SAINT.get(tok, "st"))
            continue
        if tok in _TYPES:
            types.add(_TYPES[tok])
            continue
        if tok in _STOP:
            continue
        tok = _number_token(tok)
        for suf in _TYPE_SUFFIXES:
            if tok.endswith(suf) and len(tok) - len(suf) >= 3:
                types.add(_TYPES.get(suf, suf))
                tok = tok[: -len(suf)]
                break
        name.append(tok)
    if not name:  # the name is only type words ("Ring", "Le Cours"): compare those
        name = sorted(types)
    return "".join(name), types


def street_sim(want: str | None, have: str | None) -> float:
    if not want or not have:
        return 0.0
    wn, wt = street_parts(want)
    hn, ht = street_parts(have)
    if not wn or not hn:
        return 0.0
    s = 1.0 if wn == hn else SequenceMatcher(None, wn, hn).ratio()
    if re.findall(r"\d+", wn) != re.findall(r"\d+", hn):  # "Via XX Settembre" is not "Via XXI Settembre"
        s = min(s, 0.5)
    if wt and ht and not wt & ht:  # "Rue de la Paix" vs "Avenue de la Paix" are different streets
        s -= 0.25
    return round(max(s, 0.0), 3)


def best_street_sim(want: str | None, *names: str | None) -> float:
    """Best match against a record's names, each bilingual one also in parts: Brussels' "Grote Markt - Grand-Place",
    South Tyrol's "Lauben / Portici"."""
    parts = [n for name in names if name for n in (name, *re.split(r"\s+[-/]\s+", name))]
    return max((street_sim(want, n) for n in parts), default=0.0)


def norm_num(text: str | None) -> str:
    """'18 bis' -> '18bis', '8-H' -> '8h', '011' -> '11', '26 A' -> '26a'; '4/6/8' and '7-1' keep their separators
    ('12/3' is not '123')."""
    t = re.sub(r"[\s.]", "", unicodedata.normalize("NFKC", text or "").casefold())
    t = re.sub(r"^(\d+)-([a-z])$", r"\1\2", t)
    return re.sub(r"^0+(?=\d)", "", t)


def num_matches(a: IntlAddress, have: str | None) -> str | None:
    """'full' when the record has the whole number ('4/6/8', '8-H'), 'base' when only the part before '/' or '-'
    ('12' for '12/3/4' - the rest is a flat), else None."""
    if not have or not a.number:
        return None
    h = norm_num(have)
    if h == norm_num(a.number):
        return "full"
    if a.number_ext and h == norm_num(a.number_base):
        return "base"
    return None


def norm_postcode(text: str | None) -> str:
    """'LV-1050' -> '1050', '1012 lg' -> '1012LG', 'SW1A 2AA' -> 'SW1A2AA', '00-110' -> '00110'."""
    t = re.sub(r"[\s-]", "", (text or "").upper())
    return re.sub(r"^[A-Z]{1,3}(?=\d{3,})", "", t) if re.fullmatch(r"[A-Z]{1,3}\d{3,}", t) else t


def norm_unit(text: str | None) -> str:
    """'Whg 5' / 'Apt. 5' / 'bus 0106' -> '5' / '5' / '106'; 'H0201' stays."""
    t = fold(text)
    if re.fullmatch(r"h\d{4}", t.replace(" ", "")):
        return t.replace(" ", "")
    t = re.sub(r"\b(apt|apartment|app|appartement|flat|unit|suite|wohnung|whg|top|bus|boite|bte|piso|puerta|int|"
               r"interno|lgh|leil|kv|door|tur)\b", " ", t)
    t = re.sub(r"\s+", "", t)
    return re.sub(r"(?<![0-9])0+(?=\d)", "", t)


# ---------------------------------------------------------------------------------------------- verdict

def score(a: IntlAddress, c: dict[str, Any]) -> dict[str, Any]:
    ss = best_street_sim(a.street, c.get("street"), *(c.get("street_alt") or []))
    num = num_matches(a, c.get("number")) if c.get("kind") == "address" else None
    pc = (norm_postcode(a.postcode) == norm_postcode(c["postcode"])) if a.postcode and c.get("postcode") else None
    city = city_matches(a.city, c.get("city"))
    if city is False and c.get("city_loose"):
        city = None
    unit = None
    if a.unit and c.get("kind") == "address" and "unit" in c:
        unit = bool(c.get("unit")) and norm_unit(c["unit"]) == norm_unit(a.unit)
    total = 0.55 * ss + 0.3 * (num is not None) + 0.1 * (pc is not False) + 0.05 * (city is not False)
    return {**c, "match": {"score": round(total, 3), "street_score": ss, "number_exact": num == "full" if num else False,
                           "zip_match": pc, "city_match": city, "unit_match": unit, "distance_m": c.get("distance_m"),
                           "number": num}}


AUTHORITY_CONFIDENCE = {"official": 0.97, "overture": 0.9, "osm": 0.75}


def decide(a: IntlAddress, cands: list[dict], *, authority: str, dataset: str, unit_support: bool = False,
           street_found: bool = False, area_empty: bool = False, max_candidates: int = 5) -> dict[str, Any]:
    """Candidates (kind 'address' or 'street') from one provider -> status, best match, suggestions, notes."""
    notes: list[str] = []
    scored = sorted((score(a, c) for c in cands), key=lambda c: -c["match"]["score"])
    addrs = [c for c in scored if c.get("kind") == "address"]
    same_street = [c for c in addrs if c["match"]["street_score"] >= STREET_OK]
    street_found = street_found or bool(same_street) or any(
        c.get("kind") == "street" and c["match"]["street_score"] >= STREET_OK for c in scored)
    hits = [c for c in same_street if c["match"]["number"]]
    out: dict[str, Any] = {"status": "not_found", "verified": False, "match_level": None, "confidence": 0.0,
                           "best_match": None,
                           # the same number on other streets (a typo in the street name), not every nearby address
                           "candidates": [c for c in addrs if c not in same_street and c["match"]["number"]][:max_candidates],
                           "suggestions": [], "notes": notes}
    base = AUTHORITY_CONFIDENCE[authority]

    if hits:
        # the whole number before a base-only one, the input's postcode before another, then street similarity
        hits.sort(key=lambda c: (c["match"]["number"] != "full", c["match"]["zip_match"] is False,
                                 -c["match"]["street_score"]))
        full = [c for c in hits if c["match"]["number"] == "full"] or hits
        units = sorted({c["unit"] for c in full if c.get("unit")}, key=_natural)
        best = full[0]
        conf = base if best["match"]["street_score"] >= 0.95 else base * 0.95
        level = "address"
        if a.unit or (best["match"]["number"] == "base" and unit_support):
            want = a.unit or a.number_ext
            with_unit = [c for c in full if c.get("unit") and norm_unit(c["unit"]) == norm_unit(want)]
            if with_unit:
                best, level = with_unit[0], "unit"
                best["match"]["unit_match"] = True
            elif unit_support:
                bare = [c for c in full if not c.get("unit")]
                best = bare[0] if bare else full[0]
                notes.append(f"the building exists in {dataset} but unit '{want}' is not recorded"
                             + (f"; units there: {', '.join(units[:20])}" if units else ""))
                out.update(status="unit_not_found", verified=True, match_level="address", confidence=round(conf * 0.9, 3),
                           best_match=best, candidates=[c for c in full if c is not best][:max_candidates])
                return _with_zip_note(out, a, dataset)
            else:
                notes.append(f"{dataset} has no flat / unit data, so unit '{want}' was not checked")
        elif units and not best.get("unit") and unit_support:
            bare = [c for c in full if not c.get("unit")]
            if bare:
                best = bare[0]
            else:
                notes.append(f"this number exists only with an addition in {dataset}: {', '.join(units[:20])}; "
                             "without one the parcel may not be deliverable")
        if best["match"]["number"] == "base" and level != "unit":
            notes.append(f"{dataset} has house number {best.get('number')}; '{a.number_ext}' after it was not checked")
        out.update(status="verified", verified=True, match_level=level, best_match=best,
                   confidence=round(conf, 3), candidates=[c for c in full if c is not best][:max_candidates])
        return _with_zip_note(out, a, dataset)

    sugg, seen = [], set()
    for c in sorted(same_street, key=lambda c: (_num_distance(a.number, c.get("number")), bool(c.get("unit")))):
        # one entry per house number, not one per flat or per way of writing it (Czech "190" and "190/5")
        if norm_num(c.get("number")) not in seen and c["formatted"] not in seen:
            seen.update((norm_num(c.get("number")), c["formatted"]))
            sugg.append(c)
    out["suggestions"] = sugg[:max_candidates]
    if street_found:
        if authority == "osm":  # OSM is incomplete: a missing number says nothing about the address
            notes.append(f"the street is in {dataset} but this house number is not; OpenStreetMap is missing many "
                         "house numbers, so this does not mean the address is wrong")
            out.update(status="not_covered", confidence=0.0)
        else:
            notes.append(f"the street exists in {dataset}, but house number {a.number} was not found on it")
            out.update(status="street_only", match_level="street", confidence=0.3)
        return out
    if area_empty:
        notes.append(f"{dataset} has no addresses around this location - the area is not covered")
        out["status"] = "not_covered"
        return out
    notes.append(f"no address with this street and number in {dataset}")
    out["confidence"] = 0.05
    return out


def _with_zip_note(out: dict, a: IntlAddress, dataset: str) -> dict:
    bm = out["best_match"]
    if bm and bm["match"]["zip_match"] is False:
        out["notes"].append(f"postcode differs: input {a.postcode} vs {dataset} {bm.get('postcode')}")
        out["confidence"] = round(out["confidence"] * 0.9, 3)
    return out


def _natural(text: str) -> list:
    return [int(p) if p.isdigit() else p for p in re.split(r"(\d+)", str(text))]


def _num_distance(want: str, have: str | None) -> float:
    w, h = re.match(r"\d+", want or ""), re.match(r"\d+", have or "")
    return abs(int(w.group()) - int(h.group())) if w and h else 1e9
