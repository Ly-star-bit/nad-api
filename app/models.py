"""Request / response schemas (drive the OpenAPI docs at /docs)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Status = Literal[
    "verified",        # house number + street (+ unit if given) found in NAD
    "unit_not_found",  # building found, requested unit not recorded in NAD
    "street_only",     # street exists nearby in NAD, house number does not
    "not_found",       # area is covered by NAD but nothing matched
    "not_covered",     # county / area has no NAD data - cannot judge
    "geocode_failed",  # Census geocoder could not place the address
    "invalid_input",   # no house number could be parsed
    "upstream_error",  # a free upstream service failed or timed out
]


class VerifyRequest(BaseModel):
    """Either `address` (one line) or `street` (+ optional city/state/zip) is required."""

    # Length limits: real addresses are well under 200 characters, and fuzzy street matching costs grow with the
    # square of the length (a 100 KB street name against 2000 nearby points would hold the server for a minute).
    address: str | None = Field(None, max_length=300, examples=["1700 W Washington St, Phoenix, AZ 85007"])
    street: str | None = Field(None, max_length=200, examples=["1700 W Washington St"])
    unit: str | None = Field(None, max_length=50, examples=["Apt 2"])
    city: str | None = Field(None, max_length=100, examples=["Phoenix"])
    state: str | None = Field(None, max_length=50, examples=["AZ"])
    zip: str | None = Field(None, max_length=20, examples=["85007"])
    lat: float | None = Field(None, ge=-90, le=90, description="Skip geocoding and search around this point")
    lon: float | None = Field(None, ge=-180, le=180)
    zip_fallback: bool = Field(
        False, description="If the Census geocoder fails, search the whole ZIP (ZCTA) area in NAD. Slower (5-20 s)."
    )
    overture_fallback: bool = Field(
        True, description="When NAD cannot confirm the address (not_covered / not_found / street_only / "
        "upstream_error), retry against Overture Maps addresses (NAD + OpenAddresses county/city data)."
    )
    max_candidates: int = Field(5, ge=1, le=50)

    @model_validator(mode="after")
    def _check(self) -> "VerifyRequest":
        if not (self.address or self.street):
            raise ValueError("either 'address' or 'street' is required")
        if (self.lat is None) != (self.lon is None):
            raise ValueError("'lat' and 'lon' must be given together")
        return self


class BatchRequest(BaseModel):
    addresses: list[VerifyRequest] = Field(..., min_length=1)


class MatchInfo(BaseModel):
    score: float
    street_score: float
    number_exact: bool
    zip_match: bool | None
    city_match: bool | None
    unit_match: bool | None
    distance_m: float | None


class NadRecord(BaseModel):
    model_config = ConfigDict(extra="allow")

    uuid: str | None
    formatted: str
    address_number: str | None
    street: str | None
    street_parts: dict[str, str | None]
    subaddress: dict[str, str | None]
    city: str | None
    county: str | None
    state: str | None
    zip: str | None
    lat: float | None
    lon: float | None
    placement: str | None
    address_class: str | None
    updated: str | None
    source: str | None = Field(description="NAD source agency, or 'overture:<dataset>' for fallback records")
    license: str | None = Field(None, description="Overture records only: license of the underlying dataset")


class ScoredRecord(NadRecord):
    match: MatchInfo


class Coverage(BaseModel):
    method: Literal["point", "county_name"] = Field(description="point-in-polygon, or county-name fallback")
    partial: bool = Field(False, description="sub-areas of this county have a different status")
    county: str | None
    state: str | None
    state_name: str | None = None
    status_code: int | None
    status: str
    in_nad: bool
    data_date: str | None = None


class FallbackInfo(BaseModel):
    provider: Literal["overture"]
    release: str | None = Field(description="Overture release that was queried, e.g. 2026-09-23.1")
    triggered_by: str = Field(description="NAD status that triggered the fallback")
    status: Status | None = Field(description="verdict from Overture data alone")
    adopted: bool = Field(description="true when the Overture verdict replaced the NAD one")
    error: str | None = None


class VerifyResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    status: Status
    verified: bool
    match_level: Literal["unit", "address", "street"] | None
    confidence: float
    data_source: Literal["nad", "overture"] | None = Field(description="dataset the final verdict comes from")
    nad_status: Status | None = Field(description="verdict from NAD alone (before any fallback)")
    input: str
    parsed: dict
    geocode: dict | None
    coverage: Coverage | None
    best_match: ScoredRecord | None
    candidates: list[ScoredRecord]
    suggestions: list[ScoredRecord]
    fallback: FallbackInfo | None
    notes: list[str]
    disclaimer: str
    timing_ms: dict[str, float]


class BatchItem(BaseModel):
    index: int
    result: VerifyResponse | None = None
    error: str | None = None


class BatchResponse(BaseModel):
    count: int
    summary: dict[str, int]
    results: list[BatchItem]


class NearResponse(BaseModel):
    lat: float
    lon: float
    radius_m: float
    count: int
    truncated: bool
    results: list[NadRecord]


class OvertureNearResponse(NearResponse):
    release: str


class CoverageResponse(BaseModel):
    query: dict
    coverage: Coverage | None
