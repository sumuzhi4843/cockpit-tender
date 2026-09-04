#!/usr/bin/env python3
"""backfill_ccgp_history.py — 中国政府采购网历史回填（2026-08-29）

背景：ccgp 单关键词搜索最多返回 120 条（6 页×20，约 6 天滚动窗口），
修复前 scraper 还只抓第 1 页 20 条，导致 6/1 保留线以来的历史数据
大面积缺失（DB 中 7 月仅 4 条）。

本脚本：按周分段 + 11 个关键词桶 + timeType=6 显式时间窗，
把 [start, end] 区间内的盐城公告回填入库。日常主抓仍走
6 天滚动窗口（增量），本脚本只做一次性历史补齐。

用法：
  python3 backfill_ccgp_history.py                 # 回填 2026-06-01 → 今天
  python3 backfill_ccgp_history.py --start 2026-07-01 --end 2026-07-31
  python3 backfill_ccgp_history.py --dry-run       # 只打印计划
"""
import sys
import os
import time
import argparse
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import REQUEST_DELAY, DETAIL_DELAY
from scrapers import get_all_scrapers
from store_db import TenderDB


def week_ranges(start_date, end_date):
    """按 7 天切分 [start, end]，返回 [(start, end), ...] 闭区间列表。"""
    ranges = []
    cur = start_date
    while cur <= end_date:
        seg_end = min(cur + timedelta(days=6), end_date)
        ranges.append((cur, seg_end))
        cur = seg_end + timedelta(days=1)
    return ranges


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2026-06-01")
    ap.add_argument("--end", default=datetime.now().strftime("%Y-%m-%d"))
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--retries", type=int, default=3, help="单段失败重试次数")
    ap.add_argument("--seg-delay", type=int, default=30, help="段间等待秒数（频控冷却）")
    args = ap.parse_args()

    start = datetime.strptime(args.start, "%Y-%m-%d").date()
    end = datetime.strptime(args.end, "%Y-%m-%d").date()
    ranges = week_ranges(start, end)
    print(f"[backfill_ccgp] 回填 {start} → {end}，共 {len(ranges)} 个周段", flush=True)
    if args.dry_run:
        for r in ranges:
            print(f"  {r[0]} → {r[1]}")
        return 0

    sc = get_all_scrapers()["中国政府采购网"]
    sc.set_slow_mode(True)  # 回填走慢速模式，降低 ccgp 频控触发率（2026-08-29）
    db = TenderDB()
    total_new = total_upd = total_raw = 0
    try:
        for i, (seg_start, seg_end) in enumerate(ranges):
            sc.set_time_window(str(seg_start), str(seg_end))
            print(f"[backfill_ccgp] 段 {i+1}/{len(ranges)}: {seg_start} → {seg_end}", flush=True)
            items = []
            for attempt in range(1, args.retries + 1):
                try:
                    items = sc.scrape()
                    break
                except Exception as e:
                    print(f"[backfill_ccgp] 段 {seg_start}~{seg_end} 第 {attempt} 次抓取异常: {e}", flush=True)
                    if attempt < args.retries:
                        wait = args.seg_delay * attempt
                        print(f"[backfill_ccgp] 等待 {wait}s 后重试…", flush=True)
                        time.sleep(wait)
            if not items:
                print(f"[backfill_ccgp] 段 {seg_start}~{seg_end} 重试 {args.retries} 次后仍失败，跳过", flush=True)
                continue
            total_raw += len(items)
            new_n = upd_n = 0
            for it in items:
                kw = it.get("ict_keywords", [])
                try:
                    if db.upsert("中国政府采购网", it, kw):
                        new_n += 1
                    else:
                        upd_n += 1
                except Exception as e:
                    print(f"    upsert 失败 {it.get('url', '?')}: {e}")
            total_new += new_n
            total_upd += upd_n
            print(f"[backfill_ccgp] 段 {seg_start}~{seg_end}: 抓 {len(items)} 条（新 {new_n} / 更 {upd_n}）", flush=True)
            # 段间间隔，降低频控风险（2026-08-29：6 月连跑 4 段后触发 ccgp 频控，段间冷却改为 30-60s）
            if i < len(ranges) - 1:
                time.sleep(args.seg_delay)
    finally:
        db.close()
        # 清理时间窗，避免影响后续主抓
        if hasattr(sc, "_time_window"):
            del sc._time_window
    print(f"[backfill_ccgp] 完成：原始 {total_raw} 条，新增 {total_new}，更新 {total_upd}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
