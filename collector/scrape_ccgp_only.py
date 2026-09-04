# -*- coding: utf-8 -*-
"""江苏政府采购网专项抓取（2026-08-07）

单独跑江苏政府采购网（含中标/成交/更正公告），入库 + 富化 + 重建 + 导出。
用于补齐中标数据，避免全量 run.py 耗时。

用法：python3 scrape_ccgp_only.py
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from analyzer import analyze_items
from store import load_store, is_pushed, mark_pushed
from store_db import TenderDB, item_hash


def main():
    from scrapers import get_all_scrapers
    scrapers = get_all_scrapers()
    scraper = scrapers.get("江苏政府采购网")
    if not scraper:
        print("无江苏政府采购网抓取器")
        return

    name = "江苏政府采购网"
    print(f"=== {name} 专项抓取（含中标/成交/更正）{time.strftime('%H:%M:%S')} ===")
    try:
        all_items = scraper.scrape(fetch_details=True, ict_only=False)
    finally:
        scraper.close()
    print(f"抓到 {len(all_items)} 条")

    # ICT 筛选
    ict, _ = analyze_items(all_items)
    ict_map = {it.get("url"): it.get("ict_keywords", []) for it in ict}

    # 入库
    store = load_store()
    db = TenderDB()
    new_n = upd_n = 0
    try:
        for item in all_items:
            kw = item.get("ict_keywords") or ict_map.get(item.get("url"), [])
            is_new = db.upsert(name, item, kw)
            if is_new:
                new_n += 1
            else:
                upd_n += 1
        print(f"入库：新增 {new_n}，更新 {upd_n}，总 {db.count()}")
    finally:
        db.close()

    # 标记新增
    new_items = [it for it in all_items if not is_pushed(it, store, name)]
    mark_pushed(new_items, store, name)

    # 元数据富化 + 重建 + 导出
    from enrich_meta import enrich
    enrich(stats_only=False)
    from project_engine import rebuild
    rebuild()
    from run import export_tender_json
    new_ids = [item_hash(it, name) for it in new_items]
    export_tender_json(new_ids)
    print(f"=== 完成 {time.strftime('%H:%M:%S')} ===")


if __name__ == "__main__":
    main()
