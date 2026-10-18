"""Overture Maps addresses client.

Overture publishes ~125M US address points (NAD, OpenAddresses county/city sources, ...) as
GeoParquet on a public S3 bucket. DuckDB reads only the row groups whose bbox statistics overlap
the query box, so a lookup touches a few MB instead of the 27 GB dataset (~0.6-1.7 s warm,
~3.5 s on the first query while Parquet footers are cached).
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import threading
import time
from typing import Any

import duckdb
import httpx

from . import config
from .upstream import TTLCache, UpstreamError, _describe

log = logging.getLogger(__name__)

_RELEASE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}\.\d+$")
_NUMBER_RE = re.compile(r"^(\d+)\s*([A-Z]|1/2|-\d+)?$", re.I)
_RELEASE_TTL_S = 24 * 3600
# what costs a batch row the full timeout (an error that comes back at once doesn't)
_SLOW_RE = re.compile(r"(?i)timed out|timeout|connect")


def _parse_number(number: str | None) -> tuple[int | None, str]:
    """'1700' -> (1700, ''), '350 B' / '12A' -> (n, 'B'/'A'), '45-12' -> (45, '-12'); else (None, '')."""
    m = _NUMBER_RE.match((number or "").strip())
    if not m:
        return None, ""
    return int(m.group(1)), (m.group(2) or "").upper()


def overture_record(row: dict) -> dict:
    """Map an Overture address row onto the NAD record shape used by the matcher."""
    levels = [lv.get("value") for lv in (row.get("address_levels") or []) if lv and lv.get("value")]
    state = levels[0] if levels else None
    # Prefer the municipality level: NAD-derived postal_city carries Phoenix village names ("Capitol").
    city = (levels[1] if len(levels) > 1 else None) or row.get("postal_city")
    postcode = (row.get("postcode") or "").strip() or None
    zip5, zip4 = (postcode[:5], postcode[6:10] or None) if postcode and len(postcode) > 5 else (postcode, None)
    number, street, unit = row.get("number"), row.get("street"), row.get("unit")
    num_int, num_suf = _parse_number(number)
    dataset = row.get("dataset")
    line1 = " ".join(p for p in (number, street) if p)
    formatted = ", ".join(p for p in (line1, unit, city, " ".join(p for p in (state, zip5) if p)) if p)
    return {
        "uuid": row.get("id"),
        "formatted": formatted,
        "address_number": number,
        "address_number_int": num_int,
        "address_number_prefix": None,
        "address_number_suffix": num_suf or None,
        "street": street,
        "street_parts": {k: None for k in ("pre_modifier", "pre_directional", "pre_type", "pre_separator",
                                           "name", "post_type", "post_directional", "post_modifier")},
        "subaddress": {"building": None, "floor": None, "unit": unit, "room": None, "subaddress": None},
        "landmark": None,
        "city": city,
        "postal_city": row.get("postal_city"),
        "municipality": levels[1] if len(levels) > 1 else None,
        "county": None,
        "state": state,
        "zip": zip5,
        "zip4": zip4,
        "postcode": postcode,  # as published: outside the US the ZIP split above means nothing
        "lat": row.get("lat"),  # bbox values are float32 in Overture: ~1 m precision
        "lon": row.get("lon"),
        "placement": None,
        "address_class": None,
        "address_type": None,
        "lifecycle": None,
        "delivery_type": None,
        "updated": (row.get("update_time") or "")[:10] or None,
        "address_authority": None,
        "source": f"overture:{dataset}" if dataset else "overture",
        "license": row.get("license"),
    }


class OvertureClient:
    def __init__(self, http: httpx.AsyncClient) -> None:
        self.http = http
        self.cache = TTLCache(config.CACHE_TTL_S, config.CACHE_MAX_ENTRIES)
        self._sem = asyncio.Semaphore(max(1, config.OVERTURE_MAX_CONCURRENCY))
        self._con: duckdb.DuckDBPyConnection | None = None
        self._con_lock = threading.Lock()
        self._release: str | None = config.OVERTURE_RELEASE or None
        self._release_checked = 0.0
        self._release_lock = asyncio.Lock()
        self._inflight: dict[str, asyncio.Task] = {}
        self.status: dict[str, Any] | None = None  # outcome of the latest real (uncached) query, for /health
        # a hung S3 / DuckDB would cost every row of a batch the full timeout: after 3 queries in a row timed out, wait
        # a minute. This is the US fallback's count; the European lookups pass their own (`breaker`).
        self._fails, self._closed_until = 0, 0.0

    def _mark(self, error: str | None) -> None:
        self.status = {"ok": error is None, "at": time.time(), "error": error}

    # ------------------------------------------------------------------ setup

    def _connection(self) -> duckdb.DuckDBPyConnection:
        with self._con_lock:
            if self._con is None:
                con = duckdb.connect()
                con.execute("INSTALL httpfs")
                con.execute("LOAD httpfs")
                # GLOBAL so every con.cursor() (one per worker thread) inherits them.
                con.execute(f"SET GLOBAL s3_region = '{config.OVERTURE_REGION}'")
                con.execute("SET GLOBAL enable_http_metadata_cache = true")
                con.execute("SET GLOBAL enable_object_cache = true")
                con.execute(f"SET GLOBAL memory_limit = '{config.OVERTURE_MEMORY_LIMIT}'")
                con.execute(f"SET GLOBAL threads = {max(1, config.OVERTURE_THREADS)}")
                self._con = con
            return self._con

    def _cursor(self) -> duckdb.DuckDBPyConnection:
        con = self._connection()
        with self._con_lock:
            return con.cursor()

    async def release(self, refresh: bool = False) -> str:
        """Newest release that has the addresses theme (old releases are deleted from S3)."""
        if config.OVERTURE_RELEASE:
            return config.OVERTURE_RELEASE
        async with self._release_lock:
            fresh = time.monotonic() - self._release_checked < _RELEASE_TTL_S
            if self._release and fresh and not refresh:
                return self._release
            base = f"https://{config.OVERTURE_BUCKET}.s3.{config.OVERTURE_REGION}.amazonaws.com/"
            try:
                resp = await self.http.get(base, params={"list-type": "2", "prefix": "release/", "delimiter": "/"})
                resp.raise_for_status()
                releases = sorted(
                    (r for r in re.findall(r"<Prefix>release/([^/<]+)/</Prefix>", resp.text) if _RELEASE_RE.match(r)),
                    reverse=True,
                )
                for rel in releases:
                    probe = await self.http.get(base, params={
                        "list-type": "2", "max-keys": "1", "prefix": f"release/{rel}/theme=addresses/type=address/",
                    })
                    if probe.status_code == 200 and "<Key>" in probe.text:
                        self._release, self._release_checked = rel, time.monotonic()
                        return rel
            except httpx.HTTPError as exc:
                if self._release:  # keep serving the last known release
                    return self._release
                self._mark(f"cannot list releases: {_describe(exc)}")
                raise UpstreamError("overture", f"cannot list releases: {_describe(exc)}") from exc
            if self._release:
                return self._release
            raise UpstreamError("overture", "no release with an addresses theme found")

    # ------------------------------------------------------------------ queries

    async def query(self, bbox: dict, number: int | None = None, limit: int = 5000,
                    timeout: float | None = None, any_part: bool = False, breaker: list | None = None) -> list[dict]:
        """Address points inside bbox (lon/lat degrees), optionally only those whose number starts
        with `number` (exact int/suffix matching happens in the matcher). `any_part` also takes the number after
        a '/' (Slovak and Czech "553/25": building 553, street number 25). `breaker`: the caller's own [timed-out
        queries in a row, closed until] instead of this client's (slow 20 km European street boxes must not switch
        off the US fallback)."""
        for attempt in range(2):
            release = await self.release(refresh=attempt > 0)
            key = json.dumps([release, {k: round(v, 6) for k, v in bbox.items()}, number, limit] + ([1] if any_part else []))
            cached = self.cache.get(key)
            if cached is not None:
                return cached
            # the same box being read already (duplicate rows in a batch): share that DuckDB query
            task = self._inflight.get(key)
            if task is None:
                if (breaker[1] if breaker is not None else self._closed_until) > time.monotonic():
                    raise UpstreamError("overture", "not asked: it timed out 3 times in a row within the last minute")
                task = asyncio.create_task(self._run(release, bbox, number, limit, timeout or config.OVERTURE_TIMEOUT_S,
                                                     any_part))
                self._inflight[key] = task
                task.add_done_callback(lambda t, k=key, b=breaker: self._settle(k, t, b))
            try:
                rows = await asyncio.shield(task)
            except UpstreamError as exc:
                # The release we were using was deleted: re-discover once and retry.
                if attempt == 0 and not config.OVERTURE_RELEASE and re.search(r"404|403|NoSuchKey|No files found", exc.message):
                    log.warning("overture release %s unavailable, re-discovering", release)
                    continue
                self._mark(exc.message)
                raise
            self._mark(None)
            recs = [overture_record(r) for r in rows]
            self.cache.set(key, recs)
            return recs
        raise UpstreamError("overture", "release unavailable")

    def _settle(self, key: str, task: asyncio.Task, breaker: list | None) -> None:
        """A finished query counts once for the breaker, however many callers shared it (duplicate rows of a batch)."""
        self._inflight.pop(key, None)
        exc = None if task.cancelled() else task.exception()
        if task.cancelled() or (exc is not None and not (isinstance(exc, UpstreamError) and _SLOW_RE.search(exc.message))):
            return  # neither shows that S3 / DuckDB hangs, nor that it is back
        fails = 0 if exc is None else (breaker[0] if breaker is not None else self._fails) + 1
        until = time.monotonic() + config.INTL_BREAKER_S if fails >= 3 else 0.0
        if breaker is not None:
            breaker[:] = [fails, until]
        else:
            self._fails, self._closed_until = fails, until

    async def _run(self, release: str, bbox: dict, number: int | None, limit: int, timeout: float,
                   any_part: bool = False) -> list[dict]:
        if not _RELEASE_RE.match(release):
            raise UpstreamError("overture", f"invalid release '{release}'")
        src = f"s3://{config.OVERTURE_BUCKET}/release/{release}/theme=addresses/type=address/*.parquet"
        sql = f"""
            SELECT id, number, street, unit, postcode, postal_city, address_levels,
                   bbox.xmin AS lon, bbox.ymin AS lat,
                   sources[1].dataset AS dataset, sources[1].license AS license,
                   sources[1].update_time AS update_time
            FROM read_parquet('{src}')
            WHERE bbox.xmin BETWEEN ? AND ? AND bbox.ymin BETWEEN ? AND ?
            {"AND regexp_matches(number, ?)" if number is not None and any_part else
             "AND number LIKE ? AND regexp_matches(number, ?)" if number is not None else ""}
            ORDER BY (bbox.xmin - ?) ^ 2 + (bbox.ymin - ?) ^ 2
            LIMIT {int(limit)}
        """
        params: list[Any] = [bbox["xmin"], bbox["xmax"], bbox["ymin"], bbox["ymax"]]
        if number is not None and any_part:
            params += [f"^([0-9]+/)?{int(number)}([^0-9]|$)"]
        elif number is not None:
            # "1" must match "1", "1A", "1 1/2" but not "10" / "1600" (dense areas would hit LIMIT otherwise)
            params += [f"{int(number)}%", f"^{int(number)}([^0-9]|$)"]
        params += [(bbox["xmin"] + bbox["xmax"]) / 2, (bbox["ymin"] + bbox["ymax"]) / 2]  # nearest first

        async with self._sem:
            cur = await asyncio.to_thread(self._cursor)

            def work() -> list[dict]:
                res = cur.execute(sql, params)
                cols = [d[0] for d in res.description]
                return [dict(zip(cols, row, strict=True)) for row in res.fetchall()]

            fut = asyncio.ensure_future(asyncio.to_thread(work))
            try:
                return await asyncio.wait_for(asyncio.shield(fut), timeout=timeout)
            except asyncio.TimeoutError:
                cur.interrupt()  # wait_for alone would leave the DuckDB thread running
                raise UpstreamError("overture", f"query timed out after {timeout:.0f}s") from None
            except duckdb.Error as exc:
                raise UpstreamError("overture", str(exc).splitlines()[0][:300]) from exc
            finally:
                if fut.done():
                    cur.close()
                else:  # interrupted after a timeout: close once the thread finishes, and consume its error
                    fut.add_done_callback(lambda f: (cur.close(), f.cancelled() or f.exception()))

    async def warmup(self, retry_after_s: tuple[float, ...] = (60.0, 300.0)) -> None:
        """Prime DuckDB's Parquet metadata cache so the first real fallback isn't the slow one. It runs in the
        background with its own, longer timeout (a server far from us-west-2 can need minutes for the ~43 MB of
        metadata) and is retried; any success also clears a failure shown by /health."""
        for attempt, delay in enumerate((0.0, *retry_after_s)):
            await asyncio.sleep(delay)
            try:
                t = time.perf_counter()
                await self.query({"xmin": -112.10, "ymin": 33.44, "xmax": -112.09, "ymax": 33.45}, number=1, limit=1,
                                 timeout=config.OVERTURE_WARMUP_TIMEOUT_S)
                log.info("overture warm-up done in %.1fs (release %s)", time.perf_counter() - t, self._release)
                return
            except Exception as exc:  # never block startup on the fallback
                log.warning("overture warm-up failed (attempt %d): %s", attempt + 1, exc)
