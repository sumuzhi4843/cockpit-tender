#!/bin/bash
# 招标监控自动部署脚本
# crontab: 0 8,12,18 * * * /bin/bash /root/.openclaw/workspace/agents/yankai/projects/cockpit/tender/collector/deploy.sh
#
# 流程：run.py 抓取 → 质量门禁 → 缺开标时间自动补抓（分级）→ 重建项目 → 导出 JSON → 部署
# 2026-08-06：接入中国邮政 / 江苏招标平台 PDF 补抓 + 统一缺时间补抓（backfill_open_dates.py）

set -uo pipefail

cd /root/.openclaw/workspace/agents/yankai/projects/cockpit/tender/collector || exit 1

LOG_DIR="$PWD/logs"
mkdir -p "$LOG_DIR"
DEPLOY_STATUS="$LOG_DIR/deploy_status.json"
DEPLOY_MANIFEST="$LOG_DIR/deployment_manifest.json"
exec 9>"$LOG_DIR/cron.lock"
if ! flock -n 9; then
  echo "$(date '+%Y-%m-%d %H:%M:%S') [SKIP] 主抓任务仍在运行，跳过本次" >> cron.log
  exit 0
fi

RUN_STARTED_EPOCH=$(date +%s)
RUN_STARTED_AT=$(date '+%Y-%m-%d %H:%M:%S')
DEPLOY_PHASE="starting"
DEPLOY_FINALIZED=0

write_deploy_status() {
  local status="$1"
  local phase="$2"
  local message="${3:-}"
  DEPLOY_PHASE="$phase"
  /usr/bin/python3 - "$DEPLOY_STATUS" "$$" "$status" "$phase" "$RUN_STARTED_AT" "$message" <<'PY'
import json, os, sys
from datetime import datetime

path, pid, status, phase, started_at, message = sys.argv[1:]
run_id = ""
crawl_status = os.path.join(os.path.dirname(path), "crawl_status.json")
try:
    with open(crawl_status, encoding="utf-8") as f:
        crawl = json.load(f)
    crawl_started = str(crawl.get("started_at", "")).replace("T", " ")
    if crawl_started >= started_at:
        run_id = crawl.get("run_id", "")
except Exception:
    pass
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
tmp = path + ".tmp"
with open(tmp, "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, indent=2)
os.replace(tmp, path)
PY
}

write_deploy_status "running" "collect" "主抓与质量门禁"

on_exit() {
  local rc=$?
  if [ "$rc" -ne 0 ] && [ "$DEPLOY_FINALIZED" -eq 0 ]; then
    write_deploy_status "failed" "$DEPLOY_PHASE" "deploy.sh 异常退出（exit=$rc）" || true
  fi
}
trap on_exit EXIT

fail() {
  echo "$(date '+%Y-%m-%d %H:%M:%S') [ERROR] $*，保留线上旧数据" >> cron.log
  write_deploy_status "failed" "$DEPLOY_PHASE" "$*"
  DEPLOY_FINALIZED=1
  exit 1
}

if /usr/bin/python3 run.py >> cron.log 2>&1; then
  write_deploy_status "running" "backfill" "补抓公告详情、PDF 与开标时间"
  # ── 缺开标时间自动补抓（2026-08-06，分级处理）──
  # L1 库内正文 → L2 详情页正文 → L3 PDF文本层 → L4 扫描件OCR(+M3校正)
  # 近 30 天新增的"开标时间待确认"公告自动补抓
  /usr/bin/python3 backfill_open_dates.py --days 30 >> cron.log 2>&1
  # 中国邮政：缺开标时间的公告（文本层 PDF，快，默认模式）
  /usr/bin/python3 backfill_chinapost_pdfs.py >> cron.log 2>&1
  # 中国邮政：PDF 落库 + 简称指代解析（2026-08-17 老板拍板 4 大头开工）
  /usr/bin/python3 backfill_chinapost_detail.py --limit 30 >> cron.log 2>&1
  # 江苏招标平台：近 7 天新增的缺中标单位公告（PDF 文本层直接提取；扫描件标记需 OCR，不阻塞）
  # 窗口 3→7 天（2026-08-31）：防止接口/OCR 失败的公告滑出窗口后永远无人补
  /usr/bin/python3 backfill_jszbcg_pdfs.py --days 7 >> cron.log 2>&1
  # 江苏政府采购网：缺正文定向补抓（2026-08-31）——run.py 对本站只抓列表，
  # 此前链路无本站详情步骤，8/24 起正文无人补（8 月缺 71 条已手工补齐）。
  # 与主抓详情同一接口（API 优先），只补空 content，字段仅回填空值，幂等。
  /usr/bin/python3 backfill_ccgp_details.py --days 7 >> cron.log 2>&1
  # 苏服采：详情地址/成交供应商回填（2026-08-19，只补新公告，幂等）
  # 公开列表接口不返回单位地址/供应商；详情接口按 project_key 回填，
  # 并作为区域判定的第一级证据（盐南/经开），支撑“盐开区”筛选。
  /usr/bin/python3 backfill_sufucai_detail.py >> cron.log 2>&1

  # ── 国投集团限流兜底（2026-08-10 从独立 cron 并入）──
  # 原 9/13/17 点独立补抓任务取消，改为主抓尾部兜底：仅上午 8 点那次跑
  # （对应原 9 点补抓时机），避免每次主抓都重复翻列表页。
  # 幂等：backfill_sdicc 用 url_fixed_20260807 标记已修复记录，未修复=0 时秒退。
  if [ "$(date +%H)" = "08" ]; then
    UNFIXED=$(/usr/bin/python3 -c "
import sys, os
sys.path.insert(0, '/root/.openclaw/workspace/agents/yankai/projects/cockpit/tender/collector')
from store_db import TenderDB
db = TenderDB()
n = db._conn.execute(\"SELECT COUNT(*) FROM notices WHERE site='国投集团' AND raw_json NOT LIKE '%url_fixed_20260807%'\").fetchone()[0]
db.close()
print(n)" 2>/dev/null)
    if [ -n "$UNFIXED" ] && [ "$UNFIXED" -gt 0 ] 2>/dev/null; then
      echo "$(date '+%Y-%m-%d %H:%M:%S') 国投未修复详情 $UNFIXED 条，执行兜底回填…" >> cron.log
      /usr/bin/python3 backfill_sdicc.py >> cron.log 2>&1
    else
      echo "$(date '+%Y-%m-%d %H:%M:%S') 国投无未修复详情，跳过兜底" >> cron.log
    fi
  fi

  # ── 重建项目（补抓的开标时间/中标单位影响项目状态）──
  write_deploy_status "running" "rebuild" "补抓后重建项目并导出"
  if ! /usr/bin/python3 - <<'PY' >> cron.log 2>&1
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from project_engine import rebuild
from run import export_tender_json
from verify_quality import check
rebuild()
if check() != 0:
    raise RuntimeError("补抓后质量校验未通过")
export_tender_json([])
print("补抓后重建+导出完成")
PY
  then
    fail "补抓后重建、质量校验或 JSON 导出失败"
  fi

  write_deploy_status "running" "verify" "部署前完整性与数量校验"
  if ! /usr/bin/python3 - "$RUN_STARTED_EPOCH" <<'PY' >> cron.log 2>&1
import json, os, sqlite3, sys

started = int(sys.argv[1])
path = "../data/tender.json"
if not os.path.isfile(path) or os.path.getsize(path) == 0:
    raise RuntimeError("tender.json 不存在或为空")
if os.path.getmtime(path) < started:
    raise RuntimeError("tender.json 不是本轮生成，拒绝部署旧文件")
with open(path, encoding="utf-8") as f:
    data = json.load(f)
required = {"generated_at", "schema_version", "summary", "sites", "projects", "ict_projects"}
missing = required - set(data)
if missing:
    raise RuntimeError(f"tender.json 缺少字段: {sorted(missing)}")
conn = sqlite3.connect("data/tender.db")
retired_sites = ("瑞康医院",)
retired_ph = ",".join("?" * len(retired_sites))
notice_count = conn.execute(
    f"SELECT COUNT(*) FROM notices WHERE site NOT IN ({retired_ph})", retired_sites
).fetchone()[0]
project_count = conn.execute(
    f"SELECT COUNT(*) FROM projects WHERE site NOT IN ({retired_ph})", retired_sites
).fetchone()[0]
if data["summary"]["total"] != notice_count or len(data["projects"]) != project_count:
    raise RuntimeError(
        f"JSON/DB 数量不一致: json={data['summary']['total']}/{len(data['projects'])}, "
        f"db={notice_count}/{project_count}"
    )
print(f"部署前校验通过: generated_at={data['generated_at']} notices={notice_count} projects={project_count}")
PY
  then
    fail "部署前 JSON 完整性校验失败"
  fi

  write_deploy_status "running" "publish" "同步源文件并发布到 cockpit"
  cp ../data/tender.json ../../site/data/tender.json || fail "同步站点源 JSON 失败"
  docker cp ../data/tender.json cockpit:/usr/share/nginx/html/data/tender.json \
    || fail "复制 JSON 到 cockpit 容器失败"

  if ! docker exec cockpit test -s /usr/share/nginx/html/data/tender.json; then
    fail "容器内 tender.json 校验失败"
  fi

  if ! /usr/bin/python3 - "$DEPLOY_MANIFEST" "$RUN_STARTED_AT" <<'PY' >> cron.log 2>&1
import hashlib, json, os, sqlite3, subprocess, sys
from datetime import datetime

manifest_path, started_at = sys.argv[1:]
source_path = "../../site/data/tender.json"
with open(source_path, "rb") as f:
    source_sha = hashlib.file_digest(f, "sha256").hexdigest()
container_sha = subprocess.check_output(
    ["docker", "exec", "cockpit", "sha256sum", "/usr/share/nginx/html/data/tender.json"],
    text=True,
).split()[0]
if source_sha != container_sha:
    raise RuntimeError(f"源/容器 SHA256 不一致: {source_sha} != {container_sha}")
with open(source_path, encoding="utf-8") as f:
    data = json.load(f)
conn = sqlite3.connect("data/tender.db")
retired_sites = ("瑞康医院",)
retired_ph = ",".join("?" * len(retired_sites))
notice_count = conn.execute(
    f"SELECT COUNT(*) FROM notices WHERE site NOT IN ({retired_ph})", retired_sites
).fetchone()[0]
project_count = conn.execute(
    f"SELECT COUNT(*) FROM projects WHERE site NOT IN ({retired_ph})", retired_sites
).fetchone()[0]
conn.close()
run_id = ""
try:
    with open("logs/crawl_status.json", encoding="utf-8") as f:
        run_id = json.load(f).get("run_id", "")
except Exception:
    pass
manifest = {
    "run_id": run_id,
    "run_started_at": started_at,
    "published_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    "generated_at": data.get("generated_at"),
    "schema_version": data.get("schema_version"),
    "summary": data.get("summary"),
    "db": {"notices": notice_count, "projects": project_count},
    "source": {"path": source_path, "size_bytes": os.path.getsize(source_path), "sha256": source_sha},
    "container": {"path": "/usr/share/nginx/html/data/tender.json", "sha256": container_sha},
    "verified": True,
}
tmp = manifest_path + ".tmp"
with open(tmp, "w", encoding="utf-8") as f:
    json.dump(manifest, f, ensure_ascii=False, indent=2)
os.replace(tmp, manifest_path)
print(f"发布清单写入: {manifest_path} sha256={source_sha}")
PY
  then
    fail "发布清单或源/容器一致性校验失败"
  fi
  echo "$(date '+%Y-%m-%d %H:%M:%S') 部署完成" >> cron.log

  # ── 漏缺项目信息自检（2026-08-17，只读 DB 不抓取，告警各站字段缺口恶化）──
  write_deploy_status "running" "audit" "发布后字段覆盖率审计"
  /usr/bin/python3 audit_coverage.py >> cron.log 2>&1 || true

  # 大屏数据（5313 监控中心）：读库预聚合生成 screen.json；失败不阻断主流程，5313 继续用上一版
  write_deploy_status "running" "screen" "生成大屏 screen.json"
  if /usr/bin/python3 ../../screen/export_screen_json.py >> cron.log 2>&1; then
    echo "[deploy] screen.json 生成成功" >> cron.log
  else
    echo "[deploy] screen.json 生成失败，5313 继续使用上一版" >> cron.log
  fi

  write_deploy_status "success" "completed" "抓取、补抓、校验、发布与审计完成"
  DEPLOY_FINALIZED=1
else
  fail "采集或质量门禁失败"
fi
