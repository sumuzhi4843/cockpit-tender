# 招投标信息 · 维护手册

> **架构精简（2026-09-29）**：tender.json 全量导出已下线（SQLite→8002 API 唯一出口）；6 份状态文件合一为 `logs/run_state.json`；
> 8002 由 systemd `tender-district-api.service` 托管；一次性回填/修复脚本归档 `archive/{migrations,rollback,oneoff-cron,docs}`；
> 统一维护入口 `maintenance.py`。下文历史条目凡与本段冲突，以本段为准。

> **单一维护入口** | 最后更新：2026-08-06（23 站详细抓取规则 + PDF 补抓接入定时） | 维护：yankai agent
> 工作目录：`/root/.openclaw/workspace/agents/yktender/projects/cockpit/tender/collector/`
> 本手册由原 `SCRAPING_RULES.md` + `DEPLOY_PLAN.md` 合并而成。前端 Docker 细节见 `projects/cockpit/README.md`。

---

## 目录

1. [系统架构与数据流](#一系统架构与数据流)
2. [快速上手（触发 / 部署 / 定时任务）](#二快速上手)
3. [23 个配置来源抓取规则](#三23-个配置来源抓取规则)
4. [识别与分类（ICT / 行业 / 公告类型）](#四识别与分类ict--行业--公告类型)
5. [富化与打标（字段提取 + 元数据标签）](#五富化与打标字段提取--元数据标签)
6. [存储、去重与质量校验](#六存储去重与质量校验)
7. [可信关联与项目生命周期（Schema v2）](#七可信关联与项目生命周期schema-v2)
8. [驾驶舱展示](#八驾驶舱展示2026-08-07-多次更新)
9. [监控与排错](#九监控与排错)
10. [稳定性参数](#十稳定性参数)
11. [文件清单](#十一文件清单)
12. [维护注意事项](#十二维护注意事项)
- [附录 A：特殊站点（江苏政府采购网/一汽/奇瑞）](#附录-a特殊站点)
- [附录 B：不可抓取站点](#附录-b不可抓取站点)
- [附录 C：五阶段能力总览](#附录-c五阶段能力总览)

---

## 一、系统架构与数据流

```
tender/collector/
├── config.py              # 站点配置 + ICT 关键词 + 请求参数
├── scrapers.py            # 23 个配置来源的 HTML/API 抓取器
├── enrich_engine.py       # 富化引擎 + 公告分类器（2026-08-07 并入 notice_classifier）
├── store_db.py            # SQLite 公告事实层 + 去重（2026-08-07 并入 store.py）
├── project_engine.py      # 可信关联 + 项目归并/生命周期（2026-08-07 并入 build_project_links）
├── notice_classifier.py   # 兼容层 → enrich_engine（原分类器，2026-08-07 起 re-export）
├── build_project_links.py # 兼容层 → project_engine（原关联器，2026-08-07 起 re-export）
├── store.py               # 兼容层 → store_db（原去重，2026-08-07 起 re-export）
├── manual_overrides.json  # 公告分类/关联人工校正（人工优先）
├── DATA_DICTIONARY.md     # Schema v2 字段、枚举与业务口径
├── enrich_meta.py         # 元数据富化：区县/行业分类/中小微（Phase 5）★ 2026-08-03
├── verify_quality.py      # 数据质量校验（Phase 5）★ 2026-08-03
├── backfill_details.py    # 一汽/中粮既有公告正文回填
├── analyzer.py            # ICT 分级匹配（强 1 个即判定，弱需 2 个以上）
├── reporter.py            # Markdown 日报生成
├── run.py                 # 主入口：抓取→富化→公告事实→项目归并→质量校验→Schema v2 JSON
├── ccgp_scraper.py        # 江苏政府采购网 CDP 抓取器
├── deploy.sh              # 定时部署脚本（run.py + docker cp）
├── data/store.json        # 去重记录
├── data/tender.db         # SQLite 历史库
└── reports/               # 日报输出
```

**数据流向**：
`各站公告 → 字段富化 → notices 公告事实层 → 公告子类型 → 可信关联 → projects/project_notices 项目层 → 项目状态 → SQLite(tender.db) → 8002 API → cockpit 前端`

### Schema v2 业务口径（2026-08-05）

- `notice_type` 回答“这是什么一级公告”：`tender/award/intention/other`
- `notice_subtype` 回答“具体是哪类公告”：招标、候选、最终中标、更正、流废标、终止等
- `project_stage` 回答“项目现在进行到哪一步”：待开标、今日开标、待跟踪、候选、已中标等
- **公告类型不等于项目状态；`award` 是公告类型，`awarded` 是项目状态**
- 中标候选人不再算最终中标；单独“合同”不再触发中标分类
- 无开标时间且发布不超过 30 天 → 时间待确认；超过 30 天 → 待跟踪结果
- 完整字段、枚举和 JSON 契约见 `DATA_DICTIONARY.md`

**富化流程（v2）**：
`详情页正文 → enrich_engine.enrich_detail()（新引擎）→ 合并旧正则 _legacy_extract() 补缺 → 结构化字段`

**一汽/中粮详情正文（2026-08-05）**：
- 一汽：详情外层 iframe 指向询价子系统，调用公开 `queryXiangMuByGuid.do?guid=...` JSON 接口；当前 32 条中 20 条返回完整正文（平均约 3256 字），其余 12 条主接口无正文
- 中粮：列表行 `id + noticeType + publishStatus` 调用公开 `/trade/getByTradeId`；当前 9 条全部补齐结构化正文（平均约 818 字）及采购文件下载地址
- 中粮正确页面路由为 `#/trade-info-detail?id=...&noticeType=...&publishStatus=...`；数据库稳定 URL 不变，JSON 导出优先使用正确详情 URL
- 当前不批量下载 PDF/Word 附件，只保存下载地址，避免定时任务耗时和文件膨胀
- 既有空正文回填：`python3 backfill_details.py --site all`；默认只处理空正文，`--force` 可强制刷新

**存储双轨**：
- `store.json`：MD5 去重，仅控制报告推送（FIFO 5000）
- `tender.db`：SQLite 全量历史，可回溯/按类型/站点/日期查询，upsert 按 URL 去重

**关键设计**：
- 去重只控制报告推送，**不影响** cockpit 数据（8002 API 直读 SQLite，恒全量最新）
- 单站异常不中断整体流程（try/except + traceback）
- 每站抓完 sleep 1.5s，详情页 sleep 0.5s
- 富化采用**合并策略**：新引擎优先，旧正则补缺，最大化字段覆盖率

---

## 二、快速上手

### 2.1 定时任务（已配置，2026-08-09 重排为 5 个）
```cron
# 主抓 23 站（run.py + PDF 补抓 + 重建 + 部署）每天 8/12/18 三次
0 8,12,18 * * *    /bin/bash /root/.openclaw/workspace/agents/yktender/projects/cockpit/tender/collector/deploy.sh
# KPI 顶栏数据采集（Agent/Skills/Token/Sessions），每 3 小时
# 国投限流期重试（避开主抓 8/12/18），每天 3 次
0 9,13,17 * * *    /bin/bash /root/.openclaw/workspace/agents/yktender/projects/cockpit/tender/collector/site_refresher.sh sdicc
# 凌晨补偿 A 批（江苏政采 + 中国政采，只补主抓失败站），工作日
0 3 * * 1-5        /bin/bash /root/.openclaw/workspace/agents/yktender/projects/cockpit/tender/collector/site_refresher.sh deep-a
# 凌晨补偿 B 批（苏服采 + 盐城政采，补抓完统一重建+部署），工作日
30 3 * * 1-5       /bin/bash /root/.openclaw/workspace/agents/yktender/projects/cockpit/tender/collector/site_refresher.sh deep-b
```
- **主抓**：每天 8:00 / 12:00 / 18:00 三次，`deploy.sh` 执行（run.py + PDF 补抓 + 重建 + 部署）★ 2026-08-04 增频
- **顶栏统计**：招投标数据统一由 8002 `/api/tender/meta` 提供；不再维护独立 overview.json 链路。
- **凌晨补偿**（2026-08-09 新增）：`site_refresher.sh` 按入参分派，只补偿**主抓失败**的站（读 `logs/run_state.json` 的 `failures`），避免每天全量重抓造成反爬风险；deep-a 只补抓，deep-b 补抓完统一重建。
- **调度原则**：补缺任务全部错开主抓 8/12/18 窗口；重型补抓放凌晨低峰；周末跳过（`1-5`）
- 输出日志：`tender/collector/cron.log`（tender）、`tender/collector/logs/site_refresher.log`（补抓）、`tender/collector/logs/run_state.json`（统一运行状态）

### 2.2 每日自动流程
```
run.py → 抓取 23 个配置来源 → 富化 → SQLite 落库 → 关联构建
→ 质量门禁（verify_quality，不过则回滚临时快照）
→ PDF 补抓（中国邮政缺开标时间 + 江苏招标平台近 3 天中标 PDF）★ 2026-08-06 接入
→ 重建项目 → SQLite 发布（run_state.manifest v3；8002 API 即时生效，无静态 JSON）
```

### 2.3 手动触发
```bash
cd /root/.openclaw/workspace/agents/yktender/projects/cockpit/tender/collector
python3 run.py        # 仅抓取（不含 PDF 补抓）
bash deploy.sh        # 完整流程（抓取 + PDF 补抓 + 部署）
# 单独补 PDF：
python3 backfill_chinapost_pdfs.py          # 中国邮政缺开标时间/金额（增量）
python3 backfill_chinapost_pdfs.py --url <url>  # 单条
python3 backfill_jszbcg_pdfs.py --days 3    # 江苏招标平台近 3 天中标 PDF
python3 backfill_jszbcg_pdfs.py --limit 1   # 只处理 1 条（验证）
python3 backfill_sdicc.py                   # 国投存量 URL 修复 + 详情补抓
python3 retry_sdicc.py                      # 国投限流期自动重试（30min/次，最长 6h）
# 一次性回填脚本已归档 archive/migrations/（含 backfill_3sites.py）
# 统一维护入口：python3 maintenance.py list
# 凌晨补偿（2026-08-09）：
bash site_refresher.sh deep-a              # 手动补偿 A 批（江苏+中国政采）
bash site_refresher.sh deep-b              # 手动补偿 B 批（苏服采+盐城政采）+ 重建部署
python3 deep_refresher.py --group A        # 直接调 deep_refresher（A 批）
python3 deep_refresher.py --sites 盐城市政府采购网 --rebuild   # 指定单站 + 重建
python3 deep_refresher.py --dry-run        # 只看计划不实际抓
bash site_refresher.sh sdicc               # 手动触发国投限流重试
```

### 2.4 部署到驾驶舱
```bash
# 数据更新不再需要 docker cp：deploy.sh 完成 SQLite 发布后，8002 API 即时读取（2026-09-29 起）

# 前端 JS/CSS 改动后（整目录同步）
docker cp projects/cockpit/site/. cockpit:/usr/share/nginx/html/
```
- 容器：`cockpit`，端口 5310，webroot `/usr/share/nginx/html/`
- 地址：`http://<SERVER_IP>:5310/`

### 2.5 执行频率（当前生效）
| 任务 | 频率 | 说明 |
|---|---|---|
| 招投标主抓 | 每天 08:00 / 12:00 / 18:00 | `deploy.sh`：抓取 + PDF 补抓 + 重建 + 部署（2026-08-04 起 3 次/天） |
| 顶栏统计 | 实时 | 8002 `/api/tender/meta` 直读 DB（overview.json 链 2026-09-29 下线） |
| 国投限流重试 | 每天 09:00 / 13:00 / 17:00 | `site_refresher.sh sdicc`（2026-08-09 新增，避开主抓窗口） |
| 凌晨补偿 A 批 | 工作日 03:00 | `site_refresher.sh deep-a`：江苏政采 + 中国政采（只补主抓失败站） |
| 凌晨补偿 B 批 | 工作日 03:30 | `site_refresher.sh deep-b`：苏服采 + 盐城政采（补抓完重建+部署）|

> 升频/降频只需改 crontab，`deploy.sh` 逻辑不变。

### 2.6 数据获取能力分级（抓取方式 + OCR 补充）★

> 两套分级回答两个问题：**公告数据怎么拿到**（抓取方式 S1–S4）；**拿到后字段缺失怎么补**（OCR 补充 L0–L4）。

#### 抓取方式分级（S1–S4，按技术手段从轻到重）

| 级别 | 方式 | 技术手段 | 成本 | 适用站点 |
|---|---|---|---|---|
| **S1** | 静态 HTML 直接解析 | `requests` + BeautifulSoup 解析列表页；详情页也是 HTML | 最低（无浏览器） | 15 站：本地 5 站、悦达阳光、三峡集团、江苏农华、国投集团、盐城市政府采购网、盐城市人民政府网、中国政府采购网、开发区公共资源交易网、城南新区公共资源交易网 |
| **S2** | JSON/POST API 直连 | 直接调站点 JSON 接口（部分需 POST/验证码/参数逆向），结构化返回，比 HTML 解析稳定 | 低 | 6 站：中粮e采、一汽电子采购、奇瑞电子采购、江苏招标采购服务平台、盐城市公共资源交易网、苏服采 |
| **S3** | CDP 无头浏览器渲染 | 页面 JS 异步渲染 + 验证码，headless Chromium 驱动（API 兜底） | 中（启浏览器） | 1 站：江苏政府采购网 |
| **S4** | PDF 正文提取 | 详情页正文是 pdfjs 内嵌 PDF（HTML 无文字），下载 PDF 提文本层 | 中（下载+解析） | 1 站：中国邮政（江苏招标平台中标公告同此路径） |

**维护要点（按级别）**：
- **S1**：站点改版首先影响选择器，逐个排查 `parse_list`；国投详情页需 `gcGuid+ggGuid` 双参数（缺一被 WAF 拦）+ POST 翻页；中国政采列表日期含点号格式（`2026.08.06`）
- **S2**：接口变更/验证码策略变化时需重新逆向；江苏政采搜索接口有频控（ddddocr 识码）
- **S3**：chromium 依赖（`/snap/bin/chromium`），页面结构改版需同步改 CDP 步骤
- **S4**：PDF URL 藏在 iframe `file=` 参数（注意 `%26` 需 unquote 解码）

#### OCR 补充分级（L0–L4，字段缺失自动补抓）

> 与 `backfill_open_dates.py` 一一对应；定时任务（deploy.sh）每次更新后自动执行 `--days 30`。

| 级别 | 含义 | 处理方式 | 成本 | 适用站点 |
|---|---|---|---|---|
| **L0** | 正文/接口完整，直接提取 | 富化引擎 `enrich_detail` 从库内 content 提取 | 0 token | 本地 5 站、多数 API 站 |
| **L1** | 库内已有正文但提取失败 | 补抓时从 `raw_json.content` 重新富化提取 | 0 token | 各站兜底 |
| **L2** | 详情页 HTML 有正文 | 抓详情页正文（`DETAIL_SELECTORS` 按站配置）再提取 | 0 token | 静态 HTML 站（本地 5 站/悦达/三峡/农华/瑞康/国投/政采类） |
| **L3** | PDF 有文本层 | 下载 PDF → `fitz` 提文本层 → 正则提取（含预算金额，2026-08-07 扩展） | 0 token | 中国邮政（pdfjs 内嵌）、江苏招标平台（中标 PDF） |
| **L4** | 扫描件 PDF 无文本层 | Tesseract `chi_sim+eng` OCR → 提取；低置信 → MiniMax-M3 校正 | 0 token / 少量 | 江苏招标平台扫描中标公告 |

**执行顺序**：L1 → L2 → L3 → L4，命中即停；处理后写 `raw_json.backfill_tried` 防重复撞墙。

**两套分级的关系**：S 级决定"首次抓取用什么手段"，L 级决定"抓回来的公告缺字段时往哪里深挖"。例：中国邮政 = S4 抓取 + L3 补字段；江苏招标平台 = S2 抓取 + L3/L4 补中标字段。

---

## 三、23 个配置来源抓取规则

> 每站表格标注「抓取方式级别 S1–S4」+「OCR 补充级别 L0–L4」，完整定义见 **2.6 数据获取能力分级**。

### 3.0 列表页翻页（2026-08-04 P1-3）★
> 解决中标（award）数据少的问题：原 HTML 站点只抓列表页第 1 页，中标公告多在后续页面。

- `BaseScraper` 内置翻页能力：`max_pages`（默认 2，抓第 1+2 页）+ `get_page_url(page)`
- 已支持翻页的站：盐城市大数据集团(`19-N.html`)、盐城市都市建设投资集团有限公司(`?zhaobiao_N/`)、江苏世纪新城/盐城市东方集团/经开城发集团(`index_N.html`)、悦达(`index_N.jhtml`)、农华(`?page=N`)——均实测第 2 页正常
- 不翻页的站（`max_pages=1`）：中国邮政、三峡（分页为 `javascript:;`）
- 中粮/一汽/奇瑞/江苏政府采购网及表格补充的 API 站点使用独立 scraper，自带翻页，不受影响
- 跨页按 URL 去重；单页异常跳过不中断
- **效果**：award 13→29、links 3→13（2026-08-04 实测）

### 3.1 盐城本地平台（5 个，静态 HTML）

> 特点：全部静态列表页 + 详情页 HTML，`requests + BeautifulSoup` 即可，无需 JS 渲染。
> 翻页统一走 `BaseScraper`：`max_pages=2`（第 1+2 页），`get_page_url(page)` 生成页码 URL。

#### ① 盐城市大数据集团（`YcDataGroupScraper`）
| 项 | 值 |
|---|---|
| 列表页 | `http://www.ycdatagroup.cn/news/19.html`（展示用 `https://www.ycdatagroup.cn/news/19.html`） |
| 翻页 | `19-N.html`（N=2 即第 2 页） |
| 列表选择器 | `<a href>` 含 `show-` |
| 标题提取 | `<a>` 全文本 → 用 `NOTICE_TYPES` 关键词切割（如"招标公告""中标结果公示"） |
| 日期提取 | 正则 `(\d{1,2})2026\.(\d{2})$` 匹配标题末尾 |
| 去重键 | href |
| 抓取方式级别 | **S1 静态HTML解析**（requests+BeautifulSoup 抓列表+详情，无浏览器） |
| OCR 补充级别 | L0（正文完整直接提取）；补抓兜底 L2 |
| ⚠️ 注意 | 年份硬编码 2026，**跨年需更新** |

#### ② 盐城市都市建设投资集团有限公司（`YcDsjtScraper`）
| 项 | 值 |
|---|---|
| 列表页 | `http://www.ycdsjt.cn/?zhaobiao/` |
| 翻页 | `?zhaobiao_N/` |
| 列表选择器 | `<dt class="f18">` 下的 `<a href>` |
| 日期提取 | 父级 `<dl>` → `<dd class="date">` → `<span>`（**只有日**）→ 补当前年月 |
| 去重键 | href |
| 抓取方式级别 | **S1 静态HTML解析**（requests+BeautifulSoup 抓列表+详情，无浏览器） |
| OCR 补充级别 | L0（正文完整直接提取）；补抓兜底 L2 |
| ⚠️ 注意 | 日期只有日数字，跨月可能出错（2026-08-06 已加"晚于今天自动回退上月"保护） |

#### ③④⑤ 江苏世纪新城 / 盐城市东方集团 / 经开城发集团（共用 `CommonListScraper`）
| 项 | 值 |
|---|---|
| 列表页 | 世纪新城 `jscncg.com/tenderLease/tender/`；东方集团采集 `ycdfjt.com/zbzl/qzzgs/`（展示 `ydfjt.cn`）；经开城发采集 `ycjkct.com/zbcg/zbxx/`（表格入口 `/business/town/`） |
| 翻页 | `index_N.html` |
| 列表选择器 | `<li>` 内 `<a href>` + `<span>` |
| 标题提取 | `<a title="...">` 属性优先，否则 get_text |
| 日期提取 | `<span>` 文本，匹配 `\d{4}-\d{2}-\d{2}` |
| 去重键 | href |
| 抓取方式级别 | **S1 静态HTML解析**（requests+BeautifulSoup 抓列表+详情，无浏览器） |
| OCR 补充级别 | L0；补抓兜底 L2 |

### 3.2 外部平台（7 个 HTML）

#### ⑥ 悦达阳光（`YdSunshineScraper`）
| 项 | 值 |
|---|---|
| 列表页 | `ydtender.com/zbgg/index.jhtml` |
| 翻页 | `index_N.jhtml` |
| 列表选择器 | `<a href>` 匹配 `/z[a-z]+gg/\d+\.jhtml` |
| 日期提取 | 标题链接的兄弟 `<p>` 元素 |
| 去重键 | href |
| 抓取方式级别 | **S1 静态HTML解析**（requests+BeautifulSoup 抓列表+详情，无浏览器） |
| OCR 补充级别 | L0；补抓兜底 L2 |

#### ⑦ 中国邮政（`ChinaPostScraper`）
| 项 | 值 |
|---|---|
| 列表页 | `cg.11185.cn/`（电子采购平台首页） |
| 翻页 | `max_pages=1`（首页 JS 加载，无可靠分页链接；每次抓最新 7 条左右，滚动积累） |
| 列表选择器 | `<a href>` 含 `biddingBulletin` |
| 日期提取 | URL 内 `/biddingBulletin/YYYY-MM-DD/` |
| 去重键 | href |
| 抓取方式级别 | **S4 PDF 正文提取**（详情为 pdfjs 内嵌 PDF，下载提文本层） |
| OCR 补充级别 | **L3**（详情页正文为 pdfjs 内嵌 PDF，有文本层直接提取）；扫描件 L4 OCR+M3；已接入定时（backfill_chinapost_pdfs） |
| 📄 **PDF 补抓（2026-08-06 接入）** | 详情页正文为 **pdfjs 内嵌 PDF**（iframe `pdfContainer`），HTML 无文字 → 爬虫抓不到开标时间。`backfill_chinapost_pdfs.py` 下载 PDF → `pdfplumber` 提文本层 → 正则提取开标/截止时间回填（2026-08-07 扩展：同时提取项目预算，带分包/单价防误提守卫，金额覆盖 0%→61%）。**已在 deploy.sh 定时接入** |

#### ⑧ 三峡集团（`CtgScraper`）
| 项 | 值 |
|---|---|
| 列表页 | `eps.ctg.com.cn/cms/channel/1ywgg1/index.htm` |
| 翻页 | `max_pages=1`（分页为 `javascript:;`，需 JS 渲染） |
| 列表选择器 | `ul#list1 > li > a` |
| 标题提取 | `<a>` 文本，去掉末尾日期 |
| 日期提取 | `<li>` 内日期文本 |
| 去重键 | href |
| 抓取方式级别 | **S1 静态HTML解析**（requests+BeautifulSoup 抓列表+详情，无浏览器） |
| OCR 补充级别 | L0；补抓兜底 L2 |

#### ⑨ 江苏农华（`JsNonghuaScraper`）
| 项 | 值 |
|---|---|
| 列表页 | `jd.dongyin.com/index/zhaobiao/index/type_id/8.html` |
| 翻页 | `?page=N` |
| 列表选择器 | `<a class="infofl">` 内 `infot1` / `infot2` |
| 去重键 | href |
| 抓取方式级别 | **S1 静态HTML解析**（requests+BeautifulSoup 抓列表+详情，无浏览器） |
| OCR 补充级别 | L0；补抓兜底 L2 |

#### ⑪ 国投集团（`SdiccScraper`）
| 项 | 值 |
|---|---|
| 列表页 | `sdicc.com.cn/cgxx/ggList`（POST 表单翻页，`currentPage` 参数） |
| 翻页 | `max_pages=3`（2026-08-07 逆向 POST 翻页） |
| 列表选择器 | `<tr onclick="urlChange('ggGuid','gcGuid')">` |
| 去重键 | ggGuid |
| 抓取方式级别 | **S1 静态HTML解析**（requests+BeautifulSoup 抓列表+详情，无浏览器） |
| OCR 补充级别 | L0；补抓兜底 L2 |

> ⚠️ 详情页必须同时携带 `gcGuid` + `ggGuid` 双参数，缺一即被 WAF 拦截（403/406）。
> 2026-08-07 前旧数据 URL 只带单参数导致详情全失败，用 `backfill_sdicc.py` 修复（限流期用 `retry_sdicc.py` 自动重试）。站点有请求限流，批量请求间隔 ≥2s。

### 3.3 JSON/POST API 站（7 个）

#### ⑫ 中粮e采（`CofcoEScraper`）
| 项 | 值 |
|---|---|
| API | `POST ecai.cofco.com/api/saas-portal/noauth/trans/trade/pageEs` |
| 请求体 | `{"pageNo":1,"pageSize":50,"businessName":"盐城"}` |
| 过滤 | ① `businessName` 含"盐城" ② `purchasingMethodName` 前缀：询比采购/谈判采购/公开招标 |
| 翻页 | 每页 50 条，最多 20 页，`totalCount` 达标即停 |
| 字段 | 标题 `businessName`；日期 `releaseTime`（ms）；截止 `bidEndTime`（ms）；发包人 `tendererName` |
| 去重键 | businessId 或 id |
| 抓取方式级别 | **S2 JSON/POST API 直连**（调站点 JSON 接口，结构化返回） |
| OCR 补充级别 | L0（API 直给 openBidTime/截止时间，正文接口 getByTradeId） |
| 📄 详情 | 详情接口 `getByTradeId`（2026-08-04 接入），返回项目概况/采购范围/资格条件/附件名 |

#### ⑬ 一汽电子采购（`FawScraper`）
| 项 | 值 |
|---|---|
| 列表页 | `POST etp.faw.cn/gg/allJYTypeGGList`（`searchType=5, searchText=盐城`） |
| 抓取方式 | POST 表单 + HTML 解析（服务端渲染） |
| 标题提取 | `<div class="title">` |
| 日期提取 | `<div class="zl-desc-item">` 中 `发布时间：(\d{4}-\d{2}-\d{2})` |
| 发包人 | `<div class="zl-desc-item">` 非日期/非编号文本 |
| 翻页 | `currentPage` 递增，检测 `#currentPageInput.max` 判断末页 |
| 去重键 | guid |
| 抓取方式级别 | **S2 JSON/POST API 直连**（调站点 JSON 接口，结构化返回） |
| OCR 补充级别 | L0（详情接口 queryXiangMuByGuid 返回正文，直接提取） |
| 📄 详情 | 公开接口 `queryXiangMuByGuid.do?guid=<GUID>`（2026-08-04 接入），20/32 条直接返回完整正文 |

#### ⑭ 奇瑞电子采购（`CheryScraper`）
| 项 | 值 |
|---|---|
| 列表页 | `POST ebd.mychery.com/cms/api/dynamicData/queryContentPage` |
| 请求体 | `{"pageNo":1,"pageSize":50,"dto":{"siteId":"747","categoryId":"5035","city":"320900"}}` |
| 过滤 | `city=320900`（盐城市） |
| 字段 | 标题 `title`；日期 `publishDate`；截止 `quoteEndTime`/`signUpEndTime`；发包人 `agentCompanyName`；正文 `description`/`text` |
| 翻页 | 每页 50 条，最多 20 页，`total` 达标即停 |
| 去重键 | detail_url |
| 抓取方式级别 | **S2 JSON/POST API 直连**（调站点 JSON 接口，结构化返回） |
| OCR 补充级别 | L0（API 直给 quoteEndTime） |

#### ⑮ 江苏政府采购网（`CcgpJiangsuScraper`，`ccgp_scraper.py`）
| 项 | 值 |
|---|---|
| 列表页 | `ccgp-jiangsu.gov.cn/jiangsu/cggg_search.html?lmid=cggg&qh=notic_c3` |
| 抓取方式 | ① CDP 浏览器自动化（首选）② 直接 API + 验证码 OCR（回退，`CCGP_USE_CDP=0` 强制） |
| 抓取方式级别 | **S3 CDP 无头浏览器**（headless Chromium 渲染 + 验证码；API + 验证码 OCR 作为回退） |
| OCR 补充级别 | L0（正文接口返回，富化 92% 开标覆盖）；补抓兜底 L2 |
| 验证码 | ddddocr（首选）→ tesseract（备选）→ 保存图片（最后） |
| 筛选 | 盐城经济开发区 (320991) + 盐南高新技术产业开发区 (320992) |
| 公告类型 | 采购公告(cggg) + 中标公告(zbgg) + 成交公告(cjgg) + 更正公告(gzgg)（2026-08-07 扩展，补齐历史中标） |
| 搜索 API | `GET /pss/jsp/search_cggg.jsp` 参数 `page/qy/pqy/cglx/code`；cglx 逐类型遍历 |
| 详情正文 | 逆向接口 `POST /pss/jsp/relevantCgggGetById.jsp`（参数 `ggid`）；采购公告正文在 `data.content`，中标/成交在 `data.summary`（2026-08-07 兼容）；`ict_only=False` 全量抓详情 |
| 富化效果 | 发包人 97% / 金额 99% / 开标 92%（采购公告）；中标/成交公告可提取中标单位 |
| 专项抓取 | `python3 scrape_ccgp_only.py`（只跑本站，含中标/成交，免全量 run.py 耗时） |
| ⚠️ | CDP 需 Chromium（`setup_chromium.sh`）；`CHROMIUM_PATH` 环境变量 |

#### ⑯ 江苏招标采购服务平台（`JszbcgScraper`）
| 项 | 值 |
|---|---|
| API | `GET api.jszbtb.com/DataGatewayApi/PublishBulletins` |
| 参数 | `bulletinType`(1招标/2候选/3中标/4更正) + `regionCode=320900` + 14 天窗口 |
| 翻页 | **按 `totalPage` 动态翻页**（2026-08-06 修复，单类上限 30 页=600 条；原固定 2 页截断约 60% 数据） |
| 列表字段 | `bulletinID`/`bulletinName`/`noticeSendTime`/`openBidTime`/`regionName` |
| 去重键 | bulletinID |
| 抓取方式级别 | **S2 JSON/POST API 直连**（调站点 JSON 接口，结构化返回） |
| OCR 补充级别 | L3/L4（中标详情为 PDF：文本层直接提；扫描件 Tesseract OCR + MiniMax-M3 校正）★ 已接入定时（backfill_jszbcg_pdfs + backfill_open_dates） |
| 📄 **PDF 补抓（已接入定时）** | 中标详情多为 PDF。`backfill_jszbcg_pdfs.py --days 3` 下载 → 文本层提取（`fitz`）→ 富化回填中标单位/金额；扫描件标记 `needs_ocr`（`ocr_jszbcg_pdfs.py` + MiniMax-M3 `organize_jszbcg_ocr_with_m3.py`），不自动阻塞定时任务 |

#### ⑰ 盐城市公共资源交易网（`YcPublicResourceScraper`）
| 项 | 值 |
|---|---|
| API | `GET ycggzy.jszwfw.gov.cn/cums/home/notice/noticePage` |
| 翻页 | 2 页 |
| 特点 | 正文随 JSON 返回，富化字段覆盖高 |
| 去重键 | noticeId |
| 抓取方式级别 | **S2 JSON/POST API 直连**（调站点 JSON 接口，结构化返回） |
| OCR 补充级别 | L0（正文随 JSON 返回） |

#### ⑱ 苏服采（`SufucaiScraper`）
| 项 | 值 |
|---|---|
| API | `GET js.fwgov.cn:868/purchases/tenders/notice` |
| 过滤 | `areaCode=320900`（盐城） |
| 翻页 | 比选及结果各 2 页 |
| 成交金额 | 成交/结果公告的 `transactionPrice` → winning_amount + amount（2026-08-07 修复：原字符串金额被 isinstance 漏掉且未进 winning_amount） |
| 详情正文 | 逆向接口 `POST /pss/jsp/relevantCgggGetById.jsp` 类似机制（2026-08-04 打通，富化 97%/99%/92%） |
| 抓取方式级别 | **S2 JSON/POST API 直连**（调站点 JSON 接口，结构化返回） |
| OCR 补充级别 | L0（API/正文接口返回）；补抓兜底 L2 |
| ⚠️ | 公开结果 API **不返回成交供应商**（只有采购人），质量门禁单独预警，不猜测 |
| 存量回填 | 已归档 `archive/migrations/backfill_missing_fields.py`（2026-09-29；存量已洗完进入稳态） |

### 3.4 表格补充来源（8 个，2026-08-05 接入）

#### ⑲ 盐城市政府采购网（`YcGovProcurementScraper`，**2026-08-08 v2 重写**）
| 项 | 值 |
|---|---|
| 列表页入口 | `czj.yancheng.gov.cn/col/col2383/index.html`（财政局聚合页，只展示各栏目最新 5 条） |
| 主抓取页 | **每个子栏目独立列表页** `/col/col{ID}/index.html`（server 端渲染首屏 `<record>` 块，最多 ~45 条） |
| 翻页 | `max_pages=1`（聚合页无分页；通过子栏目页覆盖数据） |
| 栏目发现 | 自动从聚合页 `art_(\d+)_` 中提取所有子栏目 ID，排除 `EXCLUDED_COLS = {"2387", "2388", "2407"}`（通知公告 / 诚信平台 / 办事指南） |
| 实际子栏目 | ~29 个：24544/24547 采购意向、24546/24549 单一来源、20139 公开招标征集、20141 竞争性谈判、20142 竞争性磋商、20144 询价、20137/20185 合同公告、20145/20180 中标公告、20146/20181 成交公告、20147/20182 终止公告、20148/20183 更正公告、20174-20179 县区采购公告、31689-31692 资格预审/中标入围、33995-33999 框架协议/征集、34066 驻盐单位采购 |
| 典型产出 | 聚合页 133 条 + 子栏目 999 条 ≈ **1132 条/次**（2026-08-08 回填验证） |
| `<record>` 解析 | 注意 `<a>` 标签 `title` 和 `href` **顺序不固定**，分别独立抽取；日期兜底取 URL 中 `/art/YYYY/M/D/` 或 `<span class="bt-list-time">` |
| WAF | 子栏目页请求头用浏览器 UA（`Mozilla/5.0 … Chrome/120 …`）即可过；AJAX 分页接口 `/module/web/jpage/dataproxy.jsp` 被知道创宇拦截，每栏最多 45 条（不是全量 1000+） |
| 去重键 | /art/ 链接 URL |
| 抓取方式级别 | **S1 静态HTML解析**（requests+BeautifulSoup 抓列表+详情，无浏览器） |
| OCR 补充级别 | L0（正文接口返回） |
| ⚠️ v1 vs v2 | v1（2026-08-05）只采 4 个子栏目（24544/24546/31689/20139），实际只有 ~18 条/次；v2 覆盖 ~29 个子栏目，~1132 条/次（约 62×）。**所有文档/截图/驾驶舱引用以 v2 为准** |

#### ⑳ 盐城市人民政府网（`YanchengGovScraper`）
| 项 | 值 |
|---|---|
| 列表页 | `yancheng.gov.cn/col/col16735/index.html`（iframe 内嵌） |
| 实际列表 | `module/xxgk/search_custom.jsp?fieldConfigId=38195` |
| 翻页 | `sortfield=createdatetime:0&currpage=N`，2 页 |
| 去重键 | /art/ 链接 URL |
| 抓取方式级别 | **S1 静态HTML解析**（requests+BeautifulSoup 抓列表+详情，无浏览器） |
| OCR 补充级别 | L0；补抓兜底 L2 |

#### ㉑ 中国政府采购网（`ChinaGovProcurementScraper`）
| 项 | 值 |
|---|---|
| 列表页 | `search.ccgp.gov.cn/bxsearch?searchtype=2&kw=盐城` |
| 翻页 | `max_pages=1`（官方检索频控，只取第 1 页 16 条） |
| 过滤 | ① 标题/列表文本含盐城辖区关键词 ② **正文行政区域二次校验**（排除"仅偶然出现盐城"的外地公告） |
| 日期提取 | 兼容 `2026-08-06` / `2026年08月06日` / **`2026.08.06`**（点号格式 2026-08-06 修复） |
| 发布日期兜底 | 列表页日期缺失时，从正文「公告时间」回填 publish_date（2026-08-07，`backfill_missing_fields.py`，现归档 archive/migrations/） |
| 频控 | 命中"频繁访问"页 → 本轮安全跳过（由省/市采购源覆盖） |
| 去重键 | URL |
| 抓取方式级别 | **S1 静态HTML解析**（requests+BeautifulSoup 抓列表+详情，无浏览器） |
| OCR 补充级别 | L0；补抓兜底 L2（官方检索，正文行政区域校验） |

#### ㉒ 开发区公共资源交易网（`LegacyPublicResourceScraper`）
| 项 | 值 |
|---|---|
| 列表页 | `218.92.181.186:8081/jyxx/about.html` |
| 翻页 | 2 页 |
| 去重键 | href |
| 抓取方式级别 | **S1 静态HTML解析**（requests+BeautifulSoup 抓列表+详情，无浏览器） |
| OCR 补充级别 | L0；补抓兜底 L2 |

#### ㉓ 城南新区公共资源交易网（`LegacyPublicResourceScraper`）
| 项 | 值 |
|---|---|
| 列表页 | `221.231.11.22:8099/jyxx/tradeInfo.html` |
| 翻页 | 2 页 |
| 去重键 | href |
| 抓取方式级别 | **S1 静态HTML解析**（requests+BeautifulSoup 抓列表+详情，无浏览器） |
| OCR 补充级别 | L0；补抓兜底 L2 |

> 两个旧交易网共用 `LegacyPublicResourceScraper`（静态列表 + 详情 HTML）。

### 3.5 OCR 补充分级体系（2026-08-06）★

> 完整分级（含抓取方式分级 S1–S4 + OCR 补充 L0–L4）已统一移至 **2.6 数据获取能力分级**（快速上手章）。
> 此处仅留维护入口：

**相关脚本**：
- `backfill_open_dates.py`：统一分级补抓（deploy.sh 定时调用）
- `backfill_chinapost_pdfs.py`：中国邮政专属 PDF 补抓（L3/L4，含预算金额提取）
- `backfill_jszbcg_pdfs.py`：江苏招标平台中标 PDF 回填（`--days 3` 增量）
- `ocr_jszbcg_pdfs.py` / `organize_jszbcg_ocr_with_m3.py`：扫描件 OCR + M3 校正
- `backfill_sdicc.py` / `retry_sdicc.py`：国投存量 URL 修复 + 详情补抓（限流自动重试）

---

## 四、识别与分类（ICT / 行业 / 公告类型）

> 三个"贴标签"规则集中在此：**ICT 关键词**（正向命中）、**行业分类**（反向兜底）、**公告类型**（标题推断）。
> 文件：`analyzer.py`（ICT）+ `enrich_meta.py`（行业，读 `rules/category.yaml`）+ `enrich_engine.py`（类型）。

### 4.1 ICT 关键词分级（analyzer.py）
- **强匹配**（1 个即判定）：视频监控、监控系统、弱电、安防、智能化、网络设备、交换机、路由器、防火墙、综合布线、门禁、停车场系统、LED大屏、数据中心、算力、动环监控、物联网、IoT、视频会议、会议系统、光纤、5G、UPS、精密空调、数字孪生、BIM、数据底座、数据空间、智能化工程、安防工程、弱电工程、智慧城市、智慧交通、智慧社区
- **弱匹配**（需 ≥2 个）：大数据、平台、AI、人工智能、机房、智慧、软件、信息系统、服务器、存储、通信、传感器、电子政务、云计算、广播、数字化、信息化
- **判定规则**：1 个强匹配（标题或正文）→ True；≥2 个弱匹配（**仅标题**，正文不算）→ True；其他 → False
- **修订背景**（2026-08-04）：江苏政采补全正文后，正文泛词（平台/软件/电子政务）大量命中弱词致 ICT 37→275 暴涨；改弱词只认标题后稳定在 79 条

### 4.2 行业分类（enrich_meta.py → rules/category.yaml）
- 读 `rules/category.yaml`（2408 行，**编辑无需改代码**）：`must_any` 命中 + `must_not` 排除
- **未命中任何分类 = 信息化商机池**（反向分类思路，与 ICT 正向匹配互补）
- 当前快照：行业分类约 44%，未分类约 56%（作为信息化商机池），比例随数据变化，排查以 `enrich_meta.py --stats` 为准

### 4.3 公告类型推断（enrich_engine.infer_notice_type）
1. 异常类（异常结果/流标/废标/终止公告/更正公告/澄清公告/补充公告/合同变更）→ `other`
2. 中标类（中标/成交/候选/结果公告/评审结果/合同/中选）→ `award`
3. 意向类（采购意向/意向公告/预算公告）→ `intention`
4. 其余 → `tender`

---

## 五、富化与打标（字段提取 + 元数据标签）

> 所有写入 `notices` 行的"补充信息"集中在此，分两层：
> **公告级富化**（从正文提字段，`enrich_engine.py`）+ **元数据打标**（区县/行业/中小微，`enrich_meta.py`）。
> 合并策略：新引擎优先，旧正则补缺；实测覆盖率（5 站 93 条）发包人 94% / 金额 60% / 开标 86%。

### 5.1 调用入口（scrapers.py）
```python
def extract_detail_fields(self, content, title=""):
    nt = infer_notice_type(title)              # tender/award/intention/other
    enriched = enrich_detail(content, nt)      # 新引擎
    old_fields = self._legacy_extract(content) # 旧正则补缺
    for k in ("tenderer", "amount", "bid_deadline"):
        if not enriched.get(k) and old_fields.get(k):
            enriched[k] = old_fields[k]
```

### 5.2 公告级字段提取（enrich_engine.py）
| 字段 | 提取要点 |
|---|---|
| 发包人 `tenderer` | 标签法（采购人/发包人/招标人/业主单位 等 + 后 5 字冒号）；叙事句兜底（因XX公司经营需要/招标人为XX）；机构后缀表 `_ORG_SUFFIX` 校验（公司/集团/局/医院/学校…） |
| 金额 `amount` | 高可信词优先（项目总投资/预算金额/最高限价/控制价…）；排除词表 `BUDGET_EXCLUDE`（保证金/代理费/评审费…）；表格兜底（`采购预算(万元)` 列）；合理性 100 元~50 亿元 |
| 开标/截止时间 | 开标关键词（开标时间/开启时间…）+ 截止关键词（报名截止/投标截止/递交截止…）；开标空时用截止兜底；输出统一 `YYYY-MM-DD HH:MM:SS` |
| 中标单位/金额 | 中标关键词（中标单位/成交供应商/中标人…）；候选人名单第一名；表格/管道表格兜底。<br>**联合写法（2026-08-08 补充）**：`WIN_AMOUNT_KEYWORDS` 含 `中标（成交）金额` / `成交（中标）金额` 等括号联合形式，适配江苏/盐城政采网中标（成交）结果公告。`_parse_amount` 区间 100~5e10，过小金额（如保证金、代理费）会被丢弃 |
| 公告类型 | 见 4.3 |

### 5.3 元数据打标（enrich_meta.py）
| 标签 | 规则 |
|---|---|
| 区县 `std_district` | 优先级：文本关键词 > 公司精准映射 > 站点硬编码（仅本地 5 站）。关键词覆盖 10 区县 + 盐城(=市级兜底)；公司映射（悦达=经开、伍佑=盐南…）；站点硬编码（大数据/都市/世纪新城=盐南，盐东方/经开城发=经开）。前端平铺栏不单列"市级"（并入"全市"） |
| 行业 `proj_major_cat` | 见 4.2 |
| 中小微 `sme_target` | 基于正文检测（仅约 28% 记录有正文，其余"不涉及"）；三分类：专门面向 / 非专门但优惠 / 不涉及 |

### 5.4 用法
```bash
python3 enrich_meta.py            # 元数据全量打标（区县/行业/中小微）
python3 enrich_meta.py --stats    # 仅打印统计
```

---

## 六、存储、去重与质量校验

> 数据落库与质量保障集中在此。文件：`store_db.py`（SQLite + 去重，2026-08-07 并入原 store.py）+ `verify_quality.py`（质量门禁）。

### 6.1 SQLite 存储层（store_db.py）
- 单库 `data/tender.db`，主表 `notices`，WAL + busy_timeout=5000
- **关键接口**：
```python
from store_db import TenderDB
db = TenderDB()
db.upsert(site, item, ict_keywords)            # 按 URL 去重，返回 True=新增
db.count(); db.count_by_type(); db.count_by_site()
db.query(site, notice_type, start, end, limit) # 通用查询
db.site_activity(since='YYYY-MM-DD')           # 站点活跃度：今日抓到数 + 最近 last_seen
```
- **notices 表核心列**：
  - 基础：`id`(md5(site|url|title|date)) / `site` / `notice_type` / `notice_subtype` / `title` / `url` / `publish_date`
  - 时间：`crawl_time`（首次入库，不随更新变）/ `last_seen`（最近抓到，站点活跃度）
  - 富化：`tenderer` / `amount`+`amount_value`+`amount_unit` / `bid_deadline` / `deadline` / `winner` / `winning_amount`
  - 标签：`ict_keywords`(JSON) / `tags`(JSON) / `std_district` / `proj_major_cat` / `proj_minor_cat` / `sme_target`
  - 分类：`classification_confidence` / `reason` / `version`
  - 原始：`raw_json`

### 6.2 双轨去重
| 机制 | 键 | 作用 |
|---|---|---|
| store.json | `md5(title\|url)` | 仅控制报告推送（mark_pushed 用 new_items），FIFO 上限 5000 |
| tender.db upsert | `id`(site+url+title+date) | 全量历史落库，重复采集只更新不重复插入 |

> 项目数据由 8002 API 直读 SQLite，恒全量最新，不受 store.json 裁剪影响（tender.json 2026-09-29 下线）。

### 6.3 质量门禁（verify_quality.py）
- run.py 每次跑完自动执行；**不通过则 deploy.sh 保留线上旧数据**
- **两级校验**：富化字段只对【本地 5 站】查（那里富化强）；外部平台只查记录数；江苏政采历史数据排除
- **基线**（critical=低于即 FAIL，warn=预警）：

| 字段 | 分母 | critical | warn |
|---|---|---|---|
| amount | tender(本地5站) | 50% | 45% |
| bid_deadline | tender(本地5站) | 70% | 65% |
| std_district | 全量(本地5站) | 90% | 85% |
| proj_major_cat | 全量(本地5站) | 30% | 25% |
| 站点数 | 排除江苏政采 | 19-22 | — |
| 未来日期 | 全库 | 0 条 | — |

- **额外检查**：`notice_subtype` 非空；最终中标 winner 覆盖率 ≥80%（排除江苏招标/苏服采/江苏政采历史数据）；未来日期校验（2026-08-06 新增，防日期解析 bug）
- **用法**：`python3 verify_quality.py`（0=通过，1=不达标）

### 6.4 已知限制
- 江苏政采（CDP）历史公告量大，质量门禁单独统计
- 江苏招标平台 API 只回看 14 天（按 totalPage 动态翻页，单类上限 30 页）；2026-08-06 修复前固定 2 页截断约 60% 数据
- 苏服采公开结果 API 不返回成交供应商，质量门禁单独预警

---

## 七、可信关联与项目生命周期（Schema v2）

- **文件**：`project_engine.py`（2026-08-07 起可信关联 + 项目归并集中在此；原 `build_project_links.py` 为兼容层）
- **输出**：`project_links`（含成功和未匹配记录）、`project_chain`、`projects`、`project_notices`
- **触发**：`run.py` 每次在公告落库和元数据富化后自动重分类、关联并重建项目视图

### 公告与项目分层
- `notices` 一行是一篇公告；`projects` 一行是一个业务项目；`project_notices` 保存关联关系
- 项目状态由项目下全部公告按优先级计算：候选公示→`candidate`，只有 `award_final` 才产生 `awarded`

### 关联评分
1. 项目名完全一致 +55；子串 +35；高相似 +30
2. 同平台 +15；采购单位一致 +15；时间合理 +10；区县一致 +5
3. 批次/标段冲突 -30；中标早于招标直接拒绝
4. `≥85=high`、`70–84=medium`、`<70` 不自动关联并记录原因
5. 仅 `manual/high/medium` 可用于折扣率；`medium` 显示"约/可能关联"
6. 人工校正：`manual_overrides.json` 优先于自动规则

### 项目状态（前端导航）
`upcoming`(待开标) / `upcoming_unknown_time`(时间待确认) / `opening_today`(今日开标) / `awarded`(历史中标) / `candidate`(候选) / `pending_result`(待跟踪) / `failed` / `terminated` / `suspended` / `correction` / `contract` / `intention`(商机)

### 用法
```bash
python3 project_engine.py                 # 重建关联 + 项目视图
python3 project_engine.py --stats         # 仅打印匹配统计（build 入口）
python3 build_project_links.py --report   # 兼容层：周期/折扣率报告
```

### 当前状态（2026-08-07 快照）
- 项目数：1440；最终中标 `award_final` 231 条
- 关联：high/medium 已挂接，低可信不自动关联（正确率优先）
- 缺开标时间自动补抓（2026-08-06 起）：`backfill_open_dates.py` 按 OCR 分级（见 3.5）

---

## 八、驾驶舱展示（2026-08-07 多次更新）

- **前端**：`projects/cockpit/site/`（Docker 容器 cockpit :5310，http://<SERVER_IP>:5310/）
- **项目输出口径（原 tender.json Schema v2，现经 API）**：`projects[]`（主列表）、`sites{}`（公告审计）、`summary`、`ict_projects`、`links`、`new_ids`

### 页面结构（自上而下）
1. **顶栏**：标题 + 当前日期 + 更新时间 + **🧩 Skills / 💬 Tokens 统计**（2026-08-06 起，KPI 卡区已移除）
2. **汇总卡 4 张**（2026-08-07 紧凑化，2026-08-07 「抓取时间」卡改为时间与「自动更新」同行）：平台数（点击弹窗）/ 项目数（全市，非中标项目数）/ ICT 相关 / 抓取时间
3. **区县平铺栏**（2026-08-06 起，原下拉改平铺）：全市 + 10 个区县共 11 个按钮，带**全量计数**（不随选中区县变化，2026-08-07 修复）；默认「全市」
4. **项目进度导航**（互斥入口，2026-08-07 重构）：
   - 主入口（左侧）：**全部**（排除已中标）/ **意向招标** / **招标公告** / **开标倒计时** / **待投标**（旧「收藏夹」）
   - 辅助入口（右侧）：**招标公示**（2026-08-07 从历史中标拆出，含中标/成交/中标候选人公示） / **历史中标**（含合并的70 个中标候选人，已中标+候选人=679） / **异常公告** / **待跟踪结果**
   - 入口计数：全部 = 非中标项目数；招标公示 = 88；历史中标 = 591（剔除公示后）
5. **筛选栏**（2026-08-07 紧凑化）：
   - 第一行 6 个筛选平铺：**分类**（政府/企业，2026-08-07 新增）/ ICT / 行业 / 监控网页 / 金额 / 中小微
   - 第二行：标题/发包人搜索框（单独一行）
   - **分类筛选项**：全部 / 政府分类 / 企业分类（按站点归属过滤，与其他筛选项 AND 叠加）
6. **项目列表**（桌面表格 / 移动端卡片，2026-08-07 紧凑化）：
   - 表头 6 列（2026-08-07 删除「行业」「关键词」两列，只做筛选用）：项目名称（长标题省略，hover 看全）/ 来源 / 发布日期 / 发包人 / 金额 / 开标时间（**中文格式 2026年08月06日08时**）
   - 每行左侧 **☆ 收藏按钮**（点击加入待投标，localStorage 持久化，2026-08-07 「加入收藏」改为「加入待投标」）
   - ICT 标签 hover 显示命中关键词；NEW 标记本次新增
7. **手册入口**：维护手册独立展示站 http://<SERVER_IP>:5311/（2026-08-09 起从页脚移出，单独访问）

### 全部视图
- 排除已中标项目（2026-08-07）：仅展示进行中项目（意向/招标/开标/待投标/异常/待跟踪）
- 排序：发布日期倒序，ICT 优先
- 计数：非中标项目总数（与数据库 awarded 总数差值）

### 招标公告视图
- 仅项目 stage = `upup`（未来开标）或 `upup_unknown_time`（开标日未知）
- 筛选「招标公告」即展示「招标公告」+「开标倒计时」+「时间待确认」的项目

### 开标倒计时视图（2026-08-07）
- 定义：现在起到 7 天内（含今天）未开标的项目，包含「今日」+「未来6 天」共 133 项（默认全市）
- 排序：**按开标时间升序**（即将开标的排在最前）—— 区别于其他视图默认按发布日期/ICT优先
- 解析规则：bid_deadline 解析失败归「时间待确认」不进倒计时；有日期无时间按当天 23:59 处理
- 后端 stage 项目（stage=opening_today 或 upcoming 且 open_date 在 7 天内）也强制归倒计时

### 历史中标视图
- 表格列：中标项目 / 中标单位 / 中标金额 / 原预算 / 折扣率 / 关联可信度 / 周期 / 公告日期 / 来源 / 原招标
- **徽章区分**（2026-08-07）：**已中标**（绿色徽章）/ **中标候选人**（橙色徽章，候选公示未出最终结果）—— 两个原本独立，后台 stage 合并为 awarded，前端通过 stage_reason 区分
- 候选人项目无中标单位/金额，列显示「—」
- 数据：项目级 `winner/winning_amount/link_confidence/cycle_days`（Schema v2 可信关联）

### 招标公示视图（2026-08-07）
- 定义：从历史中标拆出，标题含**「结果公示 / 成交公示 / 中标公示」**之一的已中标项目，单独展示（88 项）
- 判定优先级：先于「历史中标」判定（同一项目不重复出现在两边）
- 使用标准 6 列表格（同招标公告视图），不用中标专用 10 列

### 平台明细弹窗（2026-08-07 重做）
- 点击「平台数」汇总卡打开
- 表格列 6 列：**序号** / **分类**（政府/企业徽章）/ **平台名称** / **公告数** / **ICT** / **采集方式** / **留存方式**
- 排序：政府在前（14 家）、企业在后（9 家），组内保持原始顺序
- **删除了「网址」列**（不再展示原始 URL，原前端硬编码的 SITE_URLS 映射已移除）
- 「采集方式」标签：HTML解析 / API接口 / 浏览器渲染 / PDF提取
- 「留存方式」标签：DB存正文 / PDF归档 / PDF+OCR+MD
- 移动端：表格转 4 格紧凑卡片（序号/分类同行、其余4 chip）
- 数据来源：`config.py` 每站 `scrape_method` + `retention` 字段（单一数据源；随 `/api/tender/meta` 的 site_meta 输出）

### 分页
- 每页 `PAGE_SIZE=20`，上一页/下一页 + 页码（多页省略号），纯前端切片；筛选变化重置第 1 页

### 相关文件
| 文件 | 职责 |
|---|---|
| `tender/collector/run.py` | 输出 Schema v2 projects + sites + links/unmatched_notices + 采集方式/留存方式/分类 字段 |
| `projects/cockpit/site/js/tender/*.js`（ESM 模块群，真源 tender/frontend/tender/，v5.12 起） | 项目进度互斥分类（按阶段、含合并与招标公示拆出）+ 区县平铺 + 待投标 + 6 项筛选（分类/ICT/行业/平台/金额/中小微/搜索）+ 平台弹窗（6列含序号分类排序）+ 分页 + 历史中标/招标公示/开标倒计时 视图 |
| `projects/cockpit/site/css/tender.css` | 上述全部样式（含响应式：≤768px 表格转卡片） |
| `projects/cockpit/site/js/kpi.js` | 顶栏 Skills/Tokens 统计（2026-08-06 重写） |
| `projects/cockpit/site/js/enterprise.js` | 企业信息栏：天眼查汇总报告响应式展示（桌面表格/手机卡片）；数据来自 `data/enterprise.json`（2026-08-09 独立成文件） |
| `projects/cockpit/site/js/app.js` | 模块注册中心（2026-08-09 新增）：板块注册表 + 动态 tab + 独立加载/渲染/报错隔离 |
| `projects/cockpit/manual/` | 维护手册独立站（5311，build_manual_site.py 生成，deploy_manual.sh 部署） |

### 前端模块化架构（2026-08-09 ★）

驾驶舱前端改为**板块注册制**：每个板块是一个独立模块（独立 JS + 独立 CSS + 独立数据文件），互不干扰。

**模块注册（示例）**：

```js
// js/enterprise.js 末尾
App.register({
  id: 'enterprise',
  title: '企业信息栏',
  dataUrl: 'data/enterprise.json',   // 板块独立数据文件
  mountId: 'enterprise-body',        // 渲染容器 id（App 自动创建）
  render: renderEnterprise,          // 渲染函数 (data, root)
});
```

**隔离机制**：
- 每个板块独立 fetch 自己的数据文件，加载失败只显示该板块错误占位（含重试按钮），不影响其他板块
- 渲染函数异常同样被 App 捕获，只影响本板块
- 顶栏统计走 8002 `/api/tender/meta`（overview.json 链 2026-09-29 下线），失败仅缺板块不影响主列表

**新增板块三步**（不用改 index.html 结构）：
1. 写一个模块 JS（定义 render 函数 + App.register）
2. 数据放独立 JSON（如 `data/xxx.json`）
3. `index.html` 加一行 `<script src="js/xxx.js">`（在 main.js 之前）

**文件与板块对应**：

| 板块 | JS | CSS | 数据 |
|---|---|---|---|
| 招投标信息栏 | `js/tender/`（main.js 入口，ESM 模块群 2026-09-29 起） | `css/tender.css` | 8002 `/api/tender/*` |
| 企业信息栏 | `js/enterprise.js` | `css/enterprise.css`（2026-08-09 从 tabs.css 拆分） | `data/enterprise.json` |
| tab 框架 | `js/app.js` | `css/tabs.css`（仅 tab 栏） | — |

> 前端 Docker 启停、目录结构见 `projects/cockpit/README.md`。

---

## 九、监控与排错

### 10.1 检查 cron 日志
```bash
tail -50 /root/.openclaw/workspace/agents/yktender/projects/cockpit/tender/collector/cron.log
```

### 10.2 检查某站抓取失败
```bash
grep "异常\|失败\|超时" /root/.openclaw/workspace/agents/yktender/projects/cockpit/tender/collector/cron.log
```

### 10.3 检查 SQLite 数据
```bash
cd /root/.openclaw/workspace/agents/yktender/projects/cockpit/tender/collector
python3 -c "
from store_db import TenderDB
db = TenderDB()
print('总数:', db.count()); print('按类型:', db.count_by_type())
db.close()"
```

### 10.4 检查关联分析
```bash
python3 project_engine.py --stats   # 匹配统计（2026-08-07 起入口在 project_engine）
python3 build_project_links.py --report  # 兼容层：周期/折扣率报告
```

### 10.5 手动单站测试
```bash
python3 -c "
from scrapers import CtgScraper
from config import SITES
s = CtgScraper('三峡集团', SITES['三峡集团']['url'], SITES['三峡集团']['base_url'])
items = s.scrape(); s.close()
print(f'{len(items)} 条')
for i in items[:5]: print(i['title'], i['date'])"
```

### 10.6 站点改版排查
1. curl 手动看列表页 HTML 结构
2. 对比本手册第三节的选择器
3. 更新 `scrapers.py` 对应类
4. 跑 `python3 run.py` 验证

### 10.7 去重记录清理
```bash
echo '{"pushed": []}' > /root/.openclaw/workspace/agents/yktender/projects/cockpit/tender/collector/data/store.json
```
（清理后所有条目视为"新增"，tender.db 不受影响）

---

## 十、稳定性参数

| 参数 | 值 | 说明 |
|---|---|---|
| `REQUEST_TIMEOUT` | 15s | 单次请求超时 |
| `REQUEST_DELAY` | 1.5s | 站间间隔 |
| `MAX_RETRIES` | 3 | 单 URL 最大重试 |
| `RETRY_BACKOFF` | 2s | 指数退避基数 |
| `DETAIL_DELAY` | 0.5s | 详情页间隔 |
| `MAX_DETAIL_CHARS` | 8000 | 详情页正文截断 |
| `STORE_MAX_ENTRIES` | 5000 | 去重列表上限 |

---

## 十一、文件清单

| 文件 | 作用 |
|---|---|
| `config.py` | 站点配置 + ICT 关键词 + 请求参数 |
| `scrapers.py` | 23 个配置来源的 HTML/API 抓取器 |
| `enrich_engine.py` | 富化引擎 + 公告分类器（2026-08-07 并入 notice_classifier）★ Phase 1 |
| `store_db.py` | SQLite 存储层 + 去重（2026-08-07 并入 store.py）★ Phase 2 |
| `project_engine.py` | 可信关联 + 项目归并/生命周期（2026-08-07 并入 build_project_links）★ Phase 3 |
| `notice_classifier.py` | 兼容层 → enrich_engine（2026-08-07 起 re-export） |
| `store.py` | 兼容层 → store_db（2026-08-07 起 re-export） |
| `build_project_links.py` | 兼容层 → project_engine（2026-08-07 起 re-export） |
| `enrich_meta.py` | 元数据富化：区县/行业分类/中小微 ★ Phase 5 |
| `verify_quality.py` | 数据质量校验（两级基线）★ Phase 5 |
| `rebuild_ict.py` | 重跑 ICT 筛选回写 SQLite；生效链 `maintenance.py rebuild ict` → `rebuild validated` ★ 2026-08-04 |
| `backfill_jszbcg_pdfs.py` | 江苏招标采购服务平台 PDF 下载、文本层提取、Markdown 与字段回填（`--days N` 增量补抓） |
| `backfill_chinapost_pdfs.py` | 中国邮政 PDF 下载、文本层提取、开标/截止时间回填（2026-08-06 新增，已接入定时） |
| `backfill_open_dates.py` | 缺开标时间公告统一自动补抓（L1-L4 分级，2026-08-06 新增，已接入定时） |
| `backfill_missing_fields.py` | 数据缺失回填：苏服采成交金额→winning_amount、中国政采 publish_date 正文兜底、江苏政采中标单位重富化（2026-08-07 新增） |
| `backfill_3sites.py` | **定向回抓 3 站**（2026-08-08 新增）：江苏政府采购网 / 苏服采 / 中国政府采购网。`--export` 一并执行重建+导出+docker cp；`--no-detail` 仅刷列表（快速对账） |
| `deep_refresher.py` | **凌晨补偿**（2026-08-09 新增）：读 `logs/run_state.json` failures 段只补偿主抓失败站。`--group A/B` 分组、`--sites` 指定、`--dry-run`、`--rebuild`；站点间随机延迟 20-50s + 单站熔断 3 次 + 永远 exit 0 |
| `site_refresher.sh` | **统一补抓入口**（2026-08-09 新增）：flock 防并发 + 主抓运行时跳过；入参 `sdicc`/`deep-a`/`deep-b`/`noop`；仅 deep-b 承担重建+部署 |
| `scrape_ccgp_only.py` | 江苏政府采购网专项抓取（含中标/成交/更正，2026-08-07 新增） |
| `ocr_jszbcg_pdfs.py` | 扫描 PDF 本地中文 OCR（Tesseract `chi_sim+eng`） |
| `organize_jszbcg_ocr_with_m3.py` | MiniMax-M3 校正 OCR、生成 Markdown、回填中标单位/金额 |
| `retry_jszbcg_m3_errors.py` | M3 输出格式失败项的分隔符协议重试 |
| `analyzer.py` | ICT 分级匹配 |
| `reporter.py` | Markdown 日报生成 |
| `store.py` | 兼容层 → store_db（2026-08-07 起 re-export） |
| `run.py` | 主入口（抓取→富化→落库→关联→JSON→报告） |
| `ccgp_scraper.py` | 江苏政府采购网 CDP 抓取器 |
| `deploy.sh` | 定时部署脚本（run.py → PDF 补抓 → 重建项目 → 导出 JSON → docker cp；2026-08-06 起含 PDF 补抓步骤） |
| `README.md` | 本手册（原 SCRAPING_RULES + DEPLOY_PLAN 合并） |
| `data/store.json` | 去重记录 |
| `data/tender.db` | SQLite 历史库 |
| `reports/` | 日报归档 |
| `cron.log` | 定时任务日志 |

---

## 十二、维护注意事项

1. **跨年问题**：`YcDataGroupScraper` 日期正则硬编码 `2026`，跨年需更新
2. **跨月问题**：`YcDsjtScraper` 日期只有日数字，用当前月补全，跨月可能出错
3. **站点改版**：HTML 结构变化会导致 scraper 失效，需定期验证
4. **中粮e采 API 变更**：逆向得到的 API 路径可能随版本更新失效
5. **ICT 关键词**：需根据实际命中情况增补
6. **富化引擎（v2）**：字段提取集中在 `enrich_engine.py`，关键词表/正则/排除词都在该文件维护。改富化只动它，不动 `scrapers.py` 解析逻辑
7. **合并策略**：`extract_detail_fields` 先新引擎后旧正则补缺。新引擎误判某站时，可调 `_is_valid_purchaser`/`_ORG_SUFFIX`/`BUDGET_EXCLUDE` 等校验表
8. **公告类型**：`notice_type` 由 `infer_notice_type` 从标题推断，新增类型关键词时同步更新该函数
9. **日期格式**：v2 开标时间统一输出 `YYYY-MM-DD HH:MM:SS`，若驾驶舱/报告解析依赖旧格式需同步调整
10. **SQLite 存储层**：改 schema 需考虑已有 `tender.db` 迁移（当前 `CREATE TABLE IF NOT EXISTS` 自动建表，破坏性变更需手动重建）
11. **双轨去重**：`store.json`（报告推送）与 `tender.db`（历史存储）职责不同，改去重逻辑别混用
12. **金额解析**：`amount_value` 自动从 `amount` 字符串解析（亿/万/元），新增金额格式需同步更新 `store_db.py`
13. **关联分析**：改匹配逻辑（`_norm`/消歧顺序）时注意关联率依赖 award 数据量，数据少时低属正常
14. **视图重建**：`project_chain` 视图每次构建 DROP + 重建，改视图字段需同步更新 `project_engine.py` 的 `DDL_VIEW`（2026-08-07 起关联器并入 project_engine）
15. **流水线顺序依赖**：run.py 中 `project_engine.build()`（可信关联）必须在读取 `project_chain`（links）之前执行，否则驾驶舱会用旧关联数据。改 run.py 别打乱顺序
16. **单价过滤**：`enrich_engine._extract_budget` 仅当"第一个金额本身就是 X元/Y 单价"时跳过（对齐 Pro 版 P0-1），窗口内先有真预算后出现单价时保留真预算
17. **分页**：驾驶舱列表分页在 `tender.js`，每页条数改常量 `PAGE_SIZE`（当前 20）。分页基于筛选后的 `_currentList` 切片，切子标签/公告类型重置到第 1 页
18. **行业分类规则**：改行业分类只动 `rules/category.yaml`（`must_any`/`must_not`），无需改代码。未命中分类 = 信息化商机池，与 ICT 正向匹配互补
19. **区县分类**：`enrich_meta.classify_district` 优先级 文本关键词 > 公司映射 > 站点硬编码。外部平台区县多为空属预期；新增区县关键词改 `DISTRICT_KEYWORDS`
20. **质量校验基线**：`verify_quality.py` 只对本地 5 站查富化字段（外部平台/江苏政府采购网排除）。若新增站点或覆盖率变化，同步调 `BASELINES`/`LOCAL_SITES`/`EXCLUDED_SITES`
21. **KPI 定时 + 计价（2026-08-04；2026-09-29 整链下线）**：`deploy_overview.sh`/`collect_overview.py`/`overview.json`/`kpi.js` 已全部删除，cron `0 */3` 行移除；统计口径由 8002 `/api/tender/meta` 直读 DB 提供，不再存在独立 KPI 计价链。
22. **江苏政府采购网详情富化**（2026-08-04）：详情页正文走接口 `POST /pss/jsp/relevantCgggGetById.jsp`（`_fetch_detail` 优先），接口变更会失效需重逆向。`run.py` 对江苏政府采购网 `ict_only=False` 全量抓详情，耗时约 6 分钟
23. **ICT 弱词规则**（2026-08-04）：弱词判定只认标题，正文仅用于强词兜底（`analyzer.is_ict`）。改规则后用 `python3 maintenance.py rebuild ict` 从 SQLite 重算回写，再 `python3 maintenance.py rebuild validated` 重建+校验，**无需重新抓取**
24. **前端行业筛选**（2026-08-04）：驾驶舱新增行业大类筛选栏 + 表格行业列（`tender.js` 的 `filterByCat`/`_activeCat`，样式 `tender.css` 的 `.tender-cat-badge`）
25. **江苏招标平台 PDF 归档**（2026-08-05）：PDF 保存到 `data/jszbcg_pdfs/`，Markdown 保存到 `reports/jszbcg_pdf_markdown/`。文本 PDF 直接提取；扫描 PDF 用 Tesseract 中文 OCR 后交 MiniMax-M3 校正整理。`store_db.py` 更新公告时保留既有 PDF/OCR/M3 元数据及非空中标字段，避免下一轮列表重抓覆盖回填成果
26. **PDF 回填边界**：附件只按需下载江苏招标平台中标公告，不对所有站点批量下载。44 条初始中标公告中 38 条取得 PDF，37 条回填单一中标单位；6 条平台未提供 PDF，1 条包含 8 个采购包无法安全压成单一中标单位，完整分包结果保留在 Markdown
27. **中国邮政 PDF 补抓**（2026-08-06）：该站详情页正文为 pdfjs 内嵌 PDF，HTML 无文字。`backfill_chinapost_pdfs.py` 下载 PDF → `pdfplumber` 提文本层 → 正则提取开标/截止时间回填（兼容 `上午/下午`、年月日间空格、全角冒号、`递交的截止时间`、`解密截止时间` 等变体；无独立开标时间时用递交截止时间兜底）。**已接入 deploy.sh 定时**（默认只处理缺开标时间的公告，增量安全）。PDF 缓存在 `data/chinapost_pdfs/`
28. **deploy.sh 流程**（2026-08-06）：run.py 成功后 → `backfill_chinapost_pdfs.py`（中国邮政全量缺补）→ `backfill_jszbcg_pdfs.py --days 3`（江苏招标平台近 3 天中标 PDF 增量）→ 重建项目（PDF 回填影响项目状态）→ 导出 JSON → docker cp。若加新 PDF 源，在 deploy.sh 同位置追加即可
29. **凌晨补偿调度**（2026-08-09）：crontab 重排为 5 个任务（见 § 2.1）。`site_refresher.sh` 是统一入口，`deep_refresher.py` 只补偿**主抓失败**的站（读 `logs/run_state.json` failures 段），避免每天全量重抓；站点间随机延迟 20-50s + 单站熔断 3 次 + 永远 exit 0；deep-a 只补抓，deep-b 补抓完统一重建（API 直读，无导出部署）；周末跳过（`1-5`）
30. **ICT 关键词保护**（2026-08-09）：`store_db.upsert` 对 `ict_keywords`/`tags` 加保护——调用方未传（空列表）时保留 DB 已有值（同 winner/bid_deadline 策略），避免 deep_refresher 等只抓详情不跑 analyzer 的路径清空历史 ICT 标记；若需强制重算，用 `rebuild_ict.py`
31. **重复记录清理**（2026-08-09）：2026-08-06 曾用旧 id 算法（site 名差异）入库，导致中国政府采购网 11 条公告出现新旧两个 id（旧 id 记录 last_seen 不再更新）。排查法：`SELECT site,url,title,publish_date,COUNT(*) FROM notices GROUP BY site,url,title,publish_date HAVING COUNT(*)>1`，旧 id = 重算 md5 不匹配者。清理时先迁/删 `project_notices`/`project_links` 引用（新 id 已同项目时直接删冗余引用），再删旧 notices，最后 rebuild+导出。已清理 11 条（DB 3394 条），备份 `data/tender.db.bak_20260809_dupfix`

---

## 附录 A：特殊站点

### 江苏政府采购网（CDP 浏览器自动化）
| 项 | 值 |
|---|---|
| 列表页 | `ccgp-jiangsu.gov.cn/jiangsu/cggg_search.html?lmid=cggg&qh=notic_c3` |
| Scraper 类 | `CcgpJiangsuScraper`（`ccgp_scraper.py`） |
| 抓取方式 | ① CDP 浏览器自动化（首选）② 直接 API + 验证码 OCR（回退） |
| 验证码识别 | ddddocr（首选）→ tesseract（备选）→ 保存图片（最后） |
| 筛选条件 | 盐城经济开发区 (320991) + 盐南高新技术产业开发区 (320992) |
| 搜索 API | `GET /pss/jsp/search_cggg.jsp` 参数: `page`, `qy`, `pqy`, `cglx`, `code` |
| 验证码 API | `GET /pss/servlet/validateCodeServlet` |
| 详情页 URL | `/jiangsu/js_cggg/details.html?gglb={type}&ggid={id}` |

**依赖**：`pip3 install --break-system-packages ddddocr`（可选）；`apt install tesseract-ocr`（可选）；Chromium（CDP 必需，见 `setup_chromium.sh`）；`websocket-client`（CDP 必需，已装）

**环境变量**：`CHROMIUM_PATH`（Chromium 路径）；`CCGP_USE_CDP=0`（强制 API 方式）

**详情页富化（2026-08-04 新增）**：详情页正文是 JS 异步加载的，直接 requests 只能拿到空模板骨架。
已逆向出正文接口 `POST /pss/jsp/relevantCgggGetById.jsp`（参数 `ggid`，直接返回 JSON 含真实正文）。
`_fetch_detail()` 现在优先走该接口，失败再回退 requests。`run.py` 对江苏政府采购网采用 `ict_only=False` 全量抓详情。
实测 406 条富化：发包人 97% / 金额 99% / 开标 92%。

**运行**：
```bash
# CDP 方式（需 Chromium）
bash setup_chromium.sh
python3 -c "from ccgp_scraper import CcgpJiangsuScraper; s = CcgpJiangsuScraper('江苏政府采购网', use_cdp=True); print(len(s.scrape()))"
# API + OCR 方式（无需浏览器）
python3 -c "from ccgp_scraper import CcgpJiangsuScraper; s = CcgpJiangsuScraper('江苏政府采购网', use_cdp=False); print(len(s.scrape()))"
```

### ⑭ 一汽电子招标采购平台
| 项 | 值 |
|---|---|
| 列表页 | `POST etp.faw.cn/gg/allJYTypeGGList`（`searchType=5, searchText=盐城`） |
| Scraper 类 | `FawScraper`（重写 `scrape()`） |
| 抓取方式 | POST 表单 + HTML 解析（服务端渲染） |
| 标题提取 | `<div class="title">` 文本 |
| 日期提取 | `<div class="zl-desc-item">` 中 `发布时间：(\d{4}-\d{2}-\d{2})` |
| 发包人 | `<div class="zl-desc-item">` 非日期/非编号文本 |
| 翻页 | `currentPage` 递增，检测 `#currentPageInput.max` 判断末页 |
| 去重键 | guid |

### ⑮ 奇瑞集团采购服务平台
| 项 | 值 |
|---|---|
| 列表页 | `POST ebd.mychery.com/cms/api/dynamicData/queryContentPage` |
| Scraper 类 | `CheryScraper`（重写 `scrape()`） |
| 请求体 | `{"pageNo":1,"pageSize":50,"dto":{"siteId":"747","categoryId":"5035","city":"320900"}}` |
| 过滤 | `city=320900`（盐城市） |
| 字段 | 标题 `title`；日期 `publishDate`；截止 `quoteEndTime`/`signUpEndTime`；发包人 `agentCompanyName`；正文 `description`/`text` |
| 翻页 | 每页 50 条，最多 20 页，`total` 达标即停 |
| 去重键 | detail_url |

---

## 附录 B：不可抓取站点

| 站点 | 原因 |
|---|---|
| 通威太阳能 | SPA / JS 渲染 |
| 农银e采 | 需登录 |
| 海兴电子 | SPA / JS 渲染 |

---

## 附录 C：五阶段能力总览（2026-08-03 完成）

| Phase | 内容 | 关键文件 |
|---|---|---|
| 1 | 富化字段增强（发包人 94% / 金额 60% / 开标 86%） | `enrich_engine.py` |
| 2 | SQLite 存储层（4 类公告落库） | `store_db.py` |
| 3 | 关联分析（tender×award + 周期/折扣率） | `project_engine.py` |
| 4 | 驾驶舱展示（4 类公告标签 + 关联项目 + 分页） | `projects/cockpit/site/` |
| 5 | 元数据富化 + 数据质量校验 | `enrich_meta.py` / `verify_quality.py` |
