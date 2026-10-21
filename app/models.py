"""Request / response schemas (drive the OpenAPI docs at /docs)."""

from __future__ import annotations

import json
from pathlib import Path
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

STATUS_DOC = """结论：

| 值 | 含义 |
|---|---|
| `verified` | 门牌 + 街道（填了单元时含单元）有记录 |
| `unit_not_found` | 楼栋有记录，填的单元没有（很多数据源不录单元，`notes` 列出已有单元）；`verified` 仍为 true |
| `street_only` | 附近有这条街，没有这个门牌；`suggestions` 是同街最接近的门牌 |
| `not_found` | 该地区有数据，但没有匹配 —— 较大概率是错误或虚构的地址 |
| `not_covered` | 该地区没有数据，无法判断（不代表地址有问题） |
| `geocode_failed` | 定位不到这个地址（美国：Census 找不到，按 ZIP / 城市范围也查不到这条街） |
| `invalid_input` | 解析不出门牌号（PO Box、邮政信箱、自提柜也在此列） |
| `upstream_error` | 上游数据源超时或故障 —— 不代表地址有问题，可以重试 |"""

# A real response (1700 W Washington St, Phoen, AZ 85007), shown as the example in /docs: on the route, as a
# model-level example would have its keys sorted (status after best_match)
VERIFY_EXAMPLE = json.loads((Path(__file__).parent / "verify_example.json").read_text(encoding="utf-8"))

CONFIDENCE_DOC = ("置信度 0–1，就是 `best_match.match.score`，再按情况打折：美国 NAD 查不到、用 Overture 补查到的 × 0.9；"
                  "单元没找到 × 0.9；门牌后缀不同 × 0.85（美国）。美国以外还有：邮编和记录不一致 × 0.9、地址里没写城市和"
                  "邮编 × 0.9、门牌落在记录的号段内（12 在 10-14 里）× 0.95、这个门牌只有带附加号的记录（Damrak 8 只有 "
                  "8-A、8-B）× 0.9。`street_only` 固定 0.3，`not_found` 为 0.05，其余没有匹配的结论为 0。日本：到号 0.95、"
                  "到番 0.7、只到町 0.3，邮编不一致再 × 0.9。打折的原因都写在 `notes` 里。")

ADDRESS_DOC = ("一行完整地址，最长 300 个字符。美国按 `门牌 街道 单元, 城市, 州 ZIP`；欧洲按当地写法"
               "（`Unter den Linden 77, 10117 Berlin`）；日本按日文写法（`〒100-8914 東京都千代田区永田町1-6-1`）。")

COUNTRY_DOC = ("国家：ISO 代码或国名（英文、本国文字或中文都可以，如 `US`、`DE`、`Germany`、`Deutschland`、`德国`）。"
               "填了就按它查，不支持的国家返回 422。不填时从地址里识别：地址末尾写了国名（`..., 10117 Berlin, Germany`）"
               "按那个国家，日文地址按日本，都没有按美国。")


class VerifyRequest(BaseModel):
    """一条地址：一行完整地址，可选国家。"""

    # Length limit: real addresses are well under 200 characters, and fuzzy street matching costs grow with the
    # square of the length (a 100 KB street name against 2000 nearby points would hold the server for a minute).
    address: str = Field(
        ..., min_length=1, max_length=300,
        examples=["1700 W Washington St, Phoenix, AZ 85007"],
        description=ADDRESS_DOC)
    country: str | None = Field(None, max_length=60, examples=["US", "DE", "Japan"], description=COUNTRY_DOC)


class BatchRequest(BaseModel):
    addresses: list[VerifyRequest] = Field(..., min_length=1, description="每条和单条接口的请求体相同；单次最多 100 条")


class MatchInfo(BaseModel):
    """这条记录和输入逐项比对的结果。`null` 表示无法比较（输入或记录缺这一项，或数据源不提供）。"""

    score: float = Field(description="综合匹配分 0–1：美国 = 0.55 × 街道 + 0.15 × 门牌 + 0.10 × ZIP + 0.10 × 单元 "
                                     "+ 0.10 × 距离；记录排序和 `confidence` 都基于它")
    street_score: float = Field(description="街道名相似度 0–1（缩写、方向词已归一化：W = West，St = Street）；"
                                            "≥ 0.8 才算同一条街")
    number_exact: bool | None = Field(description="门牌号（含后缀，如 12B）完全一致；日本数据只到番时为 null")
    zip_match: bool | None = Field(description="邮编一致（美国比前 5 位）；输入或记录没有邮编时为 null")
    city_match: bool | None = Field(description="城市一致；美国以外记录的城市常是区名（Berlin 的 Mitte），比不了时为 null")
    unit_match: bool | None = Field(description="单元一致；输入没写单元时为 null")
    distance_m: float | None = Field(description="记录离定位点的距离（米）；没有定位点（按 ZIP / 城市范围查，或日本）时为 null")


class NadRecord(BaseModel):
    """一条地址记录。美国是 NAD 记录（或 Overture 记录，结构相同）；美国以外各数据源的记录也套用这个结构，
    没有的字段为 null 或空对象。美国记录另外带着 NAD 的原始字段：`address_number_int`、`address_number_prefix`、
    `address_number_suffix`、`postal_city`（邮政城市名）、`municipality`、`zip4`、`landmark`、`address_type`、
    `lifecycle`、`delivery_type`、`address_authority`（地址主管机构）。"""

    model_config = ConfigDict(extra="allow")

    uuid: str | None = Field(description="记录在数据源里的 ID（NAD 的 UUID、Overture 的 id）")
    formatted: str = Field(description="整条地址，按数据源的写法")
    address_number: str | None = Field(description="门牌号（日本：番或番-号）")
    street: str | None = Field(description="街道全名（日本：町名 + 丁目）")
    street_parts: dict[str, str | None] = Field(description="NAD 的街道分段：pre_directional（W）、name（Washington）、"
                                                            "post_type（Street）等；美国以外为空")
    subaddress: dict[str, str | None] = Field(description="单元：building、floor、unit、room，以及原文 subaddress")
    city: str | None
    county: str | None = Field(description="县（仅美国）")
    state: str | None = Field(description="州 / 省 / 地区代码")
    zip: str | None = Field(description="邮编")
    lat: float | None
    lon: float | None
    placement: str | None = Field(description="NAD：坐标点放在哪（屋顶、入口、地块中心等）")
    address_class: str | None = Field(description="NAD 的地址类别")
    updated: str | None = Field(description="记录更新日期")
    source: str | None = Field(description="提供这条记录的机构：NAD 是提交数据的州 / 县；Overture 记录是 "
                                           "`overture:<数据集>`；日本是 `国土地理院`")
    license: str | None = Field(None, description="数据许可（Overture 和美国以外的记录）")


class ScoredRecord(NadRecord):
    match: MatchInfo = Field(description="和输入逐项比对的结果")


class Coverage(BaseModel):
    """NAD 在这个县有没有数据（仅美国）。"""

    method: Literal["point", "county_name"] = Field(description="按定位点所在的多边形查（point），或按县名查（county_name）")
    partial: bool = Field(False, description="这个县里有的城市和县的状态不同（如 Orange County 整体没有数据，Costa Mesa 有）")
    county: str | None
    state: str | None
    state_name: str | None = None
    status_code: int | None = Field(description="NAD 参与状态代码：10、15 表示有数据")
    status: str = Field(description="参与状态原文，如 `Data in the NAD`、`No statewide data`")
    in_nad: bool = Field(description="NAD 里有这个县的数据")
    data_date: str | None = Field(None, description="这个县的数据日期")


class FallbackInfo(BaseModel):
    """第一个数据源没有给出答案、改问下一个数据源时的记录。"""

    provider: str = Field(description="补查用的数据源：美国是 `overture`；美国以外是首选数据源之后接手回答的那个")
    release: str | None = Field(description="补查的 Overture 版本，如 `2026-09-23.1`")
    triggered_by: str = Field(description="为什么补查：美国是 NAD 的结论（not_covered、not_found、street_only 等）；"
                                          "美国以外是前一个数据源的情况（upstream_error、not_covered、not_found）")
    status: Status | None = Field(description="补查数据源自己的结论")
    adopted: bool = Field(description="true：最终结论用的是补查结果")
    error: str | None = Field(None, description="补查失败的原因，或前面哪些数据源没有回答")


class ProviderInfo(BaseModel):
    """美国以外：回答这条地址的数据源。"""

    key: str = Field(description="数据源代码，和 `data_source` 相同")
    name: str
    authority: Literal["official", "overture", "osm"] = Field(
        description="`official`：官方地址登记；`overture`：Overture Maps（转载的官方开放数据）；"
                    "`osm`：OpenStreetMap（社区数据：查到是证据，查不到不能说明地址不存在）")
    url: str
    license: str = Field(description="数据许可")
    attribution: str = Field(description="展示这些数据时需要标注的来源")


class Parsed(BaseModel):
    """地址拆成的字段。已核实（美国、欧洲）时按匹配到的记录拆分：拼错的城市、缩写的街道改成记录里的写法
    （`Phoen` → `Phoenix`，`W Washington St` → `West Washington Street`），并带 `source: "record"`；
    其他情况是输入本身拆出来的。"""

    country: str | None = Field(None, description="ISO 代码")
    state: str | None = Field(None, description="州 / 省（日本：都道府県）")
    city: str | None = Field(None, description="城市（日本：市区町村）")
    zip: str | None = Field(None, description="邮编")
    address1: str | None = Field(None, description="地址 1：门牌 + 街道，按当地顺序（日本：町名 + 番地）")
    address2: str | None = Field(None, description="地址 2：单元、楼层、房间（日本：建筑名、房间号）")
    house_number: str | None = Field(None, description="门牌号")
    street: str | None = Field(None, description="街道")
    unit: str | None = Field(None, description="单元，和 address2 相同")
    source: Literal["record"] | None = Field(None, description="`record`：按匹配到的记录拆分；null：按输入拆分")


class VerifyResponse(BaseModel):
    """核验结果。先看 `status` / `verified` / `confidence`，再看 `best_match`（匹配到的记录）和 `notes`（原因）。"""

    model_config = ConfigDict(extra="allow")

    status: Status = Field(description=STATUS_DOC)
    verified: bool = Field(description="地址存在：`status` 是 `verified` 或 `unit_not_found` 时为 true")
    match_level: Literal["unit", "address", "block", "street"] | None = Field(
        description="核实到哪一级：`unit` 单元、`address` 门牌、`block` 只到番（日本，数据里没有号）、"
                    "`street` 只有街道（`street_only`）；没有匹配时为 null")
    confidence: float = Field(description=CONFIDENCE_DOC)
    data_source: str | None = Field(description="最终结论来自哪个数据集：美国是 `nad` 或 `overture`；"
                                                "美国以外是数据源代码（`fr_ban`、`nl_pdok`、`jp_gsi`、`overture`、`osm` 等，"
                                                "见 `/countries`）；`upstream_error` 时为 null")
    country: str = Field("US", description="按哪个国家核验的（ISO 代码）")
    provider: ProviderInfo | None = Field(None, description="美国以外：回答的数据源、许可和需要标注的来源；美国为 null")
    jp: dict | None = Field(None, description="日本：`parcel` 是番地式地址（不是丁目-番-号）；`go_given` 是输入在番后面"
                                              "写了号或枝番。其他国家为 null")
    nad_status: Status | None = Field(description="美国：只看 NAD 的结论（Overture 补查前）；美国以外为 null")
    input: str = Field(description="请求里的 `address` 原文")
    parsed: Parsed | None = Field(description="地址拆成的字段")
    geocode: dict | None = Field(
        description="定位结果，按 `source` 有几种结构：\n\n"
                    "- `census`（美国）：`lat`、`lon`、`matched_address`、`components`（Census 拆出的街道分段）、"
                    "`county`、`county_fips`、`state`、`zip`、`tiger_line_id`、`match_count`（匹配到多个地址时另有 "
                    "`alternatives`，只核验了第一个）\n"
                    "- `zcta`（美国，Census 定位不到、按 ZIP 范围查）：`zip`、`extent`（范围的经纬度框）\n"
                    "- `city`（美国，按城市范围查）：`city`、`state`、`zips`、`extent`\n"
                    "- `photon` 等（欧洲）：`lat`、`lon`、`matched_address`、`level`\n"
                    "- `gsi`（日本）：`lat`、`lon`、`matched_address`\n\n"
                    "没有定位时为 null")
    coverage: Coverage | None = Field(description="美国：NAD 在这个县有没有数据；美国以外为 null")
    best_match: ScoredRecord | None = Field(description="匹配到的记录，没有匹配时为 null")
    candidates: list[ScoredRecord] = Field(description="搜索范围内同门牌号的记录，按匹配分排序，最多 5 条"
                                                       "（美国包含 `best_match` 本身，如同一栋楼的其他单元）")
    suggestions: list[ScoredRecord] = Field(description="`street_only` 时：同一条街上和输入最接近的门牌，最多 5 条")
    fallback: FallbackInfo | None = Field(description="改问下一个数据源时的记录；没有补查时为 null")
    search_radius_m: float | None = Field(None, description="美国：NAD 最后一次按定位点搜索的半径（米）")
    nad_points_nearby: int | None = Field(None, description="美国：定位点附近 NAD 的地址点数，查过同街门牌时才有")
    notes: list[str] = Field(description="说明（英文）：为什么是这个结论、哪些项不一致、补查情况等")
    disclaimer: str = Field(description="固定说明：核验的是地址点存在，不是 USPS 能投递")
    timing_ms: dict[str, float] = Field(description="各步耗时（毫秒），`total` 是总耗时")


class CountryProvider(BaseModel):
    key: str
    name: str
    authority: Literal["official", "overture", "osm"]
    license: str


class CountryInfo(BaseModel):
    code: str = Field(description="ISO 3166 代码")
    name: str = Field(description="中文名")
    providers: list[CountryProvider] = Field(description="按顺序询问的数据源：前一个失败时才问下一个")


class BatchItem(BaseModel):
    index: int = Field(description="这条在请求 `addresses` 里的位置（从 0 开始）")
    result: VerifyResponse | None = None
    error: str | None = Field(None, description="这条失败的原因（如不支持的国家）；失败时 `result` 为 null")


class BatchResponse(BaseModel):
    count: int
    summary: dict[str, int] = Field(description="每种 `status` 的条数，失败的计入 `error`")
    results: list[BatchItem]


class ErrorDetail(BaseModel):
    detail: str | list[dict] = Field(description="字符串；或参数校验失败（422）时是列表，每项的 `loc` 指出哪个参数、"
                                                 "`msg` 是原因")
