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
from .address import parse_oneline
from .intl import COUNTRIES, Ctx, JapanPost, country_in_text, country_name, known_country, supported, verify_intl
from .intl.countries import crown_dependency
from .intl import INFO as INTL_INFO
from .intl.core import PROBES as INTL_PROBES
from .intl.core import check_sources
from .models import (
    ADDRESS_DOC, COUNTRY_DOC, VERIFY_EXAMPLE, BatchItem, BatchRequest, BatchResponse, CountryInfo, ErrorDetail,
    VerifyRequest, VerifyResponse,
)
from .overture import OvertureClient
from .upstream import Upstream, UpstreamError
from .verify import verify_address


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
    tasks.append(asyncio.create_task(check_sources(Ctx(app.state.upstream, app.state.overture))))
    yield
    for t in [*tasks, *getattr(app.state.upstream, "intl_rechecks", {}).values()]:
        if not t.done():
            t.cancel()
    await app.state.upstream.aclose()


app = FastAPI(
    title="NAD Address Verification API",
    version="1.0.0",
    description=(
        "核验地址是否真实存在（门牌、街道、单元），覆盖美国、欧洲和日本。\n\n"
        "- **美国**：Census 地理编码定位 → 在 USDOT **National Address Database**（约 9800 万个官方地址点）里按位置和门牌"
        "查找 → 本地比对街道、单元、ZIP、城市 → NAD 查不到时用 **Overture Maps** 补查。\n"
        "- **欧洲**：各国官方地址登记（法国 BAN、荷兰 BAG 等），Overture 转载的官方数据（德国、意大利等），其余用 "
        "OpenStreetMap。\n"
        "- **日本**：国土地理院住所検索 + 日本郵便邮编表。\n\n"
        "各国用哪些数据源见 `/countries`。核验的是地址点存在，**不是** USPS 能投递。\n\n"
        "开启 API Key 时，`/v1/` 下的接口要带请求头 `X-API-Key`。"
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
    """`country` when given; else the one the address names (a US state + ZIP is the US, Japanese text is JP);
    else the US."""
    if req.country and req.country.strip():
        code = known_country(req.country)
        if not supported(code):
            raise _unsupported(req.country.strip(), code)
        return crown_dependency(code, req.address)
    code = country_in_text(req.address)
    if code is not None and not supported(code):
        raise _unsupported(code, code)
    return crown_dependency(code or "US", req.address)


async def _run_verify(req: VerifyRequest) -> dict:
    country = _country(req)
    if country != "US":
        return await verify_intl(Ctx(app.state.upstream, app.state.overture), getattr(app.state, "japanpost", None),
                                 country, address=req.address)
    return await verify_address(app.state.upstream, parse_oneline(req.address), overture=app.state.overture)


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
        "endpoints": ["/v1/verify", "/v1/verify/batch", "/countries", "/health"],
    }


@app.get("/batch", include_in_schema=False)
async def batch_page():
    return _page("batch.html")


US_SERVICES = {"census_geocoder", "nad", "nad_coverage", "tigerweb", "overture"}
DEEP_EVERY_S = 60  # a deep check calls ~25 services (Photon, the national registers): at most once a minute
_deep: dict = {"at": 0.0, "body": {}, "lock": None}


@app.get("/health", tags=["meta"], summary="服务和上游状态")
async def health(request: Request, deep: bool = Query(False, description="立即实际调用每个上游（不走缓存），而不只是报告"
                                                                         "各自最近一次调用的结果；最多每分钟一次，开启 "
                                                                         "API Key 时需要带 Key")):
    """`upstreams` 是美国各上游最近一次实际调用的结果，`intl_upstreams` 是其他国家的；美国任一上游失败时 `status` 为
    `degraded`。"""
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
                return
            getattr(up, "intl_breaker", {}).pop(service, None)  # it answered: addresses may ask it again

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
         summary="支持的国家和数据源")
async def countries():
    """`providers` 按询问顺序排列：第一个回答的决定结论；它失败时（Overture 是在地址附近没有数据时）才问下一个。"""
    def prov(key: str) -> dict:
        p = INTL_INFO[key]
        return {"key": key, "name": p.name, "authority": p.authority, "license": p.license}
    us = {"code": "US", "name": "美国", "providers": [
        {"key": "nad", "name": "USDOT National Address Database", "authority": "official", "license": "Public domain"},
        {"key": "overture", "name": "Overture Maps", "authority": "overture", "license": "per source"},
        {"key": "overture_places", "name": "Overture Maps places", "authority": "business", "license": "CDLA-Permissive-2.0"}]}
    return [us] + [{"code": c.code, "name": c.zh, "providers": [prov(k) for k in c.providers]}
                   for c in sorted(COUNTRIES.values(), key=lambda c: (c.code != "JP", c.code))]


VERIFY_DOC = """核验一条地址。入参只有两个：

- `address`（必填）：一行完整地址，最长 300 个字符
- `country`（可选）：ISO 代码或国名；不填时从地址里识别，识别不出按美国

返回里先看 `status`（结论）、`verified`（地址是否存在）、`confidence`（置信度），再看 `best_match`（匹配到的记录）、
`parsed`（地址拆成的字段，已核实时按记录的写法）和 `notes`（结论的原因）。每个字段的说明见下方 Schema 里的
VerifyResponse；下面 200 的示例是一个真实返回，值为 null 的字段在示例里省略了。

地址不存在不是错误：照常返回 200，`status` 给出结论。只有请求本身有问题或服务出错时才返回下面的错误码。"""

# The 422 detail comes in two shapes: FastAPI's list for a malformed request, a string for an unsupported country.
ERRORS: dict = {
    401: {"model": ErrorDetail, "description": "开启了 API Key，但请求头 `X-API-Key` 缺失或错误"},
    422: {"model": ErrorDetail, "description": "参数不合法：`address` 为空或超过 300 字、`country` 超过 60 字（`detail` 是列表），"
                                               "或 `country` 不在支持范围内（`detail` 是字符串，列出支持的国家）"},
    429: {"model": ErrorDetail, "description": "超过每分钟请求数上限（默认 120 次 / 分钟 / IP），响应头 `Retry-After: 60`"},
    502: {"model": ErrorDetail, "description": "上游服务异常（极少见：上游超时一般在 200 响应里以 "
                                               "`status: upstream_error` 返回）"},
}
VERIFY_RESPONSES: dict = {200: {"content": {"application/json": {"example": VERIFY_EXAMPLE}}}, **ERRORS}


@app.get("/v1/verify", response_model=VerifyResponse, tags=["verify"], summary="核验一条地址（查询参数）",
         description=VERIFY_DOC, responses=VERIFY_RESPONSES)
async def verify_get(
    address: str = Query(..., min_length=1, max_length=300, description=ADDRESS_DOC,
                         examples=["1700 W Washington St, Phoenix, AZ 85007"]),
    country: str | None = Query(None, max_length=60, description=COUNTRY_DOC),
):
    return await _run_verify(VerifyRequest(address=address, country=country))


@app.post("/v1/verify", response_model=VerifyResponse, tags=["verify"], summary="核验一条地址（JSON 请求体）",
          description=VERIFY_DOC, responses=VERIFY_RESPONSES)
async def verify_post(req: VerifyRequest):
    return await _run_verify(req)


@app.post("/v1/verify/batch", response_model=BatchResponse, tags=["verify"], summary="批量核验",
          responses={**ERRORS, 413: {"model": ErrorDetail, "description": "超过单次条数上限（默认 100）"}})
async def verify_batch(body: BatchRequest, stream: bool = Query(
        False, description="每查完一条就输出一行 BatchItem（application/x-ndjson，按完成先后），而不是全部查完后"
                           "返回一个 JSON")):
    """每条的请求和返回都和单条接口相同，单次最多 100 条。一条失败（如不支持的国家）只在那条的 `error` 里说明，
    不影响其他条。"""
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
