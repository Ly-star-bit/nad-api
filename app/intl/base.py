"""What every non-US provider shares: polite HTTP access and the candidate record format."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from .. import config
from ..upstream import Upstream, UpstreamError, UpstreamRejected, UpstreamUnreachable

if TYPE_CHECKING:
    from ..overture import OvertureClient


@dataclass(frozen=True)
class ProviderInfo:
    key: str
    name: str             # shown to users, e.g. "BAN (Base Adresse Nationale)"
    authority: str        # official | overture | osm
    url: str
    license: str
    attribution: str = ""


class Skip(Exception):
    """This provider can't answer for this address (Brussels, a street name its exact filter doesn't know, no city
    for a service that needs one): ask the country's next provider, without reporting a failure."""


@dataclass
class Lookup:
    """One provider's answer, before the verdict."""
    cands: list[dict] = field(default_factory=list)
    unit_support: bool = False   # the source has flats / units / additions, so a missing one means something
    street_found: bool = False
    area_empty: bool = False
    status: str | None = None    # set when the provider already knows (geocode_failed, not_covered, ...)
    geocode: dict | None = None  # where the address was located, when that took a separate search
    notes: list[str] = field(default_factory=list)
    timing: dict[str, float] = field(default_factory=dict)
    view: Any = None             # the address as this provider reads it (IntlAddress), when it differs from the parse
    incomplete: str | None = None  # why a number missing from this source proves nothing (it lacks some buildings)
    credit: str | None = None      # attribution for other data used with this source's (GB postcodes)


class Ctx:
    """Access to the upstreams for one request. Each free service gets a small concurrency cap (per process, kept on
    the app's Upstream so it belongs to the app's event loop), so a batch of 100 addresses for one country never hits
    that country's service with more than a few requests at once."""

    def __init__(self, up: Upstream, ov: OvertureClient | None) -> None:
        if not hasattr(up, "intl_sems"):
            up.intl_sems, up.intl_breaker = {}, {}
        if not hasattr(up, "intl_rechecks"):
            up.intl_rechecks = {}  # service -> the task asking an unreachable service again
        # (a stand-in without the client's breaker, as in tests, is used as it is)
        self.up, self.ov = up, _Overture(ov, up.intl_breaker) if hasattr(ov, "_settle") else ov

    async def get(self, service: str, url: str, params: dict[str, Any], *, expect: type = dict) -> Any:
        """The JSON answer; one of another shape (an error page, a maintenance message) counts as an outage and is
        not kept in the cache. A service that failed 3 times in a row is not asked for a minute (a hung register
        would otherwise cost every row of a batch its full timeout). One that can't be connected to at all is not
        asked from its first failure until a check in the background reaches it again: Latvia's register, out of
        reach from abroad, would otherwise cost every Latvian address the connect timeout before Overture."""
        breaker: dict[str, list[float]] = self.up.intl_breaker
        self._closed(service)
        cached = self.up.cache.get(self.up._key(url, params)) is not None
        async with self._sem(service):
            self._closed(service)  # again after the wait: the calls ahead of this one may have found it down
            try:
                data = await self.up._get_json(service, url, params)
            except UpstreamRejected:
                raise  # it answered (a 4xx about this query)
            except UpstreamUnreachable:
                self._unreachable(service, url, params)
                raise
            except UpstreamError:
                # read again after the wait: the calls that failed alongside this one count too
                fails = breaker.get(service, (0, 0.0))[0] + 1
                breaker[service] = [fails, time.monotonic() + config.INTL_BREAKER_S if fails >= 3 else 0.0]
                raise
        if not isinstance(data, expect):
            self.up.forget(url, params)
            raise UpstreamError(service, f"unexpected answer ({type(data).__name__} instead of {expect.__name__})")
        if not cached:  # only a real answer shows the service is back
            breaker.pop(service, None)
        return data

    def _closed(self, service: str) -> None:
        if self.up.intl_breaker.get(service, (0, 0.0))[1] > time.monotonic():
            task = self.up.intl_rechecks.get(service)
            raise UpstreamError(service, f"not asked: it could not be reached; checked again every {config.INTL_BREAKER_S:.0f}s"
                                if task and not task.done() else "not asked: it failed 3 times in a row within the last minute")

    def _unreachable(self, service: str, url: str, params: dict[str, Any]) -> None:
        # open for longer than the checks' interval, so it never lapses between two of them; should the checks stop,
        # it lapses and the next address asks the service itself, as after 3 failures
        fails = self.up.intl_breaker.get(service, (0, 0.0))[0] + 1
        self.up.intl_breaker[service] = [fails, time.monotonic() + 2 * config.INTL_BREAKER_S]
        task = self.up.intl_rechecks.get(service)
        if task is None or task.done():
            self.up.intl_rechecks[service] = asyncio.create_task(self._recheck(service, url, params))

    async def _recheck(self, service: str, url: str, params: dict[str, Any]) -> None:
        """Ask the request that failed again every INTL_BREAKER_S seconds until the service answers (anything at all:
        a slow or failing service is left to the 3-failure rule)."""
        while True:
            await asyncio.sleep(config.INTL_BREAKER_S)
            try:
                await self.up._get_json(service, url, params, cache=False)
            except UpstreamUnreachable:
                self._unreachable(service, url, params)  # kept closed (this task is the one checking)
                continue
            except UpstreamError:
                pass
            self.up.intl_breaker.pop(service, None)
            return

    def _sem(self, service: str) -> asyncio.Semaphore:
        sems: dict[str, asyncio.Semaphore] = self.up.intl_sems
        if service not in sems:
            sems[service] = asyncio.Semaphore(config.INTL_SERVICE_CONCURRENCY.get(service, config.INTL_CONCURRENCY))
        return sems[service]


class _Overture:
    """The app's Overture client as the European lookups use it: the same cache and shared queries, but their own
    breaker, kept with the services' (slow 20 km street boxes here must not switch off the US fallback)."""

    def __init__(self, ov: OvertureClient, breakers: dict[str, list]) -> None:
        self._ov, self._breakers = ov, breakers

    async def query(self, *args: Any, **kw: Any) -> list[dict]:
        return await self._ov.query(*args, breaker=self._breakers.setdefault("overture", [0, 0.0]), **kw)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._ov, name)


def cand(kind: str, *, number: Any = None, street: str | None = None, postcode: Any = None, city: str | None = None,
         unit: str | None = None, lat: Any = None, lon: Any = None, id: Any = None, formatted: str | None = None,
         region: str | None = None, street_alt: list[str] | None = None, source: str | None = None,
         license: str | None = None, updated: str | None = None, distance_m: float | None = None,
         city_alt: list[str] | None = None) -> dict:
    """A provider record in the shape the verdict (match.py) and the response (core.py) use. kind is 'address' or
    'street' (a street hit, no number)."""
    num = None if number in (None, "") else str(number)
    pc = None if postcode in (None, "") else str(postcode)
    street, city, region = (None if v in (None, "") else str(v) for v in (street, city, region))
    if not formatted:
        line1 = " ".join(p for p in (street, num) if p)
        formatted = ", ".join(p for p in (line1, unit, " ".join(p for p in (pc, city) if p)) if p)
    return {
        "kind": kind, "uuid": None if id is None else str(id), "formatted": formatted, "number": num,
        "street": street, "street_alt": [str(x) for x in street_alt or [] if x], "unit": str(unit) if unit else None,
        "postcode": pc, "city": city, "city_alt": [str(x) for x in city_alt or [] if x],
        "region": region, "lat": _f(lat), "lon": _f(lon), "source": source, "license": license, "updated": updated,
        "distance_m": distance_m,
    }


def _f(v: Any) -> float | None:
    try:
        return None if v in (None, "") else round(float(v), 7)
    except (TypeError, ValueError):
        return None


def record(c: dict) -> dict:
    """Candidate -> the NAD record shape of the API response (VerifyResponse.best_match / candidates)."""
    return {
        "uuid": c.get("uuid"), "formatted": c["formatted"], "address_number": c.get("number"),
        "street": c.get("street"), "street_parts": {}, "subaddress": {"unit": c.get("unit")} if c.get("unit") else {},
        "city": c.get("city"), "county": None, "state": c.get("region"), "zip": c.get("postcode"),
        "lat": c.get("lat"), "lon": c.get("lon"), "placement": None, "address_class": None, "updated": c.get("updated"),
        "source": c.get("source"), "license": c.get("license"),
        "match": {k: v for k, v in c["match"].items() if k not in ("number", "place")},
    }
