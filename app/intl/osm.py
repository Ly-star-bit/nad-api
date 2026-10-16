"""Countries without a free official API.

- `overture`: Photon (OpenStreetMap search) finds where the street is, then the official register data that
  Overture Maps carries for the country is searched around that point - the same "geocode, then look nearby"
  method the US pipeline uses with Census + NAD.
- `osm`: no register data at all (UK, Ireland, Sweden, ...): Photon's own OpenStreetMap house numbers. A hit is
  real evidence; a miss is not, because OSM has many streets without their house numbers.

Public Nominatim is deliberately not used: its usage policy forbids address-verification services.
Photon asks only to "be fair", so its concurrency is capped (INTL_SERVICE_CONCURRENCY) and PHOTON_URL can point at a
self-hosted instance.
"""

from __future__ import annotations

import re
import time

from .. import config
from ..upstream import UpstreamError, UpstreamRejected
from ..verify import bbox_around, haversine_m
from .base import Ctx, Lookup, ProviderInfo, cand
from .countries import COUNTRIES
from .match import STREET_OK, best_street_sim, norm_num, norm_postcode
from .parse import IntlAddress

INFO = {
    "overture": ProviderInfo("overture", "Overture Maps", "overture", "https://overturemaps.org",
                             "per source (mostly CC BY 4.0 / CC0)", "Overture Maps Foundation, with OpenStreetMap"),
    "osm": ProviderInfo("osm", "OpenStreetMap (Photon)", "osm", "https://photon.komoot.io", "ODbL",
                        "© OpenStreetMap contributors"),
}


# ------------------------------------------------------------------------------------------- Photon

async def photon(ctx: Ctx, a: IntlAddress, *, limit: int = 10, bias: tuple[float, float] | None = None) -> list[dict]:
    """Structured search first (street / number / postcode / city), then free text if that finds nothing in the
    country. Returns GeoJSON features of this country only."""
    cc = a.country
    params = {"countrycode": cc, "street": a.street, "housenumber": a.number_base, "postcode": a.postcode,
              "city": a.city, "limit": limit}
    params = {k: v for k, v in params.items() if v not in ("", None)}
    feats: list[dict] = []
    if a.street:
        try:
            data = await ctx.get("photon", f"{config.PHOTON_URL}/structured", params)
            feats = _in_country(data, cc)
        except UpstreamRejected:
            feats = []
    if not any(_street_hit(f, a) for f in feats):
        q = ", ".join(p for p in (" ".join(p for p in (a.street, a.number_base) if p), a.postcode, a.city) if p)
        free = {"q": q or a.raw, "limit": limit}
        if bias:
            free.update(lat=round(bias[0], 5), lon=round(bias[1], 5))
        data = await ctx.get("photon", f"{config.PHOTON_URL}/api", free)
        seen = {(f["properties"].get("osm_type"), f["properties"].get("osm_id")) for f in feats}
        feats += [f for f in _in_country(data, cc) if (f["properties"].get("osm_type"), f["properties"].get("osm_id")) not in seen]
    return feats


def _in_country(data: dict, cc: str) -> list[dict]:
    return [f for f in (data or {}).get("features") or []
            if (f.get("properties") or {}).get("countrycode", "").upper() == cc and (f.get("geometry") or {}).get("coordinates")]


def _street_of(f: dict) -> str | None:
    p = f["properties"]
    return p.get("name") if p.get("type") == "street" else p.get("street")


def _street_hit(f: dict, a: IntlAddress) -> bool:
    return f["properties"].get("type") in ("house", "street") and best_street_sim(a.street, _street_of(f)) >= STREET_OK


def interpolated(p: dict) -> bool:
    """An OSM addr:interpolation line: a computed number, not a mapped address."""
    return p.get("osm_key") == "place" and p.get("osm_value") == "house_number" and p.get("osm_type") == "W"


def photon_cands(feats: list[dict], origin: tuple[float, float] | None = None, number_first: bool = False) -> list[dict]:
    """Photon features -> candidates, plain addresses before shops and offices that share the address. An
    interpolation line only shows that the street has a numbered range there, so it counts as the street."""
    out = []
    for f in sorted(feats, key=lambda f: bool(f["properties"].get("name")) and f["properties"].get("type") == "house"):
        p = f["properties"]
        lon, lat = f["geometry"]["coordinates"][:2]
        if p.get("type") == "house" and p.get("housenumber") and not interpolated(p):
            kind = "address"
        elif p.get("type") == "street" or interpolated(p):
            kind = "street"
        else:
            continue
        street = _street_of(f)
        num = p.get("housenumber") if kind == "address" else None
        line1 = " ".join(x for x in ((num, street) if number_first else (street, num)) if x)
        name = p.get("name") if kind == "address" and p.get("name") not in (None, street, line1) else None
        formatted = ", ".join(x for x in (name, line1, " ".join(x for x in (p.get("postcode"), p.get("city")) if x)) if x)
        out.append(cand(kind, number=p.get("housenumber") if kind == "address" else None, street=street,
                        postcode=p.get("postcode"), city=p.get("city") or p.get("town") or p.get("village"),
                        lat=lat, lon=lon, id=f"{p.get('osm_type')}{p.get('osm_id')}", formatted=formatted,
                        region=p.get("state"), source="OpenStreetMap", license="ODbL",
                        distance_m=round(haversine_m(origin[0], origin[1], lat, lon), 1) if origin else None))
    return out


def locate(feats: list[dict], a: IntlAddress) -> dict | None:
    """Where to search the register: the house itself, else the street (postcode-matching first), else nothing -
    a city-level hit is too coarse to find one house in."""
    def rank(f: dict) -> tuple:
        p = f["properties"]
        pc_ok = bool(a.postcode) and norm_postcode(p.get("postcode")) == norm_postcode(a.postcode)
        exact = p.get("type") == "house" and norm_num(p.get("housenumber")) == norm_num(a.number_base)
        return (not exact, not pc_ok, -best_street_sim(a.street, _street_of(f)))
    hits = sorted((f for f in feats if _street_hit(f, a)), key=rank)
    if not hits:
        return None
    f = hits[0]
    lon, lat = f["geometry"]["coordinates"][:2]
    p = f["properties"]
    label = ", ".join(x for x in (" ".join(x for x in (_street_of(f), p.get("housenumber")) if x),
                                   " ".join(x for x in (p.get("postcode"), p.get("city")) if x)) if x)
    return {"source": "photon", "lat": lat, "lon": lon, "matched_address": label, "level": p.get("type")}


# ---------------------------------------------------------------------------------------- Overture

async def overture(ctx: Ctx, a: IntlAddress) -> Lookup:
    if ctx.ov is None:
        raise UpstreamError("overture", "Overture is disabled (OVERTURE_ENABLED=0)")
    t = time.perf_counter()
    feats = await photon(ctx, a)
    timing = {"geocode": round((time.perf_counter() - t) * 1000, 1)}
    geo = locate(feats, a)
    if geo is None:
        return Lookup(status="geocode_failed", timing=timing,
                      notes=["OpenStreetMap search could not find this street; check the street name and city"])
    origin = (geo["lat"], geo["lon"])
    m = re.match(r"\d+", a.number_base)
    t = time.perf_counter()
    recs = await ctx.ov.query(bbox_around(*origin, config.OVERTURE_SEARCH_RADIUS_M), number=int(m.group()) if m else None)
    cands = [overture_cand(r, origin) for r in recs]
    lk = Lookup(cands=cands, geocode=geo, timing=timing)
    if not any(c["number"] and norm_num(c["number"]).startswith(norm_num(a.number_base)) and
               best_street_sim(a.street, c["street"]) >= STREET_OK for c in cands):
        # the number is not there: is the street (or anything) in the register around this point?
        nearby = await ctx.ov.query(bbox_around(*origin, 300))
        seen = {c["uuid"] for c in cands}
        lk.cands += [overture_cand(r, origin) for r in nearby if r.get("uuid") not in seen]
        lk.area_empty = not nearby
    lk.timing["overture"] = round((time.perf_counter() - t) * 1000, 1)
    return lk


def overture_cand(r: dict, origin: tuple[float, float]) -> dict:
    d = round(haversine_m(origin[0], origin[1], r["lat"], r["lon"]), 1) if r.get("lat") is not None else None
    c = cand("address", number=r.get("address_number"), street=r.get("street"), postcode=r.get("postcode"),
             city=r.get("city"), unit=(r.get("subaddress") or {}).get("unit"), lat=r.get("lat"), lon=r.get("lon"),
             id=r.get("uuid"), region=r.get("state"), source=r.get("source"), license=r.get("license"),
             updated=r.get("updated"), distance_m=d)
    # Overture's place level differs by country (Berlin records say "Mitte"): a different name is not a mismatch
    c["city_loose"] = True
    return c


# --------------------------------------------------------------------------------------- OSM only

async def osm(ctx: Ctx, a: IntlAddress) -> Lookup:
    notes: list[str] = []
    bias = None
    t = time.perf_counter()
    if a.country == "GB" and a.postcode:
        bias = await uk_postcode(ctx, a, notes)
    feats = await photon(ctx, a, bias=bias)
    geo = locate(feats, a)
    origin = (geo["lat"], geo["lon"]) if geo else bias
    lk = Lookup(cands=photon_cands(feats, origin, COUNTRIES[a.country].number_first), geocode=geo, notes=notes,
                timing={"geocode": round((time.perf_counter() - t) * 1000, 1)})
    if any(interpolated(f["properties"]) and norm_num(f["properties"].get("housenumber")) == norm_num(a.number_base)
           for f in feats):
        lk.notes.append("OpenStreetMap only has an interpolated number range on this stretch of the street (the "
                        "numbers exist along it, but each house is not mapped), so the exact number can't be confirmed")
    if not feats:
        lk.status = "geocode_failed"
        lk.notes.append("OpenStreetMap search found nothing for this address")
    return lk


async def uk_postcode(ctx: Ctx, a: IntlAddress, notes: list[str]) -> tuple[float, float] | None:
    """postcodes.io (ONS / Royal Mail open data): does the postcode exist, and where is it."""
    pc = re.sub(r"\s", "", a.postcode)
    try:
        data = await ctx.get("postcodes_io", f"{config.POSTCODES_IO_URL}/postcodes/{pc}", {})
    except UpstreamRejected:  # 404: no such postcode
        notes.append(f"postcode {a.postcode} does not exist (postcodes.io)")
        return None
    except UpstreamError as exc:
        notes.append(f"postcode check unavailable: {exc.message}")
        return None
    r = (data or {}).get("result") or {}
    if r.get("latitude") is None:
        return None
    return r["latitude"], r["longitude"]
