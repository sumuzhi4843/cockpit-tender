#!/usr/bin/env python3
"""回捞：盐城 11 个区县码补全后的全量重抓（江苏政府采购网 + 苏服采）

背景（2026-08-20）：
- 之前只配 320991+320992（仅盐南+经开），导致阜宁/盐都/亭湖/大丰/东台/建湖/
  射阳/滨海/响水这 8 个区县的政府采购公告完全漏抓。
- 苏服采 API 实测：父级 320900 不包含子级（[320900] 74 条 vs 12 码并集 250 条）。

本脚本：
1. 备份当前 tender.db 到 backups/
2. 重抓苏服采（API 模式，几分钟）
3. 重抓江苏政府采购网（CDP 模式，慢，可能 1-2 小时）

用法：
  python3 backfill_district_codes.py             # 两站都抓
  python3 backfill_district_codes.py --sufucai   # 仅苏服采
  python3 backfill_district_codes.py --ccgp      # 仅江苏政府采购网
"""
import sys, os, time, shutil, argparse
from datetime import datetime
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import SITES, REQUEST_DELAY
from store_db import TenderDB
from scrapers import get_all_scrapers


SITES_TO_BACKFILL = ["江苏政府采购网", "苏服采"]


def backup_db():
    """备份数据库"""
    src = "data/tender.db"
    if not os.path.exists(src):
        print(f"⚠️ 数据库 {src} 不存在")
        return None
    ts = datetime.now().strftime("%Y%m%d_%H%M")
    dst = f"data/backups/tender_pre_district_codes_{ts}.db"
    os.makedirs("data/backups", exist_ok=True)
    shutil.copy2(src, dst)
    size = os.path.getsize(dst) / (1024 * 1024)
    print(f"✅ 备份完成: {dst} ({size:.1f} MB)")
    return dst


def backfill_site(site_name, scrapers_dict, db):
    """回捞单个站点"""
    sc = scrapers_dict[site_name]
    t0 = time.time()
    print(f"\n{'='*70}")
    print(f"  ▶ 开始抓取: {site_name}")
    print(f"  时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*70}")

    try:
        items = sc.scrape()
    except Exception as e:
        import traceback
        print(f"  ❌ 抓取异常: {e}")
        traceback.print_exc()
        items = []
    finally:
        try:
            sc.close()
        except Exception:
            pass

    if not items:
        print(f"  ⚠️ {site_name} 未返回任何数据")
        return 0

    new_n = upd_n = 0
    for it in items:
        kw = it.get("ict_keywords", [])
        try:
            if db.upsert(site_name, it, kw):
                new_n += 1
            else:
                upd_n += 1
        except Exception as e:
            print(f"    upsert 失败 {it.get('url', '?')}: {e}")

    cost = time.time() - t0
    print(f"\n  ✅ {site_name}: 抓 {len(items)} 条 (新 {new_n} / 更新 {upd_n})，耗时 {cost:.1f}s")
    return new_n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sufucai", action="store_true", help="仅苏服采")
    ap.add_argument("--ccgp", action="store_true", help="仅江苏政府采购网")
    ap.add_argument("--no-backup", action="store_true", help="跳过数据库备份")
    args = ap.parse_args()

    # 默认两站都跑
    targets = list(SITES_TO_BACKFILL)
    if args.sufucai and not args.ccgp:
        targets = ["苏服采"]
    elif args.ccgp and not args.sufucai:
        targets = ["江苏政府采购网"]

    print(f"\n=== 区县码补全回捞 ===")
    print(f"目标站点: {targets}")
    print(f"开始时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")

    if not args.no_backup:
        backup_db()

    scrapers = get_all_scrapers()
    db = TenderDB()

    totals = {}
    for site_name in targets:
        if site_name not in scrapers:
            print(f"❌ {site_name} scraper 未找到（可能模块未安装）")
            continue
        try:
            n = backfill_site(site_name, scrapers, db)
            totals[site_name] = n
        except KeyboardInterrupt:
            print("\n\n⏸ 用户中断，退出（已抓数据已入库）")
            break
        time.sleep(REQUEST_DELAY)

    db.close()

    print(f"\n\n=== 汇总 ===")
    print(f"完成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    for k, v in totals.items():
        print(f"  {k}: 新增 {v} 条")
    print(f"  合计: {sum(totals.values())} 条")


if __name__ == "__main__":
    main()