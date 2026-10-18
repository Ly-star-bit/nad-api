"""FastAPI service exposing free US address verification backed by the USDOT National Address Database."""

from __future__ import annotations

import asyncio
import time
from collections import Counter, defaultdict, deque
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import config
from .address import fix_zip, norm_unit, parse_oneline, parse_structured, prep, state_abbr
from .intl import COUNTRIES, Ctx, JapanPost, country_in_text, country_name, known_country, supported, verify_intl
from .intl.countries import (
    crown_dependency, default_wins, postcode_in, structured_country, us_state_tail,
)
from .intl import INFO as INTL_INFO
from .intl.core import PROBES as INTL_PROBES
from .models import (
    BatchItem, BatchRequest, BatchResponse, CountryInfo, CoverageResponse, NearResponse, OvertureNearResponse,
    VerifyRequest, VerifyResponse,
)
from .overture import OvertureClient
from .upstream import Upstream, UpstreamError
from .verify import bbox_around, haversine_m, verify_address


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.upstream = Upstream()
    app.state.overture = None
    tasks = []
    if config.OVERTURE_ENABLED:
        app.state.overture = OvertureClient(app.state.upstream.client)
        tasks.append(asyncio.create_task(app.state.overture.warmup()))  # background; never blocks startup
    app.state.japanpost = JapanPost(app.state.upstream.client, Path(config.DATA_DIR))
    tasks.append(asyncio.create_task(app.state.japanpost.run()))  # loads, refreshes monthly, retries with back-off
    yield
    for t in tasks:
        if not t.done():
            t.cancel()
    await app.state.upstream.aclose()


app = FastAPI(
    title="NAD Address Verification API",
    version="1.0.0",
    description=(
        "Free US physical-address verification built on the USDOT **National Address Database** "
        "(~98M authoritative address points, public domain) plus the US Census geocoder.\n\n"
        "Pipeline: Census geocode → NAD spatial query (radius + house number) → local street/unit/ZIP "
        "matching → county coverage check → **Overture Maps fallback** (NAD + OpenAddresses county/city "
        "data on public S3, read with DuckDB) when NAD cannot confirm the address.\n\n"
        "NAD confirms that an address physically exists; it does **not** confirm USPS deliverability.\n\n"
        "**Europe and Japan** (`country`, or a country name at the end of the address): each country's official "
        "address register where it has a free API (France BAN, Netherlands BAG, ...), the official registers that "
        "Overture Maps republishes (Germany, Italy, ...), OpenStreetMap elsewhere, and 国土地理院 for Japan. "
        "See `/countries`."
    ),
    lifespan=lifespan,
)
_hits: dict[str, deque[float]] = defaultdict(deque)


@app.middleware("http")
async def guard(request: Request, call_next):
    if request.url.path.startswith("/v1/"):
        if config.API_KEYS and request.headers.get("x-api-key") not in config.API_KEYS:
            return JSONResponse({"detail": "missing or invalid X-API-Key"}, status_code=401)
        if config.RATE_LIMIT_PER_MIN > 0:
            client = request.client.host if request.client else "?"
            if config.TRUST_PROXY:
                client = request.headers.get("x-forwarded-for", "").split(",")[0].strip() or client
            now = time.monotonic()
            if len(_hits) > 10_000:  # drop idle clients so the table can't grow without bound
                for ip in [ip for ip, w in _hits.items() if not w or w[-1] < now - 60]:
                    del _hits[ip]
            window = _hits[client]
            while window and window[0] < now - 60:
                window.popleft()
            if len(window) >= config.RATE_LIMIT_PER_MIN:
                return JSONResponse(
                    {"detail": f"rate limit exceeded ({config.RATE_LIMIT_PER_MIN}/min)"},
                    status_code=429, headers={"Retry-After": "60"},
                )
            window.append(now)
    return await call_next(request)


# Added last = outermost: compression covers every response, and CORS headers reach the 401 / 429 from guard too.
app.add_middleware(GZipMiddleware, minimum_size=1000, compresslevel=6)  # a batch of 10 results: 48 KB -> 6 KB
app.add_middleware(CORSMiddleware, allow_origins=config.CORS_ORIGINS, allow_methods=["*"], allow_headers=["*"])


@app.exception_handler(UpstreamError)
async def upstream_error_handler(_: Request, exc: UpstreamError):
    return JSONResponse({"detail": f"upstream service error ({exc.service}): {exc.message}"}, status_code=502)


def _unsupported(value: str, code: str | None) -> HTTPException:
    named = f" ({country_name(code)})" if code else ""
    return HTTPException(422, f"unsupported country '{value}'{named}: this service covers the US, Europe and Japan "
                              "(本服务只核验美国、欧洲和日本的地址); supported: US, " + ", ".join(sorted(COUNTRIES)))


def _country(req: VerifyRequest) -> str:
    """The explicit country; else the one the address names (a US state + ZIP is the US); else the caller's
    default; else the US."""
    text = req.address or ", ".join(p for p in (req.street, req.city, req.state, req.zip) if p)
    if req.country and req.country.strip():
        code = known_country(req.country)
        if not supported(code):
            raise _unsupported(req.country.strip(), code)
        return crown_dependency(code, text)
    if req.address:
        code = country_in_text(req.address)
    else:
        code = structured_country(req.street, req.city, req.state, req.zip)
    raw_default = (req.default_country or "").strip()
    default = known_country(raw_default) if raw_default else None
    # a batch's own code or province after the town: "Berlin, DE" in a German batch, "Milano MI 20121" in an Italian one
    own = bool(default) and (default_wins(default, req.address, code=code) if req.address
                             else default_wins(default, text, req.state or "", req.zip, code=code))
    if own and code != default:
        code = None
    if code is None and raw_default:
        if not supported(default):
            raise _unsupported(raw_default, default)
        code = default
        # a batch default doesn't take a line that ends with a US state ("..., Springfield, IL"), unless the line
        # also has the default country's postcode ("Via Roma 10, 20121 Milano MI": MI is Milan's province)
        if code != "US" and not own and us_state_tail(text) and not postcode_in(code, text):
            code = "US"
    if code is not None and not supported(code):
        raise _unsupported(code, code)
    return crown_dependency(code or "US", text)


async def _run_verify(req: VerifyRequest) -> dict:
    country = _country(req)
    if country != "US":
        return await verify_intl(
            Ctx(app.state.upstream, app.state.overture), getattr(app.state, "japanpost", None), country,
            address=req.address, street=req.street, city=req.city, postcode=req.zip, unit=req.unit, state=req.state,
            overture_fallback=req.overture_fallback, max_candidates=req.max_candidates)
    if req.address and not req.street:
        pa = parse_oneline(req.address)
        if req.unit:
            pa.unit, pa.unit_raw = norm_unit(req.unit), prep(req.unit)
        if req.city and not pa.city:
            pa.city = prep(req.city)
        if req.state and not pa.state:
            pa.state = state_abbr(req.state) or prep(req.state).upper()
        if req.zip and not pa.zip5:
            pa.zip5, pa.zip4, warning = fix_zip(prep(req.zip), pa.state)
            pa.warnings += [warning] if warning else []
        structured = False
    else:
        pa = parse_structured(req.street or "", req.city or "", req.state or "", req.zip or "", req.unit or "")
        structured = True
    return await verify_address(
        app.state.upstream, pa, overture=app.state.overture, lat=req.lat, lon=req.lon, structured=structured,
        zip_fallback=req.zip_fallback, overture_fallback=req.overture_fallback, max_candidates=req.max_candidates,
    )


# ------------------------------------------------------------------------------- routes


STATIC_DIR = Path(__file__).parent / "static"


class _Assets(StaticFiles):
    """Web page assets, always revalidated (a cheap 304 via ETag) so a redeploy is never served stale. Vendored
    libraries only change on a deliberate upgrade, so the browser keeps them for a day without asking."""

    def file_response(self, full_path, *args, **kwargs):
        response = super().file_response(full_path, *args, **kwargs)
        vendor = (STATIC_DIR / "vendor") in Path(full_path).parents
        response.headers["Cache-Control"] = "public, max-age=86400" if vendor else "no-cache"
        return response


app.mount("/static", _Assets(directory=STATIC_DIR), name="static")


def _page(name: str) -> FileResponse:
    return FileResponse(STATIC_DIR / name, headers={"Cache-Control": "no-cache"})


@app.get("/", include_in_schema=False)
async def root(request: Request):
    if "text/html" in request.headers.get("accept", ""):  # browsers get the web page, API clients the JSON index
        return _page("index.html")
    return {
        "service": app.title,
        "docs": "/docs",
        "endpoints": ["/v1/verify", "/v1/verify/batch", "/v1/nad/near", "/v1/nad/coverage", "/countries", "/health"],
    }


@app.get("/batch", include_in_schema=False)
async def batch_page():
    return _page("batch.html")


US_SERVICES = {"census_geocoder", "nad", "nad_coverage", "tigerweb", "overture"}
DEEP_EVERY_S = 60  # a deep check calls ~25 services (Photon, the national registers): at most once a minute
_deep: dict = {"at": 0.0, "body": {}, "lock": None}


@app.get("/health", tags=["meta"])
async def health(request: Request, deep: bool = Query(False, description="Call every upstream now (uncached) instead "
                                                                         "of only reporting how each one's latest call "
                                                                         "went; at most once a minute, needs the API key "
                                                                         "when keys are set")):
    """`upstreams` has the outcome of each upstream's latest real call; `status` is `degraded` when any failed."""
    up: Upstream = app.state.upstream
    ov: OvertureClient | None = app.state.overture
    body: dict = {
        "status": "ok", "cache_entries": len(up.cache), "nad_layer": config.NAD_LAYER_URL,
        "overture": {"enabled": ov is not None, "release": ov._release if ov else None},
    }
    if deep and config.API_KEYS and request.headers.get("x-api-key") not in config.API_KEYS:
        raise HTTPException(401, "deep=true calls every upstream service: send X-API-Key")
    if deep:
        async def count() -> None:
            t = time.perf_counter()
            try:
                body["nad_point_count"] = await up.nad_count()
                body["nad_latency_ms"] = round((time.perf_counter() - t) * 1000, 1)
            except UpstreamError as exc:
                body["nad_error"] = exc.message

        async def release() -> None:
            try:
                body["overture"]["release"] = await ov.release()
            except UpstreamError as exc:
                body["overture"]["error"] = exc.message

        async def intl(service: str, url: str, params: dict) -> None:
            try:
                await up._get_json(service, url, params, cache=False)  # records the outcome in up.status
            except UpstreamError:
                pass

        _deep["lock"] = _deep["lock"] or asyncio.Lock()
        async with _deep["lock"]:  # concurrent callers share one round of probes
            age = time.monotonic() - _deep["at"]
            if age < DEEP_EVERY_S:
                body.update(_deep["body"], deep_age_s=round(age))
            else:
                await asyncio.gather(count(), up.ping(), *([release()] if ov else []),
                                     *(intl(s, u, p) for s, (u, p) in INTL_PROBES.items()))
                _deep["at"] = time.monotonic()
                _deep["body"] = {k: body[k] for k in ("nad_point_count", "nad_latency_ms", "nad_error") if k in body}
    now = time.time()
    seen = {**up.status, **({"overture": ov.status} if ov and ov.status else {})}
    view = lambda v: {"ok": v["ok"], "error": v["error"], "ago_s": round(now - v["at"])}  # noqa: E731
    body["upstreams"] = {s: view(v) for s, v in seen.items() if s in US_SERVICES}
    # Services for other countries are reported apart and don't turn the whole service "degraded": a timeout in
    # one country's register says nothing about the others.
    body["intl_upstreams"] = {s: view(v) for s, v in seen.items() if s not in US_SERVICES}
    jp: JapanPost | None = getattr(app.state, "japanpost", None)
    body["japanpost"] = {"loaded": bool(jp and jp.ready), "error": jp.error if jp else None}
    if not all(v["ok"] for v in body["upstreams"].values()) or "error" in body["overture"]:
        body["status"] = "degraded"
    return body


@app.get("/countries", tags=["meta"], response_model=list[CountryInfo],
         summary="Countries that can be verified and their data sources")
async def countries():
    """`providers` in the order they are asked: the first that answers decides; the next only when it fails (or,
    for Overture, has no data around the address)."""
    def prov(key: str) -> dict:
        p = INTL_INFO[key]
        return {"key": key, "name": p.name, "authority": p.authority, "license": p.license}
    us = {"code": "US", "name": "美国", "providers": [
        {"key": "nad", "name": "USDOT National Address Database", "authority": "official", "license": "Public domain"},
        {"key": "overture", "name": "Overture Maps", "authority": "overture", "license": "per source"}]}
    return [us] + [{"code": c.code, "name": c.zh, "providers": [prov(k) for k in c.providers]}
                   for c in sorted(COUNTRIES.values(), key=lambda c: (c.code != "JP", c.code))]


@app.get("/v1/verify", response_model=VerifyResponse, tags=["verify"], summary="Verify one address (query string)")
async def verify_get(
    address: str | None = Query(None, max_length=300, description="One-line address", examples=["1700 W Washington St, Phoenix, AZ 85007"]),
    street: str | None = Query(None, max_length=200, description="Street line (structured mode)"),
    unit: str | None = Query(None, max_length=50),
    city: str | None = Query(None, max_length=100),
    state: str | None = Query(None, max_length=50, description="Abbreviation or full name"),
    zip: str | None = Query(None, max_length=20, alias="zip", description="ZIP, or the postcode outside the US"),
    country: str | None = Query(None, max_length=60, description="ISO code or name; default: named at the end of the "
                                                                 "address, Japanese text -> JP, else US"),
    default_country: str | None = Query(None, max_length=60, description="Used when neither `country` nor the "
                                                                         "address names one (instead of the US)"),
    lat: float | None = Query(None, ge=-90, le=90),
    lon: float | None = Query(None, ge=-180, le=180),
    zip_fallback: bool = Query(False),
    overture_fallback: bool = Query(True, description="Retry against Overture Maps when NAD cannot confirm"),
    max_candidates: int = Query(5, ge=1, le=50),
):
    try:
        req = VerifyRequest(address=address, street=street, unit=unit, city=city, state=state, zip=zip,
                            country=country, default_country=default_country, lat=lat, lon=lon, zip_fallback=zip_fallback,
                            overture_fallback=overture_fallback, max_candidates=max_candidates)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return await _run_verify(req)


@app.post("/v1/verify", response_model=VerifyResponse, tags=["verify"], summary="Verify one address (JSON body)")
async def verify_post(req: VerifyRequest):
    return await _run_verify(req)


@app.post("/v1/verify/batch", response_model=BatchResponse, tags=["verify"], summary="Verify many addresses")
async def verify_batch(body: BatchRequest, stream: bool = Query(
        False, description="Stream one BatchItem per line (application/x-ndjson) as each address finishes, "
                           "instead of one JSON document once all are done")):
    if len(body.addresses) > config.MAX_BATCH_SIZE:
        raise HTTPException(413, f"at most {config.MAX_BATCH_SIZE} addresses per batch")
    sem = asyncio.Semaphore(config.BATCH_CONCURRENCY)

    async def one(i: int, req: VerifyRequest) -> BatchItem:
        async with sem:
            try:
                return BatchItem(index=i, result=await _run_verify(req))
            except Exception as exc:  # one bad row must not fail the batch
                return BatchItem(index=i, error=str(exc))

    if stream:
        async def lines():
            tasks = [asyncio.create_task(one(i, r)) for i, r in enumerate(body.addresses)]
            try:
                for done in asyncio.as_completed(tasks):
                    yield (await done).model_dump_json() + "\n"
            finally:  # client went away: stop the rest
                for t in tasks:
                    t.cancel()

        # X-Accel-Buffering: nginx would otherwise hold the lines back until the whole batch is done
        return StreamingResponse(lines(), media_type="application/x-ndjson", headers={"X-Accel-Buffering": "no"})

    items = await asyncio.gather(*(one(i, r) for i, r in enumerate(body.addresses)))
    summary = Counter(it.result.status if it.result else "error" for it in items)
    return BatchResponse(count=len(items), summary=dict(summary), results=list(items))


@app.get("/v1/nad/near", response_model=NearResponse, tags=["nad"], summary="NAD address points near a coordinate")
async def nad_near(
    lat: float = Query(..., ge=-90, le=90),
    lon: float = Query(..., ge=-180, le=180),
    radius: float = Query(100, gt=0, description="meters (capped by NAD_MAX_NEAR_RADIUS_M, default 1500)"),
    limit: int = Query(20, ge=1, le=2000),
    number: int | None = Query(None, description="Only points with this house number (Add_Number)"),
):
    radius = min(radius, config.MAX_NEAR_RADIUS_M)
    where = f"Add_Number = {int(number)}" if number is not None else "1=1"
    recs, truncated = await app.state.upstream.nad_near(lat, lon, radius, where=where)
    for r in recs:
        r["distance_m"] = (
            round(haversine_m(lat, lon, r["lat"], r["lon"]), 1) if r.get("lat") is not None and r.get("lon") is not None else None
        )
    recs.sort(key=lambda r: r["distance_m"] if r["distance_m"] is not None else 1e9)
    return {"lat": lat, "lon": lon, "radius_m": radius, "count": len(recs[:limit]),
            "truncated": truncated or len(recs) > limit, "results": recs[:limit]}


@app.get("/v1/nad/coverage", response_model=CoverageResponse, tags=["nad"], summary="Is a county covered by the NAD?")
async def nad_coverage(
    lat: float | None = Query(None, ge=-90, le=90),
    lon: float | None = Query(None, ge=-180, le=180),
    state: str | None = Query(None, min_length=2, max_length=2, description="State abbreviation, with county"),
    county: str | None = Query(None, description="County name without 'County', e.g. Maricopa"),
):
    up: Upstream = app.state.upstream
    if state and county:
        return {"query": {"state": state.upper(), "county": county}, "coverage": await up.county_status_by_name(county, state)}
    if lat is None or lon is None:
        raise HTTPException(422, "give lat+lon, or state+county")
    cov = await up.resolve_coverage(lat, lon)
    return {"query": {"lat": lat, "lon": lon}, "coverage": cov}


@app.get("/v1/overture/near", response_model=OvertureNearResponse, tags=["overture"],
         summary="Overture Maps address points near a coordinate")
async def overture_near(
    lat: float = Query(..., ge=-90, le=90),
    lon: float = Query(..., ge=-180, le=180),
    radius: float = Query(100, gt=0, description="meters (capped by NAD_MAX_NEAR_RADIUS_M, default 1500)"),
    limit: int = Query(20, ge=1, le=2000),
    number: int | None = Query(None, description="Only points whose house number is this integer"),
):
    ov: OvertureClient | None = app.state.overture
    if ov is None:
        raise HTTPException(503, "Overture fallback is disabled (OVERTURE_ENABLED=0)")
    radius = min(radius, config.MAX_NEAR_RADIUS_M)
    recs = await ov.query(bbox_around(lat, lon, radius), number=number)
    if number is not None:
        recs = [r for r in recs if r["address_number_int"] == number]
    # copies: the records are the cached ones other requests get too, so they must not carry this request's distance
    recs = [{**r, "distance_m": round(haversine_m(lat, lon, r["lat"], r["lon"]), 1) if r.get("lat") is not None else None}
            for r in recs]
    recs = sorted((r for r in recs if r["distance_m"] is None or r["distance_m"] <= radius),
                  key=lambda r: r["distance_m"] if r["distance_m"] is not None else 1e9)
    return {"lat": lat, "lon": lon, "radius_m": radius, "count": len(recs[:limit]), "truncated": len(recs) > limit,
            "release": await ov.release(), "results": recs[:limit]}
