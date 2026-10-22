#!/bin/bash
# deploy_after_backfill.sh — 中国政府采购网历史回填完成后的手动部署
# 只做：重建项目 → 质量校验；8002 API 直接读取数据库
# 不重跑 run.py 主抓（回填已入库，主抓下一轮 08/12/18 点自然增量）
# 用法：bash deploy_after_backfill.sh

set -uo pipefail
cd /root/.openclaw/workspace/agents/yktender/projects/cockpit/tender/collector || exit 1

LOG_DIR="$PWD/logs"
RUN_STARTED_AT=$(date '+%Y-%m-%d %H:%M:%S')

write_deploy_status() {
  local status="$1" phase="$2" message="${3:-}"
  /usr/bin/python3 - "$$" "$status" "$phase" "$RUN_STARTED_AT" "$message" <<'PY'
import sys
from datetime import datetime
from run_state import update
pid, status, phase, started_at, message = sys.argv[1:]
data = {
    "run_id": "manual-backfill-deploy",
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

fail() {
  echo "$(date '+%Y-%m-%d %H:%M:%S') [ERROR] $*，保留线上旧数据" >> cron.log
  write_deploy_status "failed" "manual" "$*"
  exit 1
}

write_deploy_status "running" "rebuild" "回填后重建项目并质量校验"

if ! /usr/bin/python3 - <<'PY' >> cron.log 2>&1
import os, sys, sqlite3
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from project_engine import rebuild
from verify_quality import check
rebuild()
if check() != 0:
    raise RuntimeError("重建后质量校验未通过")
conn = sqlite3.connect("data/tender.db")
if conn.execute("PRAGMA quick_check").fetchone()[0] != "ok":
    raise RuntimeError("SQLite quick_check 未通过")
print("回填后重建+质量校验完成，API 将直接读取新数据")
conn.close()
PY
then
  fail "重建、质量校验或 SQLite 校验失败"
fi

write_deploy_status "success" "completed" "手动回填重建完成（API 直接读取数据库）"
echo "$(date '+%Y-%m-%d %H:%M:%S') 手动回填重建完成（API 直接读取数据库）" >> cron.log
echo "DONE"
