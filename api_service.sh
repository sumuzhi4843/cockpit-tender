#!/bin/bash
# 招投标区县修订 API 服务管理（2026-08-16）
#
# 用法：
#   bash api_service.sh start     # 启动（后台常驻，写 api.log）
#   bash api_service.sh stop      # 停止（按 8002 端口找到真实进程）
#   bash api_service.sh restart   # 重启
#   bash api_service.sh status    # 查看状态
set -e
cd "$(dirname "$0")"
PIDFILE="api.pid"
LOG="api.log"
PORT=8002

# 找真正监听端口的进程（防 pidfile 过期/错指）
port_pid() {
  ss -tlnp 2>/dev/null | awk -v p=":$PORT" '$4 ~ p {match($0,/pid=([0-9]+)/,m); print m[1]}' | head -1
}

start() {
  local existing
  existing=$(port_pid)
  if [ -n "$existing" ]; then
    echo "$existing" > "$PIDFILE"
    echo "API 已在运行 (PID $existing)"
    return 0
  fi
  nohup python3 api.py >> "$LOG" 2>&1 &
  echo $! > "$PIDFILE"
  sleep 1.5
  existing=$(port_pid)
  if [ -n "$existing" ]; then
    echo "$existing" > "$PIDFILE"
    echo "✅ API 已启动 (PID $existing) → http://127.0.0.1:$PORT"
  else
    echo "❌ API 启动失败，查看 $LOG"
    tail -5 "$LOG"
    return 1
  fi
}

stop() {
  local pid
  pid=$(port_pid)
  if [ -z "$pid" ]; then
    echo "API 未在运行"
    rm -f "$PIDFILE"
    return 0
  fi
  kill "$pid" 2>/dev/null || true
  sleep 1
  if port_pid >/dev/null 2>&1; then
    kill -9 "$pid" 2>/dev/null || true
  fi
  rm -f "$PIDFILE"
  echo "✅ API 已停止 (PID $pid)"
}

status() {
  local pid
  pid=$(port_pid)
  if [ -n "$pid" ]; then
    echo "✅ API 运行中 (PID $pid, 端口 $PORT)"
    curl -s "http://127.0.0.1:$PORT/api/tender/health" || echo "(health 检查失败)"
  else
    echo "❌ API 未运行"
    return 1
  fi
}

case "${1:-}" in
  start)   start ;;
  stop)    stop ;;
  restart) stop; sleep 0.5; start ;;
  status)  status ;;
  *) echo "用法: $0 {start|stop|restart|status}"; exit 1 ;;
esac
