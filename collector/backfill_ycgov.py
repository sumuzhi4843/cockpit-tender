# -*- coding: utf-8 -*-
"""盐城市政府采购网全量补抓（2026-08-10）

背景：YcGovProcurementScraper 只抓各子栏目 index.html 首屏（~15-45 条），
未接入 jpage 翻页接口，28 个栏目实际 14,724 条只入库 1,159 条（缺口 ~94%）。

本脚本：用已逆出的 jpage 翻页接口逐栏目翻页抓全，复用 store_db.upsert 入库。

翻页接口（已逆向验证）：
  POST https://czj.yancheng.gov.cn/module/web/jpage/dataproxy.jsp
       ?startrecord=16&endrecord=30&perpage=15
  form: col=1&appid=1&webid=7&path=/&columnid=<col>&sourceContentType=1
        &unitid=<unitid>&webname=盐城市财政局&permissiontype=0
  → <datastore><totalrecord>540</totalrecord><totalpage>36</totalpage>...

用法：
  python3 backfill_ycgov.py                 # 全量补（28 栏目）
  python3 backfill_ycgov.py --cols 24544    # 只补指定栏目（逗号分隔）
  python3 backfill_ycgov.py --months 2      # 只补最近 N 个月（按发布日期过滤）
  python3 backfill_ycgov.py --no-detail     # 只抓列表不抓详情（快）
  python3 backfill_ycgov.py --limit 50      # 每栏目最多抓 N 条（验证用）
"""
import os
import sys
import re
import json
import time
import argparse
import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from store_db import TenderDB
from config import REQUEST_HEADERS, REQUEST_TIMEOUT, REQUEST_DELAY, MAX_RETRIES, RETRY_BACKOFF

# —— 站点常量 ——
BASE = "https://czj.yancheng.gov.cn"
PROXY_URL = f"{BASE}/module/web/jpage/dataproxy.jsp"
PAGE_SIZE = 15

# 28 个子栏目配置（columnid → unitid），2026-08-10 扫描获取
# EXCLUDED：2387 通知公告 / 2388 诚信平台 / 2407 办事指南（管理类，现有爬虫也排除）
COLUMNS = {
    "20137": {"unitid": "135526", "note": "市级"},
    "20141": {"unitid": "135567", "note": "市级"},
    "20142": {"unitid": "135567", "note": "市级"},
    "20144": {"unitid": "135567", "note": "市级"},
    "20147": {"unitid": "135567", "note": "市级"},
    "20148": {"unitid": "135567", "note": "市级"},
    "20149": {"unitid": "135567", "note": "市级"},
    "20176": {"unitid": "135567", "note": "市级"},
    "20177": {"unitid": "135567", "note": "市级"},
    "20179": {"unitid": "135567", "note": "市级"},
    "20182": {"unitid": "135567", "note": "市级"},
    "20183": {"unitid": "135567", "note": "市级"},
    "20184": {"unitid": "135567", "note": "市级"},
    "20185": {"unitid": "135526", "note": "市级"},
    "24544": {"unitid": "135567", "note": "意向-市级"},
    "24546": {"unitid": "135526", "note": "市级"},
    "24547": {"unitid": "135567", "note": "意向-区县"},
    "24549": {"unitid": "135567", "note": "市级"},
    "31689": {"unitid": "135580", "note": "区县"},
    "31690": {"unitid": "135580", "note": "区县"},
    "31691": {"unitid": "135580", "note": "区县"},
    "31692": {"unitid": "135580", "note": "区县"},
    "33995": {"unitid": "135526", "note": "区县"},
    "33996": {"unitid": "135526", "note": "区县"},
    "33998": {"unitid": "135526", "note": "区县"},
    "33999": {"unitid": "135526", "note": "区县"},
    "34066": {"unitid": "135479", "note": "驻盐单位"},
}

session = requests.Session()
session.headers.update(REQUEST_HEADERS)


def fetch_page(columnid, unitid, start, end, per_page=PAGE_SIZE):
    """调 dataproxy 翻页接口，返回 (totalrecord, items[])"""
    params = {"startrecord": start, "endrecord": end, "perpage": per_page}
    data = {
        "col": "1", "appid": "1", "webid": "7", "path": "/",
        "columnid": str(columnid), "sourceContentType": "1", "unitid": str(unitid),
        "webname": "盐城市财政局", "permissiontype": "0",
    }
    headers = {
        "Referer": f"{BASE}/col/col{columnid}/index.html",
        "X-Requested-With": "XMLHttpRequest",
    }
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = session.post(PROXY_URL, params=params, data=data, headers=headers, timeout=REQUEST_TIMEOUT)
            if resp.status_code == 200 and "<datastore>" in resp.text:
                return parse_datastore(resp.text)
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_BACKOFF * attempt)
        except requests.exceptions.RequestException as e:
            print(f"    请求异常 {e}，重试 {attempt}/{MAX_RETRIES}")
            time.sleep(RETRY_BACKOFF * attempt)
    return 0, []


def parse_datastore(text):
    """解析 <datastore> 响应：totalrecord + record 块里的条目"""
    m = re.search(r"<totalrecord>(\d+)</totalrecord>", text)
    total = int(m.group(1)) if m else 0
    items = []
    for rec in re.findall(r"<record><!\[CDATA\[(.*?)\]\]></record>", text, re.DOTALL):
        href_m = re.search(r"href=['\"]([^'\"]+)['\"]", rec)
        title_m = re.search(r"title=['\"]([^'\"]*)['\"]", rec)
        if not href_m:
            continue
        href = href_m.group(1)
        title = (title_m.group(1) or "").strip()
        if not title:
            txt_m = re.search(r"<a[^>]*>([^<]+)</a>", rec)
            if txt_m:
                title = txt_m.group(1).strip()
        if not title:
            continue
        full_url = href if href.startswith("http") else f"{BASE}{href}"
        date = ""
        dm = re.search(r"/art/(\d{4})/(\d{1,2})/(\d{1,2})/art_", href)
        if dm:
            date = f"{dm.group(1)}-{int(dm.group(2)):02d}-{int(dm.group(3)):02d}"
        else:
            tm = re.search(r"bt-list-time[^>]*>([\d\-]+)</span>", rec)
            if tm:
                date = tm.group(1)
        items.append({"title": title, "url": full_url, "date": date})
    return total, items


def fetch_detail(url):
    """抓详情页正文（复用 BaseScraper 选择器逻辑的简化版）"""
    try:
        resp = session.get(url, timeout=REQUEST_TIMEOUT)
        resp.encoding = resp.apparent_encoding or resp.encoding
        if resp.status_code != 200 or not resp.text:
            return ""
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(resp.text, 'lxml')
        best = ""
        for el in soup.select("div.article, div.content, div.bt-article, div.article-content, div.TRS_Editor, div.custom"):
            text = el.get_text(separator=" ", strip=True)
            if len(text) > len(best) and len(text) >= 100:
                best = text
        if best:
            return best[:8000]
        body = soup.find('body')
        return body.get_text(separator=" ", strip=True)[:8000] if body else ""
    except requests.exceptions.RequestException:
        return ""


def extract_fields(content, title=""):
    """详情正文 → 发包人/金额/开标时间（复用 enrich_engine，失败给空）"""
    fields = {"tenderer": "", "amount": "", "bid_deadline": ""}
    try:
        from enrich_engine import enrich_detail, infer_notice_type
        det = enrich_detail(title, content) or {}
        fields["tenderer"] = det.get("tenderer") or ""
        fields["amount"] = det.get("amount") or ""
        fields["bid_deadline"] = det.get("bid_deadline") or ""
    except Exception:
        pass
    return fields


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cols", default=None, help="只补指定栏目，逗号分隔（默认全部 27 个）")
    ap.add_argument("--months", type=int, default=0, help="只补最近 N 个月（0=全量）")
    ap.add_argument("--since", default=None, help="只补发布日期 >= 此日期（YYYY-MM-DD，优先于 --months）")
    ap.add_argument("--no-detail", action="store_true", help="只抓列表不抓详情")
    ap.add_argument("--limit", type=int, default=0, help="每栏目最多抓 N 条（验证用）")
    args = ap.parse_args()

    cols = {c: COLUMNS[c] for c in (args.cols.split(",") if args.cols else COLUMNS)}
    db = TenderDB()
    db_new = db_upd = 0
    list_total = 0

    # 日期过滤（2026-08-10：老板要求只补 6 月及以后，太老数据不要）
    from datetime import datetime, timedelta
    if args.since:
        cutoff = args.since
    elif args.months:
        cutoff = (datetime.now() - timedelta(days=30 * max(args.months, 1))).strftime("%Y-%m-%d")
    else:
        cutoff = None

    for cid, cfg in cols.items():
        print(f"\n=== col{cid} [{cfg['note']}] ===")
        total, first_items = fetch_page(cid, cfg["unitid"], 1, PAGE_SIZE)
        print(f"  栏目总数: {total}")
        if total == 0:
            print("  跳过（0 条）")
            continue

        all_items = list(first_items)
        start = 1 + PAGE_SIZE
        while len(all_items) < total:
            # 早停：列表按日期倒序，若本页最旧条目已早于 cutoff，无需继续翻
            if cutoff and all_items and all(item.get("date", "") for item in all_items):
                oldest = min(item.get("date", "") for item in all_items)
                if oldest and oldest < cutoff:
                    break
            end = min(start + PAGE_SIZE - 1, total)
            t, items = fetch_page(cid, cfg["unitid"], start, end)
            if not items:
                break
            all_items.extend(items)
            start += PAGE_SIZE
            time.sleep(REQUEST_DELAY * 0.3)
            if len(all_items) % 100 == 0:
                print(f"    列表 {len(all_items)}/{total}")

        # 过滤日期
        if cutoff:
            before = len(all_items)
            all_items = [it for it in all_items if it.get("date", "") >= cutoff]
            print(f"  日期过滤（>={cutoff}）: {before} → {len(all_items)}")
        if args.limit:
            all_items = all_items[:args.limit]
        print(f"  待处理 {len(all_items)} 条")

        # 详情 + 入库
        for i, item in enumerate(all_items):
            if not args.no_detail:
                content = fetch_detail(item["url"])
                item["content"] = content
                fields = extract_fields(content, item.get("title", ""))
                item.update({k: v for k, v in fields.items() if v})
                time.sleep(REQUEST_DELAY * 0.3)
            is_new = db.upsert("盐城市政府采购网", item, ict_keywords=None, tags=None)
            if is_new:
                db_new += 1
            else:
                db_upd += 1
            list_total += 1
            if (i + 1) % 50 == 0:
                print(f"    进度 {i + 1}/{len(all_items)}（新 {db_new} / 更新 {db_upd}）")

    db.close()
    print(f"\n✅ 完成：列表 {list_total} 条，新增 {db_new}，更新 {db_upd}")
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
