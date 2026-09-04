#!/usr/bin/env python3
"""补齐 raw_row.areaCode（2026-08-21）

背景：backfill_sufucai_detail.py 主循环只更新"主 notice"（同 project_key 最新一条），
其他 notice 的 raw_row 通过 sync SQL 同步——但 sync SQL 的 WHERE 子句要求
detail_address 为空，所有 notice 都被填过 detail_address → sync 永远跳过，
结果 222 个 notice（217 个 project_key）的 raw_row 缺失。

本脚本用纯 SQL UPDATE（不依赖 fetch_detail）一次性补齐。

用法:
  python3 backfill_sufucai_area_code.py --dry-run   # 看会更新多少条
  python3 backfill_sufucai_area_code.py            # 执行
"""
import argparse
import sqlite3
from collections import Counter
from pathlib import Path

HERE = Path(__file__).parent
DB_PATH = HERE / "data" / "tender.db"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="只统计")
    args = ap.parse_args()

    conn = sqlite3.connect(str(DB_PATH))

    # 找出所有 raw_row.areaCode 缺失但同 project_key 其他 notice 有 raw_row 的项目
    # UPDATE 用 json_set 把 raw_row 从同 project_key 的兄弟 notice 复制过来
    to_update = conn.execute("""
        SELECT COUNT(*) FROM notices n
        WHERE site='苏服采'
          AND (json_extract(n.raw_json, '$.raw_row.areaCode') IS NULL
               OR json_extract(n.raw_json, '$.raw_row.areaCode') = '')
          AND EXISTS (
            SELECT 1 FROM notices r2 WHERE r2.site='苏服采'
              AND json_extract(r2.raw_json, '$.project_key') = json_extract(n.raw_json, '$.project_key')
              AND json_extract(r2.raw_json, '$.raw_row.areaCode') IS NOT NULL
              AND json_extract(r2.raw_json, '$.raw_row.areaCode') != ''
          )
    """).fetchone()[0]
    print(f"待补 raw_row.areaCode: {to_update} 条")

    if to_update == 0:
        print("无缺失。")
        return

    if args.dry_run:
        print("[dry-run] 结束")
        return

    cur = conn.cursor()
    # 2026-08-21：用 json_set(... , $.raw_row, json(...)) 而非 json.dumps 字符串——
    # 字符串会让 sqlite 嵌套解析失败（json_set 第二个参数是 JSON 字面量）。
    cur.execute("""
        UPDATE notices SET raw_json = json_set(
            raw_json, '$.raw_row',
            json((
              SELECT json_extract(r2.raw_json, '$.raw_row')
              FROM notices r2 WHERE r2.site='苏服采'
                AND json_extract(r2.raw_json, '$.project_key') = json_extract(notices.raw_json, '$.project_key')
                AND json_extract(r2.raw_json, '$.raw_row.areaCode') IS NOT NULL
                AND json_extract(r2.raw_json, '$.raw_row.areaCode') != ''
              LIMIT 1))
        )
        WHERE site='苏服采'
          AND (json_extract(raw_json, '$.raw_row.areaCode') IS NULL
               OR json_extract(raw_json, '$.raw_row.areaCode') = '')
          AND EXISTS (
            SELECT 1 FROM notices r2 WHERE r2.site='苏服采'
              AND json_extract(r2.raw_json, '$.project_key') = json_extract(notices.raw_json, '$.project_key')
              AND json_extract(r2.raw_json, '$.raw_row.areaCode') IS NOT NULL
              AND json_extract(r2.raw_json, '$.raw_row.areaCode') != ''
          )
    """)
    print(f"已更新 {cur.rowcount} 条")
    conn.commit()

    # 验证
    valid = conn.execute("""
        SELECT COUNT(*) FROM notices WHERE site='苏服采'
          AND json_extract(raw_json, '$.raw_row.areaCode') IS NOT NULL
          AND json_extract(raw_json, '$.raw_row.areaCode') != ''
    """).fetchone()[0]
    missing = conn.execute("""
        SELECT COUNT(*) FROM notices WHERE site='苏服采'
          AND (json_extract(raw_json, '$.raw_row.areaCode') IS NULL
               OR json_extract(raw_json, '$.raw_row.areaCode') = '')
    """).fetchone()[0]
    print(f"现在有 raw_row.areaCode 的: {valid} 条")
    print(f"仍缺失: {missing} 条（需要 fetch_detail）")


if __name__ == "__main__":
    main()