# NAD Address Verification API

基于 **USDOT National Address Database（NAD）** 的免费美国地址校验接口（FastAPI），
NAD 查不到时用 **Overture Maps 地址数据** 兜底，地址数据也没有时再查 **Overture 的商户数据**（新建的仓库、物流园常常只有商户登记）。**欧洲和日本**的地址查各国官方地址登记（见[欧洲和日本](#欧洲和日本)）。
不需要下载 40 GB 全量数据，也不需要任何 API Key / 账号 —— 所有上游都是公开免费服务。

| 上游 | 用途 | 说明 |
|---|---|---|
| NAD FeatureServer（USDOT 托管在 ArcGIS Online） | 9,792 万条权威地址点 | 2026-06-30 编译版，季度更新 |
| NAD County Participation Status | 判断该县/市是否已接入 NAD | 区分“地址不存在”与“该地区无数据” |
| US Census Geocoder | 地址 → 经纬度 + 标准化 + 县 FIPS | TIGER/Line，免费无 Key |
| Census TIGERweb ZCTA / Places | ZIP 区域范围、城市所在的 ZIP（仅 Census 定位失败时使用） | |
| **Overture Maps addresses**（AWS 公开 S3 上的 GeoParquet，DuckDB 直接读） | 兜底：美国约 1.25 亿条，含 NAD + OpenAddresses 的县/市原始数据 | 自动使用最新 release，每天检查一次 |
| **Overture Maps places**（同一个 S3 桶和 release） | 最后一层兜底：商户登记的地址（Meta、BrightQuery 等），不是地址点 | 只有门牌和街道都对得上的商户才采用 |

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
        → 仍没有核实时：Overture 商户数据按同一个范围 + 门牌查询 → 有商户登记在这个门牌和街道上则采用

Census 定位失败（新建街道常见，TIGER 里还没有）：
  有 ZIP → 在该 ZIP 范围内查 NAD（Add_Number=N 且街名含输入街名中最长的词）
  无 ZIP → 同样的查询，范围换成和城市（Census place / CDP）重叠的所有 ZIP
```

几处细节：

- **跳过无用的 1500 m 搜索**：300 m 内没找到门牌时，先拉 300 m 内所有点（同时用来给出同街门牌建议）。如果这条街
  在门牌两侧都已经有门牌（比如要查 1716，附近有 1700 和 1818），那 1716 只可能在两者之间、也就在 300 m 内，
  1500 m 搜索只会找到别的街上的同号门牌，直接跳过。曼哈顿这类密集区 1500 m 搜索要 5–18 s。
- **街道比对**：编号街道必须号码一致（`5th Ave` 和 `3rd Ave` 按字符相似度是 0.8，曾被判成同一条街）；
  同名不同类型（`NW 5th St` / `NW 5th Ave`，网格城市里是两条交叉的街）不算匹配。
  核实通过但街道写法和记录不同（拼错、漏了方向或类型：`W Washingtn St`、`Washington St`、`W Washington`）时，
  `notes` 写出记录里的街名：`street written 'W Washingtn St', NAD record has 'West Washington Street'`；
  标准简写（`Street` / `St`、`West` / `W`、`Fifth` / `5th`）归一化后相同，不提示。
- **Overture 只做能改变结论的查询**：NAD 已经是 `street_only` 时，Overture 只查门牌，不再做街道探测
  （探测最多得到同样的 `street_only`，而同级结论不会替换 NAD 的结果）。
- **同一时刻的相同上游请求合并成一次**（批量里重复的地址、相邻地址的同一个县），结果照常进缓存。
- **输入清洗**：全角逗号/数字/空格（中文输入法）、单元格内换行、末尾的国家（`, USA`）、`2nd Floor` 这类序数楼层、
  `123-A` 门牌、ZIP 单独一段（Census 的 `CITY, ST, ZIP` 写法）、`Bldg 2 Apt 5` 这类多个单元标识、`PMB`、州全称（`Arizona`）、
  被 Excel 去掉前导 0 的 ZIP（`MA 2108` → `02108`，只在补 0 后的 ZIP 属于该州时才补）、单元号前导 0（`Unit 0012` = `12`）、
  楼栋和单元连写（`Apt B201` 对应 Building B + Unit 201）；
  `Pier Ave`、`Space Park Dr` 这类含单元关键词的街名不会被误当成单元。PO Box 返回 `invalid_input` 并说明原因。
- **顺序乱了的地址**：按逗号分段后逐段判断是门牌街道、单元、城市、州、ZIP 还是国家，顺序不对时按
  “门牌 街道, 单元, 城市, 州 ZIP” 重排，`notes` 里写明按什么顺序读的：`MADISON, AL, 28869 FANNING DR NW`、
  `28869 FANNING DR NW, AL, MADISON`、`Apt 5, 100 Main St, ...`、`62701, IL, Springfield, 100 Main St` 都能读对。
  没有逗号时以“州代码 + 5 位 ZIP”为锚点：它写在开头、中间都行（`CA 95814 Sacramento 980 9th St`、
  `980 9th St CA 95814 Sacramento`），后面跟着的单元也能认出（`980 9th St Sacramento CA 95814 Ste 1750`）；
  城市可以写在门牌前面（`Sacramento 980 9th St CA 95814`），但门牌后面要有街道类型词，`Apt 5 100 Main St`、
  `PO Box 12 Springfield` 里门牌前的词不当城市。街道前、后都有其他段时，
  街道前的是收件人或公司名（`Acme Corp, 100 Main St, Springfield, IL`），忽略并提示。门牌写在街名后面
  （`FANNING DR NW 28869`）不处理：末尾的 5 位数也可能是 ZIP，`Route 66` 这类街名也以数字结尾。
  不带逗号时街道类型后的简写方向归街道（`Fanning Dr NW Madison`），全拼的归城市（`Main St North Miami`）。
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
- 没有位置就无法兜底：既没有 ZIP、城市也不是 Census place 时返回 `geocode_failed`，不会查 Overture ——
  无 bbox 的属性查询要扫 27 GB。Census 定位失败但有 ZIP 或城市时，用 ZIP 范围（或城市所在 ZIP 的合并范围）作为 bbox；
  范围超过 1.5°（`CITY_FALLBACK_OVERTURE_MAX_DEG`；休斯顿整个城市 1.05° × 0.89°，Overture 查询 2–3 s）时不查 Overture，提示补 ZIP。
  按范围查时 Overture 只有找到地址才采用：范围内没有这个门牌往往只是 Overture 在那里没有数据（萨凡纳一带就是），
  不能把 NAD 的 `not_covered`（无法判断）换成 `not_found`。

### Overture 商户兜底

NAD 和 Overture 地址数据都核实不了时（结论仍是 `not_found`、`not_covered`、`street_only` 或 `upstream_error`），再查 Overture 的商户数据（places 主题）：同一个 release、同一个范围（定位点周围 1500 m，或 ZIP / 城市范围），按门牌查商户登记的地址（`1410 United Dr`、`4532 United Dr Ste 100`、`Weis Market, 1075 W King St`），用同一套比对逻辑核对街道、门牌、ZIP 和单元。新建的仓库、配送中心、物流园早就有商户登记，NAD 和 TIGER 却要过几年才有这条街。实测（2026-10-21.0）：

| 地址 | 之前 | 现在 |
|---|---|---|
| 1410 United Dr, Shippensburg, PA 17257 | `geocode_failed`（Census、NAD、Overture 地址都没有这条街） | `verified`，Walmart（meta） |
| 4532 United Dr, Shippensburg, PA 17257 | `geocode_failed` | `verified`，Penske Logistics Distribution Center（BrightQuery） |
| 500 McCarthy Dr, Lewisberry, PA 17339 | `not_covered`（York 县不在 NAD） | `verified`，PHL5 Amazon Warehouse（meta） |
| 9300 Olde Scotland Rd, Shippensburg, PA 17257 | `not_covered`（Franklin 县不在 NAD） | `verified`，P&G Northeast Fulfillment Center（meta） |
| 1400 United Dr, Shippensburg, PA 17257 | `geocode_failed` | `geocode_failed`（没有商户在这个门牌） |

- 只采用查到的：门牌和街道都对得上的商户才算（`verified`，写了单元而商户没有时 `unit_not_found`）。查不到不改变结论 ——大多数地址上没有商户，同一条街上别的商户也不能说明这个门牌存在。
- 采用时 `confidence × 0.8`，`data_source="overture_places"`，`best_match.source` 是 `overture_places:<数据集>`，`best_match.place` 给出商户名、类别、Overture 估计的存在概率和营业状态；`fallback.places` 记录这一步，`fallback.adopted` 这时为 `false`。
- 这是商户自己登记的地址，说明有商户在用这个地址，不说明地址主管机构登记过它；`notes` 里写明了依据。
- Overture 地址查询刚失败（超时、S3 出错）时不查商户，免得同一个故障再等一次超时。启动预热也会读一次商户数据的元数据。

### Census 定位不到的地址

例：`28869 FANNING DR NW, MADISON, AL` 所在的街道 Census 和 OpenStreetMap 都没有，但 NAD 里有
（Alabama 911 Board 的数据，2024-12 更新；邮政城市是 Madison，实际在 Huntsville 市界内、Limestone 县，ZIP 35756）。

- 地址带 ZIP：在这个 ZIP 的范围内查 NAD。
- 不带 ZIP：先用 TIGERweb 查城市（建制市和 CDP 都查，`Harvest, AL` 是 CDP），再找和城市范围重叠的 ZIP，
  在这些 ZIP 的合并范围内查 NAD。邮政城市常常超出市界，所以按 ZIP 找而不是按市界找。
- NAD 的属性字段没有索引，大范围内只按门牌查会超时（实测 41 s），所以同时按街名过滤：取输入街名中最长的词做
  `St_Name LIKE '%词%'`（`Saint Charles` 归一化成 `ST CHARLES`，取 `CHARLES`），完整街名仍由本地比对逻辑核对。
  Madison 一带 0.6–9 s；休斯顿（168 个 ZIP）冷查询约 30 s。
- 没找到这个门牌时，再查这片范围里有没有这条街（不限门牌，范围不超过 0.4°，`AREA_STREET_PROBE_MAX_DEG`）：
  - 街上有别的门牌：`street_only`，`suggestions` 给出最接近的门牌（`28899 Fanning Dr NW` → 28869、28860）；
  - 这条街在 NAD 里也没有：返回 `geocode_failed`（`nad_status` 是 `not_found`），`notes` 说明可能是比数据更新的地址，
    或街名写错了。Census 定位不到的地址常常是新街道：`1410 United Dr, Shippensburg, PA` 是新建的
    沃尔玛物流中心，Census、NAD（数据截至 2022-08）、Overture 地址数据都还没有这条街（OpenStreetMap 有街没有门牌），
    说它“不存在”是错的。这类地址现在多半能由 [Overture 商户兜底](#overture-商户兜底) 核实；商户数据里也没有时才是这个结论；
  - 县不在 NAD 里：`not_covered`。
  街名写法和 NAD 差得太多（`Fifth` / `5th`）时也会落到“没有这条街”。
- 有些数据提供方把空字段导出成文字 `<Null>`（门牌全称 `28869 <Null>`），按空值处理，Overture 里转载的同样处理。

## 欧洲和日本

按什么国家核对，依次看：

1. 请求里填了 `country` 就按它：ISO 代码（`DE`、`DEU`）、常见写法（`UK`、`U.S.A.`、`EL`）、国名（`Germany`、`德国`、`義大利`、`Ελλάδα`）
   或 ERP 导出的组合写法（`美国(US)`、`Germany (DE)`、`DE - Germany`，两部分必须一致），美国属地（`PR`、`GU`、`Puerto Rico`）
   按美国；写成英国但邮编是 `JE` / `GY` / `IM` 的按泽西岛、根西岛、马恩岛；
2. 地址里写的国家：末尾的国家名或单独一段的代码（`..., 10117 Berlin, Germany`、`..., London England`、`..., 75001 Paris, FR`、
   `Berlin 德国`），国家后面跟着邮编、电话或括号备注也能识别，写在开头也可以（`Germany, 10117 Berlin, ...`）；
   城市后面不加逗号的两字母代码不当国家（`50122 Firenze FI` 的 FI 是佛罗伦萨省，`3011 Bern BE` 的 BE 是伯尔尼州）；
   日文地址（假名、都道府県、丁目，或 `大阪市北区梅田3-1-1`、`京都市北区金閣寺町1` 这种市区 + 町名 + 番地写法，简体字
   `东京都`、`横滨市` 也算；`南京路1-1`、`科技园1-2-3` 这类中国地址不算），以及带 `-ku` / `chome` 的罗马字地址；
3. 都没有时按美国。

美国地址不会被误判：地址里只要有“州 + ZIP”（`AZ 85007`，也包括 `APO AE 09012`），就按美国（除非请求里填了别的 `country`），
后面的收件人名（John Holland）、中文备注（“放小区门口”）、片假名名字都不会改变国家；末尾的 `DE`、`MD`、`Rhode Island`、
`Georgia` 是美国州，`Staten Island`、`New Britain`、`New Holland`、`Little Italy` 是美国地名；街道后面直接跟一个和国家同名的
小镇（`270 S River Ave, Holland`、`300 N Coal St, Mexico`）也按美国。
地址写的是加拿大、澳大利亚、中国、俄罗斯等暂不支持的国家时返回 422（中英文说明），不会拿美国数据去“核实”。

`GET /countries` 列出支持的国家和各自的数据源。返回结构和美国一样，另带 `country`、`provider`
（数据源名称、`authority`、许可证和需要展示的署名），日本另带 `jp`（`parcel`：地番地址；`go_given`：输入里有号）。

每个国家按顺序问数据源，前一个出错（或在该地区没有数据）才问下一个：

| 国家 | 数据源 | 说明 |
|---|---|---|
| 法国 | BAN（IGN Géoplateforme 地理编码） | 不存在的门牌返回街道，不会给邻近门牌。CEDEX 邮编只作提示，不算邮编错误 |
| 荷兰 | BAG（PDOK Locatieserver） | 有邮编时用精确过滤；字母和附加号（`8-H`、`1A`、`123 hs`、`2 III`）按单元核对，大楼的附加号逐个查 |
| 比利时 | 弗拉芒：Digitaal Vlaanderen 地址登记（含 bus 单元号）；瓦隆：ICAR geocodeWS | 按邮编分区。布鲁塞尔的 UrbIS 限制商业使用，改用 Overture 里的联邦 BeST 数据（荷兰语街名也能对上） |
| 卢森堡 | geoportail.lu（BD-Adresses） | |
| 瑞士 | 联邦建筑地址目录（geo.admin.ch 搜索） | 不存在的门牌会返回邻近门牌，作为同街建议 |
| 奥地利 | BEV 地址登记（地籍查看器背后的搜索） | 不是公开文档的接口，出错或返回看不懂的数据时改用 Overture；门牌不存在时再查这条街，给出同街门牌 |
| 西班牙 | CartoCiudad（IGN） | 邮编作为过滤条件（放进查询文字会查不到）；加泰罗尼亚语街名（Carrer、Passeig、Avinguda）按西班牙语对应 |
| 挪威 | Kartverket 地址登记 | 字母属于门牌（`11A`），单元号是 H 编号（`H0201`）；先在写明的城市里查，再查全国 |
| 芬兰 | Syke Ryhti 建筑地址登记 | 芬兰语或瑞典语街名和城市名；门牌区间（`11-13`）只含同侧的 11 和 13 |
| 冰岛 | HMS 地址登记（WFS） | 需要冰岛字母；记录没有城市，按邮编换算城市再比对 |
| 爱沙尼亚 | In-ADS（Maa- ja Ruumiamet） | 含公寓号（`Narva mnt 7-1`）；不带爱沙尼亚字母查不到时改用 Overture |
| 拉脱维亚 | VZD 国家地址登记（data.gov.lv） | 需要拉脱维亚字母，不带时改用 Overture；`386 k-2`（楼号）按一个门牌查 |
| 波兰 | PRG（GUGiK） | 需要城市；没有街名的村庄（`Przybysławice 12`）按村名 + 门牌查。机构的邮政编码常和登记里的不同，邮编不一致只作提示 |
| 捷克 | RÚIAN（ČÚZK） | `151/1a`（建筑号/方位号）写哪一个都能对上；没有街名的村庄按村名 + 建筑号；`Praha 2` 这种区名保留 |
| 斯洛文尼亚 | GURS 门牌登记 | 没有街名的房子用村名代替街名（有街名的房子不行，`Bled 5` 不会对上布莱德任意一条街的 5 号） |
| 德国、意大利、葡萄牙、丹麦、立陶宛、斯洛伐克、克罗地亚、塞尔维亚、列支敦士登、法罗群岛 | Overture Maps（这些国家的官方开放地址登记），先用 Photon（OpenStreetMap 搜索）在写明的城市里找到街道，再沿整条街找门牌 | 德国缺巴伐利亚，那里直接用 OpenStreetMap。葡萄牙的数据只收住宅，商铺门牌常缺，找不到门牌时返回 `not_covered` 而不是“门牌不存在”。斯洛伐克的 `553/25` 写哪个号都能对上 |
| 英国、爱尔兰、瑞典、希腊、匈牙利、罗马尼亚、海峡群岛、马恩岛、直布罗陀等 | OpenStreetMap（Photon）；英国邮编另用 postcodes.io 核对 | 这些国家没有免费的官方门牌接口。OSM 社区数据，找到门牌算核实（置信度 0.75），找不到门牌**不算证据**：返回 `not_covered`（无法判断），只有连街道都找不到才是 `not_found` |
| 日本 | 国土地理院住所検索 + 日本邮政邮编表 | 见下 |

所有数据源找到“东西”都不直接算数：门牌（含字母）、街道名、邮编、城市都和输入重新比对，因为多数服务对不存在的门牌会返回
街道或邻近门牌，不少还会在全国范围找同名街道：

- **城市必须对上**：记录的邮编或城市和输入一致才算核实。`Karl Johans gate 22, Bergen` 不会因为奥斯陆有这个门牌就算数，
  而是返回“这条街和门牌只在别的城市有”。邮编和输入差得远（不在同一省/区，如 54470 和 89250）的记录不算输入的城市，
  即使城市同名（法国有几千个同名市镇，`Beaumont` 就有十几个）；同一城市里写错邮编（慕尼黑 80331 / 81241、`Praha 2` 配了
  Praha 1 的邮编，或邮编只差最后几位）照样核实，并标出正确邮编。名字开头相同的另一个市镇不算同一城市（Lille /
  Marquette-lez-Lille、Neu-Ulm / Ulm、Buchs SG / Buchs ZH），带括号的城市名（`Frankfurt (Oder)`、`Halle (Saale)`）不会被当成
  同名的大城市；城市的英文和中文写法（Vienna、Munich、慕尼黑）按当地名称查询和比对。没写城市也没写邮编、同名街道门牌
  又不止一处，或者只写了城市、门牌在城里不止一个邮编区时，返回 `not_covered`（请补上城市或邮编）。
- **街道必须是这一条**：数据源在这个城市里有和输入同名的街道时，只认这条街（`Markt 7` 不会被 `Marktstraße 7` 顶替）；只有
  找不到同名街道时才接受拼写相近的，并在说明里标出差异。省略的类型词不影响（`Castellana 81` = `Paseo de la Castellana 81`、
  `Jerozolimskie` = `Aleje Jerozolimskie`），但类型词不同（拉脱维亚 `iela` / `gatve`、`ul.` / `al.`）、多了方位词
  （`Norra Hamngatan`、`Nørre Voldgade`）或多了前面的名字（`Kensington High Street`）都算另一条街。街道名比对会去掉重音、展开各语言的缩写（`Str.`、`bd`、`ul.`、`gt`、`C.so`、
  `Gal`、`c.` 等）、容忍省略名字的人名街道（`ul. Kościuszki` = `ul. Tadeusza Kościuszki`）和词序（`VIA VERDI GIUSEPPE`）；
  罗马数字、数字、数词和序数词视为相同（`Via XX Settembre` / `Via Venti Settembre`，`Vittorio Emanuele II` / `SECONDO`），
  双语街名（布鲁塞尔 `Grote Markt - Grand-Place`）分开比。门牌区间（`48/50`）包含其中同侧的门牌。
- 没有逗号时，城市和邮编可以写在街道前面（`Berlin 10117 Unter den Linden 77`、`Paris 75002 12 rue de la Paix`），
  城市后面的单元（`... 10117 Berlin Wohnung 5`、`... 1012 LG Amsterdam 3 hoog`）归单元，不并进城市名。
- 地址解析能处理订单里常见的写法：街名和门牌之间的逗号（`Calle de Alcalá, 45`、`12, rue de la Paix 75002 Paris`）、
  `nº` / `Nr.`、城市写在前面（`Debrecen, Váci utca 10.`、`80331 München, ...`）、`Praha 2` / `Dublin 2` 这类区名、城区和郡
  （`Damrak 1, Centrum, Amsterdam`、`Kinsale, Co. Cork` 取真正的城市）、丢了前导 0 的邮编（`1067 Dresden` 读作 01067，
  并提示）、门牌后面的楼层和单元（`Top 5`、`3. th.`、`EG links`、`2 Izquierda`、`3 piętro`、`hs`、`A 5`、`H0201`）、
  收件人 / 公司 / 电话 / 括号备注（放进 `notes`，不参与匹配）、中文输入法的标点。认不出的部分放进 `notes`，不会悄悄丢掉。
- 邮政信箱和自提柜（`PO Box`、`Postfach`、`BP`、芬兰 `PL`、`Packstation`）返回 `invalid_input` 并说明；本来就没有门牌号的
  真实地址（英国房名、`s/n`、法国 lieu-dit、北欧和波罗的海国家的农庄名）返回 `not_covered`；普通街道漏写了门牌
  （`Hauptstraße, 80331 München`）仍是 `invalid_input`。
- 数据源出错、跳过，或官方登记什么都没找到（例如不带当地字母的街名）时问下一个数据源；官方登记什么都没找到时，后面的
  数据源只有街名完全一致才采用（根特没有 `Grote Markt`，Overture 的 `Groentenmarkt 1` 只列为候选）；后面的数据源给不出更好的
  结论时，仍用前面的结论。同一个数据源连续失败 3 次后一分钟内不再请求它；一个地址最多查 60 秒。

**日本**：地址是“町 + 丁目 + 番（街区）+ 号（建筑）”，国土地理院返回它知道的最深一级，比对规则：

- 号也对上：`verified`；只对上番（东京都心大部分地方没有号的数据）：`verified` + `match_level: "block"`，页面显示“已核实到番”，
  号无法核实（这个番有号的数据、只是没有这个号时是 `street_only`）；丁目存在但没有这个番：`street_only`；
  町存在但没有这个丁目：`not_found`；乡下用地番（`978-6`、`978番地の6`）而国土地理院只到町名的地方：`not_covered`；
  札幌等地 `北4条西4丁目1` 这种丁目里的番地按番地核对。
- 国土地理院按地名检索，`本町`、`中央` 这种全国有几十处的町名需要都道府県和市区町村：写在门牌后面的（`本町1-1-1, 中央区, 大阪府`）、
  请求里的 `state` / `city` / `zip` 字段、邮编，都会用来确定是哪一处；仍然确定不了时返回 `geocode_failed` 并列出同名地点，
  不会随便核实其中一处。`state` / `city` 字段可以写汉字、罗马字（`Tokyo`）、ISO 代码（`JP-13`）或简体字，只作为偏好：
  对不上时在说明里指出，不会因此把真实地址判成找不到。政令市漏写区（`大阪市梅田3-1-1`）时按邮编表补上区。
- 罗马字地址（`2-8-1 Nishi-Shinjuku, Shinjuku-ku, Tokyo 163-8001`）国土地理院读不了：用日本邮政的邮编表把写出的町名、
  区市名换成汉字再查。写出的都道府県必须一致（`Fuchu-cho, Hiroshima` 不会读成东京的府中市），郡名可写可不写
  （`Karuizawa-machi, Nagano`）。以写出的町名为准，邮编只用来区分同名的町，不会把地址挪到邮编所在的町（邮编写错时照常
  提示；町名和邮编在不同的都道府県、又没写都道府県时不核实）。长音、`Ō`、`Oh`（`Ohta-ku`）、`Nihombashi` / `Nihonbashi`
  等写法都能识别。
- 带邮编时核对邮编是否属于这个町（按丁目区分的邮编、大楼和大企业的专用邮编都认），不属于时给出正确邮编。
- 地址前后的收件人、公司、`住所：` 等标签和电话号码（需要有区号的 0 或 `TEL` 标签，`1-1 1203` 的 1203 是房间号）会去掉；
  楼名里的都道府県名（`東京都庁`、`北海道大学`）不会截断地址；京都的 `寺町通御池上る` 这种走法只去掉通名部分，保留区名；
  汉数字的番地（`六二五番地`、`九七八番地の六`）照常读；简体字（`东京都`、`神奈川县`）查不到时换成日文汉字再查。
  房间号、楼名（`101号室`、`5F`、`-101`）不参与核对。写在地址前面的楼名、房间号（`サンハイツ101 東京都世田谷区…`、
  `5F 東京都…`，有数字或 ビル / マンション / ハイツ 这类词）和写在后面的一样保留在地址 2，不当收件人去掉。
- 邮编表（日本邮政的 `utf_ken_all`、罗马字表和大企业邮编表，共约 5 MB）由后台任务下载到 `NAD_DATA_DIR`（默认 `data/`），
  每月更新，下载失败时按 1 分钟起翻倍的间隔重试；请求从不等待下载。没加载好时，日文地址照常核对（只是不核对邮编），
  罗马字地址返回 `upstream_error`。每个 worker 加载后约占 170 MB 内存（默认 2 个 worker 约 340 MB）。
- 国土地理院这个检索接口是它自己地图网站用的，没有承诺长期提供。

**注意**：以上都是在美国 IP 上测试的。部署到其他地区后请打开 `/health?deep=true`（会逐个请求每个国家的数据源）并跑一遍
`scripts/smoke_test.py`，看哪些数据源从服务器上连不通；页头的状态提示也会列出连不通的海外数据源。
斯洛伐克、塞尔维亚、立陶宛的官方接口只对部分国家的 IP 开放，所以这三个国家直接用 Overture。
Photon 公共实例只要求“合理使用”（每个 worker 并发限制在 2），大批量使用请自建 Photon 并设置 `PHOTON_URL`；
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
  Census 定位点和同街最近的门牌（带门牌号），同街门牌、候选地址点一下就能复查（没有定位点时不显示“距定位点”列）。
  `/` 按 `Accept` 头区分：浏览器拿到页面，`curl` 等 API 客户端仍拿到原来的 JSON。
- `http://<host>:8000/batch`：批量校验，一次最多 500 行。可以每行粘贴一个地址、从 Excel 复制多列，或上传 CSV
  （表头 `address`，或 `street` / `unit` / `city` / `state` / `zip`，也认中文表头；UTF-8 / GBK 均可）。分列的表在页面上
  拼成一行地址再发送（`street unit, city, state zip`；街道、城市、州都是汉字/假名时按日文顺序 `〒zip 州城市街道 unit`），
  `address` 列旁边的 `unit` 列插在第一个逗号前（`1700 W Washington St Apt 2, Phoenix, AZ 85007`）。
  订单导出里带前缀的列名（`Shipping Address 1`、`Ship City`、`收件人地址`、`收货城市` 等）也能识别。
  同时有买家地址和收货地址的导出（eBay 的 `Buyer Address 1` 和 `Ship To Address 1`）按收货地址。
  各国地址可以混在一起：有国家列时按它（`country`、`Ship Country`、`Destination Country`、`ISO`、`国家`、`目的国`、
  `国家二字码`、繁体 `國家` 等都认，作为这一行的 `country` 发送；没有表头、也没在页面上选国家时，最后一列全是本服务支持的
  国家代码（`FR`、`NL`、`IT` 等）才当国家列；`DE`、`MT`、`AL` 这类同时是美国州缩写的代码留在地址里由服务端判断；
  这一列其实是省份时，在页面上选一个国家即可关掉这个识别）。没有国家列的行按页面上选的国家发送，选“自动识别”时不带
  `country`，由服务端按地址里写的国家或日文识别。拼好的地址超过 300 字的行直接标出，不发送，以免整批 20 行被拒。
  页面按每批 20 条调用 `/v1/verify/batch?stream=true`，每条查完立刻显示（不用等同批最慢的那条），可停止、继续、
  重试失败行（服务明确拒绝的行，比如不支持的国家，不再重试）；地图按结论着色显示每一行（没有匹配记录时用定位点，
  官方登记没有定位点时用同街最近的门牌并注明），跟随筛选，点圆点看详情。
  导出 CSV 时原表的列原样保留，后面先追加每行拆分出的 `parsed_country`、`parsed_state`、`parsed_city`、`parsed_zip`、
  `parsed_address1`（门牌 + 街道）、`parsed_address2`（单元；已核实的行按核实到的记录拆分），再追加 `verify_status`、`verify_verdict`、`verify_confidence`、`verify_matched_address`、
  `verify_country`、`verify_nearest_number`（门牌不存在时同街最近的门牌）、`verify_census_matches`（Census 匹配到多个地址时的个数）、`verify_zip_should_be`（填的 ZIP 和记录不一致时，
  记录里的 ZIP；地址存在但 ZIP 填错会让包裹分错，结果行里也会标出来）、`verify_license` / `verify_attribution`
  （数据许可和需要标注的来源）等结果列。
  有 10 行以上结果还没导出就离开页面时，浏览器会提示。
- 页头的状态点显示各上游最近一次调用的结果，例如服务器连不上 Census 时会显示“Census 地理编码不可用”，
  鼠标悬停看具体错误；海外数据源或日本邮编表不可用时显示“部分海外数据源不可用”（状态点仍只看美国的服务）。

所有响应都做 gzip 压缩（一批 10 条结果 48 KB → 6 KB，leaflet.js 147 KB → 43 KB），服务器离用户远时差别明显。

页面文件在 `app/static/`（`index.html`、`batch.html`，共用 `style.css`、`common.js`、`map.js`）。地图用 Leaflet 1.9.4
（BSD-2，随项目放在 `app/static/vendor/leaflet/`，用到时才加载），唯一的外部资源是 OpenStreetMap 的底图瓦片：
内部工具的用量符合 [OSM 瓦片使用政策](https://operations.osmfoundation.org/policies/tiles/)，对外大量使用或访问 OSM 较慢时，
改 `map.js` 开头的 `TILES` / `ATTRIBUTION` 换成自己的瓦片源。瓦片加载失败时地图只剩灰底，标记点照常显示。
开启 `NAD_API_KEYS` 时页面会提示填写 Key（只存在浏览器本地）。

## 接口

### `GET /v1/verify` / `POST /v1/verify`

核验一条地址。GET 用查询参数，POST 用 JSON 请求体，两者参数和返回完全相同。启动后打开 `/docs` 可以直接试，
每个字段的说明也在那里。

```bash
curl -G localhost:8000/v1/verify --data-urlencode "address=1700 W Washington St, Phoen, AZ 85007"

curl -X POST localhost:8000/v1/verify -H 'content-type: application/json' \
  -d '{"address":"Unter den Linden 77, 10117 Berlin","country":"DE"}'
```

#### 入参

| 参数 | 必填 | 说明 |
|---|---|---|
| `address` | 是 | 一行完整地址，1–300 个字符。美国按 `门牌 街道 单元, 城市, 州 ZIP`；欧洲按当地写法（`Unter den Linden 77, 10117 Berlin`）；日本按日文写法（`〒100-8914 東京都千代田区永田町1-6-1`） |
| `country` | 否 | ISO 代码或国名（英文、本国文字或中文：`DE`、`Germany`、`Deutschland`、`德国`），最长 60 个字符。填了就按它查，不支持的国家返回 422。不填时从地址里识别：地址末尾写了国名（`..., 10117 Berlin, Germany`）按那个国家，日文地址按日本，都没有按美国。支持的国家见 `/countries` |

#### 返回

地址不存在不算错误：照常返回 200，结论在 `status` 里。一般先看 `status`、`verified`、`confidence`，
再看 `best_match`（匹配到的记录）、`parsed`（拆分好的地址）和 `notes`（为什么是这个结论）。

一个真实的返回（输入城市拼错了，`parsed` 按记录改成了 Phoenix；`geocode`、`coverage`、`best_match` 有删节）：

```json
{
  "status": "verified",
  "verified": true,
  "match_level": "address",
  "confidence": 0.99,
  "data_source": "nad",
  "country": "US",
  "provider": null,
  "jp": null,
  "nad_status": "verified",
  "input": "1700 W Washington St, Phoen, AZ 85007",
  "parsed": {
    "country": "US", "state": "AZ", "city": "Phoenix", "zip": "85007",
    "address1": "1700 West Washington Street", "address2": null,
    "house_number": "1700", "street": "West Washington Street", "unit": null,
    "source": "record"
  },
  "geocode": {"source": "census", "matched_address": "1700 W WASHINGTON ST, PHOENIX, AZ, 85007",
              "lat": 33.448175, "lon": -112.098251, "county": "Maricopa", "county_fips": "04013", "match_count": 1},
  "coverage": {"method": "point", "county": "Maricopa", "state": "AZ", "status_code": 10,
               "status": "Data in the NAD", "in_nad": true, "data_date": "2025-07-09"},
  "best_match": {
    "uuid": "45c50721-75aa-40aa-85b4-2a505994d41d",
    "formatted": "1700 West Washington Street, Phoenix, AZ 85007",
    "address_number": "1700", "street": "West Washington Street",
    "city": "Phoenix", "county": "Maricopa", "state": "AZ", "zip": "85007",
    "lat": 33.448129, "lon": -112.096677, "source": "State of Arizona",
    "match": {"score": 0.99, "street_score": 1.0, "number_exact": true, "zip_match": true,
              "city_match": true, "unit_match": null, "distance_m": 146.1}
  },
  "candidates": [{"formatted": "1700 West Washington Street, Phoenix, AZ 85007", "...": "..."}],
  "suggestions": [],
  "fallback": null,
  "search_radius_m": 300,
  "nad_points_nearby": null,
  "notes": [],
  "disclaimer": "NAD / Overture addresses confirm that an address point physically exists ...",
  "timing_ms": {"geocode": 664.5, "coverage": 509.7, "nad": 838.8, "total": 1504.1}
}
```

**顶层字段**

| 字段 | 说明 |
|---|---|
| `status` | 结论，见下面的 status 表 |
| `verified` | 地址存在：`status` 是 `verified` 或 `unit_not_found` 时为 `true` |
| `match_level` | 核实到哪一级：`unit` 单元、`address` 门牌、`block` 只到番（日本，数据里没有号）、`street` 只有街道；没有匹配时为 `null` |
| `confidence` | 置信度 0–1，见下面的说明 |
| `data_source` | 最终结论来自哪个数据集：美国是 `nad`、`overture` 或 `overture_places`（商户登记的地址，不是地址点）；美国以外是数据源代码（`fr_ban`、`nl_pdok`、`jp_gsi`、`overture`、`osm` 等）；`upstream_error` 时为 `null` |
| `country` | 按哪个国家核验的（ISO 代码） |
| `provider` | 美国以外：回答的数据源 `{key, name, authority, url, license, attribution}`。`authority` 是 `official`（官方登记）、`overture` 或 `osm`（社区数据：查到是证据，查不到不能说明地址不存在）；展示数据时要标注 `attribution`。美国为 `null` |
| `jp` | 日本：`parcel` 是番地式地址（不是丁目-番-号），`go_given` 是输入在番后面写了号或枝番。其他国家为 `null` |
| `nad_status` | 美国：只看 NAD 的结论（Overture 补查前）。美国以外为 `null` |
| `input` | 请求里的 `address` 原文 |
| `parsed` | 地址拆成的字段，见下面的说明 |
| `geocode` | 定位结果，见下面的说明；没有定位时为 `null` |
| `coverage` | 美国：NAD 在这个县有没有数据 `{method, partial, county, state, state_name, status_code, status, in_nad, data_date}`，`in_nad` 为 `false` 时 NAD 查不到不说明地址有问题。美国以外为 `null` |
| `best_match` | 匹配到的记录，见下面的说明；没有匹配时为 `null` |
| `candidates` | 搜索范围内同门牌号的记录，按匹配分排序，最多 5 条（美国包含 `best_match` 本身，例如同一栋楼的其他单元） |
| `suggestions` | `street_only` 时：同一条街上和输入最接近的门牌，最多 5 条，结构同 `best_match` |
| `fallback` | 第一个数据源没给出答案、改问下一个时的记录 `{provider, release, triggered_by, status, adopted, error}`：`triggered_by` 是为什么补查，`status` 是补查数据源自己的结论，`adopted` 为 `true` 表示最终结论用的是补查结果。美国另有 `places`：Overture 地址数据也核实不了、再查商户数据的记录，字段相同（`provider` 是 `overture_places`，`triggered_by` 是查商户前的结论），采用商户数据时外层 `adopted` 为 `false`；没查商户时为 `null`。没有补查时 `fallback` 为 `null` |
| `search_radius_m` | 美国：NAD 最后一次按定位点搜索的半径（米） |
| `nad_points_nearby` | 美国：定位点附近 NAD 的地址点数，查过同街门牌时才有 |
| `notes` | 说明（英文）：为什么是这个结论、哪些项不一致、补查情况等 |
| `disclaimer` | 固定说明：核验的是地址点存在（商户数据只说明有商户登记这个地址），不是 USPS 能投递 |
| `timing_ms` | 各步耗时（毫秒），`total` 是总耗时 |

**`parsed`**：地址拆成的字段。已核实（美国、欧洲）时按匹配到的记录拆分，拼错的城市、缩写的街道改成记录里的写法
（`Phoen` → `Phoenix`，`W Washington St` → `West Washington Street`），并带 `"source": "record"`；其他情况按输入拆分，
`source` 为 `null`。欧洲的门牌号和邮编保留输入的写法（记录可能省掉 `8-H` 的字母、`1012 LG` 的空格），只有输入的邮编和记录
不一致时才换成记录的；记录里的城市是区名（Berlin 的 Mitte）时保留输入的城市。

| 字段 | 说明 |
|---|---|
| `country` | ISO 代码 |
| `state` | 州 / 省（日本：都道府県） |
| `city` | 城市（日本：市区町村） |
| `zip` | 邮编 |
| `address1` | 地址 1：门牌 + 街道，按当地顺序（日本：町名 + 番地） |
| `address2` | 地址 2：单元、楼层、房间（日本：建筑名、房间号） |
| `house_number` / `street` / `unit` | 门牌号 / 街道 / 单元（`unit` 和 `address2` 相同） |
| `source` | `record`：按匹配到的记录拆分；`null`：按输入拆分 |

**`best_match`**（`candidates`、`suggestions` 的每一项结构相同）：一条地址记录。美国以外各数据源的记录也套用这个结构，
没有的字段为 `null` 或空对象。

| 字段 | 说明 |
|---|---|
| `uuid` | 记录在数据源里的 ID |
| `formatted` | 整条地址，按数据源的写法 |
| `address_number` / `street` | 门牌号 / 街道全名（日本：番或番-号 / 町名 + 丁目） |
| `street_parts` | NAD 的街道分段：`pre_directional`（West）、`name`（Washington）、`post_type`（Street）等；美国以外为空 |
| `subaddress` | 单元：`building`、`floor`、`unit`、`room`，以及原文 `subaddress` |
| `city` / `county` / `state` / `zip` | 城市 / 县（仅美国）/ 州或地区代码 / 邮编 |
| `lat` / `lon` | 坐标 |
| `placement` / `address_class` / `updated` | NAD 的坐标放置方式、地址类别；记录更新日期 |
| `source` | 提供这条记录的机构：NAD 是提交数据的州 / 县；Overture 记录是 `overture:<数据集>`，Overture 商户记录是 `overture_places:<数据集>`（`meta`、`BrightQuery` 等）；日本是 `国土地理院` |
| `license` | 数据许可（Overture 和美国以外的记录） |
| `match` | 和输入逐项比对的结果，见下表 |

美国记录还带着 NAD 的原始字段：`address_number_int`、`address_number_prefix`、`address_number_suffix`、
`postal_city`（邮政城市名）、`municipality`、`zip4`、`landmark`、`address_type`、`lifecycle`、`delivery_type`、
`address_authority`（地址主管机构）。Overture 商户记录另有 `place`：`name` 商户名、`category` 类别、`confidence`（Overture 估计这家商户存在的概率 0–1）、`operating_status`（`open`、`temporarily_closed`、`permanently_closed`）；商户名也在 `landmark` 和 `formatted` 开头。

| `match` 字段 | 说明（`null` 表示无法比较：输入或记录缺这一项，或数据源不提供） |
|---|---|
| `score` | 综合匹配分 0–1。美国 = 0.55 × 街道 + 0.15 × 门牌 + 0.10 × ZIP + 0.10 × 单元 + 0.10 × 距离 |
| `street_score` | 街道名相似度 0–1（缩写、方向词已归一化：W = West，St = Street）；≥ 0.8 才算同一条街 |
| `number_exact` | 门牌号（含后缀，如 12B）完全一致；日本数据只到番时为 `null` |
| `zip_match` | 邮编一致（美国比前 5 位） |
| `city_match` | 城市一致；美国以外记录的城市常是区名，比不了时为 `null` |
| `unit_match` | 单元一致；输入没写单元时为 `null` |
| `distance_m` | 记录离定位点的距离（米）；没有定位点（按 ZIP / 城市范围查、日本）时为 `null` |

**`confidence`**：就是 `best_match.match.score`，再按情况打折：美国 NAD 查不到、用 Overture 补查到的 × 0.9，Overture 地址数据也没有、按 Overture 商户数据补查到的 × 0.8；单元没找到 × 0.9；门牌后缀不同 × 0.85（美国）。美国以外还有：邮编和记录不一致 × 0.9、地址里没写城市和邮编 × 0.9、门牌落在记录的号段内（12 在 10-14 里）× 0.95、这个门牌只有带附加号的记录（Damrak 8 只有 8-A、8-B）× 0.9。`street_only` 固定 0.3，`not_found` 为 0.05，其余没有匹配的结论为 0。日本：到号 0.95、到番 0.7、只到町 0.3，邮编不一致再 × 0.9。打折的原因都写在 `notes` 里。

**`geocode`**：按 `source` 有几种结构。

| `source` | 什么时候 | 字段 |
|---|---|---|
| `census` | 美国，Census 定位到了 | `lat`、`lon`、`matched_address`、`components`（Census 拆出的街道分段）、`county`、`county_fips`、`state`、`zip`、`tiger_line_id`、`match_count`；匹配到多个地址时另有 `alternatives`，只核验了第一个 |
| `zcta` | 美国，Census 定位不到，按 ZIP 范围查 | `zip`、`extent`（范围的经纬度框） |
| `city` | 美国，没有 ZIP，按城市范围查 | `city`、`state`、`zips`、`extent` |
| `photon` 等 | 欧洲 | `lat`、`lon`、`matched_address`、`level` |
| `gsi` | 日本 | `lat`、`lon`、`matched_address` |

**`status`**

| status | 含义 |
|---|---|
| `verified` | 门牌 + 街道（+ 单元）存在（看 `data_source` 是 NAD、Overture 地址还是 Overture 商户数据） |
| `unit_not_found` | 楼栋存在，但该单元没有记录（很多数据源不录单元，`notes` 会列出已有单元）；`verified` 仍为 `true` |
| `street_only` | 附近有这条街，但没有这个门牌；`suggestions` 给出同街最接近的门牌 |
| `not_found` | 该地区有数据覆盖，但无匹配 —— 较大概率是错误/虚构地址 |
| `not_covered` | NAD 和 Overture 在该地区都没有数据，无法判断（美国以外还包括：OSM 没收录门牌、地址没有门牌号、没写城市而同名地址有多处） |
| `geocode_failed` | Census 找不到这个地址（或拒绝了输入），而且无法按 ZIP / 城市范围查，或者查了但 NAD 里也没有这条街（可能是新地址或街名写错） |
| `invalid_input` | 解析不出门牌号（PO Box、邮政信箱、自提柜也在此列，`notes` 会说明） |
| `upstream_error` | 免费上游超时/故障，包括连不上 Census 地理编码 —— 这种情况不代表地址有问题，可以重试 |

#### 错误码

错误时返回 `{"detail": ...}`。

| 状态码 | 什么时候 |
|---|---|
| 401 | 设置了 `NAD_API_KEYS`，但请求头 `X-API-Key` 缺失或错误 |
| 422 | 参数不合法。两种形式：`address` 为空或超过 300 字、`country` 超过 60 字时 `detail` 是列表（每项的 `loc` 指出哪个参数、`msg` 是原因）；`country` 不在支持范围内时 `detail` 是字符串，列出支持的国家 |
| 429 | 超过每分钟请求数上限（`NAD_RATE_LIMIT_PER_MIN`，默认 120 次 / 分钟 / IP），响应头带 `Retry-After: 60` |
| 502 | 上游服务异常。很少见：上游超时一般在 200 响应里以 `status: upstream_error` 返回 |

### `POST /v1/verify/batch`

`{"addresses": [{"address": "...", "country": "DE"}, ...]}`（每条的入参和返回都和单条接口一样，`country` 可省略），单次最多 100 条（超出返回 413），并发 5（可配）。
返回 `{"count": n, "summary": {"verified": 3, ...}, "results": [{"index": 0, "result": {...}, "error": null}, ...]}`：
`index` 是这条在请求里的位置，`summary` 是每种 `status` 的条数；一条失败（如不支持的国家）时那条 `result` 为 `null`、
`error` 说明原因，计入 `summary` 的 `error`，不影响其他条。

加 `?stream=true` 时返回 `application/x-ndjson`：每查完一条输出一行 `{"index": i, "result": {...}}`（顺序按完成先后），
客户端断开后剩下的地址不再查询。

### `GET /countries`

支持的国家（中文名）和各自按顺序使用的数据源。

### `GET /health` / `GET /health?deep=true`

`upstreams` 列出每个上游（`census_geocoder`、`nad`、`nad_coverage`、`tigerweb`、`overture`）最近一次真实调用是否成功、
错误信息和距今秒数；任一失败时 `status` 为 `degraded`。不带参数时不发任何上游请求；`deep=true` 会立即（不走缓存）
请求每个上游，并返回 NAD 点数/延迟、Overture 当前 release，也会用一个已知地址请求每个国家的数据源。`deep=true` 每分钟最多
真正执行一次（期间的请求直接返回上次的结果，带 `deep_age_s`），设置了 `NAD_API_KEYS` 时需要带 `X-API-Key`，以免被人
反复调用、连累服务器 IP 被各国数据源封禁。其他国家的数据源单独列在
`intl_upstreams`，它们出错不会把整个服务标成 `degraded`；`japanpost` 显示日本邮编表是否已加载。

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
| `PHOTON_CONCURRENCY` / `INTL_CONCURRENCY` | 2 / 4 | 每个海外数据源同时进行的请求数（每个 worker） |
| `NAD_INTL_TIMEOUT_S` | 60 | 美国以外的一个地址最多查多久，超时返回 `upstream_error` |
| `NAD_INTL_BREAKER_S` | 60 | 海外数据源连续失败 3 次后，这么多秒内不再请求它 |
| `NAD_DATA_DIR` | `data/` | 下载的参考数据（日本邮政邮编表） |
| `FR_BAN_URL`、`NL_PDOK_URL`、`GSI_URL` 等 | 各官方地址 | 每个数据源的地址都可以覆盖（见 `app/config.py`） |

缓存、限流和 DuckDB 连接都是进程内的：多 worker 时各自独立（每个 worker 各自预热 Overture、各占最多
`OVERTURE_MEMORY_LIMIT` 内存，各自加载日本邮编表约 170 MB，海外数据源的并发限制也按 worker 计）；需要共享缓存/限流可换 Redis。

## 测试

```bash
.venv/bin/python -m pytest -q tests                      # 离线：解析/归一化、判定流程、Overture 映射、各国数据源的响应解析、网页路由、上游客户端（1905 个）
.venv/bin/python scripts/smoke_test.py http://127.0.0.1:8000   # 在线：60 个真实地址端到端（美国 21 个，含 Overture 兜底和国家识别；欧洲和日本 39 个）
```

## 已知局限

- **覆盖不全**：NAD 依赖各州/县自愿提交，约 600 个县显示 “No statewide data”（如加州大部分县）。
  Overture 兜底能补上很大一部分，但仍有两边都没数据的地区（返回 `not_covered`）。
  县参与状态图层同时含“整县”和“城市级”多边形（如 Orange County 整体无数据，但 Costa Mesa 有），
  因此按点查询时取最小的包含多边形；点查询失败（如纽约县）时退回按县名查询。
- **Overture 不是权威源**：它是汇总数据，`confidence` 已打 9 折；`source=overture:NAD` 的记录用的 NAD 版本未公开，
  可能与官方 r23 服务不一致，属正常现象。坐标为 float32（约 1 m 精度）。
- **Overture 商户数据更不是地址库**：地址是商户自己登记的，可能写错或已经过时，所以只在其他数据都核实不了时才用，`confidence` 打 8 折；`best_match.place` 里的存在概率和营业状态可以用来再筛一遍。
- **Census 会“纠正”方向**：例如把 `200 N Spring St` 匹配成 `200 S SPRING ST`。匹配时以用户输入的方向为准，并在 `notes` 中提示。
- **NAD ≠ USPS 可投递**：`verified` 只说明物理地址点存在。需要可投递性请再接 USPS/CASS 类服务。
- 单元号覆盖取决于数据提供方，很多地区只有楼栋点，因此有 `unit_not_found` 这一中间态。
- 依赖 Census 地理编码定位；新建地址可能不在 TIGER 中 → 自动按 ZIP / 城市范围查同名街道（见[上文](#census-定位不到的地址)）。
- 威斯康星网格门牌（`N95W18000 ...`）等特殊格式暂不解析。
- 上游是公共免费服务，冷查询偶尔 5–15 s；生产环境建议保留缓存与限流。
- 若需高并发/离线/SLA，仍建议按州下载 NAD 自建 PostGIS（可沿用本项目的解析与匹配逻辑）。
- **欧洲和日本**：英国、爱尔兰、瑞典等没有免费官方门牌数据的国家只能靠 OpenStreetMap，找不到门牌时无法判断；
  日本大部分城区只能核实到番；奥地利和日本用的是没有公开承诺的接口，可能变动。
