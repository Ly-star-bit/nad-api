"""Non-US verification: pick the country's provider, run it, and build the same response shape as the US pipeline
(so the web pages, the batch export and API clients read both the same way)."""

from __future__ import annotations

import time
from typing import Any

from ..upstream import UpstreamError
from . import europe, japan, osm
from .base import Ctx, Lookup, ProviderInfo, Skip, record
from .countries import COUNTRIES
from .match import decide, score
from .parse import IntlAddress, parse_line, parse_structured

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

DISCLAIMER = ("Checks that the address exists in the country's address register (or, where none is open, in "
              "Overture Maps / OpenStreetMap); it does not confirm that the post office delivers there.")


def _provider(key: str) -> dict:
    p = INFO[key]
    return {"key": p.key, "name": p.name, "authority": p.authority, "url": p.url, "license": p.license,
            "attribution": p.attribution}


def _base(country: str, raw: str) -> dict[str, Any]:
    return {
        "status": None, "verified": False, "match_level": None, "confidence": 0.0, "data_source": None,
        "nad_status": None, "country": country, "provider": None, "input": raw, "parsed": {}, "geocode": None,
        "coverage": None, "best_match": None, "candidates": [], "suggestions": [], "fallback": None, "notes": [],
        "disclaimer": DISCLAIMER, "timing_ms": {},
    }


async def verify_intl(ctx: Ctx, jpost: japan.JapanPost | None, country: str, *, address: str | None = None,
                      street: str | None = None, city: str | None = None, postcode: str | None = None,
                      unit: str | None = None, overture_fallback: bool = True, max_candidates: int = 5) -> dict[str, Any]:
    t0 = time.perf_counter()
    if country == "JP":
        text = address or ", ".join(p for p in (postcode, city, street, unit) if p)
        out = await _japan(ctx, jpost, text)
    else:
        c = COUNTRIES[country]
        a = parse_line(address, c) if address and not street else parse_structured(c, street or "", city or "",
                                                                                    postcode or "", unit or "")
        if address and not street:  # extra structured fields fill what the line lacks
            a.postcode = a.postcode or (postcode or "").strip().upper()
            a.city = a.city or (city or "").strip()
            a.unit = a.unit or (unit or "").strip()
        out = await _europe(ctx, a, c.providers, overture_fallback, max_candidates)
    out["timing_ms"]["total"] = round((time.perf_counter() - t0) * 1000, 1)
    return out


async def _europe(ctx: Ctx, a: IntlAddress, providers: tuple[str, ...], overture_fallback: bool,
                  max_candidates: int) -> dict[str, Any]:
    out = _base(a.country, a.raw)
    out["parsed"] = {"house_number": a.number or None, "street": a.street or None, "unit": a.unit or None,
                     "city": a.city or None, "state": None, "zip": a.postcode or None}
    out["notes"] += a.warnings
    if a.extra:
        out["notes"].append(f"not used for matching: {'; '.join(a.extra)}")
    if not a.number:
        out["status"] = "invalid_input"
        return out
    chain = [p for p in providers if p != "overture" or (overture_fallback and ctx.ov is not None)]
    skipped: list[tuple[str, str]] = []  # (provider, why: upstream_error / not_covered)
    kept: dict[str, Any] | None = None   # a not_covered answer, used if nothing after it answers
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
        res = _apply(dict(out, notes=list(out["notes"]), timing_ms=dict(out["timing_ms"])), key, lk, a, max_candidates)
        if skipped:
            res["fallback"] = {"provider": key, "release": None, "triggered_by": skipped[-1][1], "status": res["status"],
                               "adopted": True, "error": "; ".join(f"{INFO[k].name}: {why}" for k, why in skipped)}
        if lk.area_empty and res["status"] == "not_covered" and key != chain[-1]:
            # e.g. Bavaria, missing from Overture's German data: ask the next source instead
            skipped.append((key, "not_covered"))
            out["notes"].append(f"{INFO[key].name} has no addresses around this location; trying the next source")
            kept = kept or res
            continue
        return res
    if kept:
        kept["notes"] = out["notes"]
        return kept
    out["status"] = "upstream_error"
    return out


def _apply(out: dict[str, Any], key: str, lk: Lookup, a: IntlAddress, max_candidates: int) -> dict[str, Any]:
    view = lk.view or a
    out["provider"], out["data_source"] = _provider(key), key
    out["notes"] += lk.notes
    out["timing_ms"].update(lk.timing)
    if lk.geocode:
        out["geocode"] = lk.geocode
    if lk.status:
        out["status"] = lk.status
        out["candidates"] = [record(score(view, c)) for c in lk.cands if c["kind"] == "address"][:max_candidates]
        return out
    d = decide(view, lk.cands, authority=INFO[key].authority, dataset=INFO[key].name, unit_support=lk.unit_support,
               street_found=lk.street_found, area_empty=lk.area_empty, max_candidates=max_candidates)
    out.update(status=d["status"], verified=d["verified"], match_level=d["match_level"], confidence=d["confidence"],
               best_match=record(d["best_match"]) if d["best_match"] else None,
               candidates=[record(c) for c in d["candidates"]], suggestions=[record(c) for c in d["suggestions"]])
    out["notes"] += d["notes"]
    return out


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
    a: japan.JpAddress = r["jp"]
    out["parsed"] = {"house_number": a.number or None, "street": a.prefix or None, "unit": a.rest or None,
                     "city": None, "state": None, "zip": a.postcode or None}
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
