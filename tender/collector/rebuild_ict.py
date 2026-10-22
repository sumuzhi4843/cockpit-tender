#!/usr/bin/env python3
"""重跑 ICT 筛选并回写 SQLite（轻量，不重新抓取）

场景：analyzer 规则变更后，只需从 SQLite 读全量原始数据，
用新规则重算 ICT 并更新 db（ict_keywords + tags），API 会直接读取新数据。

2026-09-29 架构收敛：本脚本只负责"重算 + 回写 db"。生效链路= rebuild_ict →
project_engine.rebuild → 质量校验（等价 `python3 maintenance.py rebuild validated`），
8002 API 直接读取 DB，无静态 JSON 导出环节。
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from store_db import TenderDB
from analyzer import analyze_items
from enrich_meta import classify_district, classify_category, classify_sme, build_tags
from store_db import load_store


def main():
    db = TenderDB()
    sites_map = {}
    for site in db.count_by_site():
        rows = db.query(site=site, limit=100000)
        sites_map[site] = rows

    total_ict = 0
    total_all = 0
    updates = []

    for name, rows in sites_map.items():
        items = []
        for r in rows:
            raw = json.loads(r.get("raw_json") or "{}")
            items.append({
                "title": r["title"],
                "url": r["url"],
                "content": raw.get("content", ""),
                # 2026-08-10：传 notice_type，意向/招标正文参与弱匹配
                "notice_type": r.get("notice_type", "tender"),
            })

        ict, non_ict = analyze_items(items)
        ict_map = {i["title"]: i.get("ict_keywords", []) for i in ict}
        total_all += len(items)
        total_ict += len(ict)

        for r in rows:
            title = r["title"]
            raw = json.loads(r.get("raw_json") or "{}")
            # 区县 / 行业
            dist = classify_district(title, raw.get("tenderer", ""), name)
            minor, major = classify_category(title)
            # 中小微
            sme = classify_sme(raw.get("content", ""))
            # ICT 关键词（新规则重算）
            kws = ict_map.get(title, [])
            amount_value = raw.get("amount_value")
            ntype = r.get("notice_type", "tender")
            tags = build_tags(dist, major, minor, sme, is_ict=bool(kws),
                              amount_value=amount_value, notice_type=ntype)
            updates.append((
                dist or "", major or "", minor or "", sme,
                json.dumps(kws, ensure_ascii=False),
                json.dumps(tags, ensure_ascii=False),
                r["id"],
            ))

    db._conn.executemany(
        "UPDATE notices SET std_district=?, proj_major_cat=?, proj_minor_cat=?, sme_target=?, ict_keywords=?, tags=? WHERE id=?",
        updates,
    )
    db._conn.commit()
    db.close()

    print(f"ICT 重算完成：{total_ict}/{total_all} 条")



if __name__ == "__main__":
    main()
