# -*- coding: utf-8 -*-
"""audit_winning_amount.py — 全面体检 winning_amount 字段 (2026-10-03)

检查项：
1. winning_amount 是科学计数法 (X.Ye+Z) — ycggzy 历史 bug
2. winning_amount 是 NaN / 0 / 极大值 — 其他异常
3. winning_amount 与 amount_value 不一致 — 逻辑错乱
5. winning_amount 与 raw_json.content 中数据冲突
7. winning_amount 是空字符串但 winner 有 — 半填充

输出：JSON 报告，按问题类型分组。
"""
import sys, json, sqlite3, re
from pathlib import Path
from datetime import datetime

HERE = Path(__file__).parent
DB_PATH = HERE / "data" / "tender.db"

issues = {
    "scientific_notation": [],      # X.Ye+Z
    "nan_or_zero": [],              # 0, NaN
    "excessive_value": [],          # > 1e10 元（10 亿元）
    "amount_value_mismatch": [],    # winning_amount vs amount_value 不一致
    "raw_json_conflict": [],        # winning_amount vs raw_json.winning_amount 冲突
    "winner_but_no_amount": [],     # winner 有，amount 空
    "empty_with_amount_field": [],   # winning_amount 空，但 amount 字段有
}


def audit():
    db = sqlite3.connect(str(DB_PATH))
    db.row_factory = sqlite3.Row

    print(f"=== 全面 audit: {datetime.now().isoformat()} ===\n")

    # 1. 科学计数法
    rows = db.execute("""SELECT id, site, title, winner, winning_amount
        FROM notices WHERE winning_amount GLOB '*e+*' OR winning_amount GLOB '*e-*'""").fetchall()
    for r in rows:
        issues["scientific_notation"].append({
            "id": r["id"], "site": r["site"], "title": r["title"][:60],
            "winner": r["winner"][:30] if r["winner"] else "",
            "winning_amount": r["winning_amount"],
        })

    # 2. NaN / 0 / 过大
    rows = db.execute("""SELECT id, site, title, winning_amount FROM notices
        WHERE notice_type IN ('award', 'award_candidate')
        AND winning_amount IS NOT NULL AND TRIM(winning_amount) != ''
        AND (winning_amount = '0' OR CAST(winning_amount AS REAL) > 10000000000
             OR winning_amount GLOB '*NaN*' OR winning_amount GLOB '*nan*')""").fetchall()
    for r in rows:
        issues["nan_or_zero"].append({
            "id": r["id"], "site": r["site"], "title": r["title"][:60],
            "winning_amount": r["winning_amount"],
        })

    # 3. winning_amount 与 amount_value 不一致
    rows = db.execute("""SELECT id, site, title, winner, winning_amount, amount_value
        FROM notices WHERE notice_type IN ('award', 'award_candidate')
        AND winning_amount IS NOT NULL AND TRIM(winning_amount) != ''
        AND winning_amount NOT GLOB '*e+*' AND winning_amount NOT GLOB '*e-*'
        AND amount_value IS NOT NULL AND amount_value > 0
        AND ABS(CAST(winning_amount AS REAL) - amount_value) / amount_value > 0.5""").fetchall()
    for r in rows:
        issues["amount_value_mismatch"].append({
            "id": r["id"], "site": r["site"], "title": r["title"][:60],
            "winning_amount": r["winning_amount"], "amount_value": r["amount_value"],
        })

    # 4. raw_json.conflict
    rows = db.execute("""SELECT id, site, winning_amount, raw_json FROM notices
        WHERE notice_type IN ('award', 'award_candidate')
        AND winning_amount IS NOT NULL AND TRIM(winning_amount) != ''
        AND winning_amount NOT GLOB '*e+*' AND winning_amount NOT GLOB '*e-*'
        AND raw_json != ''""").fetchall()
    for r in rows:
        try:
            raw = json.loads(r['raw_json'])
        except Exception:
            continue
        raw_wa = raw.get("winning_amount", "")
        if raw_wa and raw_wa != r["winning_amount"]:
            try:
                # 数值比较 (允许 ±5% 误差，单位换算差异)
                db_wa = float(r["winning_amount"])
                raw_float = float(raw_wa)
                if abs(db_wa - raw_float) / max(db_wa, raw_float) > 0.05:
                    issues["raw_json_conflict"].append({
                        "id": r["id"], "site": r["site"],
                        "db_winning_amount": r["winning_amount"],
                        "raw_winning_amount": raw_wa,
                    })
            except (ValueError, ZeroDivisionError):
                pass

    # 5. winner 有但 amount 空
    rows = db.execute("""SELECT id, site, title, winner, winning_amount
        FROM notices WHERE notice_type IN ('award', 'award_candidate')
        AND winner IS NOT NULL AND TRIM(winner) != ''
        AND (winning_amount IS NULL OR TRIM(winning_amount) = '')""").fetchall()
    for r in rows:
        issues["winner_but_no_amount"].append({
            "id": r["id"], "site": r["site"], "title": r["title"][:60],
            "winner": r["winner"][:30] if r["winner"] else "",
        })

    # 6. winning_amount 空但 amount 字段有
    rows = db.execute("""SELECT id, site, title, winning_amount, amount
        FROM notices WHERE notice_type IN ('award', 'award_candidate')
        AND (winning_amount IS NULL OR TRIM(winning_amount) = '')
        AND amount IS NOT NULL AND TRIM(amount) != ''
        AND amount LIKE '%万元%' OR amount LIKE '%元%'""").fetchall()
    for r in rows:
        issues["empty_with_amount_field"].append({
            "id": r["id"], "site": r["site"], "title": r["title"][:60],
            "amount": r["amount"][:30],
        })

    # 输出
    total = 0
    for cat, items in issues.items():
        total += len(items)
        if items:
            print(f"  [{cat}] {len(items)} 条")
            for it in items[:3]:
                print(f"    {it}")
            if len(items) > 3:
                print(f"    ... +{len(items)-3} more")
    print(f"\n  总计: {total} 条问题")

    # 写 JSON 报告
    report_path = HERE / "logs" / f"audit_winning_amount_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    report_path.parent.mkdir(exist_ok=True)
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump({"timestamp": datetime.now().isoformat(), "issues": issues, "total": total},
                  f, ensure_ascii=False, indent=2)
    print(f"\n  报告已写入: {report_path}")
    return total


if __name__ == "__main__":
    n = audit()
    sys.exit(0 if n == 0 else 1)