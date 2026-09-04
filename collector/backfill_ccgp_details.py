# -*- coding: utf-8 -*-
"""江苏政府采购网详情正文定向补抓（2026-08-31）

背景：run.py 对本站只抓列表（fetch_details=False，注释称正文由 deploy.sh
富化链路处理），但链路里一直没有本站详情步骤 → 8/24 起公告正文无人补，
8 月累计 71 条缺口（含 ICT 3 条）。

做法：与主抓详情阶段完全同一取文路径（CcgpJiangsuScraper._fetch_detail，
正文接口 API 优先、HTML 兜底），只处理缺正文的公告：
  - raw_json.content 写回正文（无正文不写）
  - 字段仅回填空值（不覆盖既有数据，与 jszbcg backfill 同策略）
  - amount 回填时按 upsert 同口径解析 amount_value/amount_unit
  - publish_date / 分类字段不动（保持 id 与时间窗稳定）

用法：
  python3 backfill_ccgp_details.py               # 默认近 7 天（deploy.sh 链路步骤）
  python3 backfill_ccgp_details.py --days 30     # 手动补 8 月缺口
  python3 backfill_ccgp_details.py --dry-run     # 只统计不抓
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import time
from pathlib import Path

from ccgp_scraper import CcgpJiangsuScraper
from config import DETAIL_DELAY

ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "data" / "tender.db"
LAST_RUN = ROOT / "logs" / "ccgp_backfill_last_run.json"

# 顺带回填的列：仅列值为空才写（保守，不覆盖）
FIELD_COLUMNS = ("tenderer", "amount", "bid_deadline", "deadline",
                 "winner", "winning_amount", "agency")


def _parse_amount(amount_str: str):
    """与 store_db.upsert 同口径的金额数值解析。"""
    if not amount_str:
        return None, ""
    try:
        if "亿" in amount_str:
            return float(amount_str.replace("亿元", "")) * 1e8, "元"
        if "万" in amount_str:
            return float(amount_str.replace("万元", "").replace("万", "")) * 1e4, "元"
        return float(amount_str.replace("元", "")), "元"
    except (ValueError, TypeError):
        return None, ""


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=7,
                        help="只处理最近 N 天公告（0 = 不限，慎用）")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    where = ("site='江苏政府采购网' AND url LIKE '%ggid=%' "
             "AND (json_extract(raw_json,'$.content') IS NULL "
             "     OR json_extract(raw_json,'$.content')='')")
    params: list = []
    if args.days > 0:
        where += " AND publish_date >= date('now','localtime',?)"
        params.append(f"-{args.days} days")
    rows = conn.execute(
        f"""SELECT id, url, title, tenderer, amount, bid_deadline, deadline,
                   winner, winning_amount, agency, raw_json
            FROM notices WHERE {where} ORDER BY publish_date DESC, id""",
        params,
    ).fetchall()
    if args.limit:
        rows = rows[: args.limit]
    print(f"[ccgp-backfill] 待补 {len(rows)} 条（近 {args.days} 天）")
    if args.dry_run:
        conn.close()
        return

    scraper = CcgpJiangsuScraper(use_cdp=False)
    results = []
    try:
        for idx, row in enumerate(rows, 1):
            status, err = "no_content", ""
            try:
                text = scraper._fetch_detail(row["url"])
                if text:
                    got = scraper._extract_detail_fields(text, row["title"])
                    raw = json.loads(row["raw_json"] or "{}")
                    raw["content"] = text
                    sets, vals = ["raw_json=?"], [json.dumps(raw, ensure_ascii=False)]
                    filled = []
                    for col in FIELD_COLUMNS:
                        if not (row[col] or "").strip() and got.get(col):
                            sets.append(f"{col}=?")
                            vals.append(str(got[col]))
                            filled.append(col)
                    if "amount" in filled:
                        amount_value, amount_unit = _parse_amount(str(got["amount"]))
                        sets += ["amount_value=?", "amount_unit=?"]
                        vals += [amount_value, amount_unit]
                    vals.append(row["id"])
                    conn.execute(f"UPDATE notices SET {','.join(sets)} WHERE id=?", vals)
                    conn.commit()
                    status = "filled"
                    print(f"[{idx}/{len(rows)}] filled chars={len(text)} "
                          f"fields={','.join(filled) or '-'} {row['title'][:40]}")
                else:
                    print(f"[{idx}/{len(rows)}] no_content {row['title'][:40]}")
            except Exception as exc:  # 单条失败不阻塞
                status, err = "error", str(exc)
                print(f"[{idx}/{len(rows)}] ERROR {row['title'][:40]}: {exc}")
            results.append({"id": row["id"], "title": row["title"],
                            "url": row["url"], "status": status, "error": err})
            time.sleep(DETAIL_DELAY)
    finally:
        scraper.close()
        conn.close()

    summary = {
        "ran_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "days": args.days,
        "total": len(results),
        "filled": sum(r["status"] == "filled" for r in results),
        "no_content": sum(r["status"] == "no_content" for r in results),
        "errors": sum(r["status"] == "error" for r in results),
    }
    LAST_RUN.write_text(
        json.dumps({"summary": summary, "items": results}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"last_run: {LAST_RUN}")


if __name__ == "__main__":
    main()
