#!/usr/bin/env python3
"""苏服采项目重分类（2026-08-21）。

老板要求：
- 苏服采 API row.areaCode 已按区县分发，不要二次分配（不要走「关键字/公司映射/地址」链）
- 直接用 API areaCode → 区县名映射
- 历史数据选 B：重跑 enrich_meta 让 std_district 按新规则重新判定

用法：
  python3 reclassify_sufucai.py --dry-run   # 只打印变化，不写库
  python3 reclassify_sufucai.py            # 真跑（执行 UPDATE）
  python3 reclassify_sufucai.py --rollback # 回滚到改前（用最近一次 pre-sufucai-area-code 备份）

回滚：手动 cp backups/tender.db.bak.<ts>-pre-sufucai-area-code collector/data/tender.db
"""
import argparse
import json
import os
import sqlite3
import sys
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).parent
DB_PATH = HERE / "data" / "tender.db"

# 与 enrich_meta.py SUFUCAI_AREA_CODE_MAP 完全一致（短名风格）
SUFUCAI_AREA_CODE_MAP = {
    "320900": "市级",
    "320902": "亭湖", "320903": "盐都", "320904": "大丰",
    "320921": "响水", "320922": "滨海", "320923": "阜宁",
    "320924": "射阳", "320925": "建湖", "320981": "东台",
    "320991": "经开", "320992": "盐南",
}


def classify_sufucai(raw_json: dict) -> str | None:
    """根据 raw_json 里的 raw_row.areaCode（或 areaCode）映射区县。
    返回区县名，或 None（raw 里没有有效 areaCode）。"""
    raw = json.loads(raw_json or "{}") if raw_json else {}
    raw_row = raw.get("raw_row") or {}
    api_code = str(raw_row.get("areaCode") or raw.get("areaCode") or "").strip()
    return SUFUCAI_AREA_CODE_MAP.get(api_code)


def diff(conn):
    """对所有苏服采项目计算 old_district → new_district 映射。"""
    rows = conn.execute(
        "SELECT id, title, std_district, raw_json FROM notices WHERE site='苏服采'"
    ).fetchall()
    diffs = []
    unchanged = 0
    for r in rows:
        old = r[2] or ""
        new = classify_sufucai(r[3])
        if new is None:
            new = "（无法判定）"
        if new == old:
            unchanged += 1
        else:
            diffs.append({"id": r[0], "title": r[1][:60], "old": old, "new": new})
    return diffs, unchanged, len(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="只打印变化，不写库")
    ap.add_argument("--rollback", action="store_true", help="回滚 DB 到改前备份")
    args = ap.parse_args()

    if args.rollback:
        bak_dir = HERE / "data" / "backups"
        baks = sorted(bak_dir.glob("tender.db.bak.*-pre-sufucai-area-code"))
        if not baks:
            print("未找到 -pre-sufucai-area-code 备份")
            sys.exit(1)
        latest = baks[-1]
        print(f"回滚: cp {latest} {DB_PATH}")
        import shutil
        shutil.copy(latest, DB_PATH)
        print(f"✅ 已回滚到 {latest.name}")
        return

    if not DB_PATH.exists():
        print(f"DB 不存在: {DB_PATH}")
        sys.exit(1)

    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row

    diffs, unchanged, total = diff(conn)

    print(f"=== 苏服采项目重分类 dry-run ===")
    print(f"总项目数: {total}")
    print(f"无变化: {unchanged}")
    print(f"将变化: {len(diffs)}")
    print()

    # 变化矩阵
    matrix = Counter((d["old"], d["new"]) for d in diffs)
    print("变化矩阵 (old → new):")
    for (old, new), cnt in sorted(matrix.items(), key=lambda x: (-x[1], x[0])):
        print(f"  {old or '(空)':10s} → {new:12s}  {cnt:5d} 条")
    print()

    # std_district 新旧分布对比
    # 从 conn 直查现分布（更准，避免重复算 unchanged）
    cur = sqlite3.connect(str(DB_PATH))
    cur_rows = cur.execute(
        "SELECT std_district, COUNT(*) FROM notices WHERE site='苏服采' GROUP BY std_district"
    ).fetchall()
    cur.close()
    cur_dist = {r[0] or "": r[1] for r in cur_rows}
    new_dist = Counter()
    for d in diffs:
        new_dist[d["new"]] += 1
    old_dist = Counter()
    for d in diffs:
        old_dist[d["old"]] += 1
    all_keys = set(cur_dist) | set(new_dist) | set(SUFUCAI_AREA_CODE_MAP.values())
    print(f"{'区县':12s} {'原分布':>10s} {'改后期望':>10s} {'变化':>8s}")
    print("-" * 46)
    for k in sorted(all_keys, key=lambda x: -cur_dist.get(x, 0)):
        old_total = cur_dist.get(k, 0)
        new_total = old_total - old_dist.get(k, 0) + new_dist.get(k, 0)
        delta = new_total - old_total
        sign = "+" if delta > 0 else ""
        print(f"{k or '(空)':12s} {old_total:10d} {new_total:10d} {sign}{delta:7d}")
    print()

    if args.dry_run:
        print("（DRY-RUN：不写库）")
        return

    # 真跑：UPDATE std_district
    if not diffs:
        print("无变化，跳过")
        return

    print(f"确认要写库吗？将 UPDATE {len(diffs)} 条 notices.std_district。")
    print(f"按 Enter 确认，或 Ctrl+C 取消（10 秒后超时自动取消）：")
    try:
        import select
        ready, _, _ = select.select([sys.stdin], [], [], 10)
        if ready:
            sys.stdin.readline()
        else:
            print("\n超时未确认，取消。")
            return
    except KeyboardInterrupt:
        print("\n取消。")
        return

    cur = conn.cursor()
    for d in diffs:
        cur.execute("UPDATE notices SET std_district=? WHERE id=?", (d["new"], d["id"]))
    conn.commit()
    print(f"✅ 已更新 {len(diffs)} 条")

    # 验证
    print("\n=== 改后分布 ===")
    rows = conn.execute("SELECT std_district, COUNT(*) FROM notices WHERE site='苏服采' GROUP BY std_district ORDER BY 2 DESC").fetchall()
    for r in rows: print(f"  {r[0] or '(空)':12s} {r[1]}")


if __name__ == "__main__":
    main()