# 招投标系统优化方案（v1 · 2026-09-07）

> 依据：2026-09-07 架构梳理（容器/服务/代码规模/数据量核实）。
> 状态：13 项优化分高/中/低三档，按序执行；每项独立可回滚。
> 维护：yktender · 范围：5310 招投标栏（不越界 5315/5316）

---

## 一、现状基线（已核实）

| 维度 | 数值 |
|---|---|
| 容器 | cockpit(5310)/cockpit-manual(5311)/cockpit-ent(5315)/cockpit-follow(5316) 全 healthy |
| 宿主 API | 8002 tender(systemd) / 8003 qa / 8001 enterprise(ykcompany) |
| 代码 | collector 47 py（1.3万行）/ 前端 js 4421 行 / css 4846 行 |
| 数据 | notices 2.9万 / projects 2.2万 / project_links 1万 / tender.db 183MB |
| 定时 | crontab 6 条（主抓 8/12/18 + 3am 补偿 + 备份 + 健康快照）|
| 采集 | 22 站串行抓取，单轮 ~1.8h |
| 磁盘 | 44% |

---

## 二、优化清单总览

| 档 | # | 优化项 | 预期收益 | 改动量 |
|---|---|---|---|---|
| 🔴 高 | H1 | 采集并发化 | 主抓 1.8h→~1h | 中 |
| 🔴 高 | H2 | 日志轮转 | 日志不再无限涨 | 小 |
| 🔴 高 | H3 | meta 接口缓存 | 首屏 624ms→<10ms | 小 |
| 🟡 中 | M1 | 失败站点即时重试 | 漏抓窗口 1.8h→分钟级 | 小 |
| 🟡 中 | M2 | 核心逻辑回归测试 | 防规则改动回归 | 中 |
| 🟡 中 | M3 | site/ 纳入版本管理 | 前端改动可追溯回滚 | 小 |
| 🟡 中 | M4 | 深翻页游标化 | 深翻页 1.5s→~20ms | 中 |
| 🟢 低 | L1 | collector 模块分层 | 可维护性 | 大（缓）|
| 🟢 低 | L2 | FTS5 全文检索 | 10万条搜索恒定快 | 中（缓）|
| 🟢 低 | L3 | meta 静态文件化 | 首屏更轻 | 小 |
| 🟢 低 | L4 | 备份保留周份 | 防误删找回 | 小 |
| 🟢 低 | L5 | 并发执行健康检查 | 抓取期也覆盖监控 | 小 |
| 🟢 低 | L6 | 主抓耗时告警 | 异常变慢早发现 | 小 |

---

## 三、🔴 高优项（本周）

### H1：采集并发化

**现状**：run.py 的抓取循环按站点串行（`for site in ...: scraper.run()`），22 站一轮 1.8h，长站（江苏政采 4500s 超时窗）拖后腿。

**方案**：抓取阶段改为线程池并发（3-5 并发），站点独立互不影响：
- `scrapers.py` 各 scraper 无共享 mutable 状态（已隔离：每站独立实例）→ 线程安全前提成立
- 写库用 store_db 统一入口（SQLite WAL + busy_timeout 已支持并发写）
- **长站隔离**：江苏政采/CDP 类单站超时窗长，单独一组（并发 1-2）；普通站一组（并发 4）
- 心跳/进度状态保持单写（crawl_status.json 由主线程汇总，避免并发写状态文件）
- 站点级错误隔离：单站异常不拖垮批次（现有 try/except 保持）

**改动文件**：run.py（抓取循环）、config.py（可加 `CONCURRENCY` / `SLOW_SITES` 配置）
**验证**：一次全量主抓计时对比；抽查各站条数与串行一致
**回滚**：配置文件并发数调回 1

### H2：日志轮转

**现状**：cron.log 12MB+、backfill*/site_refresher.log 只增不减，排查困难且占盘。

**方案**：deploy.sh / site_refresher.sh 的 `>> cron.log` 前统一加轮转：
```bash
# 顶部加
rotate_log() {
  local f="$1" max=${2:-20M} keep=${3:-7}
  [ -f "$f" ] && [ $(stat -c%s "$f") -gt $max ] && {
    mv "$f" "$f.1"; gzip "$f.1";
    # 只留最近 keep 个压缩档
    ls -1t "$f".*.gz 2>/dev/null | tail -n +$((keep+1)) | xargs -r rm -f
  }
}
rotate_log "$LOG_DIR/cron.log"
```
- 或用系统 logrotate 配置（更标准，但不随容器脚本走，按现状选脚本内方案）
- health_snapshot.log 已有追加，同样处理

**验证**：构造超阈值文件跑一次，确认归档+压缩

### H3：meta 接口缓存

**现状**：`/api/tender/meta` 每次 3 条全表 GROUP BY（624ms）。数据 3h 才变一次。

**方案**：api.py 的 query_meta 加进程内 TTL 缓存（60s 足够，主抓后数据变更 60s 内生效）：
```python
_META_CACHE = {"t": 0, "data": None}
def query_meta_cached():
    now = time.time()
    if _META_CACHE["data"] and now - _META_CACHE["t"] < 60:
        return _META_CACHE["data"]
    d = query_meta()
    _META_CACHE = {"t": now, "data": d}
    return d
```
- 首屏 10ms 内返回；主抓完成后最多 60s 旧数据，可接受（页面有"数据更新"时间显示）

**验证**：连续两次请求，第二次应 <20ms

---

## 四、🟡 中优项（1-2 周）

### M1：失败站点即时重试

**现状**：某站本轮抓失败（如江苏政采超时），只记 last_failed_sites.json，等 3am 补偿或下一轮主抓。

**方案**：deploy.sh 尾段（publish 前）检查 last_failed_sites.json，对失败站**立即单站重试一次**（幂等，无需全库重建）：
- run.py 已有单站抓取入口（scrape_ccgp_only.py 模式可复用/泛化）
- 重试结果合并进本轮 tender.json（增量入库 → export 时会带上）
- 加防抖：同站连续 2 轮失败才触发重试（1 轮失败的可能是瞬时抖动）

**验证**：手动制造一站超时 → 确认重试触发且数据补齐

### M2：核心逻辑回归测试

**现状**：classify_district / classify_category / is_ict / project_engine 零自动化测试。昨天修的 9.4 复核问题（外地误划盐开、ICT 误判）没有固化。

**方案**：`tender/collector/tests/` 建 pytest 集：
- `test_district.py`：9.4 复核全部案例（江西水利/天津一中/山东滨海公安/滨海陈涛镇/生态环境局/一中/经贸职校/本地边界）
- `test_ict.py`：复核 ICT 案例（凤依府立项咨询/土地招租/网球场/CA证书/快检/一中真ICT/大数据集团咨询含平台）
- `test_category.py`：13 major 抽样 + 未分类兜底
- `test_project_engine.py`：stsge 判定（terminated/awarded/upcoming/pending_result 分支）
- 用只读副本库跑，不动正式库

**验证**：`pytest tests/ -q` 全绿；改规则时先跑测试

### M3：site/ 纳入版本管理

**现状**：site/（前端源码）不在 git（此前已发现 git ls-files 不含），部署全靠 docker cp + md5 核对，改动无历史。

**方案**：
```bash
cd projects/cockpit/site
git init && git add -A && git commit -m "baseline 2026-09-07（懒加载完成后）"
```
- 每次部署前 `git add -A && git commit -m "..."`（deploy 脚本可加自动 commit 钩子）
- 与现有容器 md5 核对互补：git 管"改了什么"，md5 管"部署对没对上"

**验证**：改一个 css 提交 → git log 可追溯；回滚 = git checkout 旧 commit + docker cp

### M4：深翻页游标化

**现状**：OFFSET 深翻页 page=2000 1.55s（SQLite 需扫前 8 万行）。

**方案**：分页参数加 `cursor`（基于 latest_notice_date + id 复合索引的 keyset 分页）：
- `WHERE (latest_notice_date, id) < (?, ?) ORDER BY ... LIMIT 40` 代替 OFFSET
- 前端页码跳转保留 OFFSET（低频），“下一页”链用游标（高频）
- 需联合索引 idx_projects_latest_id（latest_notice_date DESC, id DESC）

**验证**：深翻 2000 页延迟 <50ms

---

## 五、🟢 低优项（锦上添花/数据增长后再做）

### L1：collector 模块分层（缓，配 M3 版本管理后做）
47 个 py 按「抓取器/富化/发布/运维脚本」分组目录化，不动逻辑只动结构；配合 M3 的 git 历史可安全重构。

### L2：FTS5 全文检索（数据 10 万+ 时做）
q 参数换 SQLite FTS5（title/purchaser 索引），搜索 10 万条恒定 ~5ms；现在 LIKE 49ms 已够用。

### L3：meta 静态文件化
参考 screen.json 思路：主抓后单独生成 `data/meta.json`（计数），nginx 直出，比 API 更快且省 API 压力；API 保留作 fallback。

### L4：备份保留周份
backup.sh 现只留 backups/latest 一份；每周日额外存一份 `week-YYYYMMDD/`，保留 4 周。防误删/数据找回。

### L5：并发执行健康检查
health_snapshot 每 5 分钟跑一次，但主抓 1.8h 内状态文件频繁变动可能漏报；改为抓取期间也覆盖（现状其实已在跑，只需确认锁/心跳判断在主抓中不误报——复查告警阈值）。

### L6：主抓耗时告警
health_snapshot 增加"本轮主抓耗时 > N 小时"告警（正常 1.5-2h，超 3h 怀疑卡死/网络恶化）。

---

## 六、执行顺序与工作量

| 批次 | 项 | 预计 |
|---|---|---|
| 本周边角 | H2（日志）+ H3（meta缓存）+ M3（git）+ M4? | 半天 |
| 本周核心 | H1（并发采集） | 1 天 |
| 下周 | M1（重试）+ M2（测试） | 1-2 天 |
| 按需 | M4 / L1-L6 | 逐步 |

全部改动均走现有部署链路（改码 → 最小验证 → docker cp / systemctl restart → md5/健康核对），可单项目独立回滚。

---
*本方案随执行进展更新；每项完成后在 MEMORY 记录结果与坑。*