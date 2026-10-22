# 懒加载架构改造方案（v1 · 2026-09-07）

> 目标：5310 首页从「一次全量拉 4MB JSON + 浏览器内存筛选」改为「按需查询 API（首屏 100KB）」。
> 触发：数据每天增长，2.3 万条迟早到 10 万+，提前切换避免数据膨胀后被迫大改。
> 原则：**分阶段、每步可回滚、功能零丢失**。

---

## 一、现状架构（as-is）

```
浏览器
  └─ 首页加载 → GET data/tender.json (4MB gz)
      → collectAllItems() 全量装进 _allItems 内存数组
      → 8 个 filterByXxx() 在内存里筛选 → 分页切片 → 渲染
      → 区县/阶段/行业/金额/ICT/中小微/搜索 计数全部内存遍历
      → 收藏夹/详情弹窗/区县修订 按 id 在 _allItems 里 find

宿主
  ├─ nginx :5310 —— 纯静态（tender.json 4MB gz 预压缩）
  ├─ 8002 api.py —— SQLite 常驻（区县修订 / favorites，已带 htpasswd 用户头）
  ├─ 8003 qa —— 全库问答（digest 推送已走后端，不受影响）
  └─ deploy.sh 主抓 → 全量重算 notices/projects → 导出 tender.json → docker cp
```

痛点：数据量线性增长 → JSON 线性变大 → 首屏加载线性变慢（10 万条 ≈ 15-20MB gz）。
筛选/计数/收藏全靠浏览器内存，内存占用与卡顿也会随数据量恶化。

## 二、目标架构（to-be）

```
浏览器（首屏）
  ├─ GET /api/tender/meta                     → summary + 22 站元数据 + 计数（~10KB）
  ├─ GET /api/tender/projects?stage=upcoming  → 第一页 40 条（~80KB）
  │        &district=&cat=&ict=&sme=&amount=&date_from=&q=&page=&sort=
  ├─ 切换筛选/翻页 → 再发查询（每页 ~80KB，网络往返 <100ms 内网直连）
  └─ GET /api/tender/projects/<id>            → 单项目详情（含完整 notices 时间线）

浏览器（保底）
  └─ 8002 API 不可用时 → 回退拉 data/tender.json（静态全量，现状逻辑原样保留）

宿主
  ├─ nginx :5310 ── location /api/tender/ → 8002（已有，鉴权已带）
  ├─ 8002 api.py 扩展只读查询端点（SQLite 直查 projects 表，复用既有 _conn/鉴权）
  └─ deploy.sh 不变（仍全量导出 tender.json 作为保底 + 大屏/qa 数据源）
```

核心原则：**全量静态 JSON 不删，从主链路降级为保底链路**。API 挂了页面自动回退，可随时回滚。

## 三、后端改造（8002 api.py，新增只读端点）

### 3.1 数据库索引（先加，查询性能的前提）

```sql
CREATE INDEX IF NOT EXISTS idx_projects_stage ON projects(project_stage);
CREATE INDEX IF NOT EXISTS idx_projects_district ON projects(district);
CREATE INDEX IF NOT EXISTS idx_projects_major ON projects(major_category);
CREATE INDEX IF NOT EXISTS idx_projects_latest ON projects(latest_notice_date DESC);
-- 联合索引覆盖常用组合（stage+district）
CREATE INDEX IF NOT EXISTS idx_projects_stage_dist ON projects(project_stage, district);
-- notices 按 project_id 已有 project_notices 桥表；给桥表加索引
CREATE INDEX IF NOT EXISTS idx_pn_project ON project_notices(project_id);
```
在 api.py 启动时 `ensure_indexes()` 幂等创建。

### 3.2 新端点设计

**GET /api/tender/meta** — 首屏元数据（~10KB）
```json
{
  "generated_at": "...", "summary": {...22站计数/ict/total...},
  "district_counts": {"盐南": n, "经开": n, ..., "": n},   // 13 按钮计数
  "stage_counts": {...}, "cat_counts": {...}, "site_meta": [...]
}
```
实现：一条 GROUP BY 聚合查询 × 3（毫秒级）。替代前端 render() 里 5 处内存遍历计数。

**GET /api/tender/projects** — 分页查询（核心）
```
参数（全部可选，组合过滤）:
  page=1&size=40
  stage=upcoming|upcoming_unknown_time|opening_today|pending_result|awarded|intention|exceptions|publicity
       （前端"导航入口"映射：招标公告=upcoming+unknown+opening_today → SQL IN）
  district=盐南|经开|...|none        （单选；"非盐城项目"=district=''）
  cat=物业与后勤服务,工程建设        （多选逗号分隔）
  ict=1|0
  sme=专门面向|非专门但优惠
  type=政府|企业                     （site_category，join SITES 配置或存冗余列）
  amount=0-10|10-100|100-500|500-2000|2000+  （budget_value 范围映射）
  date_from=&date_to=                （publish → latest_notice_date）
  open_from=&open_to=                （open_date）
  q=关键词                           （title/purchaser LIKE）
  site=站名1,站名2                   （监控网页多选）
  sort=latest|open|budget            （默认 latest_notice_date DESC）
返回:
  { total: 12345, page: 1, size: 40, items: [ ...40 条，字段=现 collectAllItems 的 item 字段集 ] }
```
SQL：`SELECT ... FROM projects WHERE ... ORDER BY ... LIMIT ? OFFSET ?` + 同条件 COUNT(*)。
**字段映射**：SQL 列 → 现 item 字段名（category→proj_major_cat 等），保证前端渲染层零改动。
**notices 精简**：列表页不带 notices；返回 `latest_subtype`（JOIN 桥表取最新一条 subtype）供列表 badge。

**GET /api/tender/projects/{id}** — 单项目详情
返回该项目完整字段 + **notices 完整时间线**（现 notice_list 7 字段版）。
消费点：详情弹窗、区县修订弹窗（现从 _allItems find → 改 API）。

**GET /api/tender/updates?date=** — 更新角标数据
现 pickUpdateNotice 逻辑搬到后端：返回列表页各项目"是否有更新公告+最新更新类型/日期/URL"。
实现：一条 SQL（project_notices JOIN，按 role/date 判定），避免 40 条×逐个查。

### 3.3 鉴权与兼容
- 全部走现有 `location /api/tender/`（nginx 已带 Basic Auth + X-Remote-User 头）
- api.py 加只读端点不改既有 PUT favorites/district_overrides
- 登录用户头照传（收藏/修订用）

## 四、前端改造（tender.js，渐进替换）

### 4.1 状态与数据层改造

```
删：_allItems = collectAllItems(data)（全量内存数组）
增：_queryState = {stage, district, cat[], ict, sme, amount[], type[], date, openDate, q, site[], sort, page}
增：async function queryProjects(overrides) → fetch API → {items, total}
增：_serverCounts（来自 /meta，替代内存计数）
```

### 4.2 逐函数改造清单

| 函数 | 现状 | 改造 |
|---|---|---|
| render(data,root) | 拉全量→collectAllItems→内存筛选→渲染 | 改为 await queryProjects() 拿首页；计数来自 /meta |
| 8 个 filterByXxx | 内存过滤 | **删除**，条件并入 _queryState → API 参数 |
| filterByDistrict | 内存 | 并入 API district 参数 |
| renderStageNav/renderCatDropdown 计数 | _districtItems 遍历 | /meta 的 counts 字段 |
| 分页 bindPagination | 本地切片 | API page 参数 + total 重算页码 |
| 搜索 filterBySearch | 内存 | API q 参数（输入回车触发，已有节流习惯） |
| 收藏夹 favoriteItems | _allItems.filter | 新 API：favorites 视图（后端 join favorites 表）；或首屏后按 id 批量 get |
| 详情弹窗/区县修订 | _allItems.find(id) | GET projects/{id} |
| changelog 关联（2190 行）| _allItems.find(title) | /meta 带全量 tender_name 索引（~0.5MB）或并入 links 接口 |
| updateNotice 角标 | pickUpdateNotice(内存) | /updates 接口批量返回 |
| showSites 平台弹窗 | _sitesData | /meta.site_meta |

### 4.3 保底回退（关键设计）
```
if API 请求失败（网络/服务挂）:
    fallback → 拉取 data/tender.json（仍每日全量导出）
    → 进入现状"全量内存"模式，所有功能原样可用
    → 顶栏显示 ⚠️ 降级模式 小标
```
回退开关：`const USE_LAZY = true;` 置 false 一键回到全量模式（保底亦保开关）。

## 五、数据层不变的部分

- **deploy.sh 主抓链路不变**：仍全量重算 + 导出 tender.json（保底链路 + 大屏 + qa 数据源）
- **screen.json 大屏不变**（独立数据文件）
- **tender.db 结构不变**（只加索引）
- **收藏/区县修订存储不变**（仍 8002 现有端点）

## 六、分阶段实施（每阶段独立可回滚）

| 阶段 | 内容 | 规模 | 回滚方式 |
|---|---|---|---|
| **P1** | api.py 加索引 + 4 个只读端点（meta/projects/detail/updates）+ 单测 | 后端 ~300 行 | 端点不删不影响旧链路 |
| **P2** | tender.js 加 queryProjects 数据层 + USE_LAZY 开关（默认 false）| 前端 ~200 行新增 | 开关关回 |
| **P3** | 列表主链路切 API（render/筛选/分页/计数）+ 保底回退逻辑 | 前端 ~400 行改动 | 开关关回 |
| **P4** | 收藏夹/详情弹窗/区县修订/changelog 切 API | 前端 ~200 行 | 开关关回 |
| **P5** | 删 8 个 filterByXxx + _allItems（清债）；10 万条压测 | 前端 -300 行 | — |

P1-P2 无风险可先上（新旧并存）；P3 是切换点（有保底回退）；P4 补齐边角；P5 清债+压测。

## 七、10 万条性能预估

| 项 | 现架构（10万条） | 懒加载（10万条） |
|---|---|---|
| 首屏传输 | ~18MB gz | ~100KB |
| 浏览器内存 | 全量数组 ~500MB+ 卡顿 | 当前页 40 条 ~2MB |
| 筛选响应 | 内存遍历 10 万条 ~300ms | SQL 索引查询 ~5-20ms |
| 每次主抓后浏览器 | 重新全量拉 | 仅 meta ETag 变化 |

## 八、风险与对策

| 风险 | 对策 |
|---|---|
| API 单点故障 | 保底回退到静态全量（自动检测） |
| LIKE '%q%' 全表扫慢（10 万条时）| FTS5 全文索引（P5 压测后决定是否加） |
| 筛选组合遗漏导致计数对不上 | /meta 计数与查询同 SQL 口径；前端计数全部来自后端 |
| 用户浏览器缓存旧 JS | 版本号 query（nginx 已 no-cache，刷新即新） |
| 收藏视图性能 | favorites 表 join projects（id 主键查，快） |

## 九、明确不做（本轮）

- notice 级全文搜索（qa 8003 已覆盖全库问答）
- 虚拟滚动（40 条/页分页已够用）
- tender.json 静态导出下线（保底链路 + 大屏依赖）

---
*执行顺序：P1 → P2 → P3 →（验收）→ P4 → P5。预计 P1-P3 一天内，P4-P5 半天。*
