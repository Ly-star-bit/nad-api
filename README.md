# NAD Address Verification API

基于 **USDOT National Address Database（NAD）** 的免费美国地址校验接口（FastAPI），
NAD 查不到时用 **Overture Maps 地址数据** 兜底。
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
- 延迟：首次查询约 3.5 s（DuckDB 缓存 64 个 Parquet 文件的元数据，服务启动时会后台预热），之后 0.6–2 s。
- 没有位置就无法兜底：纯 `geocode_failed`（无 ZIP）不会查 Overture —— 无 bbox 的属性查询要扫 27 GB。
  带 `zip_fallback=true` 时用 ZIP 范围作为 bbox。

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
  页面按每批 10 条串行调用 `/v1/verify/batch`，结果边查边出，可停止、继续、重试失败行；地图按结论着色显示每一行
  （没有匹配记录时用 Census 定位点），跟随筛选，点圆点看详情。导出 CSV 时原表的列原样保留，
  后面追加 `verify_status`、`verify_verdict`、`verify_confidence`、`verify_matched_address` 等结果列。

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

参数：`address`（单行）或 `street`+`city`/`state`/`zip`（结构化）；可选 `unit`、`lat`+`lon`（跳过地理编码）、
`zip_fallback`（Census 编码失败时在整个 ZIP 区域搜索，较慢 3–20 s）、`overture_fallback`（默认 `true`）、`max_candidates`。

返回要点：

```jsonc
{
  "status": "verified",          // 见下表
  "verified": true,
  "match_level": "unit",         // unit | address | street | null
  "confidence": 0.997,
  "data_source": "nad",          // nad | overture | null —— 最终结论来自哪个数据集
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
| `geocode_failed` | Census 无法定位；带 ZIP 可加 `zip_fallback=true` 重试 |
| `invalid_input` | 解析不出门牌号 |
| `upstream_error` | 免费上游超时/故障 |

### `POST /v1/verify/batch`

`{"addresses": [ {...VerifyRequest}, ... ]}`，单次最多 100 条，并发 5（可配）。返回每条结果 + `summary` 统计。

### `GET /v1/nad/near?lat=&lon=&radius=100&limit=20&number=`

逆地理：返回坐标附近的 NAD 地址点（按距离排序），半径上限 1500 m。

### `GET /v1/overture/near?lat=&lon=&radius=100&limit=20&number=`

同上，但查 Overture（返回 `release`、每条记录的 `source` / `license`）。

### `GET /v1/nad/coverage?lat=&lon=` 或 `?state=CA&county=Orange`

县参与状态。注意该图层同时含“整县”和“城市级”多边形（如 Orange County 整体无数据，但 Costa Mesa 有），
因此按点查询时取最小的包含多边形；点查询失败（如纽约县）时退回按县名查询。

### `GET /health?deep=true`

返回 NAD 点数/延迟，以及 Overture 是否启用、当前 release。

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

缓存、限流和 DuckDB 连接都是进程内的：多 worker 时各自独立（每个 worker 各自预热 Overture、各占最多
`OVERTURE_MEMORY_LIMIT` 内存）；需要共享缓存/限流可换 Redis。

## 测试

```bash
.venv/bin/python -m pytest -q tests                      # 离线：解析/归一化/Overture 映射与判定、网页路由（42 个）
.venv/bin/python scripts/smoke_test.py http://127.0.0.1:8000   # 在线：17 个真实地址端到端（含 Overture 兜底）
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
