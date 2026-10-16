"""Address verification pipeline.

geocode (Census) -> NAD spatial lookup -> local street/unit/ZIP matching -> decision,
then, when NAD cannot confirm the address, the same matching against Overture Maps addresses.
"""

from __future__ import annotations

import asyncio
import math
import re
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from . import config
from .address import ParsedAddress, StreetKey, clean, norm_text, norm_unit, street_score
from .upstream import Upstream, UpstreamError

if TYPE_CHECKING:
    from .overture import OvertureClient

# Radius for the "is this street / area covered at all" probe when the house number is missing.
STREET_PROBE_RADIUS_M = 300

DISCLAIMER = (
    "NAD / Overture confirm that an address point physically exists according to the addressing "
    "authority behind each record; they do not confirm USPS deliverability."
)

# Higher = more useful answer. Used to decide whether the Overture result replaces the NAD one.
STATUS_RANK = {"verified": 5, "unit_not_found": 4, "street_only": 3, "not_found": 2, "not_covered": 1, "upstream_error": 0}
FALLBACK_TRIGGERS = {"not_covered", "not_found", "street_only", "upstream_error"}


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371008.8
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def bbox_around(lat: float, lon: float, radius_m: float) -> dict:
    dlat = radius_m / 111_320
    dlon = radius_m / (111_320 * max(0.01, math.cos(math.radians(lat))))
    return {"xmin": lon - dlon, "ymin": lat - dlat, "xmax": lon + dlon, "ymax": lat + dlat}


def record_street_key(rec: dict) -> StreetKey:
    sp = rec["street_parts"]
    if sp.get("name"):
        return StreetKey.from_parts(
            predir=" ".join(p for p in (sp.get("pre_modifier"), sp.get("pre_directional")) if p),
            pretype=" ".join(p for p in (sp.get("pre_type"),) if p),
            name=sp["name"],
            posttype=sp.get("post_type") or "",
            postdir=" ".join(p for p in (sp.get("post_directional"), sp.get("post_modifier")) if p),
        )
    return StreetKey.from_text(rec.get("street"))  # Overture: single street string


def census_street_key(geocode: dict | None) -> StreetKey | None:
    if not geocode or not geocode.get("components"):
        return None
    c = geocode["components"]
    return StreetKey.from_parts(
        predir=" ".join(p for p in (c.get("preQualifier"), c.get("preDirection")) if p),
        pretype=c.get("preType") or "",
        name=c.get("streetName") or "",
        posttype=c.get("suffixType") or "",
        postdir=" ".join(p for p in (c.get("suffixDirection"), c.get("suffixQualifier")) if p),
    )


def _place_match(query: str, *cands: str | None) -> bool | None:
    q = norm_text(query)
    if not q:
        return None
    vals = [norm_text(c) for c in cands if c]
    if not vals:
        return None
    return any(q == v or q in v or v in q for v in vals)


def _natural(text: str) -> list:
    """Sort key so '#2' < '#10'."""
    return [int(p) if p.isdigit() else p for p in re.split(r"(\d+)", text)]


def _unit_values(rec: dict) -> list[str]:
    return [v for v in rec["subaddress"].values() if v]


def score_candidate(rec: dict, pa: ParsedAddress, keys: list[StreetKey], origin: tuple[float, float] | None,
                    ref_zip: str) -> dict:
    """`keys` = [census key, user key]; the best of both handles Census spelling fixes, but a
    directional the user typed wins: Census may "correct" N Spring St to S Spring St."""
    ckey = record_street_key(rec)
    s_score = max((street_score(k, ckey) for k in keys if k and (k.core or k.full)), default=0.0)
    user = StreetKey.from_text(pa.street_text)
    if (user.predir and ckey.predir and user.predir != ckey.predir) or (
        user.postdir and ckey.postdir and user.postdir != ckey.postdir
    ):
        s_score = round(max(0.0, s_score - 0.25), 3)

    want_suffix = clean(pa.house.suffix).replace(" ", "") if pa.house else ""
    have_suffix = clean(rec.get("address_number_suffix")).replace(" ", "")
    number_exact = pa.house is not None and rec.get("address_number_int") == pa.house.number and want_suffix == have_suffix

    zip_match = None if not (ref_zip and rec.get("zip")) else rec["zip"][:5] == ref_zip
    city_match = _place_match(pa.city, rec.get("postal_city"), rec.get("municipality"), rec.get("city"))

    unit_vals = _unit_values(rec)
    if pa.unit:
        unit_match: bool | None = any(norm_unit(v) == pa.unit for v in unit_vals)
    else:
        unit_match = None

    dist = None
    if origin and rec.get("lat") is not None and rec.get("lon") is not None:
        dist = round(haversine_m(origin[0], origin[1], rec["lat"], rec["lon"]), 1)

    dist_factor = 0.5 if dist is None else max(0.0, 1 - dist / 1500)
    tri = {True: 1.0, None: 0.5, False: 0.0}
    match_score = (
        0.55 * s_score
        + 0.15 * (1.0 if number_exact else 0.4)
        + 0.10 * tri[zip_match]
        + 0.10 * (tri[unit_match] if pa.unit else (1.0 if not unit_vals else 0.8))
        + 0.10 * dist_factor
    )
    return {
        **rec,
        "match": {
            "score": round(match_score, 3),
            "street_score": s_score,
            "number_exact": number_exact,
            "zip_match": zip_match,
            "city_match": city_match,
            "unit_match": unit_match,
            "distance_m": dist,
        },
    }


def _rank(cands: list[dict]) -> list[dict]:
    return sorted(cands, key=lambda c: (-c["match"]["score"], c["match"]["distance_m"] or 1e9))


def _street_matched(scored: list[dict]) -> bool:
    return any(c["match"]["street_score"] >= config.STREET_MATCH_THRESHOLD for c in scored)


def build_suggestions(nearby: list[dict], pa: ParsedAddress, keys: list[StreetKey], origin, ref_zip: str,
                      limit: int) -> list[dict]:
    """Same-street address points nearest (by house number) to the requested one."""
    scored = [score_candidate(r, pa, keys, origin, ref_zip) for r in nearby if r.get("address_number_int") is not None]
    same = [c for c in scored if c["match"]["street_score"] >= config.STREET_MATCH_THRESHOLD]
    same.sort(key=lambda c: (abs(c["address_number_int"] - pa.house.number), c["match"]["distance_m"] or 0))
    out, seen = [], set()
    for c in same:
        if c["formatted"] not in seen:
            seen.add(c["formatted"])
            out.append(c)
            if len(out) >= limit:
                break
    return out


@dataclass
class Decision:
    status: str
    verified: bool = False
    match_level: str | None = None
    confidence: float = 0.0
    best_match: dict | None = None
    candidates: list[dict] = field(default_factory=list)
    suggestions: list[dict] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def decide(pa: ParsedAddress, scored: list[dict], suggestions: list[dict], *, dataset: str, ref_zip: str,
           max_candidates: int, coverage: dict | None = None, area_empty: bool = False,
           empty_note: str | None = None, notes: list[str] | None = None) -> Decision:
    """Turn ranked candidates from one dataset (NAD or Overture) into a verdict."""
    d = Decision(status="not_found", candidates=scored[:max_candidates], suggestions=suggestions, notes=list(notes or []))
    matched = [c for c in scored if c["match"]["street_score"] >= config.STREET_MATCH_THRESHOLD]

    if matched:
        exact = [c for c in matched if c["match"]["number_exact"]] or matched
        if pa.unit:
            with_unit = [c for c in exact if c["match"]["unit_match"]]
            if with_unit:
                best = with_unit[0]
                d.status, d.verified, d.match_level, d.best_match = "verified", True, "unit", best
                d.confidence = best["match"]["score"]
                return d
            best = next((c for c in exact if not _unit_values(c)), exact[0])  # the building, not a random unit
            units = sorted({u for c in exact for u in _unit_values(c)}, key=_natural)
            d.notes.append(
                f"building address exists in {dataset} but unit '{pa.unit_raw}' is not recorded"
                + (f"; {dataset} units at this address: {', '.join(units[:20])}" if units else
                   f"; {dataset} has no sub-address records for this building (common - many sources omit units)")
            )
            d.status, d.verified, d.match_level, d.best_match = "unit_not_found", True, "address", best
            d.confidence = round(best["match"]["score"] * 0.9, 3)
            return d
        # Prefer the base record (no unit) as the representative point for a building.
        base = [c for c in exact if not _unit_values(c)]
        best = (base or exact)[0]
        n_units = sum(1 for c in exact if _unit_values(c))
        if n_units:
            d.notes.append(f"{n_units} sub-address (unit) records share this address number in the search window")
        if best["match"]["zip_match"] is False:
            d.notes.append(f"ZIP differs: input/geocoder {ref_zip} vs {dataset} {best['zip']}")
        if not best["match"]["number_exact"]:
            d.notes.append(f"house number suffix differs from {dataset} record")
        d.status, d.verified, d.match_level, d.best_match = "verified", True, "address", best
        d.confidence = best["match"]["score"] if best["match"]["number_exact"] else round(best["match"]["score"] * 0.85, 3)
        return d

    if suggestions:
        d.notes.append(f"street exists in {dataset} near the geocoded location, but this house number was not found")
        d.status, d.match_level, d.confidence = "street_only", "street", 0.3
        return d

    if coverage and not coverage.get("in_nad") and coverage.get("status_code") != 15:
        d.notes.append(f"county '{coverage.get('county')}, {coverage.get('state')}' is not in the NAD: {coverage.get('status')}")
        d.status = "not_covered"
        return d
    if area_empty:
        d.notes.append(f"no {dataset} address points within {STREET_PROBE_RADIUS_M} m of the geocoded location - area likely not covered")
        d.status = "not_covered"
        return d
    if empty_note:
        d.notes.append(empty_note)
    d.confidence = 0.05
    return d


# ---------------------------------------------------------------------------------------- NAD


async def _coverage(up: Upstream, geocode: dict | None, lat: float, lon: float) -> dict | None:
    try:
        return await up.resolve_coverage(lat, lon, (geocode or {}).get("county"), (geocode or {}).get("state"))
    except UpstreamError:
        return None


async def _nad_near_phase(up: Upstream, pa: ParsedAddress, geocode: dict, keys: list[StreetKey], ref_zip: str,
                          out: dict, timings: dict, max_candidates: int) -> Decision:
    origin = (geocode["lat"], geocode["lon"])
    where = f"Add_Number = {pa.house.number}"
    notes: list[str] = []

    async def timed_coverage() -> dict | None:
        tc = time.perf_counter()
        try:
            return await _coverage(up, geocode, *origin)
        finally:
            timings["coverage"] = round((time.perf_counter() - tc) * 1000, 1)

    def uncovered(cov: dict | None) -> bool:
        return bool(cov) and cov.get("status_code") not in (10, 15)

    t = time.perf_counter()
    coverage_task = asyncio.create_task(timed_coverage())
    scored: list[dict] = []
    nad_error: str | None = None
    for i, radius in enumerate(config.SEARCH_RADII_M):
        # County has no NAD data at all: don't spend seconds on wider searches.
        if i > 0 and uncovered(await coverage_task):
            break
        try:
            recs, _ = await up.nad_near(*origin, radius, where=where)
        except UpstreamError as exc:
            nad_error = exc.message
            break
        out["search_radius_m"] = radius
        scored = _rank([score_candidate(r, pa, keys, origin, ref_zip) for r in recs])
        if _street_matched(scored):
            break
    timings["nad"] = round((time.perf_counter() - t) * 1000, 1)
    out["coverage"] = await coverage_task

    if nad_error:
        return Decision(status="upstream_error", notes=[f"NAD service error: {nad_error}"])

    suggestions: list[dict] = []
    area_empty = False
    if not _street_matched(scored) and not uncovered(out["coverage"]):
        # Is the street itself in the NAD here? Pull every point nearby and match streets locally.
        t = time.perf_counter()
        try:
            nearby, truncated = await up.nad_near(*origin, STREET_PROBE_RADIUS_M)
            if truncated:
                notes.append(f"dense area: street probe hit the 2000-point limit within {STREET_PROBE_RADIUS_M} m, suggestions may be incomplete")
        except UpstreamError as exc:
            nearby = None
            notes.append(f"street probe failed: {exc.message}")
        timings["street_probe"] = round((time.perf_counter() - t) * 1000, 1)
        out["nad_points_nearby"] = len(nearby) if nearby is not None else None
        suggestions = build_suggestions(nearby or [], pa, keys, origin, ref_zip, max_candidates)
        area_empty = nearby == []

    return decide(pa, scored, suggestions, dataset="NAD", ref_zip=ref_zip, max_candidates=max_candidates,
                  coverage=out["coverage"], area_empty=area_empty, notes=notes)


# ----------------------------------------------------------------------------------- Overture


async def _overture_phase(ov: "OvertureClient", pa: ParsedAddress, keys: list[StreetKey], ref_zip: str,
                          origin: tuple[float, float] | None, extent: dict | None, max_candidates: int) -> tuple[Decision, str]:
    release = await ov.release()
    bbox = bbox_around(*origin, config.OVERTURE_SEARCH_RADIUS_M) if origin else extent
    recs = await ov.query(bbox, number=pa.house.number)
    scored = _rank([score_candidate(r, pa, keys, origin, ref_zip) for r in recs])
    suggestions: list[dict] = []
    area_empty = False
    if not _street_matched(scored) and origin:
        nearby = await ov.query(bbox_around(*origin, STREET_PROBE_RADIUS_M))
        suggestions = build_suggestions(nearby, pa, keys, origin, ref_zip, max_candidates)
        area_empty = not nearby
    dec = decide(pa, scored, suggestions, dataset="Overture", ref_zip=ref_zip, max_candidates=max_candidates,
                 area_empty=area_empty, empty_note=None if origin else "no matching Overture record in the ZIP area")
    return dec, release


# --------------------------------------------------------------------------------------- main


async def verify_address(
    up: Upstream,
    pa: ParsedAddress,
    *,
    overture: "OvertureClient | None" = None,
    lat: float | None = None,
    lon: float | None = None,
    structured: bool = False,
    zip_fallback: bool = False,
    overture_fallback: bool = True,
    max_candidates: int = 5,
) -> dict[str, Any]:
    t0 = time.perf_counter()
    timings: dict[str, float] = {}
    notes: list[str] = list(pa.warnings)
    out: dict[str, Any] = {
        "status": None,
        "verified": False,
        "match_level": None,
        "confidence": 0.0,
        "data_source": None,
        "nad_status": None,
        "input": pa.raw,
        "parsed": {
            "house_number": pa.house.raw if pa.house else None,
            "street": pa.street_text or None,
            "unit": pa.unit_raw or None,
            "city": pa.city or None,
            "state": pa.state or None,
            "zip": pa.zip5 or None,
        },
        "geocode": None,
        "coverage": None,
        "best_match": None,
        "candidates": [],
        "suggestions": [],
        "fallback": None,
        "notes": notes,
        "disclaimer": DISCLAIMER,
        "timing_ms": timings,
    }

    def finish(status: str | None = None) -> dict[str, Any]:
        if status:
            out["status"] = status
        timings["total"] = round((time.perf_counter() - t0) * 1000, 1)
        return out

    def apply(dec: Decision, source: str) -> None:
        out.update(status=dec.status, verified=dec.verified, match_level=dec.match_level, confidence=dec.confidence,
                   best_match=dec.best_match, candidates=dec.candidates, suggestions=dec.suggestions,
                   data_source=None if dec.status == "upstream_error" else source)
        notes.extend(dec.notes)

    if pa.house is None:
        return finish("invalid_input")

    # ---- 1. geocode -------------------------------------------------------------------
    geocode: dict | None = None
    if lat is not None and lon is not None:
        geocode = {"source": "input", "lat": lat, "lon": lon}
    else:
        t = time.perf_counter()
        try:
            if structured:
                street_line = " ".join(p for p in (pa.house.raw, pa.street_text) if p)
                geocode = await up.census_geocode_structured(street_line, pa.city, pa.state, pa.zip5)
            else:
                geocode = await up.census_geocode_oneline(pa.geocode_text)
        except UpstreamError as exc:
            notes.append(f"census geocoder unavailable: {exc.message}")
        timings["geocode"] = round((time.perf_counter() - t) * 1000, 1)
        if geocode:
            geocode = {"source": "census", **geocode}

    census_key, user_key = census_street_key(geocode), StreetKey.from_text(pa.street_text)
    keys = [k for k in (census_key, user_key) if k]
    if census_key and (
        (user_key.predir and census_key.predir and user_key.predir != census_key.predir)
        or (user_key.postdir and census_key.postdir and user_key.postdir != census_key.postdir)
    ):
        notes.append(f"Census geocoder matched '{geocode.get('matched_address')}', which changes the street "
                     "direction you gave; matching keeps your direction")
    ref_zip = pa.zip5 or (geocode or {}).get("zip") or ""
    origin: tuple[float, float] | None = None
    extent: dict | None = None

    # ---- 2. NAD -----------------------------------------------------------------------
    if geocode is not None:
        out["geocode"] = geocode
        origin = (geocode["lat"], geocode["lon"])
        nad_dec = await _nad_near_phase(up, pa, geocode, keys, ref_zip, out, timings, max_candidates)
    else:
        # Optional ZIP fallback when the address is not in TIGER. Without any location, neither
        # NAD nor Overture can be searched (an attribute-only scan would time out / read 27 GB).
        if not (zip_fallback and pa.zip5):
            if not pa.zip5:
                notes.append("address could not be geocoded; add a ZIP code and set zip_fallback=true to search the ZIP area")
            else:
                notes.append("address could not be geocoded; retry with zip_fallback=true to search the whole ZIP area (slower)")
            return finish("geocode_failed")
        t = time.perf_counter()
        try:
            extent = await up.zcta_extent(pa.zip5)
        except UpstreamError as exc:
            notes.append(f"ZIP lookup failed: {exc.message}")
            return finish("upstream_error")
        if not extent:
            notes.append(f"ZIP {pa.zip5} not found in Census ZCTAs")
            return finish("geocode_failed")
        out["geocode"] = {"source": "zcta", "zip": pa.zip5, "extent": extent}
        try:
            recs = await up.nad_in_envelope(extent, f"Add_Number = {pa.house.number} AND Zip_Code = '{pa.zip5}'")
            scored = _rank([score_candidate(r, pa, keys, None, ref_zip) for r in recs])
            nad_dec = decide(pa, scored, [], dataset="NAD", ref_zip=ref_zip, max_candidates=max_candidates,
                             empty_note="no matching NAD record in the ZIP area")
        except UpstreamError as exc:
            nad_dec = Decision(status="upstream_error", notes=[f"NAD ZIP search failed: {exc.message}"])
        timings["nad"] = round((time.perf_counter() - t) * 1000, 1)

    apply(nad_dec, "nad")
    out["nad_status"] = nad_dec.status

    # ---- 3. Overture fallback -----------------------------------------------------------
    if overture is not None and overture_fallback and nad_dec.status in FALLBACK_TRIGGERS:
        info: dict[str, Any] = {"provider": "overture", "release": None, "triggered_by": nad_dec.status,
                                "status": None, "adopted": False, "error": None}
        out["fallback"] = info
        t = time.perf_counter()
        try:
            ov_dec, info["release"] = await _overture_phase(overture, pa, keys, ref_zip, origin, extent, max_candidates)
        except UpstreamError as exc:
            info["error"] = exc.message
            notes.append(f"Overture fallback failed: {exc.message}")
        else:
            info["status"] = ov_dec.status
            if STATUS_RANK[ov_dec.status] > STATUS_RANK[nad_dec.status]:
                info["adopted"] = True
                ov_dec.confidence = round(ov_dec.confidence * 0.9, 3)  # not the authoritative NAD
                src = (ov_dec.best_match or {}).get("source")
                ov_dec.notes.insert(0, f"NAD result was '{nad_dec.status}'; answered from Overture Maps {info['release']}"
                                       + (f" ({src})" if src else ""))
                apply(ov_dec, "overture")
            else:
                notes.append(f"Overture fallback ({info['release']}) returned '{ov_dec.status}'; kept the NAD result")
        timings["overture"] = round((time.perf_counter() - t) * 1000, 1)

    return finish()
