# -*- coding: utf-8 -*-
"""数据缺失回填（2026-08-07）

针对排查出的两处可修复缺失，从已存 content 离线回填（不重新抓取）：
  ① 苏服采 成交/结果公告：成交金额 → winning_amount + amount
  ② 中国政府采购网：publish_date 为空 → 从 content「公告时间」回填

用法：
    python3 backfill_missing_fields.py           # 回填两处
    python3 backfill_missing_fields.py --dry     # 只统计不写入
"""
import argparse
import json
import re
import sqlite3
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "tender.db")


def backfill_sufucai(conn, dry=False):
    """苏服采成交/结果公告：content 里的成交金额回填 winning_amount + amount。"""
    rows = conn.execute(
        "SELECT id, title, notice_type, raw_json FROM notices "
        "WHERE site='苏服采' AND notice_type='award' "
        "AND (winning_amount IS NULL OR winning_amount='')"
    ).fetchall()
    updated = 0
    for r in rows:
        try:
            raw = json.loads(r["raw_json"] or "{}")
        except Exception:
            continue
        content = raw.get("content") or ""
        m = re.search(r"成交金额[：:]\s*([\d.]+)\s*元?", content)
        if not m:
            continue
        try:
            val = float(m.group(1))
        except ValueError:
            continue
        amount_str = f"{val/10000:g}万元"
        if not dry:
            conn.execute(
                "UPDATE notices SET winning_amount=?, "
                "amount=CASE WHEN amount IS NULL OR amount='' THEN ? ELSE amount END "
                "WHERE id=?",
                (amount_str, amount_str, r["id"]))
        updated += 1
    if not dry:
        conn.commit()
    return updated, len(rows)


def backfill_ccgp_date(conn, dry=False):
    """中国政府采购网：publish_date 为空 → 从 content 的公告时间回填。"""
    rows = conn.execute(
        "SELECT id, title, raw_json FROM notices "
        "WHERE site='中国政府采购网' AND (publish_date IS NULL OR publish_date='')"
    ).fetchall()
    updated = 0
    for r in rows:
        try:
            raw = json.loads(r["raw_json"] or "{}")
        except Exception:
            continue
        content = raw.get("content") or ""
        # 优先「公告时间」，其次正文首个日期
        m = re.search(r"公告时间\s*(\d{4})[年.\-](\d{1,2})[月.\-](\d{1,2})", content)
        if not m:
            m = re.search(r"(\d{4})[年.\-](\d{1,2})[月.\-](\d{1,2})", content)
        if not m:
            continue
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if not (2000 <= y <= 2030 and 1 <= mo <= 12 and 1 <= d <= 31):
            continue
        date_str = f"{y}-{mo:02d}-{d:02d}"
        if not dry:
            conn.execute("UPDATE notices SET publish_date=? WHERE id=?", (date_str, r["id"]))
        updated += 1
    if not dry:
        conn.commit()
    return updated, len(rows)


def backfill_ccgp_winner(conn, dry=False):
    """江苏政府采购网：中标/成交公告 winner 为空 → 从 content 重新富化提取（2026-08-07）。"""
    from enrich_engine import enrich_detail, infer_notice_type
    rows = conn.execute(
        "SELECT id, title, raw_json FROM notices "
        "WHERE site='江苏政府采购网' AND notice_type='award' "
        "AND (winner IS NULL OR winner='')"
    ).fetchall()
    updated = 0
    for r in rows:
        try:
            raw = json.loads(r["raw_json"] or "{}")
        except Exception:
            continue
        content = raw.get("content") or ""
        if not content:
            continue
        nt = infer_notice_type(r["title"] or "")
        try:
            e = enrich_detail(content, nt)
        except Exception:
            continue
        winner = e.get("winner") or ""
        wa = e.get("winning_amount") or ""
        if not winner:
            continue
        if not dry:
            conn.execute(
                "UPDATE notices SET winner=?, "
                "winning_amount=CASE WHEN winning_amount IS NULL OR winning_amount='' THEN ? ELSE winning_amount END "
                "WHERE id=?",
                (winner, wa, r["id"]))
        updated += 1
    if not dry:
        conn.commit()
    return updated, len(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true", help="只统计不写入")
    args = ap.parse_args()

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    n1, t1 = backfill_sufucai(conn, args.dry)
    n2, t2 = backfill_ccgp_date(conn, args.dry)
    n3, t3 = backfill_ccgp_winner(conn, args.dry)

    mode = "（dry-run）" if args.dry else ""
    print(f"① 苏服采 winning_amount 回填{mode}: {n1}/{t1} 条")
    print(f"② 中国政采 publish_date 回填{mode}: {n2}/{t2} 条")
    print(f"③ 江苏政采 中标单位回填{mode}: {n3}/{t3} 条")
    conn.close()


if __name__ == "__main__":
    main()
