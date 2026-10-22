#!/bin/bash
# m3_daily.sh — 每日假缺 M3 回填（2026-09-18 老板批准挂 cron）
#
# 两相流水线（2026-09-20 老板指示：把 OCR 识别也挂进日常回填）：
#   第一相 fake_missing_m3_backfill.py：content 有全文但字段空 → M3 抽金额/日期/发包人
#   第二相 jszbcg_ocr_m3_backfill.py --auto：jszbcg 本地 PDF 扫面补发包人 + 摘要型正文回灌
#          （注入全文后下一相假缺轮自动抽金额/真开标；ocr 页缓存幂等续跑）
#
# 调度（与主抓错开，时间闸保证下一个调度窗之前必收尾）：
#   0 9  * * *  m3_daily.sh 7200    # 8 点主抓（~08:45 收尾）后，≤11:00 结束
#   0 13 * * *  m3_daily.sh 7200    # 12 点主抓后，≤15:00 结束
#   0 19 * * *  m3_daily.sh 14400   # 18 点主抓后，≤23:00 结束（远低于 03:00 deep-a）
#   0 1  * * *  m3_daily.sh 5400    # 凌晨轮（主抓后补扫），≤02:30 结束
#
# 防护三层：
#   1. flock cron.lock —— 与 deploy.sh / site_refresher.sh 同一把锁，主抓/补偿在跑直接跳过
#   2. pgrep 自检 —— 假缺 worker 或 OCR worker（手动轮/上轮未完）在跑时跳过，绝不双开
#   3. --max-runtime 硬时间闸（参数传入，默认 7200s；两相共享同一总闸）
#
# 数据可见性：回填值经 8002 API 即时生效。
set -u
DIR=/root/.openclaw/workspace/agents/yktender/projects/cockpit/tender/collector
LOG="$DIR/logs/m3_daily.log"
RUNTIME="${1:-7200}"

# cron 环境 PATH 极简且 openclaw shebang 需要 node：把 nvm bin 目录动态解析进来（防 node 升版写死失效）
NVM_BIN=$(ls -d /root/.nvm/versions/node/*/bin 2>/dev/null | sort -V | tail -1)
export PATH="${NVM_BIN:-/root/.nvm/versions/node/v24.16.0/bin}:/usr/sbin:/usr/bin:/sbin:/bin"
export HOME=/root

bash /root/.openclaw/workspace/agents/yktender/projects/cockpit/scripts/rotate_log.sh "$LOG" 10 7 2>/dev/null
cd "$DIR" || exit 1

# —— 与 deploy.sh/site_refresher.sh 同锁：主抓/补偿在跑则跳过（拿不到锁立即退出，不排队）——
exec 8>"$DIR/logs/cron.lock"
if ! flock -n 8; then
  echo "$(date '+%F %T') [m3_daily] cron.lock held (deploy/site_refresher running), skip" >> "$LOG"
  exit 0
fi

# —— 已有 worker 在跑（手动补跑等）则跳过，绝不双开抢 infer 串行锁；OCR worker 也防 ——
if pgrep -f "fake_missing_m3_backfill[.]py" >/dev/null 2>&1 || pgrep -f "jszbcg_ocr_m3_backfill[.]py" >/dev/null 2>&1; then
  echo "$(date '+%F %T') [m3_daily] backfill/OCR worker running, skip" >> "$LOG"
  exit 0
fi

START_TS=$(date +%s)
echo "$(date '+%F %T') [m3_daily] start max_runtime=${RUNTIME}s" >> "$LOG"
/usr/bin/python3 fake_missing_m3_backfill.py --max-runtime "$RUNTIME" >> "$LOG" 2>&1
RC=$?

# —— 第二相：jszbcg 扫描件 OCR→M3（共享同一总时间闸，预留 60s 收尾；ocr 页缓存幂等）——
USED=$(( $(date +%s) - START_TS ))
OCR_RT=$(( RUNTIME - USED - 60 ))
if [ "$OCR_RT" -ge 120 ] && ! pgrep -f "fake_missing_m3_backfill[.]py" >/dev/null 2>&1; then
  echo "$(date '+%F %T') [m3_daily] phase2 ocr->m3 budget=${OCR_RT}s" >> "$LOG"
  /usr/bin/python3 jszbcg_ocr_m3_backfill.py --auto --cap=80 --max-runtime "$OCR_RT" >> "$LOG" 2>&1
  RC2=$?
  echo "$(date '+%F %T') [m3_daily] phase2 exit rc=$RC2" >> "$LOG"
fi

echo "$(date '+%F %T') [m3_daily] exit rc=$RC" >> "$LOG"


# 2026-11-02 v3: M3 抽完后再跑项目信息补充（apply_unified）
/usr/bin/python3 _unified_apply.py --site "江苏招标采购服务平台" --mode db_or_fetch --max 9999 >> logs/backfill/unified.log 2>&1 || true