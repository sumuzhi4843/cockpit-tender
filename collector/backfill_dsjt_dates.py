# -*- coding: utf-8 -*-
"""一次性回填：都市集团日期错标修复（2026-08-11）

背景：都市集团列表页只给"日"数字，scraper 用抓取当月补齐月份。
晚抓到的旧公告（如 7 月发布、8 月才入库）会被错标成抓取当月（8 月）。
详情页正文有明文"发布时间：YYYY-MM-DD"，可用于纠正。

本脚本直接从已存 raw_json.content 提取"发布时间"，回填 notices.publish_date。
只纠正能提取到真实日期的记录。

用法：python3 backfill_dsjt_dates.py [--dry-run]
"""
import json
import re
import sqlite3
import sys
from datetime import datetime

DB = "data/tender.db"
PAT = re.compile(r"发布时间[^0-9年]{0,10}(20\d{2})[-年/.]?(\d{1,2})[-月/.]?(\d{1,2})")
SITE = "盐城市都市建设投资集团有限公司"


def main():
    dry = "--dry-run" in sys.argv
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    rows = cur.execute(
        "SELECT id,title,publish_date,raw_json FROM notices WHERE site=?", (SITE,)
    ).fetchall()

    fixed = []
    skipped = 0
    for r in rows:
        try:
            raw = json.loads(r["raw_json"] or "{}")
            content = raw.get("content", "") or ""
        except Exception:
            content = ""
        m = PAT.search(content)
        if not m:
            skipped += 1
            continue
        y, mo, d = m.group(1), m.group(2).zfill(2), m.group(3).zfill(2)
        real = f"{y}-{mo}-{d}"
        if real != r["publish_date"]:
            fixed.append((r["id"], r["publish_date"], real, r["title"]))

    print(f"扫描 {len(rows)} 条，可提取日期 {len(rows)-skipped}，需纠正 {len(fixed)} 条（content 无'发布时间' {skipped} 条）")

    if dry:
        print("\n[dry-run] 仅预览，不写库：")
        for nid, old, new, title in fixed[:20]:
            print(f"  {old} → {new} | {title[:40]}")
        conn.close()
        return

    for nid, old, new, title in fixed:
        cur.execute(
            "UPDATE notices SET publish_date=? WHERE id=?",
            (new, nid),
        )
    conn.commit()
    print(f"\n✅ 已回填纠正 {len(fixed)} 条 publish_date")
    conn.close()


if __name__ == "__main__":
    main()