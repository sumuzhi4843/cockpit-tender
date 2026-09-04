#!/bin/bash
# site_refresher.sh — 统一站点补抓入口（2026-08-09）
#
# 被 cron 调用，按参数分派任务：
#   deep-a  → 凌晨补偿 A 批（江苏政采 + 中国政采，仅抓主抓失败的站）
#   deep-b  → 凌晨补偿 B 批（苏服采 + 盐城政采，仅抓主抓失败的站；最后重建+部署）
#
# 2026-08-10：sdicc 任务已并入 deploy.sh 尾部（上午 8 点主抓后自动兜底），不再单独调度。
#
# 防护：
#   1. flock 防并发（同一任务重复触发 / 与主抓 deploy.sh 重叠时跳过）
#   2. 补偿失败或未生成新 JSON 时不覆盖线上数据
#
# crontab（配合 deploy.sh / deploy_overview.sh）：
#   0 3 * * 1-5       site_refresher.sh deep-a
#   0 30 3 * * 1-5    site_refresher.sh deep-b

set -u

PROJECT_ROOT=/root/.openclaw/workspace/agents/yankai/projects/cockpit/tender/collector
cd "$PROJECT_ROOT" || exit 0

LOG_DIR="$PROJECT_ROOT/logs"
mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/site_refresher.log"

# —— flock：避免重复触发；主抓 deploy.sh 运行中也跳过（用 cron.lock 判断）——
LOCK="$LOG_DIR/site_refresher.lock"
exec 9>"$LOCK"
if ! flock -n 9; then
  echo "$(date '+%Y-%m-%d %H:%M:%S') [SKIP] site_refresher 已在运行，跳过本次" >> "$LOG"
  exit 0
fi

# 与主抓共用锁，消除 pgrep 检查后的竞态窗口。
exec 8>"$LOG_DIR/cron.lock"
if ! flock -n 8; then
  echo "$(date '+%Y-%m-%d %H:%M:%S') [SKIP] 主抓 deploy.sh 运行中，跳过本次补抓" >> "$LOG"
  exit 0
fi

# —— 主抓是否在跑？在跑则跳过（避免并发打同源站）——
if pgrep -f "deploy.sh" > /dev/null 2>&1 || pgrep -f "python3 run.py" > /dev/null 2>&1; then
  echo "$(date '+%Y-%m-%d %H:%M:%S') [SKIP] 主抓 deploy.sh 运行中，跳过本次补抓" >> "$LOG"
  exit 0
fi

echo "=== $(date '+%Y-%m-%d %H:%M:%S') site_refresher.sh $* ===" >> "$LOG"

case "${1:-noop}" in
  sdicc)
    # 国投限流期重试（错开 8/12/18 主抓；<1min，平时 no-op）
    /usr/bin/python3 retry_sdicc.py >> "$LOG" 2>&1
    echo "  retry_sdicc 退出码 $?" >> "$LOG"
    ;;

  deep-a)
    # 凌晨 A 批：江苏 + 中国政采（只补偿主抓失败的站）
    RUN_STARTED_EPOCH=$(date +%s)
    /usr/bin/python3 deep_refresher.py --group A --rebuild >> "$LOG" 2>&1
    RC=$?
    echo "  deep-a 退出码 $RC" >> "$LOG"
    JSON_PATH="$PROJECT_ROOT/../data/tender.json"
    JSON_MTIME=$(stat -c %Y "$JSON_PATH" 2>/dev/null || echo 0)
    if [ "$RC" -eq 0 ] && [ -s "$JSON_PATH" ] && [ "$JSON_MTIME" -ge "$RUN_STARTED_EPOCH" ] && \
       cp "$JSON_PATH" "$PROJECT_ROOT/../../site/data/tender.json" && \
       docker cp "$JSON_PATH" cockpit:/usr/share/nginx/html/data/tender.json && \
       docker exec cockpit test -s /usr/share/nginx/html/data/tender.json; then
      echo "$(date '+%Y-%m-%d %H:%M:%S') [OK] deep-a 补偿部署完成" >> "$LOG"
    else
      echo "$(date '+%Y-%m-%d %H:%M:%S') [SKIP] deep-a 未生成本轮新 JSON，保留线上数据" >> "$LOG"
    fi
    ;;

  deep-b)
    # 凌晨 B 批：苏服采 + 盐城政采；抓完统一重建 + 部署
    RUN_STARTED_EPOCH=$(date +%s)
    /usr/bin/python3 deep_refresher.py --group B --rebuild >> "$LOG" 2>&1
    RC=$?
    echo "  deep-b 退出码 $RC" >> "$LOG"

    # 仅部署本轮成功生成的 JSON；无补偿任务时不会重复复制旧文件。
    JSON_PATH="$PROJECT_ROOT/../data/tender.json"
    JSON_MTIME=$(stat -c %Y "$JSON_PATH" 2>/dev/null || echo 0)
    if [ "$RC" -eq 0 ] && [ -s "$JSON_PATH" ] && [ "$JSON_MTIME" -ge "$RUN_STARTED_EPOCH" ] && \
       cp "$JSON_PATH" "$PROJECT_ROOT/../../site/data/tender.json" && \
       docker cp "$JSON_PATH" cockpit:/usr/share/nginx/html/data/tender.json && \
       docker exec cockpit test -s /usr/share/nginx/html/data/tender.json; then
      echo "$(date '+%Y-%m-%d %H:%M:%S') [OK] deep-b 部署完成" >> "$LOG"
    else
      echo "$(date '+%Y-%m-%d %H:%M:%S') [SKIP] deep-b 未生成本轮新 JSON，保留线上数据" >> "$LOG"
    fi
    ;;

  noop|*)
    # 其他时段：快速 no-op
    echo "  noop" >> "$LOG"
    ;;
esac

echo "=== $(date '+%Y-%m-%d %H:%M:%S') done ===" >> "$LOG"
exit 0
