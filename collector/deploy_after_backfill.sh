#!/bin/bash
# deploy_after_backfill.sh — 中国政府采购网历史回填完成后的手动部署
# 只做：重建项目 → 质量校验 → 导出 JSON → 一致性校验 → 发布到 cockpit
# 不重跑 run.py 主抓（回填已入库，主抓下一轮 08/12/18 点自然增量）
# 用法：bash deploy_after_backfill.sh

set -uo pipefail
cd /root/.openclaw/workspace/agents/yankai/projects/cockpit/tender/collector || exit 1

LOG_DIR="$PWD/logs"
DEPLOY_STATUS="$LOG_DIR/deploy_status.json"
DEPLOY_MANIFEST="$LOG_DIR/deployment_manifest.json"
RUN_STARTED_AT=$(date '+%Y-%m-%d %H:%M:%S')

write_deploy_status() {
  local status="$1" phase="$2" message="${3:-}"
  /usr/bin/python3 - "$DEPLOY_STATUS" "$$" "$status" "$phase" "$RUN_STARTED_AT" "$message" <<'PY'
import json, os, sys
from datetime import datetime
path, pid, status, phase, started_at, message = sys.argv[1:]
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
tmp = path + ".tmp"
with open(tmp, "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, indent=2)
os.replace(tmp, path)
PY
}

fail() {
  echo "$(date '+%Y-%m-%d %H:%M:%S') [ERROR] $*，保留线上旧数据" >> cron.log
  write_deploy_status "failed" "manual" "$*"
  exit 1
}

write_deploy_status "running" "rebuild" "回填后重建项目并导出"

if ! /usr/bin/python3 - <<'PY' >> cron.log 2>&1
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from project_engine import rebuild
from run import export_tender_json
from verify_quality import check
rebuild()
if check() != 0:
    raise RuntimeError("重建后质量校验未通过")
export_tender_json([])
print("回填后重建+导出完成")
PY
then
  fail "重建、质量校验或 JSON 导出失败"
fi

# 部署前一致性校验（复用 deploy.sh 逻辑）
write_deploy_status "running" "verify" "部署前完整性与数量校验"
if ! /usr/bin/python3 - <<'PY' >> cron.log 2>&1
import json, os, sqlite3, sys
path = "../data/tender.json"
if not os.path.isfile(path) or os.path.getsize(path) == 0:
    raise RuntimeError("tender.json 不存在或为空")
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

# 发布
write_deploy_status "running" "publish" "同步源文件并发布到 cockpit"
cp ../data/tender.json ../../site/data/tender.json || fail "同步站点源 JSON 失败"
docker cp ../data/tender.json cockpit:/usr/share/nginx/html/data/tender.json \
  || fail "复制 JSON 到 cockpit 容器失败"

if ! docker exec cockpit test -s /usr/share/nginx/html/data/tender.json; then
  fail "容器内 tender.json 校验失败"
fi

# 发布清单 + SHA256 校验
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
manifest = {
    "run_id": "manual-backfill-deploy",
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

write_deploy_status "running" "audit" "发布后字段覆盖率审计"
/usr/bin/python3 audit_coverage.py >> cron.log 2>&1 || true
write_deploy_status "success" "completed" "手动部署完成（ccgp 历史回填）"
echo "$(date '+%Y-%m-%d %H:%M:%S') 手动部署完成（ccgp 历史回填）" >> cron.log
echo "DONE"
