# -*- coding: utf-8 -*-
"""国投集团回填重试器（2026-08-07）

站点限流（406）期间的自动重试：每 30 分钟尝试一次，最多 max_hours 小时，
全部修复后自动退出。每轮：翻列表页收集 ggGuid→gcGuid → 修 URL → 抓详情补字段。

用法：
    python3 retry_sdicc.py                 # 前台跑（调试）
    nohup python3 retry_sdicc.py >> logs/retry_sdicc.log 2>&1 &   # 后台
"""
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from store_db import TenderDB  # noqa: E402

INTERVAL = 1800      # 30 分钟
MAX_HOURS = 6
SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "backfill_sdicc.py")


def remaining():
    db = TenderDB()
    n = db._conn.execute(
        "SELECT COUNT(*) c FROM notices WHERE site='国投集团' "
        "AND raw_json NOT LIKE '%url_fixed_20260807%'").fetchone()["c"]
    db.close()
    return n


def main():
    start = time.time()
    attempt = 0
    while (time.time() - start) < MAX_HOURS * 3600:
        attempt += 1
        n = remaining()
        if n == 0:
            print(f"[{time.strftime('%H:%M:%S')}] 全部修复完成，退出")
            return
        print(f"[{time.strftime('%H:%M:%S')}] 第 {attempt} 轮，剩余 {n} 条未修复")
        try:
            r = subprocess.run([sys.executable, SCRIPT], capture_output=True,
                               text=True, timeout=600)
            tail = (r.stdout or "").strip().split("\n")[-1]
            print(f"  结果: {tail}")
        except Exception as e:
            print(f"  执行异常: {e}")
        if remaining() == 0:
            print(f"[{time.strftime('%H:%M:%S')}] 全部修复完成，退出")
            return
        time.sleep(INTERVAL)
    print(f"[{time.strftime('%H:%M:%S')}] 达到 {MAX_HOURS} 小时上限，剩余 {remaining()} 条待人工处理")


if __name__ == "__main__":
    main()
