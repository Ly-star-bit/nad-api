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
from .upstream import Upstream, UpstreamError, UpstreamRejected

if TYPE_CHECKING:
    from .overture import OvertureClient

# Radius for the "is this street / area covered at all" probe when the house number is missing.
STREET_PROBE_RADIUS_M = 300

DISCLAIMER = (
    "NAD / Overture addresses confirm that an address point physically exists according to the addressing "
    "authority behind each record; an Overture places match only shows that a business lists the address. "
    "None of them confirms USPS deliverability."
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
        sub = rec["subaddress"]
        # "Apt B201" / "Unit 2-201" against Building B + Unit 201: building or floor joined to the unit counts too
        joined = [f"{b}{sub['unit']}" for b in (sub.get("building"), sub.get("floor")) if b and sub.get("unit")]
        unit_match: bool | None = any(norm_unit(v) == pa.unit for v in unit_vals + joined)
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


def same_street(nearby: list[dict], pa: ParsedAddress, keys: list[StreetKey], origin, ref_zip: str) -> list[dict]:
    """Scored address points on the requested street."""
    scored = [score_candidate(r, pa, keys, origin, ref_zip) for r in nearby if r.get("address_number_int") is not None]
    return [c for c in scored if c["match"]["street_score"] >= config.STREET_MATCH_THRESHOLD]


def brackets(same: list[dict], number: int) -> bool:
    """True when this street has numbers on both sides of `number` among the points given."""
    nums = [c["address_number_int"] for c in same]
    return any(n < number for n in nums) and any(n > number for n in nums)


def build_suggestions(same: list[dict], pa: ParsedAddress, limit: int) -> list[dict]:
    """Same-street address points nearest (by house number) to the requested one."""
    same = sorted(same, key=lambda c: (abs(c["address_number_int"] - pa.house.number), c["match"]["distance_m"] or 0))
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


def _street_note(pa: ParsedAddress, best: dict, dataset: str) -> list[str]:
    """Name the record's street when it is written differently from the input: a typo, a left-out direction or type.
    Standard abbreviations (Street / St, West / W, Fifth / 5th) normalize to the same key and say nothing."""
    user, have = StreetKey.from_text(pa.street_text), record_street_key(best)
    if not user.full or not have.full or user.full.replace(" ", "") == have.full.replace(" ", ""):
        return []
    return [f"street written '{pa.street_text}', {dataset} record has '{best.get('street') or have.full}'"]


def record_split(rec: dict, level: str | None, parsed: dict) -> dict:
    """A verified address split as the record has it ('Phoen' -> Phoenix, 'W Washington St' -> West Washington
    Street). The unit is the record's when it was matched too; else the one written, which the record can't speak to.
    A part the record lacks stays as written. `source: record` tells it from the input's own split."""
    sub = rec.get("subaddress") or {}
    unit = (" ".join(v for v in (sub.get("building"), sub.get("floor"), sub.get("unit"), sub.get("room")) if v)
            or sub.get("subaddress")) if level == "unit" else None
    unit = unit or parsed.get("address2")
    number, street = rec.get("address_number"), rec.get("street")
    return {**parsed, "city": rec.get("city") or parsed.get("city"), "zip": rec.get("zip") or parsed.get("zip"),
            "address1": " ".join(p for p in (number, street) if p) if street else parsed.get("address1"), "address2": unit,
            "house_number": number or parsed.get("house_number"), "street": street or parsed.get("street"),
            "unit": unit, "source": "record"}


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
                d.notes += _street_note(pa, best, dataset)
                d.status, d.verified, d.match_level, d.best_match = "verified", True, "unit", best
                d.confidence = best["match"]["score"]
                return d
            best = next((c for c in exact if not _unit_values(c)), exact[0])  # the building, not a random unit
            units = sorted({u for c in exact for u in _unit_values(c)}, key=_natural)
            d.notes += _street_note(pa, best, dataset)
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
        d.notes += _street_note(pa, best, dataset)
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


def _name_token(key: StreetKey) -> str:
    """The street name's most distinctive word, for a LIKE filter on NAD's St_Name: the matcher compares the full
    name afterwards. The key is normalized ('Saint Charles' -> 'ST CHARLES', '5th' -> '5'), so short words, which are
    the ones normalization rewrites, are avoided."""
    words = [re.sub(r"[^A-Z0-9]", "", w) for w in key.core.split()]
    words = [w for w in words if w]
    return max((w for w in words if len(w) >= 3), key=len, default="") or max(words, key=len, default="")


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

    probe: dict[str, Any] | None = None  # every point within STREET_PROBE_RADIUS_M, fetched at most once

    async def street_probe() -> dict[str, Any]:
        """Is the street itself in the NAD here? Pull every point nearby and match streets locally."""
        nonlocal probe
        if probe is None:
            tp, note = time.perf_counter(), None
            try:
                nearby, truncated = await up.nad_near(*origin, STREET_PROBE_RADIUS_M)
                if truncated:
                    note = f"dense area: street probe hit the 2000-point limit within {STREET_PROBE_RADIUS_M} m, suggestions may be incomplete"
            except UpstreamError as exc:
                nearby, note = None, f"street probe failed: {exc.message}"
            timings["street_probe"] = round((time.perf_counter() - tp) * 1000, 1)
            out["nad_points_nearby"] = len(nearby) if nearby is not None else None
            probe = {"nearby": nearby, "same": same_street(nearby or [], pa, keys, origin, ref_zip), "note": note}
        return probe

    coverage_task = asyncio.create_task(timed_coverage())
    scored: list[dict] = []
    nad_error: str | None = None
    nad_ms = 0.0
    for i, radius in enumerate(config.SEARCH_RADII_M):
        if i > 0:
            # County has no NAD data at all: don't spend seconds on wider searches.
            if uncovered(await coverage_task):
                break
            # This street's own numbers on both sides of the requested one are within the radius already
            # searched, so the number would sit between them, inside it: a wider search (seconds in a dense
            # city) could only turn up the same number on another street.
            if config.SEARCH_RADII_M[i - 1] >= STREET_PROBE_RADIUS_M and brackets((await street_probe())["same"], pa.house.number):
                break
        t = time.perf_counter()
        try:
            recs, _ = await up.nad_near(*origin, radius, where=where)
        except UpstreamError as exc:
            nad_error = exc.message
            break
        finally:
            nad_ms += time.perf_counter() - t
        out["search_radius_m"] = radius
        scored = _rank([score_candidate(r, pa, keys, origin, ref_zip) for r in recs])
        if _street_matched(scored):
            break
    timings["nad"] = round(nad_ms * 1000, 1)
    out["coverage"] = await coverage_task

    if nad_error:
        return Decision(status="upstream_error", notes=[f"NAD service error: {nad_error}"])

    suggestions: list[dict] = []
    area_empty = False
    if not _street_matched(scored) and not uncovered(out["coverage"]):
        p = await street_probe()
        suggestions = build_suggestions(p["same"], pa, max_candidates)
        area_empty = p["nearby"] == []
        notes += [p["note"]] if p["note"] else []  # only said when the answer rests on the probe

    return decide(pa, scored, suggestions, dataset="NAD", ref_zip=ref_zip, max_candidates=max_candidates,
                  coverage=out["coverage"], area_empty=area_empty, notes=notes)


# ----------------------------------------------------------------------------------- Overture


async def _overture_phase(ov: "OvertureClient", pa: ParsedAddress, keys: list[StreetKey], ref_zip: str,
                          origin: tuple[float, float] | None, extent: dict | None, max_candidates: int,
                          need_street: bool = True) -> tuple[Decision, str]:
    """`need_street=False` skips the street probe: when NAD already found the street, the probe (a second DuckDB
    read) could at best tie that verdict, and a tie keeps the NAD result anyway."""
    release = await ov.release()
    bbox = bbox_around(*origin, config.OVERTURE_SEARCH_RADIUS_M) if origin else extent
    recs = await ov.query(bbox, number=pa.house.number)
    scored = _rank([score_candidate(r, pa, keys, origin, ref_zip) for r in recs])
    suggestions: list[dict] = []
    area_empty = False
    if not _street_matched(scored) and origin and need_street:
        nearby = await ov.query(bbox_around(*origin, STREET_PROBE_RADIUS_M))
        suggestions = build_suggestions(same_street(nearby, pa, keys, origin, ref_zip), pa, max_candidates)
        area_empty = not nearby
    empty_note = ("no matching Overture record in the ZIP area" if not origin
                  else None if need_street else "this house number is not in Overture either")
    dec = decide(pa, scored, suggestions, dataset="Overture", ref_zip=ref_zip, max_candidates=max_candidates,
                 area_empty=area_empty, empty_note=empty_note)
    return dec, release


async def _places_phase(ov: "OvertureClient", pa: ParsedAddress, keys: list[StreetKey], ref_zip: str,
                        origin: tuple[float, float] | None, extent: dict | None, max_candidates: int) -> Decision:
    """Businesses whose listed address is this one. Only a match counts: most addresses have no business, so a miss
    says nothing, and other businesses on the street are no evidence about this number."""
    bbox = bbox_around(*origin, config.OVERTURE_SEARCH_RADIUS_M) if origin else extent
    recs = await ov.query(bbox, number=pa.house.number, theme="places")
    # the query matched the number anywhere after a comma of the free text; the number read from it must be this one
    recs = [r for r in recs if r["address_number_int"] == pa.house.number]
    scored = _rank([score_candidate(r, pa, keys, origin, ref_zip) for r in recs])
    return decide(pa, scored, [], dataset="Overture places", ref_zip=ref_zip, max_candidates=max_candidates)


# --------------------------------------------------------------------------------------- main


async def verify_address(
    up: Upstream,
    pa: ParsedAddress,
    *,
    overture: "OvertureClient | None" = None,
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
            "country": "US",
            "state": pa.state or None,
            "city": pa.city or None,
            "zip": pa.zip5 or None,
            "address1": " ".join(p for p in (pa.house.raw if pa.house else "", pa.street_text) if p) or None,
            "address2": pa.unit_raw or None,
            "house_number": pa.house.raw if pa.house else None,
            "street": pa.street_text or None,
            "unit": pa.unit_raw or None,
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
    geocode_error: str | None = None
    t = time.perf_counter()
    try:
        geocode = await up.census_geocode_oneline(pa.geocode_text)
    except UpstreamRejected as exc:
        notes.append(f"census geocoder rejected the address: {exc.message}")
    except UpstreamError as exc:
        geocode_error = exc.message
        notes.append(f"census geocoder unavailable: {exc.message}")
    timings["geocode"] = round((time.perf_counter() - t) * 1000, 1)
    if geocode:
        geocode = {"source": "census", **geocode}
        if geocode.get("alternatives"):
            others = "; ".join(a["matched_address"] for a in geocode["alternatives"][:4])
            notes.append(f"Census found {geocode['match_count']} addresses for this input and only the first "
                         f"({geocode['matched_address']}) was checked; add the state or ZIP to pick one. "
                         f"Others: {others}")

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
    overture_area = True  # whether `extent` is small enough for the Overture fallback
    street_unknown = False  # an area search found no street of that name: not_found would overstate it
    # notes that a business listed at the address (step 4) proves wrong, and what they say then
    if_listed: dict[str, str] = {}
    if geocode is not None:
        out["geocode"] = geocode
        origin = (geocode["lat"], geocode["lon"])
        nad_dec = await _nad_near_phase(up, pa, geocode, keys, ref_zip, out, timings, max_candidates)
    elif (pa.zip5 or (pa.city and pa.state)) and not geocode_error and _name_token(user_key):
        # Search the ZIP area (or the ZIP areas overlapping the named city) for this number on a street with this
        # name: new streets are often missing from TIGER but already in NAD (28869 Fanning Dr NW, Madison, AL).
        t = time.perf_counter()
        try:
            if pa.zip5:
                zip_extent = await up.zcta_extent(pa.zip5)
                area = {"zips": [pa.zip5], "extent": zip_extent} if zip_extent else None
            else:
                area = await up.city_area(pa.city, pa.state)
        except UpstreamError as exc:
            notes.append(f"{'ZIP' if pa.zip5 else 'city'} lookup failed: {exc.message}")
            return finish("upstream_error")
        if not area:
            notes.append(f"ZIP {pa.zip5} not found in Census ZCTAs" if pa.zip5 else
                         f"address could not be geocoded and '{pa.city}, {pa.state}' is not a Census place; "
                         "add a ZIP code to search the ZIP area")
            return finish("geocode_failed")
        extent = area["extent"]
        if pa.zip5:
            out["geocode"] = {"source": "zcta", "zip": pa.zip5, "extent": extent}
            where = f"ZIP {pa.zip5}"
        else:
            out["geocode"] = {"source": "city", "city": pa.city, "state": pa.state, "zips": area["zips"], "extent": extent}
            zips = area["zips"]
            where = (f"the ZIP areas of {pa.city}, {pa.state} ({', '.join(zips[:8])}"
                     + (f" and {len(zips) - 8} more" if len(zips) > 8 else "") + ")")
        name_filter = f"St_Name LIKE '%{_name_token(user_key)}%'"
        try:
            recs = await up.nad_in_envelope(extent, f"Add_Number = {pa.house.number} AND {name_filter}")
            scored = _rank([score_candidate(r, pa, keys, None, ref_zip) for r in recs])
            center = ((extent["ymin"] + extent["ymax"]) / 2, (extent["xmin"] + extent["xmax"]) / 2)
            coverage = None if _street_matched(scored) else await _coverage(up, {"state": pa.state}, *center)
            out["coverage"] = coverage
            suggestions: list[dict] = []
            empty_note = (f"address could not be geocoded, and NAD has no {pa.house.number} on a street named like "
                          f"'{pa.street_text}' in {where}")
            if (not _street_matched(scored) and (coverage or {}).get("in_nad", True)
                    and max(extent["xmax"] - extent["xmin"], extent["ymax"] - extent["ymin"]) <= config.AREA_STREET_PROBE_MAX_DEG):
                # Is the street itself there? Other numbers on it are evidence against this one; no street at all
                # is not: Census misses are mostly streets newer than the data (1410 United Dr, Shippensburg, PA).
                try:
                    on_street = same_street(await up.nad_in_envelope(extent, name_filter, limit=2000), pa, keys, None, ref_zip)
                except UpstreamError:
                    on_street = None
                if on_street:
                    suggestions = build_suggestions(on_street, pa, max_candidates)
                elif on_street is not None:
                    street_unknown = True
                    dated = f" (data as of {coverage['data_date']})" if (coverage or {}).get("data_date") else ""
                    no_street = f"neither Census nor NAD{dated} has a street named like '{pa.street_text}' in {where}"
                    empty_note = f"{no_street}: the address may be newer than the data, or the street name may be misspelled"
                    if_listed[empty_note] = f"{no_street}, likely because the street is newer than the data"
            nad_dec = decide(pa, scored, suggestions, dataset="NAD", ref_zip=ref_zip, max_candidates=max_candidates,
                             coverage=coverage, empty_note=empty_note)
        except UpstreamError as exc:
            nad_dec = Decision(status="upstream_error", notes=[f"NAD street search failed: {exc.message}"])
        timings["nad"] = round((time.perf_counter() - t) * 1000, 1)
        if max(extent["xmax"] - extent["xmin"], extent["ymax"] - extent["ymin"]) > config.CITY_FALLBACK_OVERTURE_MAX_DEG:
            overture_area = False
    else:
        # Without any location, neither NAD nor Overture can be searched (an attribute-only scan would
        # time out / read 27 GB).
        if geocode_error:  # the geocoder was unreachable: nothing is known about the address itself
            return finish("upstream_error")
        notes.append("address could not be geocoded" + ("" if pa.zip5 else "; add a ZIP code to search the ZIP area"))
        return finish("geocode_failed")

    apply(nad_dec, "nad")
    out["nad_status"] = nad_dec.status

    # ---- 3. Overture fallback -----------------------------------------------------------
    if overture is not None and nad_dec.status in FALLBACK_TRIGGERS and not overture_area:
        notes.append("the search area is too large for the Overture fallback" + ("" if pa.zip5 else "; add a ZIP code"))
    elif overture is not None and nad_dec.status in FALLBACK_TRIGGERS:
        info: dict[str, Any] = {"provider": "overture", "release": None, "triggered_by": nad_dec.status,
                                "status": None, "adopted": False, "error": None}
        out["fallback"] = info
        t = time.perf_counter()
        try:
            ov_dec, info["release"] = await _overture_phase(
                overture, pa, keys, ref_zip, origin, extent, max_candidates,
                need_street=STATUS_RANK["street_only"] > STATUS_RANK[nad_dec.status])
        except UpstreamError as exc:
            info["error"] = exc.message
            notes.append(f"Overture fallback failed: {exc.message}")
        else:
            info["status"] = ov_dec.status
            # Searched over a whole ZIP / city area, a miss only shows Overture has no point there - often no data
            # at all (Savannah, GA) - so it must not replace NAD's not_covered with not_found.
            if STATUS_RANK[ov_dec.status] > STATUS_RANK[nad_dec.status] and (origin or ov_dec.verified):
                info["adopted"] = True
                ov_dec.confidence = round(ov_dec.confidence * 0.9, 3)  # not the authoritative NAD
                src = (ov_dec.best_match or {}).get("source")
                ov_dec.notes.insert(0, f"NAD result was '{nad_dec.status}'; answered from Overture Maps {info['release']}"
                                       + (f" ({src})" if src else ""))
                if_listed[ov_dec.notes[0]] = f"Overture fallback ({info['release']}) returned '{ov_dec.status}'"
                apply(ov_dec, "overture")
            else:
                notes.append(f"Overture fallback ({info['release']}) returned '{ov_dec.status}'; kept the NAD result")
                if_listed[notes[-1]] = f"Overture fallback ({info['release']}) returned '{ov_dec.status}'"
        timings["overture"] = round((time.perf_counter() - t) * 1000, 1)

        # ---- 4. Overture places: a business listed at this address. New industrial parks and warehouses are
        # listed long before NAD or TIGER has their streets (1410 United Dr, Shippensburg, PA). Not asked when the
        # addresses query just failed: the same S3 / DuckDB would cost a second timeout.
        if out["status"] in FALLBACK_TRIGGERS and info["error"] is None:
            places: dict[str, Any] = {"provider": "overture_places", "release": info["release"],
                                      "triggered_by": out["status"], "status": None, "adopted": False, "error": None}
            info["places"] = places
            t = time.perf_counter()
            try:
                pl_dec = await _places_phase(overture, pa, keys, ref_zip, origin, extent, max_candidates)
            except UpstreamError as exc:
                places["error"] = exc.message
                notes.append(f"Overture places fallback failed: {exc.message}")
            else:
                places["status"] = pl_dec.status
                if pl_dec.verified:
                    places["adopted"], info["adopted"] = True, False
                    pl_dec.confidence = round(pl_dec.confidence * 0.8, 3)  # a business's own address, not an address point
                    bm = pl_dec.best_match
                    src = bm["source"].partition(":")[2]
                    pl_dec.notes.insert(0, f"neither NAD nor Overture addresses has this address; answered from Overture "
                                           f"Maps {info['release']} places: '{bm['place']['name']}'"
                                           + (f" ({src})" if src else "") + " is listed at it, which shows a business "
                                           "uses the address, not that an addressing authority recorded it")
                    notes[:] = [if_listed.get(n, n) for n in notes]
                    apply(pl_dec, "overture_places")
                else:
                    notes.append(f"Overture places ({info['release']}) has no business listed at this address either")
            timings["overture_places"] = round((time.perf_counter() - t) * 1000, 1)

    if street_unknown and out["status"] == "not_found":
        out.update(status="geocode_failed", confidence=0.0)
    if out["verified"]:
        out["parsed"] = record_split(out["best_match"], out["match_level"], out["parsed"])
        out["parsed"]["state"] = out["best_match"].get("state") or out["parsed"]["state"]
    return finish()
