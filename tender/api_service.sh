#!/bin/bash
# 招投标区县修订 API 服务管理（兼容入口）
# 正式托管者是 systemd：tender-district-api.service。
#
# 用法：
#   bash api_service.sh start|stop|restart|status
set -euo pipefail
SERVICE=tender-district-api.service
case "${1:-status}" in
  start|stop|restart|status)
    exec systemctl "$1" "$SERVICE"
    ;;
  *) echo "用法: $0 {start|stop|restart|status}"; exit 1 ;;
esac
