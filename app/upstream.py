"""Async clients for the free upstream services (NAD on ArcGIS Online, Census geocoder, TIGERweb)."""

from __future__ import annotations

import asyncio
import json
import time
from collections import OrderedDict
from datetime import datetime, timezone
from typing import Any

import httpx

from . import config

# Fields pulled from the NAD layer (never request '*': the full schema is ~60 columns).
NAD_OUT_FIELDS = [
    "UUID", "AddNum_Pre", "Add_Number", "AddNum_Suf", "AddNo_Full",
    "St_PreMod", "St_PreDir", "St_PreTyp", "St_PreSep", "St_Name", "St_PosTyp", "St_PosDir",
    "St_PosMod", "StNam_Full", "Building", "Floor", "Unit", "Room", "SubAddress", "LandmkName",
    "County", "Inc_Muni", "Post_City", "Uninc_Comm", "State", "Zip_Code", "Plus_4",
    "Longitude", "Latitude", "Placement", "AddrClass", "Addr_Type", "Lifecycle", "DeliverTyp",
    "DateUpdate", "AddAuth", "NAD_Source",
]

COUNTY_STATUS_LABELS = {
    10: "Data in the NAD",
    15: "Data in progress",
    20: "Awaiting data from partner",
    30: "No statewide data",
    40: "Data not in public domain",
}


class UpstreamError(Exception):
    def __init__(self, service: str, message: str):
        super().__init__(f"{service}: {message}")
        self.service = service
        self.message = message


class UpstreamRejected(UpstreamError):
    """The upstream answered but refused the request (HTTP 4xx): the input is the problem, not the service."""


def _describe(exc: BaseException | None) -> str:
    """Name a transport failure. httpx's async ConnectError is empty when the peer resets the connection during
    the TLS handshake (typical of a firewall in the path), so add the innermost exception, which has the OS error."""
    if exc is None:
        return "unknown error"
    root = exc
    for _ in range(10):
        nxt = root.__cause__ or root.__context__
        if nxt is None:
            break
        root = nxt
    text = str(exc) or type(exc).__name__
    return text if root is exc or str(root) in text else f"{text} ({type(root).__name__}: {root})"


class TTLCache:
    def __init__(self, ttl_s: int, max_entries: int):
        self.ttl_s, self.max_entries = ttl_s, max_entries
        self._data: OrderedDict[str, tuple[float, Any]] = OrderedDict()

    def get(self, key: str) -> Any | None:
        item = self._data.get(key)
        if item is None:
            return None
        expires, value = item
        if expires < time.monotonic():
            self._data.pop(key, None)
            return None
        self._data.move_to_end(key)
        return value

    def set(self, key: str, value: Any) -> None:
        if self.ttl_s <= 0:
            return
        self._data[key] = (time.monotonic() + self.ttl_s, value)
        self._data.move_to_end(key)
        while len(self._data) > self.max_entries:
            self._data.popitem(last=False)

    def pop(self, key: str) -> None:
        self._data.pop(key, None)

    def __len__(self) -> int:
        return len(self._data)


class Upstream:
    def __init__(self) -> None:
        self.client = httpx.AsyncClient(
            timeout=httpx.Timeout(config.HTTP_TIMEOUT, connect=10.0),
            headers={"User-Agent": config.USER_AGENT, "Accept": "application/json"},
            limits=httpx.Limits(max_connections=40, max_keepalive_connections=20),
            follow_redirects=True,
        )
        self.cache = TTLCache(config.CACHE_TTL_S, config.CACHE_MAX_ENTRIES)
        self._inflight: dict[str, asyncio.Task] = {}
        # service -> outcome of its latest real (uncached) call, so /health can say which upstream is failing
        self.status: dict[str, dict[str, Any]] = {}

    async def aclose(self) -> None:
        await self.client.aclose()

    @staticmethod
    def _key(url: str, params: dict[str, Any]) -> str:
        return url + "?" + json.dumps(params, sort_keys=True, default=str)

    def forget(self, url: str, params: dict[str, Any]) -> None:
        """Drop a cached answer that turned out to be unusable."""
        self.cache.pop(self._key(url, params))

    async def _get_json(self, service: str, url: str, params: dict[str, Any], *, cache: bool = True) -> dict:
        """`cache=False` always asks the upstream (health checks); the answer is still cached for others."""
        key = self._key(url, params)
        if cache:
            cached = self.cache.get(key)
            if cached is not None:
                return cached
        # Identical requests already on their way (the same address twice in a batch, the same county for
        # neighbours) wait for that one call instead of making their own.
        task = self._inflight.get(key)
        if task is None:
            task = asyncio.create_task(self._fetch(service, url, params, key))
            self._inflight[key] = task
            task.add_done_callback(lambda t: (self._inflight.pop(key, None), t.cancelled() or t.exception()))
        return await asyncio.shield(task)  # a caller that gives up doesn't cancel the call for the others

    async def _fetch(self, service: str, url: str, params: dict[str, Any], key: str) -> dict:
        try:
            data = await self._request(service, url, params)
        except UpstreamRejected:
            self.status[service] = {"ok": True, "at": time.time(), "error": None}  # it answered
            raise
        except UpstreamError as exc:
            self.status[service] = {"ok": False, "at": time.time(), "error": exc.message}
            raise
        self.status[service] = {"ok": True, "at": time.time(), "error": None}
        self.cache.set(key, data)
        return data

    async def _request(self, service: str, url: str, params: dict[str, Any]) -> dict:
        last_exc: Exception | None = None
        for attempt in range(2):
            try:
                resp = await self.client.get(url, params=params)
            except httpx.TimeoutException as exc:
                raise UpstreamError(service, f"timed out after {config.HTTP_TIMEOUT:.0f}s") from exc
            except httpx.TransportError as exc:  # connection reset etc. -> one retry
                last_exc = exc
                await asyncio.sleep(0.3)
                continue
            if resp.status_code >= 500 and attempt == 0:
                await asyncio.sleep(0.3)
                continue
            if 400 <= resp.status_code < 500 and resp.status_code != 429:
                try:  # Census: {"errors": ["Specify House number and Street name along with City and State ..."]}
                    errors = resp.json().get("errors") or []
                except (ValueError, AttributeError):
                    errors = []
                raise UpstreamRejected(service, f"HTTP {resp.status_code}" + (f": {'; '.join(map(str, errors))}" if errors else ""))
            if resp.status_code != 200:
                raise UpstreamError(service, f"HTTP {resp.status_code}")
            try:
                data = resp.json()
            except ValueError as exc:
                raise UpstreamError(service, "non-JSON response") from exc
            # ArcGIS reports failures as HTTP 200 + {"error": {...}}.
            if isinstance(data, dict) and isinstance(data.get("error"), dict):
                err = data["error"]
                detail = "; ".join(err.get("details") or []) or err.get("message") or "unknown error"
                raise UpstreamError(service, f"ArcGIS error {err.get('code')}: {detail}")
            return data
        raise UpstreamError(service, f"connection failed: {_describe(last_exc)}")

    async def ping(self) -> dict[str, str | None]:
        """One uncached call to each upstream except NAD (see nad_count) -> {service: None if it answered, else the error}."""
        checks = {
            "census_geocoder": (f"{config.CENSUS_GEOCODER_URL}/benchmarks", {"format": "json"}),
            "nad_coverage": (config.COVERAGE_LAYER_URL, {"f": "json"}),
            "tigerweb": (config.ZCTA_LAYER_URL, {"f": "json"}),
        }

        async def one(service: str, url: str, params: dict) -> str | None:
            try:
                await self._get_json(service, url, params, cache=False)
            except UpstreamError as exc:
                return exc.message
            return None

        errors = await asyncio.gather(*(one(s, u, p) for s, (u, p) in checks.items()))
        return dict(zip(checks, errors))

    # ------------------------------------------------------------------ Census geocoder

    async def census_geocode_oneline(self, address: str) -> dict | None:
        data = await self._get_json(
            "census_geocoder",
            f"{config.CENSUS_GEOCODER_URL}/geographies/onelineaddress",
            {
                "address": address, "benchmark": config.CENSUS_BENCHMARK,
                "vintage": config.CENSUS_VINTAGE, "layers": "Counties", "format": "json",
            },
        )
        return _first_census_match(data)

    async def census_geocode_structured(self, street: str, city: str, state: str, zip_code: str) -> dict | None:
        params = {
            "street": street, "benchmark": config.CENSUS_BENCHMARK,
            "vintage": config.CENSUS_VINTAGE, "layers": "Counties", "format": "json",
        }
        if city:
            params["city"] = city
        if state:
            params["state"] = state
        if zip_code:
            params["zip"] = zip_code
        data = await self._get_json("census_geocoder", f"{config.CENSUS_GEOCODER_URL}/geographies/address", params)
        return _first_census_match(data)

    async def census_county_at(self, lat: float, lon: float) -> dict | None:
        data = await self._get_json(
            "census_geocoder",
            f"{config.CENSUS_GEOCODER_URL}/geographies/coordinates",
            {
                "x": round(lon, 6), "y": round(lat, 6), "benchmark": config.CENSUS_BENCHMARK,
                "vintage": config.CENSUS_VINTAGE, "layers": "Counties", "format": "json",
            },
        )
        counties = (data.get("result") or {}).get("geographies", {}).get("Counties") or []
        return counties[0] if counties else None

    # ------------------------------------------------------------------ NAD address points

    async def nad_near(
        self,
        lat: float,
        lon: float,
        radius_m: float,
        where: str = "1=1",
        limit: int = 2000,
    ) -> tuple[list[dict], bool]:
        """Address points within radius_m of (lat, lon). Returns (records, exceeded_transfer_limit)."""
        data = await self._get_json(
            "nad",
            f"{config.NAD_LAYER_URL}/query",
            {
                "geometry": f"{lon:.6f},{lat:.6f}",
                "geometryType": "esriGeometryPoint",
                "inSR": 4326,
                "spatialRel": "esriSpatialRelIntersects",
                "distance": int(radius_m),
                "units": "esriSRUnit_Meter",
                "where": where,
                "outFields": ",".join(NAD_OUT_FIELDS),
                "returnGeometry": "false",
                "resultRecordCount": max(1, min(limit, 2000)),
                "f": "json",
            },
        )
        feats = data.get("features") or []
        return [nad_record(f.get("attributes") or {}) for f in feats], bool(data.get("exceededTransferLimit"))

    async def nad_in_envelope(self, extent: dict, where: str, limit: int = 200) -> list[dict]:
        data = await self._get_json(
            "nad",
            f"{config.NAD_LAYER_URL}/query",
            {
                "geometry": json.dumps({**extent, "spatialReference": {"wkid": 4326}}),
                "geometryType": "esriGeometryEnvelope",
                "inSR": 4326,
                "spatialRel": "esriSpatialRelIntersects",
                "where": where,
                "outFields": ",".join(NAD_OUT_FIELDS),
                "returnGeometry": "false",
                "resultRecordCount": limit,
                "f": "json",
            },
        )
        return [nad_record(f.get("attributes") or {}) for f in data.get("features") or []]

    async def nad_count(self) -> int:
        data = await self._get_json(
            "nad", f"{config.NAD_LAYER_URL}/query", {"where": "1=1", "returnCountOnly": "true", "f": "json"}, cache=False,
        )
        return int(data.get("count", 0))

    # ------------------------------------------------------------------ Coverage

    async def county_status_by_name(self, county: str, state: str) -> dict | None:
        """Lookup by county name. The layer mixes whole-county polygons with smaller partner
        polygons (e.g. Costa Mesa inside Orange County), so the largest polygon is reported as
        the county status and `partial` flags counties whose sub-areas differ."""
        county, state = county.replace("'", "''"), state.replace("'", "''").upper()
        data = await self._get_json(
            "nad_coverage",
            f"{config.COVERAGE_LAYER_URL}/query",
            {
                "where": f"STATE_ABBR='{state}' AND UPPER(NAME)=UPPER('{county}')",
                "outFields": _COVERAGE_FIELDS,
                "returnGeometry": "false",
                "f": "json",
            },
        )
        rows = [f["attributes"] for f in data.get("features") or []]
        if not rows:
            return None
        rows.sort(key=lambda a: a.get("Shape__Area") or 0, reverse=True)
        cov = _coverage(rows[0], "county_name")
        cov["partial"] = len({r.get("CountyStatus") for r in rows}) > 1
        return cov

    async def county_status_at(self, lat: float, lon: float) -> dict | None:
        """Point-in-polygon lookup; the smallest containing polygon is the most specific."""
        data = await self._get_json(
            "nad_coverage",
            f"{config.COVERAGE_LAYER_URL}/query",
            {
                "geometry": f"{lon:.6f},{lat:.6f}",
                "geometryType": "esriGeometryPoint",
                "inSR": 4326,
                "spatialRel": "esriSpatialRelIntersects",
                "outFields": _COVERAGE_FIELDS,
                "returnGeometry": "false",
                "f": "json",
            },
        )
        rows = [f["attributes"] for f in data.get("features") or []]
        if not rows:
            return None
        rows.sort(key=lambda a: a.get("Shape__Area") or 0)
        cov = _coverage(rows[0], "point")
        cov["partial"] = len({r.get("CountyStatus") for r in rows}) > 1
        return cov

    async def resolve_coverage(self, lat: float | None, lon: float | None,
                               county: str | None = None, state: str | None = None) -> dict | None:
        """Point lookup first; fall back to county name (the point query misses some counties,
        e.g. New York County), resolving the county via the Census geocoder when needed."""
        if lat is not None and lon is not None:
            cov = await self.county_status_at(lat, lon)
            if cov:
                return cov
            if not (county and state):
                c = await self.census_county_at(lat, lon)
                if c:
                    county = c.get("BASENAME")
                    state = state or STATE_FIPS.get(str(c.get("STATE")))
        if county and state:
            return await self.county_status_by_name(county, state)
        return None

    # ------------------------------------------------------------------ ZIP (ZCTA) extent

    async def zcta_extent(self, zip5: str) -> dict | None:
        if not (zip5.isdigit() and len(zip5) == 5):
            return None
        data = await self._get_json(
            "tigerweb",
            f"{config.ZCTA_LAYER_URL}/query",
            {"where": f"ZCTA5='{zip5}'", "returnExtentOnly": "true", "outSR": 4326, "f": "json"},
        )
        ext = data.get("extent") or {}
        if not ext or ext.get("xmin") in (None, "NaN"):
            return None
        return {k: ext[k] for k in ("xmin", "ymin", "xmax", "ymax")}


# ---------------------------------------------------------------------- helpers


def _first_census_match(data: dict) -> dict | None:
    matches = (data.get("result") or {}).get("addressMatches") or []
    if not matches:
        return None
    m = matches[0]
    county = ((m.get("geographies") or {}).get("Counties") or [{}])[0]
    comps = m.get("addressComponents") or {}
    # "100 Main St, Springfield" matches seven Springfields; the other matches let the caller pick
    alternatives = [{"matched_address": x.get("matchedAddress"), "lat": x["coordinates"]["y"], "lon": x["coordinates"]["x"]}
                    for x in matches[1:6] if x.get("coordinates")]
    return {
        "matched_address": m.get("matchedAddress"),
        "lat": m["coordinates"]["y"],
        "lon": m["coordinates"]["x"],
        "components": comps,
        "county": county.get("BASENAME"),
        "county_fips": county.get("GEOID"),
        "state": comps.get("state"),
        "zip": comps.get("zip"),
        "tiger_line_id": (m.get("tigerLine") or {}).get("tigerLineId"),
        "match_count": len(matches),
        **({"alternatives": alternatives} if alternatives else {}),
    }


_COVERAGE_FIELDS = "NAME,STATE_NAME,STATE_ABBR,CountyStatus,DataDate,Shape__Area"


def _coverage(attrs: dict, method: str) -> dict:
    code = attrs.get("CountyStatus")
    return {
        "method": method,
        "county": attrs.get("NAME"),
        "state": attrs.get("STATE_ABBR"),
        "state_name": attrs.get("STATE_NAME"),
        "status_code": code,
        "status": COUNTY_STATUS_LABELS.get(code, "Unknown"),
        "in_nad": code == 10,
        "data_date": attrs.get("DataDate"),
    }


def _ms_to_date(value: Any) -> str | None:
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value / 1000, tz=timezone.utc).date().isoformat()
    return None


def _s(value: Any) -> str | None:
    if value is None:
        return None
    value = str(value).strip()
    return value or None


_NOT_A_PLACE = {"UNINCORPORATED", "NOT STATED", "UNKNOWN", "NONE", "N/A"}


def _place(value: Any) -> str | None:
    value = _s(value)
    return None if value is None or value.upper() in _NOT_A_PLACE else value


def nad_record(a: dict) -> dict:
    """Flatten an NAD feature into the API's record shape."""
    zip_code = _s(a.get("Zip_Code"))
    plus4 = _s(a.get("Plus_4"))
    number = _s(a.get("AddNo_Full")) or (str(a["Add_Number"]) if a.get("Add_Number") is not None else None)
    street = _s(a.get("StNam_Full"))
    sub = {
        "building": _s(a.get("Building")), "floor": _s(a.get("Floor")), "unit": _s(a.get("Unit")),
        "room": _s(a.get("Room")), "subaddress": _s(a.get("SubAddress")),
    }
    # Some partners put village / station names in Post_City (Phoenix: "Capitol", "Rio Salado"),
    # so the incorporated municipality is preferred for display.
    city = _place(a.get("Inc_Muni")) or _place(a.get("Post_City")) or _place(a.get("Uninc_Comm"))
    sub_text = " ".join(v for v in (sub["building"], sub["floor"], sub["unit"], sub["room"]) if v) or sub["subaddress"]
    line1 = " ".join(p for p in (number, street) if p)
    formatted = ", ".join(p for p in (line1, sub_text, city, " ".join(p for p in (_s(a.get("State")), zip_code) if p)) if p)
    return {
        "uuid": _s(a.get("UUID")),
        "formatted": formatted,
        "address_number": number,
        "address_number_int": a.get("Add_Number"),
        "address_number_prefix": _s(a.get("AddNum_Pre")),
        "address_number_suffix": _s(a.get("AddNum_Suf")),
        "street": street,
        "street_parts": {
            "pre_modifier": _s(a.get("St_PreMod")), "pre_directional": _s(a.get("St_PreDir")),
            "pre_type": _s(a.get("St_PreTyp")), "pre_separator": _s(a.get("St_PreSep")),
            "name": _s(a.get("St_Name")), "post_type": _s(a.get("St_PosTyp")),
            "post_directional": _s(a.get("St_PosDir")), "post_modifier": _s(a.get("St_PosMod")),
        },
        "subaddress": sub,
        "landmark": _s(a.get("LandmkName")),
        "city": city,
        "postal_city": _place(a.get("Post_City")),
        "municipality": _place(a.get("Inc_Muni")),
        "county": _s(a.get("County")),
        "state": _s(a.get("State")),
        "zip": zip_code,
        "zip4": plus4,
        "lat": a.get("Latitude"),
        "lon": a.get("Longitude"),
        "placement": _s(a.get("Placement")),
        "address_class": _s(a.get("AddrClass")),
        "address_type": _s(a.get("Addr_Type")),
        "lifecycle": _s(a.get("Lifecycle")),
        "delivery_type": _s(a.get("DeliverTyp")),
        "updated": _ms_to_date(a.get("DateUpdate")),
        "address_authority": _s(a.get("AddAuth")),
        "source": _s(a.get("NAD_Source")),
    }


STATE_FIPS = {
    "01": "AL", "02": "AK", "04": "AZ", "05": "AR", "06": "CA", "08": "CO", "09": "CT", "10": "DE",
    "11": "DC", "12": "FL", "13": "GA", "15": "HI", "16": "ID", "17": "IL", "18": "IN", "19": "IA",
    "20": "KS", "21": "KY", "22": "LA", "23": "ME", "24": "MD", "25": "MA", "26": "MI", "27": "MN",
    "28": "MS", "29": "MO", "30": "MT", "31": "NE", "32": "NV", "33": "NH", "34": "NJ", "35": "NM",
    "36": "NY", "37": "NC", "38": "ND", "39": "OH", "40": "OK", "41": "OR", "42": "PA", "44": "RI",
    "45": "SC", "46": "SD", "47": "TN", "48": "TX", "49": "UT", "50": "VT", "51": "VA", "53": "WA",
    "54": "WV", "55": "WI", "56": "WY", "72": "PR", "78": "VI", "66": "GU", "60": "AS", "69": "MP",
}
