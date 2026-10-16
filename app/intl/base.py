"""What every non-US provider shares: polite HTTP access and the candidate record format."""

from __future__ import annotations

import asyncio
from collections import defaultdict
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from .. import config
from ..upstream import Upstream

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


class Ctx:
    """Per-process access to the upstreams. Each free service gets a small concurrency cap, so a batch of 100
    addresses for one country never hits that country's service with more than a few requests at once."""

    _sems: dict[str, asyncio.Semaphore] = defaultdict(lambda: asyncio.Semaphore(config.INTL_CONCURRENCY))

    def __init__(self, up: Upstream, ov: OvertureClient | None) -> None:
        self.up, self.ov = up, ov

    async def get(self, service: str, url: str, params: dict[str, Any]) -> Any:
        async with self._sem(service):
            return await self.up._get_json(service, url, params)

    def _sem(self, service: str) -> asyncio.Semaphore:
        if service not in self._sems and service in config.INTL_SERVICE_CONCURRENCY:
            self._sems[service] = asyncio.Semaphore(config.INTL_SERVICE_CONCURRENCY[service])
        return self._sems[service]


def cand(kind: str, *, number: Any = None, street: str | None = None, postcode: Any = None, city: str | None = None,
         unit: str | None = None, lat: Any = None, lon: Any = None, id: Any = None, formatted: str | None = None,
         region: str | None = None, street_alt: list[str] | None = None, source: str | None = None,
         license: str | None = None, updated: str | None = None, distance_m: float | None = None) -> dict:
    """A provider record in the shape the verdict (match.py) and the response (core.py) use. kind is 'address' or
    'street' (a street hit, no number)."""
    num = None if number in (None, "") else str(number)
    pc = None if postcode in (None, "") else str(postcode)
    if not formatted:
        line1 = " ".join(p for p in (street, num) if p)
        formatted = ", ".join(p for p in (line1, unit, " ".join(p for p in (pc, city) if p)) if p)
    return {
        "kind": kind, "uuid": None if id is None else str(id), "formatted": formatted, "number": num,
        "street": street, "street_alt": street_alt or [], "unit": unit or None, "postcode": pc, "city": city,
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
        "source": c.get("source"), "license": c.get("license"), "match": {k: v for k, v in c["match"].items() if k != "number"},
    }
