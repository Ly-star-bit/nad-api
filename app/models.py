"""Request / response schemas (drive the OpenAPI docs at /docs)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

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
    """One address as a single line, and optionally its country."""

    # Length limit: real addresses are well under 200 characters, and fuzzy street matching costs grow with the
    # square of the length (a 100 KB street name against 2000 nearby points would hold the server for a minute).
    address: str = Field(..., min_length=1, max_length=300, examples=["1700 W Washington St, Phoenix, AZ 85007"])
    country: str | None = Field(
        None, max_length=60, examples=["US", "DE", "Japan"],
        description="ISO code or name (English, native or Chinese). Empty: the country named in the address "
                    "('..., 10117 Berlin, Germany'), JP for a Japanese address, else US.")


class BatchRequest(BaseModel):
    addresses: list[VerifyRequest] = Field(..., min_length=1)


class MatchInfo(BaseModel):
    score: float
    street_score: float
    number_exact: bool | None = Field(description="null when the source cannot tell (Japan: data only to the 番)")
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
    provider: str = Field(description="'overture' for US addresses; outside the US the provider that answered "
                                      "after the country's first choice failed")
    release: str | None = Field(description="Overture release that was queried, e.g. 2026-09-23.1")
    triggered_by: str = Field(description="US: the NAD status that triggered the fallback; elsewhere why the source "
                                          "before it gave no answer (upstream_error, not_covered, not_found)")
    status: Status | None = Field(description="verdict from the fallback source alone")
    adopted: bool = Field(description="true when the fallback verdict replaced the first one")
    error: str | None = None


class ProviderInfo(BaseModel):
    key: str
    name: str
    authority: Literal["official", "overture", "osm"] = Field(
        description="official register; Overture Maps (official open data republished); OpenStreetMap (community "
                    "data: a match is evidence, a miss is not)")
    url: str
    license: str
    attribution: str


class VerifyResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    status: Status
    verified: bool
    match_level: Literal["unit", "address", "block", "street"] | None = Field(
        description="'block': Japan, the 番 (block) exists but the data has no 号 (building number) to check")
    confidence: float
    data_source: str | None = Field(description="dataset the final verdict comes from: 'nad' / 'overture' in the US, "
                                                "else the provider key (fr_ban, nl_pdok, jp_gsi, overture, osm)")
    country: str = Field("US", description="ISO 3166 code of the country the address was checked in")
    provider: ProviderInfo | None = Field(None, description="outside the US: who answered, its licence and the "
                                                            "attribution to show with the data")
    jp: dict | None = Field(None, description="Japan: {parcel: a 番地 (parcel-number) address rather than 丁目-番-号, "
                                              "go_given: the input has a 号 or branch number after the 番}")
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


class CountryProvider(BaseModel):
    key: str
    name: str
    authority: Literal["official", "overture", "osm"]
    license: str


class CountryInfo(BaseModel):
    code: str = Field(description="ISO 3166 code")
    name: str = Field(description="Chinese name")
    providers: list[CountryProvider] = Field(description="asked in this order: the next only when one fails")


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
