#!/usr/bin/env python3
"""重新抓取 3 个数据缺失站：江苏政府采购网 / 苏服采 / 中国政府采购网

仅针对这 3 个站重跑：
1. 调用对应 scraper 全量抓取（含详情）
2. 写入 SQLite（upsert）
3. 不重建 project_engine、不导出 tender.json —— 由调用方负责

用法：
  python3 backfill_3sites.py            # 仅抓
  python3 backfill_3sites.py --export   # 抓 + 重建 + 导出 + docker deploy
"""
import sys, os, time, argparse
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import REQUEST_DELAY, SITES
from store_db import TenderDB
from scrapers import get_all_scrapers


TARGET_SITES = ["江苏政府采购网", "苏服采", "中国政府采购网"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--export", action="store_true", help="抓完后重建+导出+部署")
    ap.add_argument("--no-detail", action="store_true", help="跳过详情抓取（仅列表）")
    args = ap.parse_args()

    scrapers = get_all_scrapers()
    available = [s for s in TARGET_SITES if s in scrapers]
    if not available:
        print("❌ 目标站 scraper 未找到")
        return 1

    print(f"=== 重新抓取 {len(available)} 站：{available} ===\n")

    db = TenderDB()
    totals = {}
    for site_name in available:
        sc = scrapers[site_name]
        t0 = time.time()
        try:
            if args.no_detail:
                # 仅列表（直接调 _safe_parse_list，避免详情）
                html = sc.fetch(sc.url)
                items = sc._safe_parse_list(html) if html else []
            else:
                items = sc.scrape()
        except Exception as e:
            import traceback
            print(f"  [{site_name}] 抓取异常: {e}")
            traceback.print_exc()
            items = []
        finally:
            try:
                sc.close()
            except Exception:
                pass

        # 落库
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
        totals[site_name] = (len(items), new_n, upd_n, cost)
        print(f"  [{site_name}] 抓 {len(items)} 条 (新 {new_n} / 更 {upd_n})，耗时 {cost:.1f}s\n")
        time.sleep(REQUEST_DELAY)

    db.close()

    print("\n=== 汇总 ===")
    for site, (n, new, upd, cost) in totals.items():
        print(f"  {site:20s} 抓取 {n:4d} 条  新 {new:4d}  更 {upd:4d}  耗时 {cost:.1f}s")

    if args.export:
        from project_engine import rebuild
        from run import export_tender_json
        print("\n=== 重建项目 + 导出 tender.json ===")
        rebuild()
        export_tender_json([])

        # 部署到 cockpit（2026-08-09：输出目录改为板块 data/）
        tender_json_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "..", "data", "tender.json"
        )
        if os.path.getsize(tender_json_path) > 0:
            os.system(f"docker cp {tender_json_path} cockpit:/usr/share/nginx/html/data/tender.json")
            print("✅ docker cp 完成")
        else:
            print("❌ tender.json 为空，跳过部署")
    return 0


if __name__ == "__main__":
    sys.exit(main())