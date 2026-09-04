#!/usr/bin/env python3
"""deep_refresher.py — 凌晨深度补偿抓取（2026-08-09）

职责：只补偿「主抓失败」的站点（读 logs/last_failed_sites.json），
不重复抓主抓已成功的站；避免每天全量重抓 4 站造成反爬风险与资源浪费。

分组（供 site_refresher.sh 分两批错峰调用）：
  --group A  江苏政府采购网 + 中国政府采购网
  --group B  苏服采 + 盐城市政府采购网

防护：
  1. 站点间随机延迟 20-50s
  2. 单站连续失败 3 次自动跳过（熔断）
  3. 补偿仍失败时 exit 1，调用方据此拒绝部署不完整结果
  4. --sites 显式指定时，强制抓指定站（手动验证用）

用法：
  python3 deep_refresher.py --group A
  python3 deep_refresher.py --group B
  python3 deep_refresher.py --sites "江苏政府采购网" "中国政府采购网"
  python3 deep_refresher.py --dry-run          # 只看计划不实际抓
"""
import sys
import os
import time
import json
import random
import argparse
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import REQUEST_DELAY
from scrapers import get_all_scrapers
from store_db import TenderDB
from sufucai_ledger import SufucaiLedger

LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
FAILED_FILE = os.path.join(LOG_DIR, "last_failed_sites.json")

GROUPS = {
    "A": ["江苏政府采购网", "中国政府采购网"],
    "B": ["苏服采", "盐城市政府采购网"],
}

MAX_RETRIES_PER_SITE = 3
SITE_DELAY_RANGE = (20, 50)  # 秒


def log(msg):
    print(f"[deep_refresher] {msg}", flush=True)


def read_failed_sites():
    """读主抓记录的失败站点列表（异常抛出的站）"""
    try:
        with open(FAILED_FILE, encoding="utf-8") as f:
            data = json.load(f)
        failed = data.get("failed", [])
        if failed:
            log(f"主抓失败站（{data.get('collected_at', '?')}）：{failed}")
        else:
            log("主抓无失败站，本次无需补偿")
        return failed, data.get("details", {})
    except FileNotFoundError:
        log(f"未找到 {FAILED_FILE}（主抓还没跑过或未记录），本次跳过")
        return [], {}
    except Exception as e:
        log(f"读取失败站文件异常: {e}")
        return [], {}


def refresh_one(site_name, db, failed_details=None):
    """补抓单站，含熔断。返回 (成功?, 新增数, 更新数)"""
    scrapers = get_all_scrapers()
    if site_name not in scrapers:
        log(f"[SKIP] {site_name} 不在 scrapers 注册表")
        return False, 0, 0

    sc = scrapers[site_name]
    details = (failed_details or {}).get(site_name, {})
    failed_scopes = details.get("failed_scopes") or []
    if failed_scopes and hasattr(sc, "set_scope_filter"):
        sc.set_scope_filter(failed_scopes)
        log(f"{site_name} 仅补抓缺口范围：{failed_scopes}")
    for attempt in range(1, MAX_RETRIES_PER_SITE + 1):
        try:
            if site_name == "江苏政府采购网":
                # 补偿优先补齐列表，不让详情抓取再次拖垮任务；详情由后续富化链路补齐。
                items = sc.scrape(fetch_details=False, ict_only=False)
            else:
                items = sc.scrape()
            health = sc.health() if hasattr(sc, "health") else {"status": "success"}
            if health.get("status") != "success":
                raise RuntimeError(
                    f"抓取仍为 {health.get('status')}，剩余范围 {health.get('failed_scopes', [])}"
                )
            if site_name == "苏服采":
                ledger = SufucaiLedger(db.db_path)
                try:
                    ledger.record_scrape(sc)
                    ledger.record_source_items(items)
                    ledger.record_existing_source_items()
                finally:
                    ledger.close()
            new_n = upd_n = 0
            for it in items:
                kw = it.get("ict_keywords", [])
                try:
                    if db.upsert(site_name, it, kw):
                        new_n += 1
                    else:
                        upd_n += 1
                except Exception as e:
                    log(f"    upsert 失败 {it.get('url', '?')}: {e}")
            log(f"[OK] {site_name}: 抓 {len(items)} 条（新 {new_n} / 更 {upd_n}），第 {attempt} 次尝试")
            return True, new_n, upd_n
        except Exception as e:
            log(f"[FAIL] {site_name} 第 {attempt} 次: {e}")
            traceback.print_exc()
            if attempt < MAX_RETRIES_PER_SITE:
                time.sleep(REQUEST_DELAY * 2)
        finally:
            try:
                sc.close()
            except Exception:
                pass

    log(f"[CIRCUIT-BREAK] {site_name} 连续失败 {MAX_RETRIES_PER_SITE} 次，跳过（记 WARN）")
    return False, 0, 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--group", choices=["A", "B"], help="按分组抓取（A=江苏+中国政采，B=苏服采+盐城政采）")
    ap.add_argument("--sites", nargs="+", help="显式指定站点（手动验证用，跳过失败站判断）")
    ap.add_argument("--dry-run", action="store_true", help="只看计划，不实际抓取")
    ap.add_argument("--rebuild", action="store_true", help="抓完后重建项目+导出（仅 deep-b 调用方使用）")
    args = ap.parse_args()

    # 确定本次要抓的站
    if args.sites:
        sites = args.sites
        log(f"显式指定站点：{sites}")
    elif args.group:
        # 分组 + 失败站交集：只有「主抓失败」的站才补偿
        failed, failed_details = read_failed_sites()
        group_sites = GROUPS[args.group]
        sites = [s for s in group_sites if s in failed]
        if not sites:
            log(f"分组 {args.group}（{group_sites}）均不在失败列表，跳过")
            return 0
        log(f"分组 {args.group} 需补偿：{sites}")
    else:
        log("用法: deep_refresher.py --group A|B | --sites <site...>")
        return 1

    if args.sites:
        failed_details = {}

    if args.dry_run:
        log(f"[DRY-RUN] 计划抓取：{sites}（不实际执行）")
        return 0

    db = TenderDB()
    total_new = total_upd = 0
    failed_refreshes = []
    for i, site in enumerate(sites):
        if i > 0:
            delay = random.randint(*SITE_DELAY_RANGE)
            log(f"[SLEEP] 站点间等待 {delay}s")
            time.sleep(delay)
        ok, n_new, n_upd = refresh_one(site, db, failed_details)
        if not ok:
            failed_refreshes.append(site)
        total_new += n_new
        total_upd += n_upd

    db.close()
    log(f"本次补偿完成：新增 {total_new} 条，更新 {total_upd} 条")

    if args.rebuild and not failed_refreshes:
        log("重建项目 + 导出 tender.json …")
        from project_engine import rebuild
        from run import export_tender_json
        from verify_quality import check
        rebuild()
        if check() != 0:
            raise RuntimeError("补偿抓取后的质量校验未通过")
        export_tender_json([])
        log("重建+导出完成")
    elif args.rebuild:
        log(f"仍有失败站 {failed_refreshes}，跳过重建和导出，保留线上旧数据")

    return 1 if failed_refreshes else 0


if __name__ == "__main__":
    sys.exit(main())
