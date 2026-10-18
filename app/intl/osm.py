"""Countries without a free official API.

- `overture`: Photon (OpenStreetMap search) finds where the street is, then the official register data that
  Overture Maps carries for the country is searched along it - the same "geocode, then look nearby" method the US
  pipeline uses with Census + NAD.
- `osm`: no register data at all (UK, Ireland, Sweden, ...): Photon's own OpenStreetMap house numbers. A hit is
  real evidence; a miss is not, because OSM has many streets without their house numbers.

Either way the street is only taken in the town (or postcode) the input names: "Bahnhofstraße 1" exists in hundreds
of German towns, and Photon happily returns one of the others.

Public Nominatim is deliberately not used: its usage policy forbids address-verification services.
Photon asks only to "be fair", so its concurrency is capped (INTL_SERVICE_CONCURRENCY) and PHOTON_URL can point at a
self-hosted instance.
"""

from __future__ import annotations

import math
import re
import time
from urllib.parse import quote

from .. import config
from ..upstream import UpstreamError, UpstreamRejected
from ..verify import bbox_around, haversine_m
from .base import Ctx, Lookup, ProviderInfo, Skip, cand
from .countries import COUNTRIES
from .match import STREET_OK, best_street_sim, norm_num, norm_postcode, pc_near, street_sim
from .parse import IntlAddress
from .places import district_differs, same_city

INFO = {
    "overture": ProviderInfo("overture", "Overture Maps", "overture", "https://overturemaps.org",
                             "per source (mostly CC BY 4.0 / CC0)", "Overture Maps Foundation, with OpenStreetMap"),
    "osm": ProviderInfo("osm", "OpenStreetMap (Photon)", "osm", "https://photon.komoot.io", "ODbL",
                        "© OpenStreetMap contributors"),
}

# Overture's data for these countries leaves out many shops and offices (Portugal: dwellings only), so a number
# missing there is no evidence against the address
INCOMPLETE = {"PT": "Overture's Portuguese data lists dwellings and misses many shops and offices"}
_EXTENT_MAX_KM = 20  # a long street is searched along its whole length, up to this size


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
    if not any(_street_hit(f, a) and place_ok(f, a) is not False for f in feats):
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
            if isinstance(f, dict) and isinstance(f.get("properties"), dict)
            and str(f["properties"].get("countrycode", "")).upper() == cc
            and len((f.get("geometry") or {}).get("coordinates") or []) >= 2]


def _street_of(f: dict) -> str | None:
    p = f["properties"]
    return p.get("name") if p.get("type") == "street" else p.get("street")


def _street_hit(f: dict, a: IntlAddress) -> bool:
    return f["properties"].get("type") in ("house", "street") and best_street_sim(a.street, _street_of(f), cc=a.country) >= STREET_OK


def _towns(p: dict) -> list[str]:
    # not the county: 'Greater Manchester' would make a Stockport house one in Manchester. But a London borough
    # ('Royal Borough of Kensington and Chelsea', county 'Greater London') is in London - in a London postal district;
    # Croydon (CR), Harrow (HA) or Kingston (KT) are post towns of their own
    towns = [p[k] for k in ("city", "town", "village", "district", "locality") if p.get(k)]
    london = re.match(r"(EC|WC|NW|SE|SW|E|N|W)\d", norm_postcode(p.get("postcode")))
    return towns + ["London"] if p.get("county") == "Greater London" and london and "London" not in towns else towns


def place_ok(f: dict, a: IntlAddress) -> bool | None:
    """Is a Photon feature in the town or postcode the input names? None when that can't be told (the input names
    neither, or only a postcode one typo away)."""
    p = f["properties"]
    pc_ok = norm_postcode(p.get("postcode")) == norm_postcode(a.postcode) if a.postcode and p.get("postcode") else None
    towns = _towns(p)
    city_ok = None
    if a.city and towns:  # the same town, or the start of its longer name (None: 'Frankfurt' / 'Frankfurt am Main')
        same = [same_city(a.city, t, a.country) for t in towns]
        city_ok = True if True in same else None if None in same else False
        if city_ok and district_differs(a.city, p.get("postcode"), a.country):
            city_ok = False  # 'Dublin 2', an Eircode in D04
    if pc_ok or city_ok:
        return True
    if pc_ok is False and city_ok is None and pc_near(a.postcode, p.get("postcode")):
        return None
    return False if pc_ok is False or city_ok is False else None


def _pc_ok(f: dict | None, a: IntlAddress) -> bool:
    """Photon located the street in the input's own postcode."""
    pc = f["properties"].get("postcode") if f else None
    return bool(a.postcode and pc and norm_postcode(pc) == norm_postcode(a.postcode))


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
        towns = _towns(p)
        out.append(cand(kind, number=p.get("housenumber") if kind == "address" else None, street=street,
                        postcode=p.get("postcode"), city=towns[0] if towns else None, city_alt=towns[1:],
                        lat=lat, lon=lon, id=f"{p.get('osm_type')}{p.get('osm_id')}", formatted=formatted,
                        region=p.get("state"), source="OpenStreetMap", license="ODbL",
                        distance_m=round(haversine_m(origin[0], origin[1], lat, lon), 1) if origin else None))
    return out


def locate(feats: list[dict], a: IntlAddress) -> tuple[dict | None, dict | None, list[str]]:
    """Where to search the register: the street (or the house itself) in the input's town, exactly that street name
    first -> (geocode, the Photon feature, towns where only a namesake was found). A city-level hit is too coarse
    to find one house in."""
    def rank(f: dict) -> tuple:
        p = f["properties"]
        sim = best_street_sim(a.street, _street_of(f), cc=a.country)
        exact_num = p.get("type") == "house" and norm_num(p.get("housenumber")) == norm_num(a.number_base)
        return (place_ok(f, a) is not True, sim < 0.999, not exact_num, -sim)
    streets = [f for f in feats if _street_hit(f, a)]
    elsewhere = list(dict.fromkeys(" ".join(x for x in (f["properties"].get("postcode"), (_towns(f["properties"]) or [""])[0]) if x)
                                   for f in streets if place_ok(f, a) is False))
    hits = sorted((f for f in streets if place_ok(f, a) is not False), key=rank)
    if not hits:
        return None, None, elsewhere
    f = hits[0]
    lon, lat = f["geometry"]["coordinates"][:2]
    p = f["properties"]
    label = ", ".join(x for x in (" ".join(x for x in (_street_of(f), p.get("housenumber")) if x),
                                   " ".join(x for x in (p.get("postcode"), p.get("city")) if x)) if x)
    return {"source": "photon", "lat": lat, "lon": lon, "matched_address": label, "level": p.get("type")}, f, elsewhere


def _not_located(elsewhere: list[str], timing: dict) -> Lookup:
    notes = ["OpenStreetMap search could not find this street; check the street name and city"]
    if elsewhere:
        notes = [f"OpenStreetMap has this street only in other places ({'; '.join(elsewhere[:5])}), not in the town or "
                 "postcode given"]
    return Lookup(status="geocode_failed", timing=timing, notes=notes)


# ---------------------------------------------------------------------------------------- Overture

async def overture(ctx: Ctx, a: IntlAddress) -> Lookup:
    if ctx.ov is None:
        raise UpstreamError("overture", "Overture is disabled (OVERTURE_ENABLED=0)")
    t = time.perf_counter()
    feats = await photon(ctx, a)
    timing = {"geocode": round((time.perf_counter() - t) * 1000, 1)}
    geo, f, elsewhere = locate(feats, a)
    if geo is None:
        return _not_located(elsewhere, timing)
    p = f["properties"]
    if a.country == "DE" and str(p.get("state", "")).lower() in ("bayern", "bavaria"):
        raise Skip("Bavaria's addresses are not in Overture")
    origin = (geo["lat"], geo["lon"])
    m = re.match(r"\d+", a.number_base)
    any_part = a.country in ("SK", "CZ")  # "553/25": either number may be written
    # for rows without them: the located place's town, and the postcode only of the located house itself (a street
    # has several: Photon gives Via del Corso segments 00187 where number 336 is 00186)
    house = p.get("type") == "house" and norm_num(p.get("housenumber")) == norm_num(a.number_base)
    area = {"area_postcode": p.get("postcode") if house else None, "area_city": (_towns(p) or [None])[0],
            "pc_ok": _pc_ok(f, a)}
    bilingual = list(dict.fromkeys(n for x in feats if (n := _street_of(x)) and re.search(r"\s[-/]\s", n)))
    t = time.perf_counter()
    recs = await ctx.ov.query(_street_box(feats, a, origin), number=int(m.group()) if m else None, any_part=any_part)
    cands = [x for r in recs for x in _number_parts(overture_cand(r, origin, a.country, area, bilingual), a.country)]
    near: list[dict] | None = None  # the rows around the located point
    street = _street_area(feats, a, origin)
    if any(_namesake(c, a, street) for c in cands):  # Köln: is it the located street, or one in another district?
        near = await _nearby(ctx, origin, a, area, bilingual)
        _other_districts(cands, a, street, near)
    # the street as OpenStreetMap names it in this town: it is there even where Overture has no such number
    cands += [c for c in photon_cands([x for x in feats if x["properties"].get("type") == "street"
                                       and place_ok(x, a) is not False], origin) if c["kind"] == "street"]
    lk = Lookup(cands=cands, geocode=geo, timing=timing)
    if a.country in INCOMPLETE:
        lk.incomplete = INCOMPLETE[a.country]
    if not any(c["kind"] == "address" and c["number"] and norm_num(c["number"]).startswith(norm_num(a.number_base))
               and best_street_sim(a.street, c["street"], *c["street_alt"], cc=a.country) >= 0.999 for c in cands):
        # the number is not there on exactly this street: is the street (or anything) in the register around here?
        if near is None:
            near = await _nearby(ctx, origin, a, area, bilingual)
        seen = {c["uuid"] for c in cands}
        lk.cands += [c for c in near if c["uuid"] not in seen]
        lk.area_empty = not near
    for c in lk.cands:  # on the located street: an exact-name row there is the input's street, whatever its town
        c["on_street"] = (c["kind"] == "address" and c["lat"] is not None
                          and street["xmin"] <= c["lon"] <= street["xmax"] and street["ymin"] <= c["lat"] <= street["ymax"])
    lk.timing["overture"] = round((time.perf_counter() - t) * 1000, 1)
    return lk


async def _nearby(ctx: Ctx, origin: tuple[float, float], a: IntlAddress, area: dict, bilingual: list[str]) -> list[dict]:
    rows = await ctx.ov.query(bbox_around(*origin, 300), limit=500)  # the nearest 500 are plenty (and cached)
    return [x for r in rows for x in _number_parts(overture_cand(r, origin, a.country, area, bilingual), a.country)]


def _street_extents(feats: list[dict], a: IntlAddress):
    """The input's street as Photon has it in the input's town: the extents of its OSM ways [W, N, E, S]."""
    for f in feats:
        ext = f["properties"].get("extent")
        if (f["properties"].get("type") == "street" and isinstance(ext, list) and len(ext) == 4
                and best_street_sim(a.street, _street_of(f), cc=a.country) >= 0.999 and place_ok(f, a) is not False):
            yield ext


def _street_box(feats: list[dict], a: IntlAddress, origin: tuple[float, float]) -> dict:
    """1.5 km around the located point, widened to the whole street when it is longer (Via Appia Nuova runs for
    20 km: a number at its far end would be missed)."""
    box = bbox_around(*origin, config.OVERTURE_SEARCH_RADIUS_M)
    for ext in _street_extents(feats, a):
        box = {"xmin": min(box["xmin"], ext[0]), "ymax": max(box["ymax"], ext[1]),
               "xmax": max(box["xmax"], ext[2]), "ymin": min(box["ymin"], ext[3])}
    # never more than _EXTENT_MAX_KM across, centred on the located point
    half_lat = _EXTENT_MAX_KM / 2 / 111.0
    half_lon = half_lat / max(math.cos(math.radians(origin[0])), 0.1)
    return {"xmin": max(box["xmin"], origin[1] - half_lon), "xmax": min(box["xmax"], origin[1] + half_lon),
            "ymin": max(box["ymin"], origin[0] - half_lat), "ymax": min(box["ymax"], origin[0] + half_lat)}


def _street_area(feats: list[dict], a: IntlAddress, origin: tuple[float, float], pad_m: float = 200) -> dict:
    """The located point and the street's extents, 200 m around."""
    xs, ys = [origin[1]], [origin[0]]
    for ext in _street_extents(feats, a):
        xs, ys = xs + [ext[0], ext[2]], ys + [ext[1], ext[3]]
    pad = bbox_around(*origin, pad_m)
    dlon, dlat = pad["xmax"] - origin[1], pad["ymax"] - origin[0]
    return {"xmin": min(xs) - dlon, "xmax": max(xs) + dlon, "ymin": min(ys) - dlat, "ymax": max(ys) + dlat}


def _namesake(c: dict, a: IntlAddress, street: dict) -> bool:
    """A district-coded row of the input's street name away from the located street."""
    return (bool(c.get("district")) and c["lat"] is not None and best_street_sim(a.street, c["street"], cc=a.country) >= 0.999
            and not (street["xmin"] <= c["lon"] <= street["xmax"] and street["ymin"] <= c["lat"] <= street["ymax"]))


def _other_districts(cands: list[dict], a: IntlAddress, street: dict, near: list[dict]) -> None:
    """Köln's codes tell its same-named streets apart ('Mainstr. Wv' in Westhoven, 'Mainstr. Ro' in Rodenkirchen, 2 km
    away). A coded row away from the located street is that street only when the street's rows around the located
    point carry its code; otherwise it is another street: shown under its full name, never compared as the input's."""
    here = {c["district"] for c in near if c["street"] and c.get("district") is not None
            and c["distance_m"] is not None and c["distance_m"] <= 300 and best_street_sim(a.street, c["street"], cc=a.country) >= 0.999}
    for c in cands:
        if _namesake(c, a, street) and c["district"] not in here:
            c["formatted"] = c["formatted"].replace(c["street"], f"{c['street']} {c['district']}", 1)
            c["street"] = None


_DE_DISTRICT_RE = re.compile(r"\s+[A-ZÄÖÜ][a-zäöü]?$")  # Köln's "Paulstr. Wv": the district after the name


def _district(street: str, country: str) -> tuple[str, str | None]:
    """'Mainstr. Ro' -> ('Mainstr.', 'Ro'); ('Paulstraße', '') without a code; (street, None) outside Germany. The
    letter of Köln's allotment paths ('Kalscheurer Weg,Weg S') names the path, not a district."""
    if country != "DE":
        return street, None
    m = _DE_DISTRICT_RE.search(street)
    if m and "," not in street and re.search(r"(?i)str\.|stra(ss|ß)e|weg|platz|gasse|allee|ring", street):
        return street[: m.start()], m.group().strip()
    return street, ""


def overture_cand(r: dict, origin: tuple[float, float], country: str = "", area: dict | None = None,
                  bilingual: list[str] | None = None) -> dict:
    d = round(haversine_m(origin[0], origin[1], r["lat"], r["lon"]), 1) if r.get("lat") is not None else None
    street, district = _district(r.get("street") or "", country)
    c = cand("address", number=r.get("address_number"), street=street or None, postcode=r.get("postcode"),
             city=r.get("city"), unit=(r.get("subaddress") or {}).get("unit"), lat=r.get("lat"), lon=r.get("lon"),
             id=r.get("uuid"), region=r.get("state"), source=r.get("source"), license=r.get("license"),
             updated=r.get("updated"), distance_m=d)
    c["district"] = district
    # Brussels: Overture has the French name only, the input may be in Dutch ("Rue Neuve - Nieuwstraat" in OSM)
    c["street_alt"] += [b for b in bilingual or [] if street and any(street_sim(street, part) >= 0.999
                                                                     for part in re.split(r"\s+[-/]\s+", b))]
    # Overture's place level differs by country (Berlin records say "Mitte"): a different name is not a mismatch
    c["city_loose"] = True
    if area:
        # a row without a postcode: the located house's, right next to it. The town Photon located the street in, near
        # it: it stands for a row without a town, and on a row with its own (often a district) it can only show that
        # the input's town is another (match.score)
        c["area_postcode"] = area.get("area_postcode") if not c["postcode"] and d is not None and d <= 100 else None
        c["area_city"] = area.get("area_city") if d is not None and d <= 300 else None
        c["area_pc_ok"] = area.get("pc_ok")
    return c


def _number_parts(c: dict, country: str) -> list[dict]:
    """A Slovak / Czech "553/25" (building / street number) row under each way of writing it, as cz_cands does."""
    if country == "CZ" and re.fullmatch(r"\d+", c["number"] or "") and re.fullmatch(r"\d+[a-zA-Z]?", c["unit"] or ""):
        c = {**c, "number": f"{c['number']}/{c['unit']}", "unit": None}  # ČÚZK's rows: orientation number as 'unit'
    m = re.fullmatch(r"(\d+)/(\d+[a-zA-Z]?)", c["number"] or "") if country in ("SK", "CZ") else None
    return [{**c, "number": n} for n in (m.group(2), m.group(1), c["number"])] if m else [c]


# --------------------------------------------------------------------------------------- OSM only

async def osm(ctx: Ctx, a: IntlAddress) -> Lookup:
    notes: list[str] = []
    bias = None
    t = time.perf_counter()
    if a.country == "GB" and a.postcode:
        bias = await uk_postcode(ctx, a, notes)
    feats = await photon(ctx, a, bias=bias)
    geo, f, elsewhere = locate(feats, a)
    origin = (geo["lat"], geo["lon"]) if geo else bias
    cands = photon_cands(feats, origin, COUNTRIES[a.country].number_first)
    for c in cands:  # distances are from a point in the input's postcode (match.score's same_area)
        c["area_pc_ok"] = _pc_ok(f, a) if geo else bool(bias)  # or from the postcode itself (postcodes.io)
    lk = Lookup(cands=cands, geocode=geo, notes=notes,
                timing={"geocode": round((time.perf_counter() - t) * 1000, 1)},
                credit=UK_POSTCODE_CREDIT if bias else None)  # the postcode's position was used
    if any(interpolated(f["properties"]) and norm_num(f["properties"].get("housenumber")) == norm_num(a.number_base)
           for f in feats):
        lk.notes.append("OpenStreetMap only has an interpolated number range on this stretch of the street (the "
                        "numbers exist along it, but each house is not mapped), so the exact number can't be confirmed")
    if not feats:
        lk.status = "geocode_failed"
        lk.notes.append("OpenStreetMap search found nothing for this address")
    elif geo is None and elsewhere:
        lk.notes.append(f"OpenStreetMap has this street only in other places ({'; '.join(elsewhere[:5])})")
    return lk


_UK_PC_RE = re.compile(r"[A-Z]{1,2}\d[A-Z\d]?\d[A-Z]{2}")
# what the ONS Postcode Directory behind postcodes.io asks to be shown with its data
UK_POSTCODE_CREDIT = ("postcodes.io: Contains OS data © Crown copyright and database right; Contains Royal Mail data "
                      "© Royal Mail copyright and database right; Source: Office for National Statistics licensed under "
                      "the Open Government Licence v.3.0")


async def uk_postcode(ctx: Ctx, a: IntlAddress, notes: list[str]) -> tuple[float, float] | None:
    """postcodes.io (ONS / Royal Mail open data): does the postcode exist, and where is it."""
    pc = re.sub(r"\s", "", a.postcode).upper()
    if not _UK_PC_RE.fullmatch(pc):  # a partial or malformed postcode: nothing to look up
        return None
    try:
        data = await ctx.get("postcodes_io", f"{config.POSTCODES_IO_URL}/postcodes/{quote(pc)}", {})
    except UpstreamRejected:  # 404: no such postcode
        notes.append(f"postcode {a.postcode} does not exist (postcodes.io)")
        return None
    except UpstreamError as exc:
        notes.append(f"postcode check unavailable: {exc.message}")
        return None
    r = (data or {}).get("result") or {}
    if not isinstance(r, dict) or r.get("latitude") is None:
        return None
    return r["latitude"], r["longitude"]
