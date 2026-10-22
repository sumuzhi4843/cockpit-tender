#!/bin/bash
# 招标监控自动部署脚本
# crontab: 0 8,12,18 * * * /bin/bash /root/.openclaw/workspace/agents/yktender/projects/cockpit/tender/collector/deploy.sh
#
# 流程：临时回滚快照 → run.py 抓取 → 缺项补抓 → 重建项目 → 质量门禁 → SQLite 发布
# 2026-08-06：接入中国邮政 / 江苏招标平台 PDF 补抓 + 统一缺时间补抓（backfill_open_dates.py）

set -uo pipefail

cd /root/.openclaw/workspace/agents/yktender/projects/cockpit/tender/collector || exit 1

HERE="$PWD"  # 2026-09-26 修复：日报段引用 $HERE 但从未定义，set -u 下子 shell 静默死掉，daily_report.py 自 9-23 起从未被 deploy 执行过
LOG_DIR="$PWD/logs"
mkdir -p "$LOG_DIR"
# 2026-09-07 H2 日志轮转：主抓前检查，超 20MB 归档压缩（保留 7 份）
bash ../../scripts/rotate_log.sh "$PWD/cron.log" 20 7
RUN_STATE="$LOG_DIR/run_state.json"
ROLLBACK_DB="/tmp/cockpit-tender-predeploy-$$.db"
exec 9>"$LOG_DIR/cron.lock"
if ! flock -n 9; then
  echo "$(date '+%Y-%m-%d %H:%M:%S') [SKIP] 主抓任务仍在运行，跳过本次" >> cron.log
  exit 0
fi
LOCK_ACQUIRED=1   # 2026-10-09 EXIT trap 配套：标记“已抢到锁”——lock 抢占失败的那次不该触发 failed 写

RUN_STARTED_EPOCH=$(date +%s)
RUN_STARTED_AT=$(date '+%Y-%m-%d %H:%M:%S')
DEPLOY_PHASE="starting"
DEPLOY_FINALIZED=0
ROLLBACK_READY=0

# 2026-10-09：加 EXIT trap，捕捉非正常退出（set -e 触发 / 被信号打断 / 静默 exit）
# 现象：10-08 + 10-09 18:00 轮 deploy.sh 走到“日报生成成功”后没写 success 状态就退出，两文件都没生成 → 黑盒
# 修法：顶层注册 EXIT trap；只在 OK=0 + DEPLOY_FINALIZED=0 + LOCK_ACQUIRED=1 时落 failed，
# 正常路径在 success 之前置 OK=1 让 trap 跳过
OK=0
trap 'if [ "${OK:-0}" != "1" ] && [ "${DEPLOY_FINALIZED:-0}" != "1" ] && [ "${LOCK_ACQUIRED:-0}" = "1" ]; then
  write_deploy_status "failed" "${DEPLOY_PHASE:-unknown}" "deploy.sh 异常退出 exit=$? line=${LINENO}" 2>/dev/null || true
fi' EXIT

snapshot_db() {
  /usr/bin/python3 - "$ROLLBACK_DB" <<'PY'
import sqlite3, sys
src = sqlite3.connect("data/tender.db", timeout=60)
dst = sqlite3.connect(sys.argv[1])
src.backup(dst)
dst.close(); src.close()
PY
  ROLLBACK_READY=1
}

restore_db() {
  if [ "$ROLLBACK_READY" -ne 1 ] || [ ! -s "$ROLLBACK_DB" ]; then return 0; fi
  /usr/bin/python3 - "$ROLLBACK_DB" <<'PY'
import sqlite3, sys
src = sqlite3.connect(sys.argv[1], timeout=60)
dst = sqlite3.connect("data/tender.db", timeout=60)
src.backup(dst)
dst.close(); src.close()
PY
  echo "$(date '+%Y-%m-%d %H:%M:%S') [ROLLBACK] 已从临时快照恢复 tender.db" >> cron.log
}

cleanup_snapshot() {
  rm -f "$ROLLBACK_DB"
  ROLLBACK_READY=0
}

write_deploy_status() {
  local status="$1"
  local phase="$2"
  local message="${3:-}"
  DEPLOY_PHASE="$phase"
  /usr/bin/python3 - "$$" "$status" "$phase" "$RUN_STARTED_AT" "$message" <<'PY'
import sys
from datetime import datetime
from run_state import get, update

pid, status, phase, started_at, message = sys.argv[1:]
crawl = get("crawl") or {}
crawl_started = str(crawl.get("started_at", "")).replace("T", " ")
run_id = crawl.get("run_id", "") if crawl_started >= started_at else ""
data = {
    "run_id": run_id,
    "pid": int(pid),
    "status": status,
    "phase": phase,
    "started_at": started_at,
    "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    "message": message,
}
if status in ("success", "failed"):
    data["finished_at"] = data["updated_at"]
update("deployment", data)
PY
}

write_deploy_status "running" "collect" "主抓与质量门禁"

on_exit() {
  local rc=$?
  if [ "$rc" -ne 0 ] && [ "$DEPLOY_FINALIZED" -eq 0 ]; then
    restore_db || true
    cleanup_snapshot
    write_deploy_status "failed" "$DEPLOY_PHASE" "deploy.sh 异常退出（exit=$rc）" || true
  fi
}
trap on_exit EXIT

fail() {
  echo "$(date '+%Y-%m-%d %H:%M:%S') [ERROR] $*，保留线上旧数据" >> cron.log
  restore_db || true
  cleanup_snapshot
  write_deploy_status "failed" "$DEPLOY_PHASE" "$*"
  DEPLOY_FINALIZED=1
  exit 1
}

snapshot_db || fail "创建本轮临时回滚快照失败"

# 可控回滚演练：只在显式环境变量开启时短暂改变 SQLite user_version，
# 随即走与真实失败完全相同的 fail→restore_db 路径；不会发起抓取。
if [ "${COCKPIT_DEPLOY_ROLLBACK_DRILL:-0}" = "1" ]; then
  /usr/bin/python3 - <<'PY' || fail "回滚演练注入失败"
import sqlite3
conn = sqlite3.connect("data/tender.db", timeout=60)
current = conn.execute("PRAGMA user_version").fetchone()[0]
conn.execute(f"PRAGMA user_version={current + 1}")
conn.commit()
conn.close()
print(f"[ROLLBACK-DRILL] user_version {current} -> {current + 1}")
PY
  fail "可控回滚演练"
fi

if /usr/bin/python3 run.py >> cron.log 2>&1; then
  write_deploy_status "running" "backfill" "补抓公告详情、PDF 与开标时间"
  # ── M1 失败站即时重试（2026-09-07）：主抓失败站不再等 3am 凌晨补偿 ──
  # 仅当本轮有失败站时执行；timeout 2700s(45min) 兕底防重试拖垮主抓；
  # deep_refresher 自行写库，重试数据随本轮后续 rebuild+导出+发布一起上线；
  # 仍失败则留给凌晨 deep-a/b 双重兕底（幂等）。
  FAILED_SITES=$(/usr/bin/python3 -c "
import json
try:
    d = json.load(open('$RUN_STATE')).get('failures', {})
    print(' '.join(d.get('failed', [])))
except Exception:
    pass" 2>/dev/null)
  if [ -n "$FAILED_SITES" ]; then
    echo "$(date '+%Y-%m-%d %H:%M:%S') [M1] 主抓失败站即时重试: $FAILED_SITES" >> cron.log
    timeout 2700 /usr/bin/python3 deep_refresher.py --sites $FAILED_SITES >> cron.log 2>&1 \
      || echo "$(date '+%Y-%m-%d %H:%M:%S') [M1] 重试未完成（超时或仍失败），留给凌晨补偿" >> cron.log
  fi
  # ── 补抓并行化（2026-09-08）：6 组互不依赖的补抓同时跑，各组独立日志防交错 ──
  # 串行时代此段 ~18min（占整轮 26min 的大头）；并行后 ≈ 最慢一组耗时。
  # SQLite WAL + busy_timeout=5000 支持多进程并发写；各组目标站/字段不同，冲突面小。
  # 组内依赖保持串行：邮政 pdfs → detail（detail 依赖 pdfs 下载的 PDF）。
  BF_PIDS=()
  BF_NAMES=()

  # 组1：缺开标时间统一补抓（L1 库内正文→L2 详情页→L3 PDF文本层→L4 OCR）
  /usr/bin/python3 backfill_open_dates.py --days 30 >> "$LOG_DIR/bf_open_dates.log" 2>&1 &
  BF_PIDS+=($!); BF_NAMES+=(bf_open_dates)

  # 组2：中国邮政（PDF 开标时间 → 详情+简称指代，组内串行）
  ( /usr/bin/python3 backfill_chinapost_pdfs.py && /usr/bin/python3 backfill_chinapost_detail.py --limit 30 ) >> "$LOG_DIR/bf_chinapost.log" 2>&1 &
  BF_PIDS+=($!); BF_NAMES+=(bf_chinapost)

  # 组3：江苏招标平台 PDF（近 7 天全子类型：中标 award_final + 招标公告 tender_notice——
  # 招标列表页只存摘要，正文/预算/真开标在详情 PDF；2026-09-20 老板批准闭环）
  /usr/bin/python3 backfill_jszbcg_pdfs.py --all-types --days 7 >> "$LOG_DIR/bf_jszbcg.log" 2>&1 &
  BF_PIDS+=($!); BF_NAMES+=(bf_jszbcg)

  # 组4：江苏政采缺正文定向补抓（近 7 天，只补空 content，幂等）
  /usr/bin/python3 backfill_ccgp_details.py --days 7 >> "$LOG_DIR/bf_ccgp.log" 2>&1 &
  BF_PIDS+=($!); BF_NAMES+=(bf_ccgp)

  # 组5：苏服采详情地址/供应商回填（只补新公告，幂等）
  /usr/bin/python3 backfill_sufucai_detail.py >> "$LOG_DIR/bf_sufucai.log" 2>&1 &
  BF_PIDS+=($!); BF_NAMES+=(bf_sufucai)

  # 组6：国投限流兜底（仅 8 点轮；先查未修复数，0 条秒退，幂等）
  if [ "$(date +%H)" = "08" ]; then
    UNFIXED=$(/usr/bin/python3 - <<'PYQ' 2>/dev/null
import sys
sys.path.insert(0, '/root/.openclaw/workspace/agents/yktender/projects/cockpit/tender/collector')
from store_db import TenderDB
db = TenderDB()
n = db._conn.execute("SELECT COUNT(*) FROM notices WHERE site='国投集团' AND raw_json NOT LIKE '%url_fixed_20260807%'").fetchone()[0]
db.close()
print(n)
PYQ
)
    if [ -n "$UNFIXED" ] && [ "$UNFIXED" -gt 0 ] 2>/dev/null; then
      echo "$(date '+%Y-%m-%d %H:%M:%S') 国投未修复详情 $UNFIXED 条，执行兜底回填…" >> cron.log
      /usr/bin/python3 backfill_sdicc.py >> "$LOG_DIR/bf_sdicc.log" 2>&1 &
      BF_PIDS+=($!); BF_NAMES+=(bf_sdicc)
    else
      echo "$(date '+%Y-%m-%d %H:%M:%S') 国投无未修复详情，跳过兜底" >> cron.log
    fi
  fi

  # 等全部补抓组结束；单组失败不阻断（|| true 语义），各组日志尾部汇总进 cron.log
  echo "$(date '+%Y-%m-%d %H:%M:%S') [补抓并行] ${#BF_PIDS[@]} 组同时执行: ${BF_NAMES[*]}" >> cron.log
  for i in "${!BF_PIDS[@]}"; do
    wait "${BF_PIDS[$i]}" || echo "$(date '+%Y-%m-%d %H:%M:%S') [补抓并行] ${BF_NAMES[$i]} 退出码非 0（不阻断主流程，详见 ${BF_NAMES[$i]}.log）" >> cron.log
  done
  for nm in "${BF_NAMES[@]}"; do
    if [ -f "$LOG_DIR/$nm.log" ]; then
      echo "--- $nm (tail) ---" >> cron.log
      tail -3 "$LOG_DIR/$nm.log" >> cron.log 2>/dev/null || true
    fi
  done
  echo "$(date '+%Y-%m-%d %H:%M:%S') [补抓并行] 全部完成" >> cron.log

  # ── 重建项目 + 质量门禁（API 仅在本段成功后重新开放）──
  write_deploy_status "running" "rebuild" "补抓后重建项目并执行质量门禁"
  if ! /usr/bin/python3 - <<'PY' >> cron.log 2>&1
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from project_engine import rebuild
from verify_quality import check
rebuild()
if check() != 0:
    raise RuntimeError("补抓后质量校验未通过")
print("补抓后重建+质量校验完成")
PY
  then
    fail "补抓后重建或质量校验失败"
  fi

  # 正式发布对象只有 SQLite 主库；校验 quick_check + 核心行数，写轻量 manifest。
  write_deploy_status "running" "verify" "校验 SQLite 正式库"
  if ! /usr/bin/python3 - "$RUN_STARTED_AT" <<'PY' >> cron.log 2>&1
import json, os, sqlite3, sys
from datetime import datetime
from run_state import get, update
started_at = sys.argv[1]
conn = sqlite3.connect("data/tender.db", timeout=60)
quick = conn.execute("PRAGMA quick_check").fetchone()[0]
if quick != "ok":
    raise RuntimeError(f"SQLite quick_check 失败: {quick}")
notice_count = conn.execute("SELECT COUNT(*) FROM notices WHERE site!='瑞康医院'").fetchone()[0]
project_count = conn.execute("SELECT COUNT(*) FROM projects WHERE site!='瑞康医院'").fetchone()[0]
ict_count = conn.execute("SELECT COUNT(*) FROM projects WHERE site!='瑞康医院' AND COALESCE(is_ict,0)=1").fetchone()[0]
generated_at = (conn.execute("SELECT MAX(updated_at) FROM projects").fetchone()[0] or "")[:16]
conn.close()
run_id = ""
try:
    run_id = (get("crawl") or {}).get("run_id", "")
except Exception:
    pass
manifest = {
    "run_id": run_id,
    "run_started_at": started_at,
    "published_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    "generated_at": generated_at,
    "schema_version": 3,
    "db": {"path": "data/tender.db", "notices": notice_count,
           "projects": project_count, "ict": ict_count, "quick_check": quick},
    "verified": True,
}
update("manifest", manifest)
print(f"数据库发布清单写入: notices={notice_count} projects={project_count} ict={ict_count}")
PY
  then
    fail "SQLite 正式库校验或发布清单写入失败"
  fi

  cleanup_snapshot
  echo "$(date '+%Y-%m-%d %H:%M:%S') 数据库发布完成，8002 API 将读取本轮数据" >> cron.log

  # ── 漏缺项目信息自检（2026-08-17，只读 DB 不抓取，告警各站字段缺口恶化）──
  write_deploy_status "running" "audit" "发布后字段覆盖率审计"
  /usr/bin/python3 audit_coverage.py >> cron.log 2>&1 || true

  # 大屏数据（监控中心）：读库预聚合生成 screen.json；失败不阻断主流程，继续用上一版
  # 2026-09-06：5313 独立端口服务退役，屏站并入 cockpit :5310 /screen/ 子路径，
  # 生成后同步到 site/screen/data/，由前端部署一并进入容器。
  write_deploy_status "running" "screen" "生成大屏 screen.json"
  if /usr/bin/python3 ../../screen/export_screen_json.py >> cron.log 2>&1 \
     && cp ../../screen/data/screen.json ../../site/screen/data/screen.json; then
    echo "[deploy] screen.json 生成并同步到 site/screen/ 成功" >> cron.log
  else
    echo "[deploy] screen.json 生成/同步失败，/screen/ 继续使用上一版" >> cron.log
  fi

  OK=1   # 2026-10-09 EXIT trap 配套：正常完成置 1，trap 跳过
  write_deploy_status "success" "completed" "抓取、补抓、校验、发布与审计完成"
  DEPLOY_FINALIZED=1

  # —— 日报：跟随主抓节奏（8/12/18）自动跑，跟着主数据一起刷新 latest.html ——
  # 2026-09-23：原 25 8 单一触发 cron 删掉，改嵌到 deploy.sh 末尾
  # 用单独锁文件（fd 200）防偶发重叠；失败不阻断主流程，latest.html 保留上一份
  # 跑完 daily_report.py 后回写 run_state.deployment，供 daily_link_push.sh 判定。
  write_deploy_status "running" "daily" "生成日报 latest.html"
  (
    flock -n 200 || { echo "[deploy] daily_report: 锁被占用，跳过本次" >> cron.log; exit 0; }
    cd "$HERE" && /usr/bin/python3 daily_report.py >> "$LOG_DIR/daily_report.log" 2>&1
    RC=$?
    if [ $RC -eq 0 ]; then
      echo "[deploy] 日报 latest.html 生成成功（$(date '+%Y-%m-%d %H:%M:%S')）" >> cron.log
      write_deploy_status "success" "daily-done" "本轮 deploy + 日报均成功"
    else
      echo "[deploy] daily_report 失败，latest.html 保留上一份" >> cron.log
      write_deploy_status "failed" "daily-failed" "daily_report.py 退出码=${RC}，详见 daily_report.log"
      # 2026-10-02 v3: 项目信息补充（apply_unified，success 末段触发）
    /usr/bin/python3 _unified_apply.py --site "中国政府采购网" --mode db_or_fetch --max 9999 >> logs/backfill/unified.log 2>&1 || true
  fi
  ) 200>/tmp/cockpit-daily-report.lock
else
  fail "采集或质量门禁失败"
fi
