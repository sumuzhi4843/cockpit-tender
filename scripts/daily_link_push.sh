#!/bin/bash
# daily_link_push.sh — 每天定时按 PROTOCOL-v0.4 把当日日报链接推给 yk agent
# 2026-09-26 升级（v0.3 → v0.4）：
#   - 协议头：[HUB-PUSH] → [WPUSH-SEND]（通用推送通道，老板 13:58 拍板）
#   - 字段映射：[AGENT]→[SENDER]、[TYPE] tender-daily→broadcast、[DATE]/[STATUS]/[WARN] 合并到 [CONTENT]/[KEY]
#   - URL 用 /daily/{YYYY-MM-DD}.html（按日期归档，永久有效）
# 老板 14:03 拍板：
#   - A 项：3 条/天推送 → [KEY] 含 slot 后缀（morning/noon/evening），让 yk 透传给 send.sh --key，
#     WPush 端按不同 idempotency-key 实际推 3 条（不依赖 yk dedup）
#   - C 项：接 run_state.json deployment 信号 → 读 status/phase 决定 [STATUS] 字段
# 触发：crontab 0 9 / 0 13 / 0 19 三槽位
#   - 8:00 deploy  → ~8:30 gen → 9:00 push（+30 min，slot=morning）
#   - 12:00 deploy → ~12:30 gen → 13:00 push（+30 min，slot=noon）
#   - 18:00 deploy → ~18:30 gen → 19:00 push（+30 min，slot=evening）
# mtime 新鲜度窗口 90 min + run_state deployment 双闸：任何一闸失败就跳过本轮、让下一槽接管。

set -u

# cron PATH 太瘦找不到 openclaw
export PATH="/root/.openclaw/tmp/agent-cli:/root/.nvm/versions/node/v24.16.0/bin:$PATH"

PROJECT_ROOT=/root/.openclaw/workspace/agents/yktender/projects/cockpit
DAILY_DIR="$PROJECT_ROOT/site/daily"
LOG_DIR="$PROJECT_ROOT/tender/collector/logs"
LOG="$LOG_DIR/daily_link_push.log"
LOCK="$LOG_DIR/daily_link_push.lock"
RUN_STATE_JSON="$LOG_DIR/run_state.json"
PUBLIC_BASE='http://<SERVER_IP>:5310'
YK_AGENT='yk'
YK_SESSION='agent:yk:main'
TIMEOUT=90

mkdir -p "$LOG_DIR"
bash "$PROJECT_ROOT/scripts/rotate_log.sh" "$LOG" 5 5 2>/dev/null

# flock 防并发
exec 9>"$LOCK"
if ! flock -n 9; then
  echo "$(date '+%F %T') [SKIP] push 已在运行" >> "$LOG"
  exit 0
fi

echo "$(date '+%F %T') === push start ===" >> "$LOG"

DATE=$(date '+%Y-%m-%d')
ARCHIVED_HTML="$DAILY_DIR/${DATE}.html"
LATEST_HTML="$DAILY_DIR/latest.html"

# Slot 判定（cron 精确触发，按 HHMM 匹配）
CURRENT_HHMM=$(date '+%H%M')
case "$CURRENT_HHMM" in
  0900) SLOT="morning" ; SLOT_CN="早" ; SLOT_KEY="0900" ;;
  1300) SLOT="noon"    ; SLOT_CN="午" ; SLOT_KEY="1300" ;;
  1900) SLOT="evening" ; SLOT_CN="晚" ; SLOT_KEY="1900" ;;
  *)    SLOT="manual"  ; SLOT_CN=""   ; SLOT_KEY="$CURRENT_HHMM" ;;
esac

# ============ 闸 1：归档文件 mtime 新鲜度（90 min 窗口） ============
FRESH_WINDOW_S=5400
STATUS="ok"
WARN_LINE=""
PUSH_URL=""
PUSH_FILE=""

if [ -f "$ARCHIVED_HTML" ]; then
  ARCHIVED_MTIME=$(stat -c %Y "$ARCHIVED_HTML" 2>/dev/null || echo 0)
  NOW_TS=$(date +%s)
  ARCHIVED_AGE=$(( NOW_TS - ARCHIVED_MTIME ))
  if [ $ARCHIVED_AGE -le $FRESH_WINDOW_S ]; then
    PUSH_URL="${PUBLIC_BASE}/daily/${DATE}.html"
    PUSH_FILE="$ARCHIVED_HTML"
  else
    echo "$(date '+%F %T') [SKIP-闸1] 归档文件 mtime 太旧（AGE=${ARCHIVED_AGE}s > ${FRESH_WINDOW_S}s），本轮 deploy 未在 cron 前完成，让下一轮接管" >> "$LOG"
    echo "$(date '+%F %T') === push skipped (stale archived html) ===" >> "$LOG"
    exit 0
  fi
elif [ -f "$LATEST_HTML" ]; then
  ACTUAL_DATE=$(date -r "$LATEST_HTML" '+%Y-%m-%d')
  echo "$(date '+%F %T') [SKIP-闸1] 今日 daily_report.py 未生成归档（仅 latest.html 来自 ${ACTUAL_DATE}），跳过推送" >> "$LOG"
  echo "$(date '+%F %T') === push skipped (no archived html) ===" >> "$LOG"
  exit 0
else
  echo "$(date '+%F %T') [FAIL-闸1] latest.html 与 ${DATE}.html 都不存在，跳过推送" >> "$LOG"
  echo "$(date '+%F %T') === push skipped (no html at all) ===" >> "$LOG"
  exit 1
fi

# ============ 闸 2：run_state.json deployment 信号 ============
# 期望值（deploy.sh 末尾回写）：
#   status="success" + phase="daily-done"  → 当轮 deploy + 日报全部成功
#   status="failed"  + phase="daily-failed" → daily_report.py 失败
#   status="running" + phase="daily"       → 仍在跑，本轮跳过让下一槽接管
DEPLOY_STATUS_VAL="unknown"
DEPLOY_PHASE_VAL="unknown"
if [ -f "$RUN_STATE_JSON" ]; then
  read -r DEPLOY_STATUS_VAL DEPLOY_PHASE_VAL < <(python3 -c "
import json, sys
try:
    with open('$RUN_STATE_JSON', encoding='utf-8') as f:
        d = json.load(f).get('deployment', {})
    print(d.get('status', 'unknown'), d.get('phase', 'unknown'))
except Exception as e:
    print('unknown', 'unknown')
" 2>/dev/null)
fi

if [ "$DEPLOY_STATUS_VAL" = "success" ] && [ "$DEPLOY_PHASE_VAL" = "daily-done" ]; then
  STATUS="ok"
elif [ "$DEPLOY_STATUS_VAL" = "failed" ] || [ "$DEPLOY_PHASE_VAL" = "daily-failed" ]; then
  STATUS="generated-with-warning"
  WARN_LINE="本轮 deploy 失败（run_state deployment: ${DEPLOY_STATUS_VAL}/${DEPLOY_PHASE_VAL}），日报可能是基于未刷新数据生成"
elif [ "$DEPLOY_STATUS_VAL" = "running" ]; then
  echo "$(date '+%F %T') [SKIP-闸2] run_state deployment 显示 status=${DEPLOY_STATUS_VAL}/phase=${DEPLOY_PHASE_VAL}，deploy 未完成，本轮跳过让下一槽接管" >> "$LOG"
  echo "$(date '+%F %T') === push skipped (deploy still running) ===" >> "$LOG"
  exit 0
else
  # 文件不存在或解析失败：兜底按 ok（不阻挡推送），仅 WARN 日志
  echo "$(date '+%F %T') [WARN-闸2] run_state deployment 不可读（status=${DEPLOY_STATUS_VAL}/phase=${DEPLOY_PHASE_VAL}），按 ok 兜底推送" >> "$LOG"
  STATUS="ok"
fi

HTML_SIZE=$(wc -c <"$PUSH_FILE")
# 损坏防护（老板 14:35 检查拍板）：HTML 小于 5KB 视为异常产物（模板没渲染/写盘中断），不推给 yk
if [ "$HTML_SIZE" -lt 5000 ]; then
  echo "$(date '+%F %T') [FAIL-闸1] 推送文件疑似损坏（${HTML_SIZE}B < 5000B），不推送" >> "$LOG"
  echo "$(date '+%F %T') === push skipped (html too small) ===" >> "$LOG"
  exit 1
fi
echo "$(date '+%F %T') [HTML] $PUSH_FILE size=${HTML_SIZE}B url=$PUSH_URL status=$STATUS slot=$SLOT deploy=$DEPLOY_STATUS_VAL/$DEPLOY_PHASE_VAL" >> "$LOG"

# ============ 组装 v0.4 [WPUSH-SEND] 协议消息 ============
# 字段映射（v0.3 → v0.4）：
#   [AGENT] yktender        → [SENDER] yktender
#   [TYPE] tender-daily     → [TYPE] broadcast
#   [DATE]                  → 合并到 [CONTENT] 末行
#   [URL]                   → [URL] 同名
#   [STATUS]/[WARN]         → 合并到 [CONTENT] 末尾（warn 时附说明）
# 新增：[TITLE] / [TOPIC] / [KEY]
# [KEY] 含 slot 后缀（老板 14:03 拍板 A 项）—— 让 yk 透传给 send.sh --key，WPush 端按不同 idempotency-key 实际推 3 条
DATE_MD=$(date '+%m-%d')
if [ -n "$SLOT_CN" ]; then
  TITLE="📊 盐开招标日报 · ${DATE_MD}（${SLOT_CN}）"
else
  TITLE="📊 盐开招标日报 · ${DATE_MD}"
fi
KEY="tender-daily-${DATE}-${SLOT_KEY}"

if [ "$STATUS" = "generated-with-warning" ] && [ -n "$WARN_LINE" ]; then
  CONTENT="今日招投标数据已出，点链接查看完整日报（页内含 PDF 二维码）：

${PUSH_URL}

⚠️ ${WARN_LINE}"
else
  CONTENT="今日招投标数据已出，点链接查看完整日报（页内含 PDF 二维码）：

${PUSH_URL}"
fi

MSG="[WPUSH-SEND]
[SENDER] yktender
[TYPE] broadcast
[TITLE] ${TITLE}
[CONTENT] ${CONTENT}
[TOPIC] yk-tender-daily
[URL] ${PUSH_URL}
[KEY] ${KEY}"

echo "$(date '+%F %T') [PUSH] → ${YK_AGENT} (${YK_SESSION}) : [WPUSH-SEND] title=${TITLE} status=${STATUS} key=${KEY}" >> "$LOG"
openclaw agent --agent "$YK_AGENT" --session-key "$YK_SESSION" --message "$MSG" --timeout "$TIMEOUT" >> "$LOG" 2>&1
RC=$?
echo "$(date '+%F %T') === push done rc=${RC} ===" >> "$LOG"
exit $RC
