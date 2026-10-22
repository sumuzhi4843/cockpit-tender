#!/bin/bash
# rotate_log.sh — 日志轮转工具（2026-09-07 H2）
# 用法: bash rotate_log.sh <logfile> [max_mb=20] [keep=7]
# 逻辑: 超过 max_mb → 改名 .1 并 gzip；只保留最近 keep 个压缩档。
# 设计为幂等、可从任意脚本 source 或独立调用。

rotate_one_log() {
  local f="$1" max_mb="${2:-20}" keep="${3:-7}"
  [ -f "$f" ] || return 0
  local max_bytes=$((max_mb * 1024 * 1024))
  local size
  size=$(stat -c%s "$f" 2>/dev/null || echo 0)
  [ "$size" -le "$max_bytes" ] && return 0
  # 归档：时间戳后缀 + gzip
  local ts arch
  ts=$(date '+%Y%m%d_%H%M%S')
  arch="${f}.${ts}.gz"
  if gzip -c "$f" > "$arch" 2>/dev/null; then
    : > "$f"   # 原文件清空（保留 inode，避免正在写入的句柄失效）
    chmod 644 "$arch" 2>/dev/null
  else
    rm -f "$arch"
    return 0
  fi
  # 只保留最近 keep 个压缩档
  ls -1t "${f}".*.gz 2>/dev/null | tail -n +$((keep + 1)) | xargs -r rm -f
  return 0
}

# 命令行模式：rotate_log.sh <logfile> [max_mb] [keep]
if [ -n "$1" ]; then
  rotate_one_log "$1" "${2:-20}" "${3:-7}"
fi
