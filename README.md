# cockpit-tender

盐城市招投标信息采集与驾驶舱展示系统（纯代码备份）

> 最近同步：2026-10-22 ｜ 对应生产版本：`projects/cockpit`（5310 招投标信息栏）

## 简介
抓取政府采购/招投标公开公告（中国政府采购网、江苏政府采购、苏服采、盐城公共资源交易等），
清洗富化后入库 SQLite，前端驾驶舱展示，支持区县归属人工修订、收藏、投标问答、日报与大屏。

## 目录结构（按现行工程布局）

- `site/` — 前端站点（nginx 静态根）
  - `index.html` / `login.html` — 驾驶舱主页面 / 登录页
  - `js/tender/` — 前端模块（api、state、render、filters、district、badge、controls、modals、fav）
  - `js/` — 应用壳（app.js、main.js、auth.js、user_tags.js、foldable.js）
  - `css/` — 样式（variables、layout、components、kpi、panel、tabs、tender、reset、foldable）
  - `screen/` — 数据大屏（图表）
  - `auth/` — nginx 认证配置
- `tender/` — 后端与采集
  - `collector/` — 采集、清洗、富化、质量门禁、定时调度
    - `run.py` / `scrapers.py` / `enrich_engine.py` / `analyzer.py` / `project_engine.py` — 主链路
    - `store_db.py` / `store.py` — SQLite 落库
    - `rules/category.yaml` — 行业分类规则
    - `deploy.sh` / `site_refresher.sh` — crontab 调度入口（8/12/18 点主抓，3am 站点刷新）
    - `daily_report.py` — 日报生成
    - `backfill_*.py` — 各类补抓/回灌脚本
  - `frontend/tender/` — 前端模块源码（与 `site/js/tender/` 同源，站点目录为部署产物）
  - `api.py` + `api_service.sh` — 区县修订 / 收藏 / 投标问答 API（8002）
- `scripts/` — 运维脚本（部署、备份、健康快照、日报推送、日志轮转）
- `experience/` — 抽取经验样本机制（经验三件套 + 前端展示）
- `manual/md/` — 招投标信息栏维护手册（源文件，唯一事实源）
- `docs/` — 优化方案与懒加载方案
- `ARCHITECTURE.md` — 系统架构说明
- `Dockerfile` / `docker-compose.yml` / `nginx.conf` — 部署配置（容器 `cockpit`，5310:80）

## 说明
- 本仓库为**代码备份**，**不含业务数据**：SQLite 数据库、采集 JSON、报表、日志、截图、日报 HTML 均不入库
- 账号凭据（nginx `.htpasswd`）、`.env` 一律不入库
- 涉及内部运维信息的地址已脱敏（服务器地址以 `<SERVER_IP>` 占位）
- 三方库（ECharts 等 `*.min.js`）不入库，部署时自行引入
- 本仓库只收录 5310 招投标信息栏代码，不含企业信息栏 / 运营商中标动态栏

## 数据与调度要点
- 采集：crontab 8/12/18 点 `deploy.sh` 主抓；3am `site_refresher.sh` 站点刷新
- 落库：`tender/collector/data/tender.db`（生产环境，不入仓）
- 展示：nginx 静态站点 + 8002 API（区县修订/收藏）+ 8003 投标问答

## License
MIT