# 招投标信息栏（:5310 / cockpit-tender）架构说明书

> 版本：2026-09-29 架构精简完成版（SQLite→8002 API-only / run_state 合一 / systemd / 一次性脚本归档）
> 适用范围：5310 招投标信息栏全栈（采集→导出→API→前端→日报→运维）
> 边界：不覆盖 5315 企业栏（ykcompany）、5316 运营商栏（ykfollow）的内部实现；
>       仅在 sync_frontend.sh 与 backup.sh 中作为跨栏目触发点出现。

---

## 1. 系统拓扑（30 秒看完）

```
                ┌────────────────────────────────────────────────────┐
                │              宿主机 (root cron + docker)             │
                │                                                     │
   采集 ───┐    │   ┌─collector/ ──┐   ┌─ project_engine ──┐          │
   scrapers ──► │   │   run.py       │──►│   rebuild()       │──┐     │
   (19+ 站)   │   │   (主抓 / deep- │   │   (link / stage /  │  │     │
   2528 行   │    │   b 重试)       │   │   classify)       │  │     │
                │   └──────┬────────┘   └───────────────────┘  │     │
                │          ▼                                       │     │
                │   ┌─────────────────────────────────────────┐  │     │
                │   │  store_db.py ─► tender.db (SQLite 283MB) │  │     │
                │   │   ├ notices         (原始公告)          │  │     │
                │   │   ├ project_notices (link 表)          │  │     │
                │   │   ├ projects        (项目主表)          │  │     │
                │   │   ├ district_overrides (人工修订)       │  │     │
                │   │   └ favorites       (收藏)              │  │     │
                │   └────────────┬────────────────────────────┘  │     │
                │                ▼                                ▼     │
                │       SQLite 发布 ──► run_state.manifest v3  │
                │                + daily_report()──► site/daily/*.html │
                │                + screen.json  ──►  /screen/ 旁路    │
                │                ▼                                   │
                │       ┌─api.py 8002 (ThreadingHTTPServer)─┐        │
                │       │  /api/tender/{meta,projects,    │        │
                │       │   updates,district,district_over-│        │
                │       │   rides,favorites,health,...}   │        │
                │       └────────────┬────────────────────┘        │
                │                    ▼                              │
                │    ┌─ cockpit 容器 (nginx + 静态) ─┐             │
                │    │  /usr/share/nginx/html/  ◄──docker cp        │
                │    │   ├ index.html  login.html  logout-done       │
                │    │   ├ js/  (auth,app,main,tender,...)           │
                │    │   ├ css/ (11 个)                              │
                │    │   ├ data/user_tags.json（项目JSON已下线）   │
                │    │   └ auth/ (default.conf + verify.json)        │
                │    └────────┬─────────────────────────────────────┘
                │             ▼  ← https://<SERVER_IP>:5310/      │
                └─────────────┴──────────────────────────────────────┘
```

---

## 2. 仓库结构

```
projects/cockpit/
├── docker-compose.yml, Dockerfile         # cockpit 容器构建（:80→5310）
├── nginx.conf (67 行)                     # Basic Auth + 401→403 + gzip_static
├── htpasswd, htpasswd-tender             # 账号清单
├── sync_frontend.sh                       # tender/ent/follow 三站点同步
├── MEMORY.md, README.md                   # 操作记忆 / 项目说明
│
├── site/                                  # 站点静态资源（5310 真源）
│   ├── index.html  login.html  logout-done.html
│   ├── js/  (11 个) auth, app, main, tender, kpi, foldable, user_tags, utils, echarts.min
│   ├── css/ (11 个) layout, panel, components, tabs, foldable, kpi, tender, variables, reset
│   ├── data/ tender.db（唯一事实源）+ PDF/OCR 缓存；tender.json/overview.json 已下线(2026-09-29)
│   ├── daily/  YYYY-MM-DD.html  latest.html  latest.pdf  qrcode.min.js
│   └── auth/  default.conf  verify.json
│
├── tender/
│   ├── api.py (46KB) + api_service.sh     # 8002 后端（区县修订 / 收藏 / 元信息）
│   ├── frontend/                          # ★tender.js 真源（2672 行 IIFE）
│   │   └── tender.js  .bak-20260929-stagetip
│   ├── collector/                         # 采集器（Python）
│   │   ├── 核心 (5)：run.py project_engine.py store_db.py scrapers.py verify_quality.py
│   │   ├── 回填 (18)：backfill_*.py + fix_*.py
│   │   ├── M3/OCR (6)：jszbcg_ocr_m3_backfill fake_missing_m3_backfill ocr_jszbcg_pdfs ...
│   │   ├── 配置/工具：config.py scrapers.py store.py analyzer.py audit_coverage.py ...
│   │   ├── 报表/账本：daily_report.py crawl_ledger.py sufucai_ledger.py reporter.py
│   │   └── tests：test_crawl_resilience.py test_sufucai_area_code_only.py
│   ├── data/                              # collector/data/（运行态）
│   │   ├── tender.db (283MB) + .db-shm/-wal
│   │   ├── crawl_ledger.db
│   │   ├── store.json (200KB，遗留，见 §6-C 候选)
│   │   ├── auto_pdfs/  chinapost_pdfs/  jszbcg_ocr/  (3 个 PDF 缓存目录)
│   │   └── *.json *.tsv *.bak-2026* （历次回填/账本/快照）
│   └── logs/                              # 运行日志
│       ├── cron.log  site_refresher.log  m3_daily.log  health_snapshot.log
│       ├── run_state.json   # 唯一运行态(crawl/heartbeat/deployment/failures/manifest/fetch_history)
│       └── legacy-state-20260929/  # 旧 6 份状态文件归档(只读)
│
├── scripts/                               # 全局运维（不限于 tender）
│   ├── backup.sh                         # 每日 7:30 库快照（跨 tender.db + ykqcc.db）
│   ├── daily_link_push.sh                # 9/13/19 三槽位 WPush 推送
│   ├── health_snapshot.py --once          # */5  7-22 健康体检
│   ├── rotate_log.sh  backup_inventory.py  build_legacy_manifests.py
│
├── experience/                            # ykfollow 的镜像/数据（5316）
├── screen/                                # 监控大屏（旁路子项目）
├── manual/                                # 维护手册源（build_manual.py + md/）
├── backups/{latest,previous,week-*}       # 跨栏目备份快照
└── docs/
```

---

## 3. 调度层（Cron 与部署）

### 3.1 cron 任务（2026-09-29 快照：12 行，deploy_overview 已下线）

| 时间 | 命令 | 关键资源 | 失败兜底 |
|---|---|---|---|
| `0 8,12,18` | `tender/collector/deploy.sh` | SQLite 发布(`run_state.manifest`) + `site/daily/` + 容器 | 主抓失败→快照自动回滚 |
| `0 3, 30 3 (周一~五)` | `site_refresher.sh deep-a / deep-b` | 单站补偿（江苏/中国/苏服采/盐城政采） | 重抓不阻塞主流程 |
| `30 7` | `scripts/backup.sh` | `backups/latest/tender.db` | 周末兜底 previous |
| `*/5 7-22` | `scripts/health_snapshot.py --once` | `health_snapshot.log` + webchat 推 | 失败 5min 后再试 |
| `0 9,0 13,0 19,0 1` | `m3_daily.sh 5400/7200/7200/14400` | M3 假缺 + jszbcg OCR→M3 两相 | 单点失败 5min 巡检发现 |
| `0 9,0 13,0 19` | `scripts/daily_link_push.sh` | WPush 推最新日报 URL | 三槽位冗余 |

### 3.2 `deploy.sh` 内部阶段（一主多辅）

`deploy.sh` 在 `0 8,12,18` 触发，内部流水线：

0. **回滚快照**：主库 `tender.db` clone 至 `/tmp/cockpit-tender-predeploy-*.db`，任何失败自动整库恢复
1. **跳过守卫**：`flock -n 200`（主抓锁）+ `pgrep -f run.py`（已跑则本次跳过）
2. **主抓**：`run.py` → 多线程按站点抓列表+详情 → 写入 `tender.db`
3. **M1 重试**：对失败站即时补偿一次（无 dead_letter）
4. **M2 兜底回填**（国投未修复详情等专属补偿）
5. **并行补抓**（以防单站拖累整体）
6. **ICT 筛选 + 项目装配**（`project_engine.rebuild()`）
7. **SQLite 发布**：`quick_check` + notices/projects/ict 行数 → 写 `run_state.manifest`（schema v3），8002 即时生效
8. **screen.json 生成** → `/screen/` 旁路
9. **日报**：`daily_report.py` → `latest.html` + `latest.pdf` + `YYYY-MM-DD.html`（双写归档，无 cap）
10. **部署**：`docker cp site/. cockpit:/usr/share/nginx/html/`（同步三栏站点只走同步、不部署）

### 3.3 `sync_frontend.sh` 三栏目同步（5310/5315/5316）

每次跑都会把本仓 `tender/frontend/tender.js`、`enterprise/frontend/*.js`、`experience/frontend/*.js` 拷到对应 `site/` 中转目录。只有带 `--deploy-tender` 等才 docker cp。**易踩坑**：改 `site/js/tender.js` 而不改 `tender/frontend/tender.js` → 下次同步被静默回滚（9-20 实证）。

---

## 4. 采集层（tender/collector/）

### 4.1 主抓 `run.py`（696 行）

- 入口 `main()`：装载 `SITES` 注册表（来自 `config.py`）→ 启闹钟 (`signal.alarm`) → 启心跳线程 (`_heartbeat_loop`) → 调度 `_scrape_site` 每站多线程
- 每站封装 `_run_site_task`：抓列表 → 必要时抓详情（详情 → PDF/OCR/M3）→ `store_db.upsert` 落 SQLite
- `_atomic_json(path, data)`：写 JSON 走「临时文件 → rename」原子替换（避免半截文件被前端读到）
- ~~export_tender_json~~（2026-09-29 下线）：SQLite 即发布对象，不再导出扁平 JSON
- 关键产物：`logs/run_state.json` 六段（crawl / heartbeat / deployment / failures / manifest / fetch_history）

### 4.2 站点注册表 `config.py`

- `SITES` 字典：约 16~20 个本地/省级采购/资源交易/邮政/粮储/司法/SUFUCAI 等站点
- 每个站点一项：`url / base_url / category / scrape_method / retention`
- `REQUEST_HEADERS`：通用 HTTP 头
- `CRAWL_SLOW_SITES`：慢源白名单（{江苏政府采购网}），放宽超时
- `DATE_UNSTABLE_SITES`：日期不可靠白名单（{盐城都市建设投资集团}），日期不可被顶掉

### 4.3 抓取器家族 `scrapers.py`（2528 行，19+ 个类）

```
BaseScraper
  ├─ YcDataGroupScraper / YcDsjtScraper       本地大集团
  ├─ CommonListScraper                          通用列表模板
  ├─ YcGovProcurementScraper / YanchengGovScraper  盐城政采系列
  ├─ ChinaGovProcurementScraper                 中央政采（CDP+API 双通道，9-23 倒置 API 主）
  ├─ JszbcgScraper                              江苏中标（PDF/OCR/M3 链路）
  ├─ LegacyPublicResourceScraper / YcPublicResourceScraper
  ├─ SufucaiScraper                             苏服采（POST 分桶，按 area_code 拉）
  ├─ YdSunshineScraper                          盐都阳光
  ├─ ChinaPostScraper                           中国邮政
  ├─ CtgScraper / CofcoEScraper / JsNonghuaScraper 央企/省农资
  └─ SdiccScraper                               山东采购（带重试+回调）
```

每个抓取器继承 `BaseScraper.scrape()`，按需实现 `_iter_endpoint / _parse_list / _parse_detail`；少数站点有独立模块（`ccgp_scraper.py` 2528 行外抽）。

### 4.4 项目装配 `project_engine.py`（567 行）

- `rebuild()`：从原始 `notices` → 归一名称 → 跨公告链接 → 计算项目主键 → 写 `projects` + `project_notices`
- 关键阶段：`norm_name / _pid / reclassify_all / build_projects / rebuild`
- 副产品：`_link_norm / _link_batch / _link_ensure_schema / _link_score`：跨站同 `canonical_name` 项目拉群（6860 条 orphan `tender_id IS NULL` 中标归属在这）

### 4.5 落库 `store_db.py`（374 行）

5 张表 + 索引：

| 表 | 主键 | 关键字段 |
|---|---|---|
| `notices` | id (hash of site+url) | site, title, url, publish_date, notice_type, notice_subtype, raw_json, last_seen, ict_keywords, crawl_time |
| `project_notices` | (project_id, notice_id) | relation_role (tender_primary/tender_reopen/award_final/...) |
| `projects` | id (p_xxx) | canonical_name, display_name, purchaser, site, district, project_stage, stage_reason, first_notice_date, latest_notice_date, open_date, budget_value, budget_type, winner, winning_amount, link_confidence, is_ict, updated_at |
| `district_overrides` | project_id | district, created_at, updated_at |
| `favorites` | (user_id, bucket, fav_key) | created_at, updated_at |

辅助函数：站点活跃度图（每站 last_seen + 当日新增）、`_conn()`、`upsert_notice`、`set_overrides`、`save_favorites`。

### 4.6 质量门禁 `verify_quality.py`（320 行）

`run.py` 主抓完成后调一遍：覆盖率、关键字段缺失、站点失败率。失败 → `deploy.sh` 从临时快照回滚 SQLite 并写 `run_state.deployment=failed`（线上 API 恒为上一份已校验数据）。

---

## 5. 字段回填（M3/OCR 子系统）

### 5.1 触发链路

`m3_daily.sh` × 4 槽位 → 两相：
- **相1 假缺**：`fake_missing_m3_backfill.py`（M3 抽取「库里有 raw_json 但字段空」的真缺）
- **相2 jszbcg OCR→M3**：`jszbcg_ocr_m3_backfill.py` + `ocr_jszbcg_pdfs.py`（PDF→OCR→M3 抽取，cap=80，9-21 由 40 提升）

`pgrep` 守卫两 worker 并行不撞；总时间闸共享。

### 5.2 累计账本

- `crawl_ledger.db`（1.5MB）：每抓一行账
- `fix_ccgp_dates_ledger.tsv`（9-29 修日期账本 31KB）
- `cleanup_project_links_orphans_ledger.json`（6860 orphan 核算结果）
- `backfill_*.json` / `*_report.json`（每站回填账本 + 报告）

### 5.3 脚本家族（18 个 backfill_*.py + 6 个 fix_*）

| 站点 | 回填脚本 |
|---|---|
| ccgp 中央政采 | backfill_ccgp_details / ccgp_history / ccgp_winners |
| chinapost 中国邮政 | backfill_chinapost_detail / chinapost_pdfs |
| jszbcg 江苏中标 | backfill_jszbcg_detail / jszbcg_pdfs + ocr_jszbcg_pdfs + jszbcg_ocr_m3_backfill |
| sdicc 山东采购 | backfill_sdicc + retry_sdicc |
| sufucai 苏服采 | backfill_sufucai_area_code / sufucai_detail + reclassify_sufucai |
| ycgov 盐城国投 | backfill_ycgov |
| dsjt 盐城都投 | backfill_dsjt_dates + fix_dsjt_dates |
| 共性 | backfill_3sites / backfill_details / backfill_district_codes / backfill_missing_fields / backfill_open_dates / backfill_purchaser / fix_amounts |

---

## 6. 数据导出层（双写 + 双链路）

### 6.1 全量静态导出 → 已下线（2026-09-29）

- 原 `tender.json`(51MB)/`.gz` 全量链已删除：不再导出、不再同步、容器内物理清除（URL 404 验证）
- 项目数据唯一出口 = 8002 API 直读 `tender.db`；大屏保留 `screen.json` 旁路；日报保留 `site/daily/` 静态页

### 6.2 后端 API：`tender/api.py`（46KB / 8002 端口）

8 个 query 函数 + CRUD：

| 端点 | 函数 | 用途 |
|---|---|---|
| `/api/tender/meta` | `query_meta / query_meta_cached` | 顶栏生成时间等轻量元信息 |
| `/api/tender/projects` | `query_projects` | 主列表分页（lazy 懒加载） |
| `/api/tender/projects/<pid>` | `get_project_detail` | 单项目详情（含 notices 时间线） |
| `/api/tender/updates` | `query_updates` | 列表角标「更新」批量 |
| `/api/tender/ict_dist` | `query_ict_dist` | KPI 区 ICT 分布 |
| `/api/tender/fav_projects` | `query_fav_projects` | 收藏视图批量归一匹配 |
| `/api/tender/district_overrides` GET | `load_overrides` | 读全部区县修订 |
| `/api/tender/district` PUT/DELETE | `save_override` | 写/撤销区县人工修订 |
| `/api/tender/favorites` GET/POST | `load_favorites / save_favorites` | 收藏读/写 |
| `/api/tender/health` | 简易心跳 | health_snapshot 调用 |

`api.py` 用 `http.server.BaseHTTPRequestHandler` + `ThreadingHTTPServer` 自实现，路径分发走 `parse_qs`。**不是 flask/fastapi**——轻量但路由全是 if/else 分支。

### 6.3 双链路语义对齐（最近修过）

| 字段 | 全量 JSON（已下线 2026-09-29） | API 8002（唯一出口） | 历史问题 |
|---|---|---|---|
| `last_seen` | `max(notices.last_seen)` 抓取命中时间 | 旧版用 `projects.updated_at` | 9-29 已修（新增 `proj_last_seen` 子查询） |
| `tender_url / award_url` | 同口径（皆取 relation_role 第一笔） | `_url_subqueries()` 同口径 | OK |
| `latest_notice_date` | `notices.at(-1).publish_date` | `projects.latest_notice_date` | 同口径 |
| `notices` | 完整时间线 | `_enrich_items` 批量拼回 | OK |
| `update_notice` | 有 | `_enrich_items` 内联 | OK |

---

## 7. 前端层

### 7.1 文件拓扑

| 文件 | 真源 | 角色 |
|---|---|---|
| `site/index.html` | site/ | 单页壳：tab 容器 + 引入 css/js |
| `site/login.html` | site/ | 登录页（auth.js 守卫） |
| `site/logout-done.html` | site/ | 登出完成页 |
| `site/auth/default.conf` | site/ | nginx 鉴权代理片段（build 镜像时 COPY） |
| `site/js/auth.js` | site/ | **同步脚本**（登录守卫需在业务 JS 前生效） |
| `site/js/app.js` | site/ | 模块注册中心：`App.register / App.init / App.refresh` |
| `site/js/main.js` | site/ | 入口：时钟 + 顶栏 KPI + 用户菜单 + 模块调度 |
| `site/js/tender.js` | **`tender/frontend/tender.js`** | 招投标板块（2672 行 IIFE） |
| `site/js/kpi.js` | site/ | KPI 卡渲染 |
| `site/js/foldable.js` | site/ | 折叠面板（localStorage 配方） |
| `site/js/user_tags.js` | site/ | 账号→区县标签（9-23 加） |
| `site/js/utils.js` | site/ | 工具函数（escapeHtml/formatMoney/formatGenTime/…） |
| `site/js/echarts.min.js` | site/ | 仅 /screen/ 引用，首页已移除（9-09 A1） |

### 7.2 真源 vs 中转（重要）

`tender.js` 是**双源**架构：
- 真源 `tender/frontend/tender.js`
- 中转 `site/js/tender.js`（每次 sync_frontend.sh 被覆盖）
- 容器 `/usr/share/nginx/html/js/tender.js`（docker cp 后）

**必须改真源**，否则下次同步静默回滚（2026-09-20 CSS 改动被吞实证）。

### 7.3 模块加载链

```
index.html
  └─ auth.js (同步，先于业务)         登录守卫
  └─ user_tags.js (同步)             账号→区县映射（须在 tender.js 前）
  └─ defer: utils.js
      ├─ app.js
      ├─ kpi.js
      ├─ tender.js
      ├─ foldable.js
      └─ main.js                      入口：触发 App.init/refresh
```

### 7.4 `tender.js` IIFE 内部分层（2672 行）

```
Tender = (() => {
  // 1. 常量/字典（STRONG_KW, TYPE_MAP, PROJECT_STAGE_MAP, DISTRICT, …）
  // 2. 工具：parseBidDeadline / classifyItem / normalizeProjectName / collectAllItems
  // 3. 数据获取：refreshContent / loadFavorites / saveFavorites / saveDistrictOverride
  // 4. 弹窗：showStageTip / openDistrictModal / showToast / closeXxxModal
  // 5. 渲染：render / renderItemBadge / renderAwardGroup / renderFavBtn / renderUpdateBadge
  // 6. 委托绑定：bindDropdown / bindMenuFixedObservers / dropdownMenuFixup
  // 7. 暴露：window.Tender = { render, refreshContent, … }
})();
```

### 7.5 CSS（11 个）

`reset / variables / layout / panel / components / tabs / foldable / kpi / tender`（再加历史 .bak 3 个）

---

## 8. 鉴权与跨栏目

### 8.1 Basic Auth → 401→403（9-21 修复）

- nginx 入口：所有 `/api/tender/` 与 `/auth/verify` → `auth_basic` 触发 401
- 自定义 `location @authfail`：401 → 改写 403 + JSON（去 `WWW-Authenticate` 头）→ 浏览器**不再弹原生密码框**
- `/logout` 的 401（Tender Logout realm）保留 = 登出机制本体
- 前端 `auth.js` `Auth.handleAuthFail`：清 `cockpit_auth`（localStorage+sessionStorage）+ `cockpit_cred` → 跳 `login.html?bye=1`

### 8.2 账号-区县自动定位（9-23 P2）

- `user_tags.js`：`window.userTagDistrict(name) / window.userTagLabel(district)`
- 数据源：`site/data/user_tags.json`（同步脚本生成）
- 顶栏 chip 标识「Yankai 市级 / 默认全部」

### 8.3 三栏目同步（sync_frontend.sh）

```
tender/frontend/tender.js    → site/js/tender.js        → cockpit   (5310) ★我管
enterprise/frontend/*.js     → /…/ykcompany/.../site/  → cockpit-ent (5315) ykcompany 管
experience/frontend/*.js     → /…/ykfollow/.../site/   → cockpit-follow (5316) ykfollow 管
```

---

## 9. 日报 / KPI

### 9.1 `daily_report.py`（412+ 行）

- 触发：`deploy.sh` 主抓成功后调 `daily_report.main()` → 写 `latest.html` + `YYYY-MM-DD.html`
- 段落：招标 / 中标 / 区县分布 / ICT / **更正公告**（9-28 加）/ 合同（按版型）
- PDF：`make_pdf()` 用 weasyprint / chromium headless（待核）
- 双写：latest.html + 日期归档（无 cap 全量展示，9-26 老板拍板）

### 9.2 `daily_link_push.sh` v0.4（WPush-SEND）

- 三槽位 0 9 / 0 13 / 0 19 → WPush 推 `/daily/latest.html` + 摘要
- 旧 daily_report_push.sh 9-26 老板拍板停用，**不是故障**
- 旧 .bak 版本（v0.3 hub / v0.4 pre）保留即可清理

### 9.3 deploy_overview / collect_overview / overview.json → 已下线（2026-09-29）

- cron `0 */3` 行、两脚本、`site/js/kpi.js`、`site/data/overview.json` 全部删除（容器残留同步清理，URL 404）
- 顶栏/板块统计改由 8002 `/api/tender/meta` 直读 DB

### 9.4 健康 `health_snapshot.py`

- `*/5 7-22`：读 `run_state.json` 六段 + 服务探针 + `cron.log` 末尾 → 汇总
- 失败推 webchat；通过静默
- 已知常态告警：江苏政采 partial、CCGP 「CDP 0 条回退」签名（API 倒置后属正常态）

---

## 10. 运维 / 备份

### 10.1 备份 `scripts/backup.sh`

- 每日 30 7：snapshot `tender.db` + `ykqcc.db`（5315 跨栏）+ `manual_overrides.json`
- 落 `backups/latest/`、`backups/previous/`、`backups/week-YYYYMMDD/`
- 锁设计：latest 原子，previous 周末兜底

### 10.2 日志轮转 `rotate_log.sh`

- `site_refresher.log` 按周 gzip 4 份

### 10.3 状态文件家族

| 文件 | 写者 | 读者 | 字段举例 |
|---|---|---|---|
| `run_state.json` | run.py / deploy.sh / deep_refresher / health_snapshot | api 维护门 / health_snapshot / daily_link_push | schema v1 六段：crawl/heartbeat/deployment/failures/manifest/fetch_history |
| `logs/legacy-state-20260929/` | （归档只读） | — | 2026-09-29 合并前的旧 6 份状态文件 |

**潜在冗余**：4 份文件每份都有一个 run_id 字段，独立保存，无单一事实源。

### 10.4 `manual/` + `build_manual.py`

- 维护手册 md 源 → `build_manual.py` → `site/manual/`（5311 端口展示）
- 改 md 即真源，不要直接改生成的 HTML

---

## 11. 数据资产清单（体积量级）

| 资产 | 位置 | 大小 | 增长趋势 | 备注 |
|---|---|---|---|---|
| `tender.db` | collector/data/ | **283MB** | +5MB/天 | 含 .db-shm/-wal，**最大单资产** |
| ~~tender.json(.gz)~~ | site/data/ | — | 2026-09-29 下线 | 项目数据唯一出口 = 8002 API |
| `backups/latest/tender.db` | backups/ | 283MB×1 | 周轮转 | latest+previous+week |
| `crawl_ledger.db` | collector/data/ | 1.5MB | +5KB/轮 | 审计账本 |
| `store.json` | collector/data/ | 200KB | 0 | **疑似遗留**（见 §12-B） |
| `auto_pdfs/` | collector/data/ | 目录 36864 B | +N/天 | 自动下载的 PDF 缓存 |
| `chinapost_pdfs/` | collector/data/ | 57344 B | +N/天 | chinapost 详情 PDF |
| `jszbcg_ocr/` | collector/data/ | 641 项 | 大 | 江苏中标 OCR 中间产物 |
| `site/screen/`、`site/daily/`、`site/data/`、`backups/` | 站/容器 | 55MB+ | — | 静态导出与归档 |

---

## 12. 精简候选清单（增删查改）

> 核心问题：**5 个并发维护的子系统、4 份状态文件、3 套数据存储（JSON+DB+gz）、2 套前端源**，
> 导致任何"小改动"都要在多处同步、记忆负担重。

### 12-A 「增」（该加的）

1. **`backfill_*` 通用化（**估算 18 个脚本 → 4 个**；2026-09-29 部分达成：`maintenance.py` 统一入口 + 19 个一次性脚本归档 `archive/`）**
   - 把"按站点抓详情 + 字段抽取"的通用骨架抽成 `backfill_runner.py` + per-site 配置（site/url/fields）
   - M3 抽取层独立成 `extractor/m3.py`，被各 backfill 复用
   - 收益：18 × ~150 行 → 4 × ~200 行；新站点回填脚本从 ~150 行降到 ~30 行
   - 代价：通用化设计 + per-site 配置 schema 起草
   - 回滚：旧 backfill 全部保留 .bak

2. **`scripts/deploy_overview.sh` + `collect_overview.py` + `overview.json` 收口 ✅ 已完成（2026-09-29，选择整链下线而非合并）**
   - 现在 3 文件只产一个轻量 JSON，可以合并成一个 `scripts/overview.py --refresh` 命令
   - 收益：少一个外部脚本依赖 + 一个文件
   - 代价：低

4. **`scripts/deploy_daily_report.py` 抽出独立 CLI（**当前是 deploy.sh 内嵌 heredoc**）**
   - 部署时日志丢失溯源难（9-26 静默死根因），独立 CLI 可单独跑 + 健康检查集成
   - 收益：可单独跑 + 排障更清晰

5. **前端 tender.js 模块化（2672 行 IIFE）✅ 已完成（2026-09-29）**
   - 实施：3102 行拆为 13 模块群 `tender/frontend/tender/`（state.js S 可变状态 / constants.js K / registry.js F 跨模块登记 + util/badge/fav/api/filters/controls/render/district/modals + main.js 入口组装 window.Tender+App.register）；机械重写器 split_tender.py（一次性迁移，含对象键位/局部遮蔽保护）；index.html 改 type=module；同步脚本改目录同步；Playwright 14 项全交互回归全绿
   - 按上述 §7.4 切分 render/badge/favorites/district/modal/stage/data/utility → ES Modules
   - 收益：单文件维护负担下降，懒加载友好
   - 代价：ESM 化需要把所有 IIFE 解绑（注意 `<script type="module">` 的 top-level await 不一定阻塞 defer，9-23 实踩——改用普通 script + 内联数据）

### 12-B 「删」（该清的）

1. **历史 .bak 文件清仓**：
   - `site/js/changelog.js.bak-20260917-remove-version`
   - `site/js/user_tags.js.bak-20260923`
   - `site/css/components.css.bak-20260917-remove-version`
   - `site/css/layout.css.bak-20260917-remove-version`
   - `tender/frontend/tender.js.bak-20260929-stagetip`（**保留到 9-30 24h 后再删**）
   - `tender/api.py.bak-20260929-lastseen`（同上）
   - `tender/collector/data/tender.db.bak-20260928-*` 三份（各 283MB = 850MB，**立刻可清**）
   - `tender/collector/data/tender.db.bak-20260919-jszbcg-district`（283MB，**可清**）
   - `scripts/daily_link_push.sh.bak-20260926-*` 两份
   - `scripts/backup.sh.bak-20260923`
   - 收益：单仓减少 ~3GB 占用
   - 风险：低（每 .bak 都是已有替换产物；git 不跟踪但本地可查 tar 历史）

2. **`store.json`（200KB）** vs `tender.db`：
   - `store.json` 写入只在 store.py（20 行）里；store_db.py 是真存储
   - store.py 路径：grep 调用方，若只有 store.py 单点使用，可合并进 store_db.py
   - 收益：少一份数据资产 + 20 行死代码
   - 风险：低

3. **`manual_overrides.json` vs SQLite `district_overrides` 表**：
   - 早期 JSON 修订；现 SQLite 表已包含完整 schema（created_at/updated_at/proj_key）
   - 若 backup.sh 仍用它作为兜底 → 查 dump 脚本，确认一致性
   - 收益：少一份数据 + 备份文件更小

4. **`.bak` 历史 16+ 个回填脚本账本**：
   - `backfill_ccgp_winners_report.json` 等 12 个 .json/*.tsv 一次性产物，跨月 commit 无价值
   - 收益：少 3MB+

5. **过期 cron 行 `daily_report_push.sh`（已被 `daily_link_push.sh` 取代）**：
   - 已无 cron 调用，文件已删；**只是在备份里还有 .bak-20260926**，可清

6. **`screen/vendor/`**：屏幕大屏独立子项目；占用是否需要由屏幕项目组管，可协商

### 12-C 「查」（待你拍板）

1. **前端双源架构是否彻底重构？**
   - 选项 A：保持双源（sync 同步） + 加 build 步骤（npm/无依赖 make）
   - 选项 B：单一真源（site/） + 移除 tender/frontend/ + sync 改名为"复制到 ykcompany/follow"
   - 选项 C：单一真源（tender/frontend/） + build 出 site/js + 容器路径
   - 推荐 C（最小惊讶），代价是把 sync 脚本的 cp 顺序倒过来

2. **API vs 全量 JSON 双链路？ ✅ 已拍板 2026-09-29：选项 B（JSON 下线，API-only）**
   - 当前：51MB JSON 每日拉一次 + API 8002 实际承担 lazy 实时
   - 现状 JSON 主要供首屏「视感秒开」用，但 51MB 体积过重
   - 选项 A：保留 JSON（首屏秒开）+ API lazy（保持现状）→ 不动
   - 选项 B：取消 JSON 首屏，由 API lazy + 列表骨架（头 200 条项目 ID + 摘要）→ 减 51MB 静态资产
   - 选项 C：JSON 只导出 `summary + projects[200 条摘要]` 的轻量版（<5MB），详情全走 API
   - 推荐 B（**减 50MB 静态资产**，维护更统一），但首屏感需要 1~2s 改成骨架屏

3. **18 个 backfill_*.py 是否全保留？ ✅ 已完成 2026-09-29：19 个一次性脚本归档 `archive/{migrations,rollback}`**
   - 大多数是临时修补（fix_*.*），问题修完就该删
   - 例如 `fix_dsjt_dates.py`（228 行，dsjt 日期修复完成）→ 9-29 已落账，可删
   - `fix_amounts.py`（186 行）→ 修金额后已稳态，可删
   - `reclassify_sufucai.py`（166 行）→ 已落账，可删
   - 永久保留：`backfill_purchaser / backfill_district_codes / backfill_open_dates / backfill_missing_fields` 这类长期补字段的
   - 推荐：保留 ~5 个长期回填 + 删 ~10 个一次性 fix → 18 个减到 5 个

4. **`store.json` 是否真在用？**
   - `store.py` 20 行；`store_db.py` 374 行
   - grep 所有 `from store import` 调用方，确认是否被新代码彻底弃用
   - 若 0 调用方 → 同 12-B-2 删；若还有 fallback 用 → 保留+注释

5. **cross-column `sync_frontend.sh` 是否要简化为 `sync.sh --tender / --ent / --follow` 三件套？**
   - 当前一个脚本承担三栏目同步；维护时三个 agent 都要碰
   - 选项 A：保持一脚本 + 加注释标注 "tender: 5310 owner=yktender, ent: 5315 owner=ykcompany, follow: 5316 owner=ykfollow"
   - 选项 B：拆成三个独立 `sync_tender.sh / sync_ent.sh / sync_follow.sh`，各 agent 管自己
   - 推荐 A（**最小风险**），但需要写明 ownership 注释

6. **健康/状态文件家族（4 份）是否合并？ ✅ 已完成 2026-09-29：`run_state.json` 六段合一**
   - `crawl_status.json / crawl_heartbeat.json / deploy_status.json / deployment_manifest.json`
   - 每份都有 run_id / timestamp；可合并为一份 `run_state.json`（latest + history）
   - 收益：少 3 份文件 + 单一事实源
   - 代价：reader 改写（health_snapshot.py）

7. **`api.py` 自实现 vs 改 flask/fastapi？（服务管理半边 2026-09-29 已解：systemd `tender-district-api.service` 托管；换框架仍可选）**
   - 8002 API 用 BaseHTTPRequestHandler 自己拼 JSON；路由全 if/else
   - 改 flask/fastapi 收益：路由声明式 + middleware 复用
   - 代价：依赖 + 服务管理变更（现在 nohup 起）

### 12-D 「改」（该改的）

1. **`daily_link_push.sh` v0.4 写一份 inline-README 注释**
   - 当前 shell + python 混编（虽然已简化）；写一份文档标"槽位 / 时间 / 重试 / 失败兜底"
   - 收益：未来接手不用猜

2. **`api.py` 路由加 OpenAPI 注释**
   - 现有注释已较详细，但路径分发是 if/else；建议加一份 `api_routes.md` 维护

3. **`store.py` 与 `store_db.py` 合并到 `store_db.py`（如果 12-C-4 确认无调用）**

4. **`tender/frontend/tender.js` 加 `.editorconfig / .jshintrc` 规范化**
   - 2672 行 IIFE，单空格 / 单引号 / 行尾分号风格不统一
   - 收益：diff 干净 + 评审容易

5. **`deploy.sh` 内嵌 heredoc 全部提取**
   - 当前 [deploy] 段塞 `daily_report.main()` heredoc；提取成 `daily-report`
   - 收益：deploy.sh 减 100+ 行 + 排障链清晰

6. **新增 `site/data/version.json` 单一真源 ✅ 被替代（2026-09-29）**
   - tender.json 下线且前端 API-only 后，发布元数据直接读 `run_state.manifest`（schema v3），无需新文件

---

## 13. 推进顺序建议

| 阶段 | 工作 | 收益 | 风险 |
|---|---|---|---|
| **Phase 0 立即可清（30min）** | 删 .bak + 删过期回填账本 | -3GB 磁盘 | 极低 |
| **Phase 1 ✅（2026-09-29）** | 状态文件合一 → `run_state.json`（version.json 已由 manifest 替代；manual_overrides 未动） | 少 5 文件 | 已完成 |
| **Phase 2 ◐（2026-09-29）** | `maintenance.py` 统一入口 + 一次性 `backfill/fix` 归档 `archive/`（daily_report heredoc 提取待办） | 维护单一入口 | 部分完成 |
| **Phase 3 ✅（2026-09-29）** | API vs JSON 取舍（JSON 下线）+ tender.js 模块化（ESM 13 模块，Playwright 14 项回归全绿）均已完成 | 维护负担下降 | 已完成 |
| **Phase 4 有推进** | `api.py` systemd 托管 ✅（2026-09-29，flask/fastapi 仍可选）+ `sync_frontend.sh` ownership 注释 | 长期收益 | 中 |

---

## 14. 已知风险点（不能动的）

1. **gc 进程 / 重抓**：当前 `flock -n 200` + `pgrep -f run.py` 是 8/12/18 主抓不撞的硬保证；任何重构都不能破坏
2. **jszbcg OCR→M3 两相 + 总时间闸**：9-21 改造，9-22 实测可并行；M3 cap=80 保护单次批跑
3. **API 双链路语义对齐**：9-29 刚修完，不能在没有全库回归前动 SELECT
4. **`sync_frontend.sh` 不能轻易拆**：三栏目归属不同 agent，拆错会越界

---

> 文档位置：`projects/cockpit/ARCHITECTURE.md`
> 关联：每个待办都标了"代价 / 收益 / 回滚"，可直接拆成 ticket 进 todo-board