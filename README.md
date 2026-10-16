# NAD Address Verification API

基于 **USDOT National Address Database（NAD）** 的免费美国地址校验接口（FastAPI），
NAD 查不到时用 **Overture Maps 地址数据** 兜底。**欧洲和日本**的地址查各国官方地址登记（见[欧洲和日本](#欧洲和日本)）。
不需要下载 40 GB 全量数据，也不需要任何 API Key / 账号 —— 所有上游都是公开免费服务。

| 上游 | 用途 | 说明 |
|---|---|---|
| NAD FeatureServer（USDOT 托管在 ArcGIS Online） | 9,792 万条权威地址点 | 2026-06-30 编译版，季度更新 |
| NAD County Participation Status | 判断该县/市是否已接入 NAD | 区分“地址不存在”与“该地区无数据” |
| US Census Geocoder | 地址 → 经纬度 + 标准化 + 县 FIPS | TIGER/Line，免费无 Key |
| Census TIGERweb ZCTA | ZIP 区域范围（仅 `zip_fallback` 时使用） | |
| **Overture Maps addresses**（AWS 公开 S3 上的 GeoParquet，DuckDB 直接读） | 兜底：美国约 1.25 亿条，含 NAD + OpenAddresses 的县/市原始数据 | 自动使用最新 release，每天检查一次 |

## 为什么是“先地理编码、再空间查询”

逆向 NAD 官方查看器（usdot.maps.arcgis.com 的 Instant Apps portfolio）后确认：官方页面只有参与状态/覆盖率专题图，
没有隐藏的地址检索接口；唯一可用的是公开的 FeatureServer `query`。实测该图层 **只对 OBJECTID 和 Shape 建了索引**：

- `where=State='CA' AND Add_Number=1600 ...` 这类纯属性查询：55 s 后服务端超时（返回 HTTP 200 + `error`）
- 空间查询（点 + 半径）+ `Add_Number = N`：约 0.3–2 s

所以流程是：

```
输入地址 → 解析(门牌/单元/ZIP) → Census 地理编码 → NAD 空间查询(300 m → 1500 m, Add_Number=N)
        → 本地比对街道名(USPS/FGDC 归一化)/单元/ZIP/城市 → 县覆盖状态 → NAD 结论
        → NAD 结论为 not_covered / not_found / street_only / upstream_error 时：
          Overture 按 bbox(1500 m) + 门牌查询 → 同一套比对逻辑 → 结论更好则采用
```

几处细节：

- **跳过无用的 1500 m 搜索**：300 m 内没找到门牌时，先拉 300 m 内所有点（同时用来给出同街门牌建议）。如果这条街
  在门牌两侧都已经有门牌（比如要查 1716，附近有 1700 和 1818），那 1716 只可能在两者之间、也就在 300 m 内，
  1500 m 搜索只会找到别的街上的同号门牌，直接跳过。曼哈顿这类密集区 1500 m 搜索要 5–18 s。
- **街道比对**：编号街道必须号码一致（`5th Ave` 和 `3rd Ave` 按字符相似度是 0.8，曾被判成同一条街）；
  同名不同类型（`NW 5th St` / `NW 5th Ave`，网格城市里是两条交叉的街）不算匹配。
- **Overture 只做能改变结论的查询**：NAD 已经是 `street_only` 时，Overture 只查门牌，不再做街道探测
  （探测最多得到同样的 `street_only`，而同级结论不会替换 NAD 的结果）。
- **同一时刻的相同上游请求合并成一次**（批量里重复的地址、相邻地址的同一个县），结果照常进缓存。
- **输入清洗**：全角逗号/数字/空格（中文输入法）、单元格内换行、末尾的国家（`, USA`）、`2nd Floor` 这类序数楼层、
  `123-A` 门牌、ZIP 单独一段（Census 的 `CITY, ST, ZIP` 写法）、`Bldg 2 Apt 5` 这类多个单元标识、`PMB`、州全称（`Arizona`）、
  被 Excel 去掉前导 0 的 ZIP（`MA 2108` → `02108`，只在补 0 后的 ZIP 属于该州时才补）、单元号前导 0（`Unit 0012` = `12`）、
  楼栋和单元连写（`Apt B201` 对应 Building B + Unit 201）；
  `Pier Ave`、`Space Park Dr` 这类含单元关键词的街名不会被误当成单元。PO Box 返回 `invalid_input` 并说明原因。
- **Census 匹配到多个地址**（如只写 `100 Main St, Springfield`，有 7 个州的 Springfield）：只核对第一个，
  `geocode.match_count` / `geocode.alternatives` 给出其他候选，`notes` 提示补上州或 ZIP。

### Overture 兜底

NAD 只覆盖约 2/3 的县（加州大部分县、Irvine、LA、旧金山等都没有）。Overture 的美国地址除 NAD 外还汇集了
OpenAddresses 里各县/市的原始数据，正好补上这些空白。实测：

| 地址 | NAD | 最终结果 |
|---|---|---|
| 1600 Amphitheatre Pkwy, Mountain View | `not_covered` | `verified`（`overture:us/ca/santa_clara`） |
| 108 Bryant St Unit 32, Mountain View | `not_covered` | `verified`，精确到单元 |
| 200 N Spring St, Los Angeles | `not_covered` | `verified`（`overture:us/ca/los_angeles`） |
| 1700 W Washington St, Phoenix（模拟 NAD 服务挂掉） | `upstream_error` | `verified`（`overture:NAD`） |

- 只有 Overture 结论**比 NAD 更好**时才采用（`verified > unit_not_found > street_only > not_found > not_covered`），
  采用时 `confidence × 0.9`，`data_source="overture"`，`nad_status` 保留 NAD 原结论，`fallback` 字段说明过程。
- 每条 Overture 记录带 `source`（如 `overture:us/ca/santa_clara`、`overture:NAD`）和 `license`（底层数据集许可证，
  对外展示时按 [Overture 署名要求](https://docs.overturemaps.org/attribution) 处理）。
- 延迟：第一次查询要读 64 个 Parquet 文件的元数据（约 43 MB），服务启动时会在后台预热（单独的 180 s 超时，失败会在
  1 分钟、5 分钟后重试）；预热完成后每次 0.3–2 s。离 us-west-2 远、CPU 少的服务器上预热更慢，
  DuckDB 默认开 16 个线程并行下载（`OVERTURE_THREADS`）。
- 没有位置就无法兜底：纯 `geocode_failed`（无 ZIP）不会查 Overture —— 无 bbox 的属性查询要扫 27 GB。
  带 `zip_fallback=true` 时用 ZIP 范围作为 bbox。

## 欧洲和日本

请求带 `country`（ISO 代码或国名，如 `DE`、`Germany`、`德国`），或者地址末尾写了国家名（`..., 10117 Berlin, Germany`），
或者是日文地址时，按该国的数据源核对；都没有时按美国处理（地址末尾的 `DE`、`MD`、`Georgia` 是美国州，不会被当成国家）。
`GET /countries` 列出支持的国家和各自的数据源。返回结构和美国一样，另带 `country` 和 `provider`
（数据源名称、`authority`、许可证和需要展示的署名）。

每个国家按顺序问数据源，前一个出错（或在该地区没有数据）才问下一个：

| 国家 | 数据源 | 说明 |
|---|---|---|
| 法国 | BAN（IGN Géoplateforme 地理编码） | 不存在的门牌返回街道，不会给邻近门牌 |
| 荷兰 | BAG（PDOK Locatieserver） | 有邮编时用精确过滤；字母和附加号（`8-H`、`1A`）按单元核对 |
| 比利时 | 弗拉芒：Digitaal Vlaanderen 地址登记（含 bus 单元号）；瓦隆：ICAR geocodeWS | 按邮编分区。布鲁塞尔的 UrbIS 限制商业使用，改用 Overture 里的联邦 BeST 数据 |
| 卢森堡 | geoportail.lu（BD-Adresses） | |
| 瑞士 | 联邦建筑地址目录（geo.admin.ch 搜索） | 不存在的门牌会返回邻近门牌，作为同街建议 |
| 奥地利 | BEV 地址登记（地籍查看器背后的搜索） | 不是公开文档的接口，失效时自动改用 Overture |
| 西班牙 | CartoCiudad（IGN） | 邮编不能放进查询，单独比对 |
| 挪威 | Kartverket 地址登记 | 字母属于门牌（`11A`），单元号是 H 编号（`H0201`） |
| 芬兰 | Syke Ryhti 建筑地址登记 | 芬兰语或瑞典语街名；门牌区间（`11-13`）包含 12 |
| 冰岛 | HMS 地址登记（WFS） | 需要冰岛字母 |
| 爱沙尼亚 | In-ADS（Maa- ja Ruumiamet） | 含公寓号（`Narva mnt 7-1`） |
| 拉脱维亚 | VZD 国家地址登记（data.gov.lv） | 需要拉脱维亚字母；不带时自动改用 Overture |
| 波兰 | PRG（GUGiK） | 需要城市。机构的邮政编码常和登记里的不同，邮编不一致只作提示 |
| 捷克 | RÚIAN（ČÚZK） | `151/1a`（建筑号/方位号）写哪一个都能对上 |
| 斯洛文尼亚 | GURS 门牌登记 | 没有街名的村庄用村名代替街名 |
| 德国、意大利、葡萄牙、丹麦、立陶宛、斯洛伐克、克罗地亚、塞尔维亚、列支敦士登、法罗群岛 | Overture Maps（这些国家的官方开放地址登记），先用 Photon（OpenStreetMap 搜索）找到街道位置 | 德国缺巴伐利亚，那里改用 OpenStreetMap |
| 英国、爱尔兰、瑞典、希腊、匈牙利、罗马尼亚等 | OpenStreetMap（Photon）；英国邮编另用 postcodes.io 核对 | 这些国家没有免费的官方门牌接口。OSM 社区数据，找到门牌算核实（置信度 0.75），找不到门牌**不算证据**：返回 `not_covered`（无法判断），只有连街道都找不到才是 `not_found` |
| 日本 | 国土地理院住所検索 + 日本邮政邮编表 | 见下 |

所有数据源找到“东西”都不直接算数：门牌（含字母）、街道名、邮编都和输入重新比对，因为多数服务对不存在的门牌会返回
街道或邻近门牌。街道名比对会去掉重音、展开各语言的缩写（`Str.`、`bd`、`ul.`、`gt` 等），罗马数字、数字和数词视为相同
（意大利登记写 `Via Venti Settembre`，用户写 `Via XX Settembre`），双语街名（布鲁塞尔 `Grote Markt - Grand-Place`）分开比。
邮编不一致时照样核实，但标出（和美国的 ZIP 一样，页面会提示正确邮编）。

**日本**：地址是“町 + 丁目 + 番（街区）+ 号（建筑）”，国土地理院返回它知道的最深一级，比对规则：

- 号也对上：`verified`；只对上番（东京都心大部分地方没有号的数据）：`verified` + `match_level: "block"`，页面显示“已核实到番”，
  号无法核实；丁目存在但没有这个番：`street_only`；町存在但没有这个丁目：`not_found`；
  乡下用地番（`978-6`）而国土地理院只到町名的地方：`not_covered`。
- 罗马字地址（`2-8-1 Nishi-Shinjuku, Shinjuku-ku, Tokyo 163-8001`）国土地理院读不了：用日本邮政的邮编表（含罗马字）
  把邮编或町名、区市名换成汉字再查。邮编表启动时下载到 `NAD_DATA_DIR`（默认 `data/`，约 3 MB），每月更新。
- 带邮编时核对邮编是否属于这个町，不属于时给出正确邮编。
- 房间号、楼名（`101号室`、`5F`、`-101`）会去掉。
- 国土地理院这个检索接口是它自己地图网站用的，没有承诺长期提供。

**注意**：以上都是在美国 IP 上测试的。部署到其他地区后请跑一遍 `scripts/smoke_test.py`（含每个国家的用例），
看哪些数据源从服务器上连不通。斯洛伐克、塞尔维亚、立陶宛的官方接口只对部分国家的 IP 开放，所以这三个国家直接用 Overture。
Photon 公共实例只要求“合理使用”（并发限制在 2），大批量使用请自建 Photon 并设置 `PHOTON_URL`；
公共 Nominatim 的使用政策禁止地址校验类服务，所以没有使用。

## 启动

```bash
./run.sh                         # 默认 0.0.0.0:8000，2 workers
# 或
uv venv && uv pip install -r requirements.txt
.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000

# Docker
docker build -t nad-api . && docker run -p 8000:8000 nad-api
```

交互文档：`http://<host>:8000/docs`

网页：
- `http://<host>:8000/`：单条校验。结果页列出匹配记录、逐项比对、县覆盖和 Overture 补查情况；地图上标出匹配记录、
  Census 定位点和同街最近的门牌（带门牌号），同街门牌、候选地址点一下就能复查。
  `/` 按 `Accept` 头区分：浏览器拿到页面，`curl` 等 API 客户端仍拿到原来的 JSON。
- `http://<host>:8000/batch`：批量校验，一次最多 500 行。可以每行粘贴一个地址、从 Excel 复制多列，或上传 CSV
  （表头 `address`，或 `street` / `unit` / `city` / `state` / `zip`，也认中文表头；UTF-8 / GBK 均可）。
  订单导出里带前缀的列名（`Shipping Address 1`、`Ship City`、`收件人地址`、`收货城市` 等）也能识别。
  各国地址可以混在一起：有 `country`（“国家”）列时按它，否则按地址末尾的国家名或日文识别，其余按页面上选的默认国家。
  页面按每批 20 条调用 `/v1/verify/batch?stream=true`，每条查完立刻显示（不用等同批最慢的那条），可停止、继续、
  重试失败行；地图按结论着色显示每一行（没有匹配记录时用 Census 定位点），跟随筛选，点圆点看详情。
  导出 CSV 时原表的列原样保留，后面追加 `verify_status`、`verify_verdict`、`verify_confidence`、`verify_matched_address`、
  `verify_country`、`verify_nearest_number`（门牌不存在时同街最近的门牌）、`verify_census_matches`（Census 匹配到多个地址时的个数）、`verify_zip_should_be`（填的 ZIP 和记录不一致时，
  记录里的 ZIP；地址存在但 ZIP 填错会让包裹分错，结果行里也会标出来）等结果列。
  有 10 行以上结果还没导出就离开页面时，浏览器会提示。
- 页头的状态点显示各上游最近一次调用的结果，例如服务器连不上 Census 时会显示“Census 地理编码不可用”，
  鼠标悬停看具体错误。

所有响应都做 gzip 压缩（一批 10 条结果 48 KB → 6 KB，leaflet.js 147 KB → 43 KB），服务器离用户远时差别明显。

页面文件在 `app/static/`（`index.html`、`batch.html`，共用 `style.css`、`common.js`、`map.js`）。地图用 Leaflet 1.9.4
（BSD-2，随项目放在 `app/static/vendor/leaflet/`，用到时才加载），唯一的外部资源是 OpenStreetMap 的底图瓦片：
内部工具的用量符合 [OSM 瓦片使用政策](https://operations.osmfoundation.org/policies/tiles/)，对外大量使用或访问 OSM 较慢时，
改 `map.js` 开头的 `TILES` / `ATTRIBUTION` 换成自己的瓦片源。瓦片加载失败时地图只剩灰底，标记点照常显示。
开启 `NAD_API_KEYS` 时页面会提示填写 Key（只存在浏览器本地）。

## 接口

### `GET /v1/verify` / `POST /v1/verify`

```bash
curl -G localhost:8000/v1/verify --data-urlencode "address=980 9th St Ste 1750, Sacramento, CA 95814"

curl -X POST localhost:8000/v1/verify -H 'content-type: application/json' \
  -d '{"street":"11 W Washington St","unit":"Suite 240","city":"Phoenix","state":"AZ","zip":"85003"}'
```

参数（长度上限：`address` 300、`street` 200 个字符，超出返回 422）：`address`（单行）或 `street`+`city`/`state`/`zip`（结构化）；可选 `country`（见[欧洲和日本](#欧洲和日本)，
不支持的国家返回 422）、`unit`、`lat`+`lon`（跳过地理编码）、
`zip_fallback`（Census 编码失败时在整个 ZIP 区域搜索，较慢 3–20 s）、`overture_fallback`（默认 `true`）、`max_candidates`。

返回要点：

```jsonc
{
  "status": "verified",          // 见下表
  "verified": true,
  "match_level": "unit",         // unit | address | block（日本：只核实到番）| street | null
  "confidence": 0.997,
  "data_source": "nad",          // nad | overture | null —— 最终结论来自哪个数据集；美国以外是数据源代码（fr_ban、jp_gsi、osm …）
  "country": "US",               // 美国以外另带 "provider": {"name", "authority", "license", "attribution"}
  "nad_status": "verified",      // 仅 NAD 的结论（兜底前）
  "fallback": null,              // 触发兜底时：{"provider":"overture","release":"2026-09-23.1","triggered_by":"not_covered","status":"verified","adopted":true}
  "best_match": {                // NAD 官方记录（或 Overture 记录，结构相同，另带 license）
    "uuid": "0a94ab67-b07b-46de-9d67-0717f9b42e59", "formatted": "980 9TH Street, STE 1750, SACRAMENTO, CA 95814",
    "lat": 38.581349, "lon": -121.494763, "placement": "Unknown", "source": "Sacramento County CA", "updated": "2025-03-11",
    "match": {"street_score": 1.0, "number_exact": true, "zip_match": true, "unit_match": true, "distance_m": 47.2}
  },
  "coverage": {"county": "Sacramento", "state": "CA", "status": "Data in the NAD", "in_nad": true},
  "geocode": {"source": "census", "matched_address": "980 9TH ST, SACRAMENTO, CA, 95814", "county_fips": "06067"},
  "suggestions": [], "notes": [], "timing_ms": {...}
}
```

| status | 含义 |
|---|---|
| `verified` | 门牌 + 街道（+ 单元）存在（看 `data_source` 是 NAD 还是 Overture） |
| `unit_not_found` | 楼栋存在，但该单元没有记录（很多数据源不录单元，`notes` 会列出已有单元） |
| `street_only` | 附近有这条街，但没有这个门牌；`suggestions` 给出同街最接近的门牌 |
| `not_found` | 该地区有数据覆盖，但无匹配 —— 较大概率是错误/虚构地址 |
| `not_covered` | NAD 和 Overture 在该地区都没有数据，无法判断 |
| `geocode_failed` | Census 找不到这个地址（或拒绝了输入）；带 ZIP 可加 `zip_fallback=true` 重试 |
| `invalid_input` | 解析不出门牌号（PO Box 也在此列，`notes` 会说明） |
| `upstream_error` | 免费上游超时/故障，包括连不上 Census 地理编码 —— 这种情况不代表地址有问题 |

### `POST /v1/verify/batch`

`{"addresses": [ {...VerifyRequest}, ... ]}`，单次最多 100 条，并发 5（可配）。返回每条结果 + `summary` 统计。

加 `?stream=true` 时返回 `application/x-ndjson`：每查完一条输出一行 `{"index": i, "result": {...}}`（顺序按完成先后），
客户端断开后剩下的地址不再查询。

### `GET /countries`

支持的国家（中文名）和各自按顺序使用的数据源。

### `GET /v1/nad/near?lat=&lon=&radius=100&limit=20&number=`

逆地理：返回坐标附近的 NAD 地址点（按距离排序），半径上限 1500 m。

### `GET /v1/overture/near?lat=&lon=&radius=100&limit=20&number=`

同上，但查 Overture（返回 `release`、每条记录的 `source` / `license`）。

### `GET /v1/nad/coverage?lat=&lon=` 或 `?state=CA&county=Orange`

县参与状态。注意该图层同时含“整县”和“城市级”多边形（如 Orange County 整体无数据，但 Costa Mesa 有），
因此按点查询时取最小的包含多边形；点查询失败（如纽约县）时退回按县名查询。

### `GET /health` / `GET /health?deep=true`

`upstreams` 列出每个上游（`census_geocoder`、`nad`、`nad_coverage`、`tigerweb`、`overture`）最近一次真实调用是否成功、
错误信息和距今秒数；任一失败时 `status` 为 `degraded`。不带参数时不发任何上游请求；`deep=true` 会立即（不走缓存）
请求每个上游，并返回 NAD 点数/延迟、Overture 当前 release。其他国家的数据源单独列在 `intl_upstreams`，
它们出错不会把整个服务标成 `degraded`；`japanpost` 显示日本邮编表是否已加载。

## 配置（环境变量）

| 变量 | 默认 | 说明 |
|---|---|---|
| `NAD_API_KEYS` | 空（不鉴权） | 逗号分隔；设置后 `/v1/*` 需要请求头 `X-API-Key` |
| `NAD_RATE_LIMIT_PER_MIN` | 120 | 每 IP 每分钟请求数，0 关闭（保护免费上游，别把它打挂） |
| `NAD_TRUST_PROXY` | 0 | 部署在 nginx/Caddy/Cloudflare 后面时设为 1，按 `X-Forwarded-For` 限流 |
| `NAD_CORS_ORIGINS` | `*` | |
| `NAD_SEARCH_RADII_M` | `300,1500` | 搜索半径阶梯 |
| `NAD_STREET_MATCH_THRESHOLD` | 0.8 | 街道名相似度阈值 |
| `NAD_CACHE_TTL_S` | 86400 | 上游响应内存缓存（NAD 季度更新） |
| `NAD_HTTP_TIMEOUT` | 25 | 上游超时秒数 |
| `NAD_MAX_BATCH_SIZE` / `NAD_BATCH_CONCURRENCY` | 100 / 5 | |
| `OVERTURE_ENABLED` | 1 | 0 关闭 Overture 兜底 |
| `OVERTURE_RELEASE` | 空（自动最新） | 固定某个 release，如 `2026-09-23.1`。注意旧 release 会被 Overture 从 S3 删除 |
| `OVERTURE_SEARCH_RADIUS_M` | 1500 | 兜底搜索范围 |
| `OVERTURE_TIMEOUT_S` | 30 | 单次 DuckDB 查询超时（超时会中断查询） |
| `OVERTURE_MAX_CONCURRENCY` | 4 | 同时进行的 Overture 查询数 |
| `OVERTURE_MEMORY_LIMIT` | 1GB | DuckDB 内存上限 |
| `OVERTURE_THREADS` | 16 | DuckDB 线程数。读 S3 时线程主要在等网络，比 CPU 核数多更快（冷启动实测：32 线程 2.3 s，1–2 线程 9–26 s） |
| `OVERTURE_WARMUP_TIMEOUT_S` | 180 | 启动预热查询的超时（后台进行，不影响用户请求；用户请求仍用 `OVERTURE_TIMEOUT_S`） |
| `PHOTON_URL` | `https://photon.komoot.io` | OpenStreetMap 搜索（德国、意大利等定位，英国等 OSM 国家核对）。大批量使用请自建 |
| `PHOTON_CONCURRENCY` / `INTL_CONCURRENCY` | 2 / 4 | 每个海外数据源同时进行的请求数 |
| `NAD_DATA_DIR` | `data/` | 下载的参考数据（日本邮政邮编表） |
| `FR_BAN_URL`、`NL_PDOK_URL`、`GSI_URL` 等 | 各官方地址 | 每个数据源的地址都可以覆盖（见 `app/config.py`） |

缓存、限流和 DuckDB 连接都是进程内的：多 worker 时各自独立（每个 worker 各自预热 Overture、各占最多
`OVERTURE_MEMORY_LIMIT` 内存）；需要共享缓存/限流可换 Redis。

## 测试

```bash
.venv/bin/python -m pytest -q tests                      # 离线：解析/归一化、判定流程、Overture 映射、各国数据源的响应解析、网页路由、上游客户端（111 个）
.venv/bin/python scripts/smoke_test.py http://127.0.0.1:8000   # 在线：45 个真实地址端到端（美国 17 个，含 Overture 兜底；欧洲和日本 28 个）
```

## 已知局限

- **覆盖不全**：NAD 依赖各州/县自愿提交，约 600 个县显示 “No statewide data”（如加州大部分县）。
  Overture 兜底能补上很大一部分，但仍有两边都没数据的地区（返回 `not_covered`）。
- **Overture 不是权威源**：它是汇总数据，`confidence` 已打 9 折；`source=overture:NAD` 的记录用的 NAD 版本未公开，
  可能与官方 r23 服务不一致，属正常现象。坐标为 float32（约 1 m 精度）。
- **Census 会“纠正”方向**：例如把 `200 N Spring St` 匹配成 `200 S SPRING ST`。匹配时以用户输入的方向为准，并在 `notes` 中提示。
- **NAD ≠ USPS 可投递**：`verified` 只说明物理地址点存在。需要可投递性请再接 USPS/CASS 类服务。
- 单元号覆盖取决于数据提供方，很多地区只有楼栋点，因此有 `unit_not_found` 这一中间态。
- 依赖 Census 地理编码定位；新建地址可能不在 TIGER 中 → 用 `zip_fallback` 或直接传 `lat`/`lon`。
- 威斯康星网格门牌（`N95W18000 ...`）等特殊格式暂不解析。
- 上游是公共免费服务，冷查询偶尔 5–15 s；生产环境建议保留缓存与限流。
- 若需高并发/离线/SLA，仍建议按州下载 NAD 自建 PostGIS（可沿用本项目的解析与匹配逻辑）。
- **欧洲和日本**：英国、爱尔兰、瑞典等没有免费官方门牌数据的国家只能靠 OpenStreetMap，找不到门牌时无法判断；
  日本大部分城区只能核实到番；奥地利和日本用的是没有公开承诺的接口，可能变动。
