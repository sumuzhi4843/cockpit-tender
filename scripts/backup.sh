#!/bin/bash
# Cockpit 统一备份：保留 latest(当日) + previous(上一日) + week(最近一周日) 共 3 套可恢复数据。
# 2026-09-23 优化：previous 改为持久保留（周末兜底），week 由 4 份缩到 1 份。
set -euo pipefail

ROOT="/root/.openclaw/workspace/agents/yktender/projects/cockpit"
# 2026-09-07 H2：备份前对所有超 20MB 日志做轮转（cron.log/health_snapshot.log/api.log 等）
bash "$ROOT/scripts/rotate_log.sh" "$ROOT/tender/collector/cron.log" 20 7
for lg in "$ROOT"/tender/collector/logs/*.log "$ROOT"/tender/api.log "$ROOT"/enterprise/api.log; do
  [ -f "$lg" ] && bash "$ROOT/scripts/rotate_log.sh" "$lg" 20 5
done
BACKUP_ROOT="$ROOT/backups"
LATEST="$BACKUP_ROOT/latest"
TMP="$BACKUP_ROOT/.latest_tmp"
PREVIOUS="$BACKUP_ROOT/previous"   # 保留昨日备份，周末兜底（周五 latest 异常时周六仍有周五可恢复）
LOG="$BACKUP_ROOT/backup.log"
TENDER_DB="$ROOT/tender/collector/data/tender.db"
# 2026-09-06 三栏目拆分：企业库已迁至 ykcompany 工作区（原 enterprise/db 为旧拷贝，将清理）
# 2026-09-12 企业库改名 tianyancha.db → ykqcc.db（ykcompany 侧操作），备份产物同名跟进
ENTERPRISE_DB="/root/.openclaw/workspace/agents/ykcompany/projects/cockpit-ent/enterprise/db/ykqcc.db"
OVERRIDES="$ROOT/tender/collector/manual_overrides.json"

mkdir -p "$BACKUP_ROOT"
exec 9>"$BACKUP_ROOT/backup.lock"
if ! flock -n 9; then
  echo "$(date '+%Y-%m-%d %H:%M:%S') [SKIP] backup already running" >> "$LOG"
  exit 0
fi

# 2026-10-20 老板拍板：备份周期改为 3 天一次（tender.db 已 305MB，日备太重；3 天 ≈ 丢 ~1500 条 notices，可接受）。
# 用 LATEST/BACKUP_INFO.txt 的 created_at 算间隔，刻意不用 cron `*/3`——它有月初重置坑（31 号→1 号只隔 1 天）。
BACKUP_PERIOD_DAYS=3
if [ -f "$LATEST/BACKUP_INFO.txt" ]; then
  _last_at=$(sed -n 's/^created_at=//p' "$LATEST/BACKUP_INFO.txt" | head -1)
  _last_epoch=$(date -d "$_last_at" +%s 2>/dev/null || echo 0)
  if [ "$_last_epoch" -gt 0 ]; then
    _elapsed_days=$(( ( $(date +%s) - _last_epoch ) / 86400 ))
    if [ "$_elapsed_days" -lt "$BACKUP_PERIOD_DAYS" ]; then
      echo "$(date '+%Y-%m-%d %H:%M:%S') [SKIP] 距上次备份 ${_elapsed_days} 天 < ${BACKUP_PERIOD_DAYS} 天（上次 $_last_at）" >> "$LOG"
      exit 0
    fi
  fi
fi

# 2026-10-20：ykcompany 企业库已下线（ykqcc.db 及其目录均不存在），备份侧联动同步摘除。
# 这里做容错而非删除：企业库缺失时跳过、只备 tender.db，绝不让 set -e 静默杀掉整条备份链。
# （10-08~10-19 备份停摆 12 天，正是此行 set -e 静默死亡所致——日志只有 [START] 没有 [OK]。）
BACKUP_ENTERPRISE=0
if [ -f "$ENTERPRISE_DB" ]; then
  BACKUP_ENTERPRISE=1
else
  echo "$(date '+%Y-%m-%d %H:%M:%S') [WARN] 企业库不存在，跳过 ykqcc.db（本轮仅备 tender.db）: $ENTERPRISE_DB" >> "$LOG"
fi

cleanup_tmp() {
  rm -rf -- "$TMP"
}
trap cleanup_tmp EXIT

rm -rf -- "$TMP"
mkdir -p "$TMP"
echo "$(date '+%Y-%m-%d %H:%M:%S') [START] unified backup" >> "$LOG"

# SQLite 在线备份可在业务读写期间生成一致快照。
sqlite3 "$TENDER_DB" ".timeout 30000" ".backup '$TMP/tender.db'"
if [ "$BACKUP_ENTERPRISE" = "1" ]; then
  sqlite3 "$ENTERPRISE_DB" ".timeout 30000" ".backup '$TMP/ykqcc.db'"
  cp_contents="tender.db,ykqcc.db,manual_overrides.json"
  cp_sums="tender.db ykqcc.db manual_overrides.json"
else
  cp_contents="tender.db,manual_overrides.json"
  cp_sums="tender.db manual_overrides.json"
fi
cp -f "$OVERRIDES" "$TMP/manual_overrides.json"

# 最小可用性校验：文件可读、结构完整、核心业务表存在且非空。
[ "$(sqlite3 -readonly "$TMP/tender.db" 'PRAGMA quick_check;')" = "ok" ]
[ "$(sqlite3 -readonly "$TMP/tender.db" 'SELECT COUNT(*) FROM notices;')" -gt 0 ]
if [ "$BACKUP_ENTERPRISE" = "1" ]; then
  [ "$(sqlite3 -readonly "$TMP/ykqcc.db" 'PRAGMA quick_check;')" = "ok" ]
  [ "$(sqlite3 -readonly "$TMP/ykqcc.db" 'SELECT COUNT(*) FROM enterprise_detail;')" -gt 0 ]
fi
# 只保留主 DB；只读校验 WAL 库时可能产生空的 shm/wal 辅助文件。
rm -f -- "$TMP"/*.db-wal "$TMP"/*.db-shm

tender_count=$(sqlite3 -readonly "$TMP/tender.db" 'SELECT COUNT(*) FROM notices;')
enterprise_count="(未备份)"
if [ "$BACKUP_ENTERPRISE" = "1" ]; then
  enterprise_count=$(sqlite3 -readonly "$TMP/ykqcc.db" 'SELECT COUNT(*) FROM enterprise_detail;')
fi
created_at=$(date '+%Y-%m-%d %H:%M:%S %z')
cat > "$TMP/BACKUP_INFO.txt" <<EOF
created_at=$created_at
tender_notices=$tender_count
enterprises=$enterprise_count
contents=$cp_contents
period_days=$BACKUP_PERIOD_DAYS
retention=daily:2,weekly:1
EOF

(cd "$TMP" && sha256sum $cp_sums > SHA256SUMS)

# 新备份完整生成后再替换；失败时原 latest/previous 均不受影响。
# 2026-09-23 优化：previous 持久保留为上一日备份供周末兜底；轮转前先丢弃前日旧 previous，
# 避免 mv 把 latest 嵌套进已存在的 previous/ 目录（沙箱模拟发现，同日修复）。
rm -rf -- "$PREVIOUS"
if [ -d "$LATEST" ]; then
  mv "$LATEST" "$PREVIOUS"
fi
mv "$TMP" "$LATEST"
trap - EXIT

echo "$(date '+%Y-%m-%d %H:%M:%S') [OK] tender=$tender_count enterprise=$enterprise_count" >> "$LOG"

# ── L4 周备份保留（2026-10-20 改：改为每轮成功备份都留，滚动保 1 份）──
# 原为「仅周日」触发，但备份周期改 3 天后周日未必命中，会导致周备份长期不更新。
# 3 天一轮 + 每次都留一份 = 天然多一份异地副本；gzip 后仅 ~40M，滚动保 1 份成本可忽略。
# 只备 tender.db + overrides（企业库属 ykcompany，且已下线），与原口径一致。
WEEK_DIR="$BACKUP_ROOT/week-$(date '+%Y%m%d')"
rm -rf -- "$WEEK_DIR"
mkdir -p "$WEEK_DIR"
gzip -c "$LATEST/tender.db" > "$WEEK_DIR/tender.db.gz"
cp -a "$LATEST/manual_overrides.json" "$WEEK_DIR/" 2>/dev/null || true
cp -a "$LATEST/BACKUP_INFO.txt" "$WEEK_DIR/" 2>/dev/null || true
# 只保留最近 1 个周份（2026-09-23 由 4 缩到 1）
ls -1dt "$BACKUP_ROOT"/week-* 2>/dev/null | tail -n +2 | xargs -r rm -rf --
echo "$(date '+%Y-%m-%d %H:%M:%S') [WEEK] 周备份已留存 $WEEK_DIR（仅 tender.db.gz）" >> "$LOG"

echo "backup OK: $LATEST"
