#!/usr/bin/env python3
"""金额数据全量修正（2026-08-17）

背景：scrapers.py 旧正则会抓"中标金额：1192108.56元"的小数残留（56元），
以及"万 元"带空格导致万元模式失效；合同预估金额（合同预估金额（万元）列）混入预算字段。

本脚本以 enrich_engine.enrich_detail 的金额提取为准，对全部 notices 重提金额，
只更新 amount/amount_value/amount_unit/budget_type 四列（不动其他字段）。

用法：
  python3 fix_amounts.py --dry-run    # 预览（默认）
  python3 fix_amounts.py --apply      # 写库（先自动备份 DB）
"""
import argparse
import json
import shutil
import sqlite3
import sys
import time
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
DB_PATH = HERE / "data" / "tender.db"
BACKUP_DIR = HERE.parent.parent / "backups"

from enrich_engine import enrich_detail  # noqa: E402


def ensure_cols(conn):
    cols = [r[1] for r in conn.execute("PRAGMA table_info(notices)")]
    if "budget_type" not in cols:
        conn.execute("ALTER TABLE notices ADD COLUMN budget_type TEXT")
        conn.commit()
        print("  [schema] notices 新增 budget_type 列")


def extract_amount(content, notice_type):
    """从正文重提金额 → (amount_str, amount_value, amount_unit, budget_type) 或 None"""
    if not content or not content.strip():
        return None
    try:
        out = enrich_detail(content, notice_type)
    except Exception:
        return None
    if out.get("amount_value"):
        return (out["amount"], out["amount_value"], out.get("amount_unit") or "元",
                out.get("budget_type"))
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="写库（默认 dry-run 预览）")
    args = ap.parse_args()

    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    ensure_cols(conn)

    rows = conn.execute(
        "SELECT id, title, notice_type, amount, amount_value, raw_json, budget_type FROM notices"
    ).fetchall()

    stats = {
        "total": len(rows),
        "fixed_wrong": 0,      # 错值（<1000）被修正/清空
        "updated_value": 0,    # 值变化（非错值类）
        "backfilled": 0,       # 空金额补提成功
        "contract_estimate": 0,  # 标记为合同预估
        "limit_type": 0,       # 标记为限价
        "cleared": 0,          # 错值且无法重提 → 置空
        "kept": 0,             # 保留原值
    }
    changes = []  # (id, old_v, new_v, kind, note)

    for r in rows:
        content = None
        try:
            content = json.loads(r["raw_json"] or "{}").get("content") or ""
        except Exception:
            pass
        old_v = r["amount_value"]
        extracted = extract_amount(content, r["notice_type"])

        if extracted:
            amt_str, amt_val, amt_unit, btype = extracted
            if btype == "contract_estimate":
                stats["contract_estimate"] += 1
            elif btype == "limit":
                stats["limit_type"] += 1

            if old_v is None or old_v == 0:
                stats["backfilled"] += 1
                changes.append((r["id"], old_v, amt_val, "backfill", f"{amt_str} [{btype}]"))
            elif old_v != amt_val:
                # 旧值不同：错值（<1000）优先修；其他差异也更新（以重提为准）
                kind = "fix_wrong" if old_v < 1000 else "update"
                if kind == "fix_wrong":
                    stats["fixed_wrong"] += 1
                else:
                    stats["updated_value"] += 1
                changes.append((r["id"], old_v, amt_val, kind, f"{amt_str} [{btype}]"))
            else:
                # 值相同：补 budget_type
                if btype and btype != (r["budget_type"] or None):
                    changes.append((r["id"], old_v, amt_val, "type_only", f"[{btype}]"))
                stats["kept"] += 1
        else:
            if old_v is not None and 0 < old_v < 1000:
                stats["cleared"] += 1
                changes.append((r["id"], old_v, None, "clear", "错值无法重提，置空"))
            else:
                stats["kept"] += 1

    # ── 报告 ──
    print(f"== 金额修正预览（{'apply' if args.apply else 'dry-run'}）==")
    print(f"  公告总数: {stats['total']}")
    print(f"  错值修正(<1000): {stats['fixed_wrong']}")
    print(f"  值更新: {stats['updated_value']}")
    print(f"  空金额补提: {stats['backfilled']}")
    print(f"  错值清空: {stats['cleared']}")
    print(f"  合同预估标记: {stats['contract_estimate']}")
    print(f"  限价标记: {stats['limit_type']}")
    print(f"  保留: {stats['kept']}")
    total_change = sum(1 for c in changes if c[3] != "type_only")
    print(f"  需写入变更: {total_change} 条（另 type_only {len(changes) - total_change} 条）")

    # 抽样展示修正
    shown = 0
    for c in changes:
        if c[3] in ("fix_wrong", "clear", "backfill") and shown < 10:
            note = c[4][:50]
            print(f"    e.g. {c[3]:10s} {c[1]} -> {c[2]}  {note}")
            shown += 1

    # 修正前后整体分布对比
    before = conn.execute(
        "SELECT SUM(amount_value IS NOT NULL AND amount_value>0), SUM(amount_value>0 AND amount_value<1000) FROM notices").fetchone()
    print(f"\n  当前库: 有金额={before[0]}  <1000元={before[1]}")

    if not args.apply:
        print("\n（dry-run，未写库。加 --apply 执行）")
        conn.close()
        return

    # ── 写库 ──
    os.makedirs(BACKUP_DIR, exist_ok=True)
    # 统一备份体系：只保留一份写库前快照（覆盖式），不积累多版本。
    dst = BACKUP_DIR / "fix_amounts.pre.db"
    shutil.copy2(DB_PATH, dst)
    print(f"\n[backup] {dst}")

    n_ok = 0
    for c in changes:
        nid, old_v, new_v, kind, note = c
        if kind == "clear":
            conn.execute("UPDATE notices SET amount='', amount_value=NULL, amount_unit='', budget_type=NULL WHERE id=?", (nid,))
        elif kind == "type_only":
            btype = note[1:-1]
            conn.execute("UPDATE notices SET budget_type=? WHERE id=?", (btype, nid))
        else:
            amt_str, amt_val, amt_unit, btype = note, new_v, "元", None
            # note 形如 "21.44万元 [budget]"；拆分
            if " [" in note:
                amt_str, btype = note.split(" [")
                btype = btype.rstrip("]")
            else:
                amt_str = note
            conn.execute(
                "UPDATE notices SET amount=?, amount_value=?, amount_unit=?, budget_type=? WHERE id=?",
                (amt_str, amt_val, amt_unit, btype, nid))
        n_ok += 1
    conn.commit()

    after = conn.execute(
        "SELECT SUM(amount_value IS NOT NULL AND amount_value>0), SUM(amount_value>0 AND amount_value<1000) FROM notices").fetchone()
    print(f"  已写入 {n_ok} 条")
    print(f"  修正后: 有金额={after[0]}  <1000元={after[1]}")
    conn.close()
    print("\n✅ 完成。下一步：enrich_meta 重打标 + project_engine rebuild + 导出部署")


if __name__ == "__main__":
    import os  # noqa: E402
    main()
