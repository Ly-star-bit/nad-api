# NAD Address Verification API

基于 **USDOT National Address Database（NAD）** 的免费美国地址校验接口（FastAPI），
NAD 查不到时用 **Overture Maps 地址数据** 兜底。**欧洲和日本**的地址查各国官方地址登记（见[欧洲和日本](#欧洲和日本)）。
不需要下载 40 GB 全量数据，也不需要任何 API Key / 账号 —— 所有上游都是公开免费服务。

| 上游 | 用途 | 说明 |
|---|---|---|
| NAD FeatureServer（USDOT 托管在 ArcGIS Online） | 9,792 万条权威地址点 | 2026-06-30 编译版，季度更新 |
| NAD County Participation Status | 判断该县/市是否已接入 NAD | 区分“地址不存在”与“该地区无数据” |
| US Census Geocoder | 地址 → 经纬度 + 标准化 + 县 FIPS | TIGER/Line，免费无 Key |
| Census TIGERweb ZCTA / Places | ZIP 区域范围、城市所在的 ZIP（仅 Census 定位失败时使用） | |
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

Census 定位失败（新建街道常见，TIGER 里还没有）：
  有 ZIP → 在该 ZIP 范围内查 NAD（Add_Number=N 且街名含输入街名中最长的词）
  无 ZIP → 同样的查询，范围换成和城市（Census place / CDP）重叠的所有 ZIP
  zip_fallback=true → 在 ZIP 范围内查该门牌的所有街道（旧行为）
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
- **顺序乱了的地址**：按逗号分段后逐段判断是门牌街道、单元、城市、州、ZIP 还是国家，顺序不对时按
  “门牌 街道, 单元, 城市, 州 ZIP” 重排，`notes` 里写明按什么顺序读的：`MADISON, AL, 28869 FANNING DR NW`、
  `28869 FANNING DR NW, AL, MADISON`、`Apt 5, 100 Main St, ...`、`62701, IL, Springfield, 100 Main St` 都能读对。
  没有逗号时只认“城市 州 [ZIP] 门牌 街道”（`MADISON AL 35756 28869 FANNING DR NW`）。街道前、后都有其他段时，
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
    沃尔玛物流中心，Census、OpenStreetMap、NAD（坎伯兰县数据截至 2022-08）、Overture 都还没有这条街，
    说它“不存在”是错的；
  - 县不在 NAD 里：`not_covered`。
  街名写法和 NAD 差得太多（`Fifth` / `5th`）时也会落到“没有这条街”。
- 有些数据提供方把空字段导出成文字 `<Null>`（门牌全称 `28869 <Null>`），按空值处理，Overture 里转载的同样处理。

## 欧洲和日本

按什么国家核对，依次看：

1. 请求里的 `country`：ISO 代码（`DE`、`DEU`）、常见写法（`UK`、`U.S.A.`、`EL`）、国名（`Germany`、`德国`、`義大利`、`Ελλάδα`）
   或 ERP 导出的组合写法（`美国(US)`、`Germany (DE)`、`DE - Germany`，两部分必须一致），美国属地（`PR`、`GU`、`Puerto Rico`）
   按美国；写成英国但邮编是 `JE` / `GY` / `IM` 的按泽西岛、根西岛、马恩岛；
2. 地址里写的国家：末尾的国家名或单独一段的代码（`..., 10117 Berlin, Germany`、`..., London England`、`..., 75001 Paris, FR`、
   `Berlin 德国`），国家后面跟着邮编、电话或括号备注也能识别，写在开头也可以（`Germany, 10117 Berlin, ...`）；
   城市后面不加逗号的两字母代码不当国家（`50122 Firenze FI` 的 FI 是佛罗伦萨省，`3011 Bern BE` 的 BE 是伯尔尼州）；
   日文地址（假名、都道府県、丁目，或 `大阪市北区梅田3-1-1`、`京都市北区金閣寺町1` 这种市区 + 町名 + 番地写法，简体字
   `东京都`、`横滨市` 也算；`南京路1-1`、`科技园1-2-3` 这类中国地址不算），以及带 `-ku` / `chome` 的罗马字地址；
3. 请求里的 `default_country`（批量页的“没写国家的行按”就是它）；但以美国州结尾的行（`..., Springfield, IL`）仍按美国，
   除非行里同时有默认国家的邮编（`Via Roma 10, 20121 Milano MI` 的 MI 是米兰省）；默认国家是意大利时，
   `Milano MI 20121` 这种“省份 + 邮编”只要邮编不可能是该州的 ZIP（密歇根是 480–499 开头）就按意大利；
4. 都没有时按美国。

美国地址不会被误判：地址里只要有“州 + ZIP”（`AZ 85007`，也包括 `APO AE 09012`），就一定按美国（`default_country` 也改不了），
后面的收件人名（John Holland）、中文备注（“放小区门口”）、片假名名字都不会改变国家；末尾的 `DE`、`MD`、`Rhode Island`、
`Georgia` 是美国州，`Staten Island`、`New Britain`、`New Holland`、`Little Italy` 是美国地名；街道后面直接跟一个和国家同名的
小镇（`270 S River Ave, Holland`、`300 N Coal St, Mexico`）也按美国。结构化请求只从街道行、州栏（`England`）和城市栏末尾
（`Berlin, Germany`）读国家：城市栏整个是国名时当作美国小镇（Greece NY、Holland MI），单元栏从不读。
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
  房间号、楼名（`101号室`、`5F`、`-101`）不参与核对。
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
  （表头 `address`，或 `street` / `unit` / `city` / `state` / `zip`，也认中文表头；UTF-8 / GBK 均可）。
  订单导出里带前缀的列名（`Shipping Address 1`、`Ship City`、`收件人地址`、`收货城市` 等）也能识别。
  同时有买家地址和收货地址的导出（eBay 的 `Buyer Address 1` 和 `Ship To Address 1`）按收货地址。
  各国地址可以混在一起：有国家列时按它（`country`、`Ship Country`、`Destination Country`、`ISO`、`国家`、`目的国`、
  `国家二字码`、繁体 `國家` 等都认；没有表头、也没选默认国家时，最后一列全是本服务支持的国家代码（`FR`、`NL`、`IT` 等）
  才当国家列，而且只作为这一行的默认国家（`default_country`）发送：地址里写了美国州和 ZIP、或写了国家的，仍按地址本身；
  `DE`、`MT`、`AL` 这类同时是美国州缩写的代码留在地址里由服务端判断；这一列其实是省份时，在页面上选默认国家即可关掉这个识别），
  否则按地址里写的国家或日文识别，只有这两样都没有的行才按页面上选的默认国家（作为 `default_country` 发送）。某一格超过接口长度上限（地址 300 字等）的行直接标出，不发送，以免整批 20 行被拒。
  页面按每批 20 条调用 `/v1/verify/batch?stream=true`，每条查完立刻显示（不用等同批最慢的那条），可停止、继续、
  重试失败行（服务明确拒绝的行，比如不支持的国家，不再重试）；地图按结论着色显示每一行（没有匹配记录时用定位点，
  官方登记没有定位点时用同街最近的门牌并注明），跟随筛选，点圆点看详情。
  导出 CSV 时原表的列原样保留，后面追加 `verify_status`、`verify_verdict`、`verify_confidence`、`verify_matched_address`、
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

```bash
curl -G localhost:8000/v1/verify --data-urlencode "address=980 9th St Ste 1750, Sacramento, CA 95814"

curl -X POST localhost:8000/v1/verify -H 'content-type: application/json' \
  -d '{"street":"11 W Washington St","unit":"Suite 240","city":"Phoenix","state":"AZ","zip":"85003"}'
```

参数（长度上限：`address` 300、`street` 200 个字符，超出返回 422）：`address`（单行）或 `street`+`city`/`state`/`zip`（结构化）；可选 `country`（见[欧洲和日本](#欧洲和日本)，
不支持的国家返回 422）、`default_country`（地址里没写国家时才用）、`unit`、`lat`+`lon`（跳过地理编码）、
`zip_fallback`（Census 编码失败时在整个 ZIP 区域搜索该门牌的所有街道，较慢 3–20 s；不加时只查同名街道）、`overture_fallback`（默认 `true`，只影响美国地址：
欧洲的 Overture 是主要数据源，不受它影响）、`max_candidates`。美国以外 `zip` 是邮编，日本的 `state` / `city` 用来确定都道府県和市区町村。

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
| `not_covered` | NAD 和 Overture 在该地区都没有数据，无法判断（美国以外还包括：OSM 没收录门牌、地址没有门牌号、没写城市而同名地址有多处） |
| `geocode_failed` | Census 找不到这个地址（或拒绝了输入），而且无法按 ZIP / 城市范围查，或者查了但 NAD 里也没有这条街（可能是新地址或街名写错） |
| `invalid_input` | 解析不出门牌号（PO Box、邮政信箱、自提柜也在此列，`notes` 会说明） |
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
.venv/bin/python -m pytest -q tests                      # 离线：解析/归一化、判定流程、Overture 映射、各国数据源的响应解析、网页路由、上游客户端（1831 个）
.venv/bin/python scripts/smoke_test.py http://127.0.0.1:8000   # 在线：60 个真实地址端到端（美国 21 个，含 Overture 兜底和国家识别；欧洲和日本 39 个）
```

## 已知局限

- **覆盖不全**：NAD 依赖各州/县自愿提交，约 600 个县显示 “No statewide data”（如加州大部分县）。
  Overture 兜底能补上很大一部分，但仍有两边都没数据的地区（返回 `not_covered`）。
- **Overture 不是权威源**：它是汇总数据，`confidence` 已打 9 折；`source=overture:NAD` 的记录用的 NAD 版本未公开，
  可能与官方 r23 服务不一致，属正常现象。坐标为 float32（约 1 m 精度）。
- **Census 会“纠正”方向**：例如把 `200 N Spring St` 匹配成 `200 S SPRING ST`。匹配时以用户输入的方向为准，并在 `notes` 中提示。
- **NAD ≠ USPS 可投递**：`verified` 只说明物理地址点存在。需要可投递性请再接 USPS/CASS 类服务。
- 单元号覆盖取决于数据提供方，很多地区只有楼栋点，因此有 `unit_not_found` 这一中间态。
- 依赖 Census 地理编码定位；新建地址可能不在 TIGER 中 → 自动按 ZIP / 城市范围查同名街道（见[上文](#census-定位不到的地址)），也可以直接传 `lat`/`lon`。
- 威斯康星网格门牌（`N95W18000 ...`）等特殊格式暂不解析。
- 上游是公共免费服务，冷查询偶尔 5–15 s；生产环境建议保留缓存与限流。
- 若需高并发/离线/SLA，仍建议按州下载 NAD 自建 PostGIS（可沿用本项目的解析与匹配逻辑）。
- **欧洲和日本**：英国、爱尔兰、瑞典等没有免费官方门牌数据的国家只能靠 OpenStreetMap，找不到门牌时无法判断；
  日本大部分城区只能核实到番；奥地利和日本用的是没有公开承诺的接口，可能变动。
