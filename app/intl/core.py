"""Non-US verification: pick the country's provider, run it, and build the same response shape as the US pipeline
(so the web pages, the batch export and API clients read both the same way)."""

from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import Any

from .. import config
from ..upstream import UpstreamError
from . import europe, japan, osm
from .base import Ctx, Lookup, ProviderInfo, Skip, record
from .countries import COUNTRIES
from .match import decide, score, street_parts
from .parse import IntlAddress, parse_line
from .places import local_city

PROVIDERS = {
    "fr_ban": europe.fr_ban,
    "nl_pdok": europe.nl_pdok,
    "be_register": europe.be_register,
    "lu_geoportail": europe.lu_geoportail,
    "ch_geoadmin": europe.ch_geoadmin,
    "at_bev": europe.at_bev,
    "es_cartociudad": europe.es_cartociudad,
    "no_kartverket": europe.no_kartverket,
    "fi_ryhti": europe.fi_ryhti,
    "is_hms": europe.is_hms,
    "ee_inads": europe.ee_inads,
    "lv_vzd": europe.lv_vzd,
    "pl_gugik": europe.pl_gugik,
    "cz_ruian": europe.cz_ruian,
    "si_gurs": europe.si_gurs,
    "overture": osm.overture,
    "osm": osm.osm,
}
INFO: dict[str, ProviderInfo] = {**europe.INFO, **osm.INFO, **japan.INFO}

log = logging.getLogger(__name__)

# One cheap query with a known answer per source, for /health?deep=true: a register that moved its API, or one that
# blocks the server's IP, shows up there instead of as failed addresses.
PROBES: dict[str, tuple[str, dict]] = {
    "fr_ban": (config.FR_BAN_URL, {"q": "8 Boulevard du Port 80000 Amiens", "limit": 1}),
    "nl_pdok": (config.NL_PDOK_URL, {"q": "Damrak 1 Amsterdam", "rows": 1}),
    "be_vlaanderen": (config.BE_VL_GEO_URL, {"q": "Grote Markt 1, 2000 Antwerpen", "c": 1}),
    "be_wallonie": (config.BE_WA_URL, {"address": "Place du Marché 2, 4000 Liège", "crs": "EPSG:4326"}),
    "lu_geoportail": (config.LU_URL, {"queryString": "42 Place Guillaume II 1648 Luxembourg"}),
    "ch_geoadmin": (config.CH_URL, {"type": "locations", "origins": "address", "limit": 1, "searchText": "Bundesplatz 3 3011 Bern"}),
    "at_bev": (config.AT_URL, {"term": "1010 Wien Rathausplatz 1"}),
    "es_cartociudad": (config.ES_URL, {"q": "Plaza de Cibeles 1 Madrid", "limit": 1, "no_process": "callejero"}),
    "no_kartverket": (config.NO_URL, {"adressenavn": "Karl Johans gate", "nummer": 22, "treffPerSide": 1}),
    "fi_ryhti": (config.FI_URL, {"f": "json", "limit": 1}),
    "is_hms": (config.IS_URL, {"service": "WFS", "version": "2.0.0", "request": "GetFeature", "typeNames": "public:Stadfangaskra",
                               "outputFormat": "application/json", "count": 1}),
    "ee_inads": (config.EE_URL, {"address": "Raekoja plats 1, Tallinn", "results": 1}),
    "lv_vzd": (config.LV_URL, {"resource_id": config.LV_RESOURCE, "limit": 1}),
    "pl_gugik": (config.PL_URL, {"request": "GetAddress", "address": "Warszawa, Marszałkowska 99A", "srid": "4326"}),
    "cz_ruian": (config.CZ_URL, {"SingleLine": "Mariánské náměstí 2, Praha", "maxLocations": 1, "f": "json"}),
    "si_gurs": (config.SI_URL, {"f": "application/geo+json", "limit": 1}),
    "jp_gsi": (config.GSI_URL, {"q": "東京都千代田区丸の内一丁目"}),
    "photon": (f"{config.PHOTON_URL}/api", {"q": "Berlin", "limit": 1}),
    "postcodes_io": (f"{config.POSTCODES_IO_URL}/postcodes/SW1A2AA", {}),
}

DISCLAIMER = ("Checks that the address exists in the country's address register (or, where none is open, in "
              "Overture Maps / OpenStreetMap); it does not confirm that the post office delivers there.")


def _provider(key: str) -> dict:
    p = INFO[key]
    return {"key": p.key, "name": p.name, "authority": p.authority, "url": p.url, "license": p.license,
            "attribution": p.attribution}


def _base(country: str, raw: str) -> dict[str, Any]:
    return {
        "status": None, "verified": False, "match_level": None, "confidence": 0.0, "data_source": None,
        "nad_status": None, "country": country, "provider": None, "input": raw, "parsed": {"country": country},
        "geocode": None, "coverage": None, "best_match": None, "candidates": [], "suggestions": [], "fallback": None,
        "notes": [], "disclaimer": DISCLAIMER, "timing_ms": {},
    }


async def verify_intl(ctx: Ctx, jpost: japan.JapanPost | None, country: str, *, address: str,
                      max_candidates: int = 5) -> dict[str, Any]:
    t0 = time.perf_counter()
    try:
        async with asyncio.timeout(config.INTL_TIMEOUT_S):
            out = await _verify(ctx, jpost, country, address, max_candidates)
    except TimeoutError:
        out = _base(country, address)
        out["status"] = "upstream_error"
        out["notes"].append(f"this country's sources gave no answer within {config.INTL_TIMEOUT_S:.0f} s")
    out["timing_ms"]["total"] = round((time.perf_counter() - t0) * 1000, 1)
    return out


async def _verify(ctx: Ctx, jpost: japan.JapanPost | None, country: str, address: str,
                  max_candidates: int) -> dict[str, Any]:
    if country == "JP":
        return await _japan(ctx, jpost, address)
    c = COUNTRIES[country]
    a = parse_line(address, c)
    if a.city and (local := local_city(a.city, country)) != a.city:  # "Vienna" is searched as Wien
        a.warnings.append(f"town '{a.city}' read as {local}")
        a.city = local
    return await _europe(ctx, a, c.providers, max_candidates)


# how good an answer is, to keep the best when a later source is asked too
_RANK = {"verified": 0, "unit_not_found": 0, "street_only": 1, "not_found": 2, "not_covered": 3, "geocode_failed": 4,
         "invalid_input": 4, "upstream_error": 5}
# Where a named house, farm or townland without a number is a normal address: a "street" without a street word is
# then such a name ("Rose Cottage", "Hvammur", "Ozoli", "Mäe talu"); in the British Isles the name is often a line of
# its own above the street ("Rose Cottage, High Street"). A French lieu-dit is one anywhere, and so is a hamlet, farm
# or estate written with its language's word for one ("Località Valle", "Le Bourg", "Lugar de Outeiro", "Gut Hohenholz";
# not "Hofstraße" or "Rue du Bourg"). A bare word is no name ("Hof, 95028" is the town Hof), but "Le Bourg" is one.
_HOUSE_NAMES = {"GB", "IE", "IM", "JE", "GG", "IS", "FO", "NO", "SE", "FI", "AX", "DK", "EE", "LV", "LT"}
_HOUSE_NAME_LINE = {"GB", "IE", "IM", "JE", "GG"}
_NAMED_PLACE_RE = re.compile(r"(?i)lieu[\s-]?dit\b")
_RURAL = {"it": r"localit[àa]|loc\.?|frazione|fraz\.?|contrada|c\.da|podere|cascina|masseria",
          "fr": r"le\s+bourg|hameau|domaine|la\s+grange|les\s+granges",
          "es": r"lugar|paraje|diseminados?|partida|caser[íi]o|cortijo",
          "pt": r"lugar|quinta|casal|herdade",
          "de": r"gut|hof|gutshof|einzelhof"}
_RURAL_LANGS = {"IT": "it", "SM": "it", "VA": "it", "FR": "fr", "MC": "fr", "BE": "fr", "LU": "fr", "ES": "es",
                "AD": "es", "PT": "pt", "DE": "de", "AT": "de", "LI": "de", "CH": "de fr it"}
_RURAL_RE = {cc: re.compile(rf"(?i)(?:{'|'.join(_RURAL[x] for x in langs.split())})"
                            r"(?:(?<=\.)\s*\S|\s+\S|(?<=bourg)$|(?<=grange)$|(?<=granges)$)")
             for cc, langs in _RURAL_LANGS.items()}


async def _europe(ctx: Ctx, a: IntlAddress, providers: tuple[str, ...], max_candidates: int) -> dict[str, Any]:
    """Ask the country's sources in order. The next one is asked when a source is down or skips the address, when
    Overture has no data around it (Bavaria), or when an official register finds nothing at all - a spelling it
    can't read ('Mustamae tee' for Mustamäe tee) - in which case its answer still stands unless the next does better.
    (Here Overture is a primary source, so only OVERTURE_ENABLED=0 drops it.)"""
    out = _base(a.country, a.raw)
    line = (a.number, a.street) if COUNTRIES[a.country].number_first else (a.street, a.number)
    out["parsed"] = {"country": a.country, "state": None, "city": a.city or None, "zip": a.postcode or None,
                     "address1": " ".join(p for p in line if p) or None, "address2": a.unit or None,
                     "house_number": a.number or None, "street": a.street or None, "unit": a.unit or None}
    out["notes"] += a.warnings
    if a.extra:
        out["notes"].append(f"not used for matching: {'; '.join(a.extra)}")
    if not a.number:
        named = a.street and (_NAMED_PLACE_RE.match(a.street) or (a.country in _RURAL_RE and _RURAL_RE[a.country].match(a.street))
                              or (a.country in _HOUSE_NAME_LINE and bool(a.extra))
                              or (a.country in _HOUSE_NAMES and not street_parts(a.street)[1]))
        if not a.po_box and (a.no_number or (named and (a.postcode or a.city))):
            # a house name, "s/n", a farm: real addresses, but the registers are searched by number (a street
            # with its number left out is not one of these: "Hauptstraße, 80331 München" is invalid_input)
            out["status"] = "not_covered"
            out["notes"].append("without a house number the address can't be checked against the register "
                                "(house names, 's/n' and rural addresses are not searchable)")
        else:
            out["status"] = "invalid_input"
        return out
    chain = [p for p in providers if p != "overture" or ctx.ov is not None]
    if len(chain) < len(providers):
        out["notes"].append("Overture Maps is turned off on this server (OVERTURE_ENABLED=0)")
    skipped: list[tuple[str, str]] = []  # (provider, why: upstream_error / not_covered / not_found)
    kept: dict[str, Any] | None = None   # an answer a later source may still improve on
    kept_rank = 99
    register_empty = False  # an official register found nothing: a later source must have the exact street name
    for key in chain:
        try:
            lk: Lookup = await PROVIDERS[key](ctx, a)
        except Skip as exc:
            out["notes"].append(f"{INFO[key].name}: {exc}; trying the next source")
            continue
        except UpstreamError as exc:
            skipped.append((key, "upstream_error"))
            out["notes"].append(f"{INFO[key].name} unavailable: {exc.message}")
            continue
        except Exception as exc:  # an answer in a shape the reader doesn't know: the same as an outage
            log.exception("%s failed for %r", key, a.raw)
            skipped.append((key, "upstream_error"))
            out["notes"].append(f"{INFO[key].name} gave an answer that could not be read ({type(exc).__name__})")
            continue
        res = _apply(dict(out, notes=list(out["notes"]), timing_ms=dict(out["timing_ms"])), key, lk, a, max_candidates)
        if skipped:
            res["fallback"] = {"provider": key, "release": None, "triggered_by": skipped[-1][1], "status": res["status"],
                               "adopted": True, "error": "; ".join(f"{INFO[k].name}: {why}" for k, why in skipped)}
        if register_empty and res["verified"] and res["best_match"]["match"]["street_score"] < 0.999:
            # a similar name there is another street ('Grote Markt 1, 9000 Gent' is not in the register; Overture's
            # 'Groentenmarkt 1' is another square): the register's answer stands, with these only shown
            seen = {c["formatted"] for c in kept["candidates"]}
            for c in (res["best_match"], *res["candidates"]):
                if c["formatted"] not in seen and len(kept["candidates"]) < max_candidates:
                    seen.add(c["formatted"])
                    kept["candidates"].append(c)
            out["notes"].append(f"{INFO[key].name} has only a similar street name ({res['best_match']['formatted']}), "
                                "not taken as this address")
            continue
        rank = 6 if lk.area_empty and res["status"] == "not_covered" else _RANK.get(res["status"], 5)
        if kept is not None and rank >= kept_rank:
            out["notes"].append(f"{INFO[key].name} could not do better ({res['status']})")
            continue
        kept, kept_rank = res, rank
        last = key == chain[-1]
        if rank == 6 and not last:  # e.g. Bavaria, missing from Overture's German data
            skipped.append((key, "not_covered"))
            out["notes"].append(f"{INFO[key].name} has no addresses around this location; trying the next source")
            continue
        if (INFO[key].authority == "official" and res["status"] == "not_found" and not lk.cands and not last):
            skipped.append((key, "not_found"))
            register_empty = True
            out["notes"].append(f"{INFO[key].name} found nothing at all for this address; trying the next source")
            continue
        return res
    if kept:
        kept["notes"] = kept["notes"] + [n for n in out["notes"] if n not in kept["notes"]]
        return kept
    out["status"] = "upstream_error"
    return out


def _apply(out: dict[str, Any], key: str, lk: Lookup, a: IntlAddress, max_candidates: int) -> dict[str, Any]:
    view = lk.view or a
    out["provider"], out["data_source"] = _provider(key), key
    if lk.credit:
        out["provider"]["attribution"] = "; ".join(x for x in (out["provider"]["attribution"], lk.credit) if x)
    out["notes"] += lk.notes
    out["timing_ms"].update(lk.timing)
    if lk.geocode:
        out["geocode"] = lk.geocode
    if lk.status:
        out["status"] = lk.status
        out["candidates"] = [record(score(view, c)) for c in lk.cands if c["kind"] == "address"][:max_candidates]
        return out
    d = decide(view, lk.cands, authority=INFO[key].authority, dataset=INFO[key].name, unit_support=lk.unit_support,
               street_found=lk.street_found, area_empty=lk.area_empty, max_candidates=max_candidates,
               incomplete=lk.incomplete)
    out.update(status=d["status"], verified=d["verified"], match_level=d["match_level"], confidence=d["confidence"],
               best_match=record(d["best_match"]) if d["best_match"] else None,
               candidates=[record(c) for c in d["candidates"]], suggestions=[record(c) for c in d["suggestions"]])
    bm = d["best_match"]
    if bm and not bm.get("postcode") and bm.get("area_postcode"):  # an Overture row without one: OSM's house postcode
        d["notes"] = [n.replace(f" vs {INFO[key].name} ", " vs OpenStreetMap ", 1) if n.startswith("postcode differs")
                      else n for n in d["notes"]]
    out["notes"] += d["notes"]
    if d["verified"]:
        out["parsed"] = _record_split(out["parsed"], out["best_match"], COUNTRIES[a.country].number_first)
    return out


def _record_split(parsed: dict, rec: dict, number_first: bool) -> dict:
    """A verified address split with the record's street ('unter den linden' -> Unter den Linden) and town, when the
    record's town is the one written: Overture's town can be a district (Mitte for Berlin), and its postcode when the
    one written differs. The number and a matching postcode stay as written: a record can drop the letter (Damrak 8
    for 8-H) or the space (1012LG)."""
    street, number, written = rec.get("street") or parsed.get("street"), parsed.get("house_number"), parsed.get("city")
    city = rec.get("city") if rec.get("city") and (rec["match"].get("city_match") or not written) else written
    line = (number, street) if number_first else (street, number)
    zip_ = rec.get("zip") if rec.get("zip") and (rec["match"].get("zip_match") is False or not parsed.get("zip")) \
        else parsed.get("zip")
    return {**parsed, "city": city, "zip": zip_, "street": street,
            "address1": " ".join(p for p in line if p) or parsed.get("address1"), "source": "record"}


async def _japan(ctx: Ctx, jpost: japan.JapanPost | None, text: str) -> dict[str, Any]:
    out = _base("JP", text)
    out["provider"], out["data_source"] = _provider("jp_gsi"), "jp_gsi"
    try:
        r = await japan.verify_jp(ctx, jpost, text)
    except UpstreamError as exc:
        out["status"] = "upstream_error"
        out["data_source"] = None
        out["notes"].append(f"国土地理院 address search unavailable: {exc.message}")
        return out
    except Exception as exc:  # an answer in a shape the reader doesn't know
        log.exception("jp_gsi failed for %r", text)
        out["status"] = "upstream_error"
        out["data_source"] = None
        out["notes"].append(f"国土地理院 gave an answer that could not be read ({type(exc).__name__})")
        return out
    a: japan.JpAddress = r["jp"]
    pref, city, town = japan.split_place(jpost, a)
    out["parsed"] = {"country": "JP", "state": pref or None, "city": city or None, "zip": a.postcode or None,
                     "address1": town + a.number or None, "address2": a.rest or None,
                     "house_number": a.number or None, "street": a.prefix or None, "unit": a.rest or None}
    out["notes"] += r["notes"]
    out["timing_ms"].update(r["timing"])
    out["geocode"] = r["geocode"]
    out["status"] = r["status"]
    b: japan.Title | None = r["best"]
    if b is None or out["status"] in ("invalid_input", "geocode_failed"):
        return out
    level = r["level"]
    numbers = [n for n in (b.ban, b.go) if n is not None]
    zip_shown = (a.postcode if r["zip_match"] else r["zip_should_be"]) or None
    best = {
        "uuid": None, "formatted": b.raw, "address_number": "-".join(map(str, numbers)) or None,
        "street": b.town + (f"{b.chome}丁目" if b.chome is not None else ""), "street_parts": {}, "subaddress": {},
        "city": None, "county": None, "state": None, "zip": zip_shown, "lat": b.lat, "lon": b.lon,
        "placement": None, "address_class": None, "updated": None, "source": "国土地理院", "license": "PDL1.0",
        "match": {"score": 1.0 if level == "address" else 0.8 if level == "block" else 0.4, "street_score": 1.0,
                  "number_exact": True if level == "address" else None, "zip_match": r["zip_match"], "city_match": None,
                  "unit_match": None, "distance_m": None},
    }
    out["jp"] = {"parcel": r.get("parcel"), "go_given": r.get("go_given")}
    if out["status"] in ("verified", "street_only"):
        out["best_match"] = best if out["status"] == "verified" else None
        out["match_level"] = level
        out["verified"] = out["status"] == "verified"
        out["confidence"] = {"address": 0.95, "block": 0.7, "street": 0.3}.get(level, 0.0)
        if r["zip_match"] is False:
            out["confidence"] = round(out["confidence"] * 0.9, 3)
    if out["status"] == "street_only":
        out["suggestions"] = []
    return out
