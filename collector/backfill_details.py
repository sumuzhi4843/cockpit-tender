# -*- coding: utf-8 -*-
"""回填一汽电子采购和中粮e采已入库公告的正文。

默认只处理 raw_json.content 为空的记录，不新增公告，不改变稳定 URL/ID。
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import time
from pathlib import Path
from urllib.parse import urlparse, parse_qs

from analyzer import is_ict
from config import SITES, DETAIL_DELAY, REQUEST_DELAY, REQUEST_TIMEOUT
from scrapers import FawScraper, CofcoEScraper
from store_db import TenderDB, DB_PATH


def _rows(site, force=False):
    conn = sqlite3.connect(DB_PATH); conn.row_factory = sqlite3.Row
    sql = "SELECT * FROM notices WHERE site=?"
    if not force:
        sql += " AND (json_extract(raw_json,'$.content') IS NULL OR length(json_extract(raw_json,'$.content'))=0)"
    rows = conn.execute(sql, (site,)).fetchall(); conn.close()
    return rows


def _update(db, row, item):
    flag, keywords = is_ict(item.get("title", ""), item.get("content", ""))
    try: tags = json.loads(row["tags"] or "[]")
    except Exception: tags = []
    db.upsert(row["site"], item, keywords if flag else [], tags)


def backfill_faw(force=False):
    site = "一汽电子采购"; rows = _rows(site, force)
    scraper = FawScraper(site, SITES[site]["url"], SITES[site]["base_url"])
    db = TenderDB(); ok = no_body = failed = 0
    try:
        for row in rows:
            try:
                item = json.loads(row["raw_json"] or "{}")
                guid = parse_qs(urlparse(row["url"] or "").query).get("guid", [""])[0]
                if not guid:
                    failed += 1; continue
                content, direct_fields, _ = scraper._fetch_faw_detail(guid)
                if not content:
                    no_body += 1; continue
                fields = scraper.extract_detail_fields(content, row["title"])
                item.update({k: v for k, v in fields.items() if v not in (None, "")})
                item.update({k: v for k, v in direct_fields.items() if v not in (None, "")})
                item.update({"title": row["title"], "url": row["url"],
                             "date": row["publish_date"], "content": content,
                             "detail_api_guid": guid})
                _update(db, row, item); ok += 1
            except Exception as exc:
                print(f"[一汽] {row['title']} 回填失败: {exc}"); failed += 1
            time.sleep(DETAIL_DELAY)
    finally:
        scraper.close(); db.close()
    return {"target": len(rows), "updated": ok, "no_body": no_body, "failed": failed}


def _cofco_rows(scraper):
    session = scraper._get_session()
    session.headers["Accept"] = "application/json, text/plain, */*"
    page = 1; result = {}
    while page <= 20:
        resp = session.post(scraper.url,
            json={"pageNo": page, "pageSize": 100, "businessName": "盐城"},
            headers={"Accept": "application/json, text/plain, */*"},
            timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        payload = resp.json(); data = payload.get("data") or {}; rows = data.get("list") or []
        for raw in rows:
            if raw.get("businessId"):
                result[str(raw["businessId"])] = raw
        if not rows or page * 100 >= data.get("totalCount", 0): break
        page += 1; time.sleep(REQUEST_DELAY)
    return result


def backfill_cofco(force=False):
    site = "中粮e采"; rows = _rows(site, force)
    scraper = CofcoEScraper(site, SITES[site]["api_url"], SITES[site]["base_url"])
    db = TenderDB(); ok = missing = failed = 0
    try:
        source = _cofco_rows(scraper)
        for row in rows:
            try:
                item = json.loads(row["raw_json"] or "{}")
                business_id = parse_qs(urlparse(row["url"] or "").fragment.partition("?")[2]).get("tradeId", [""])[0]
                raw = source.get(str(business_id))
                if not raw:
                    missing += 1; continue
                content, attachments, _ = scraper._fetch_trade_detail(raw)
                if not content and not attachments:
                    missing += 1; continue
                fields = scraper.extract_detail_fields(content, row["title"]) if content else {}
                item.update({k: v for k, v in fields.items() if v not in (None, "")})
                actual_url = (
                    f"{scraper.base_url}/web-portal/index.html#/trade-info-detail"
                    f"?id={raw.get('id')}&noticeType={raw.get('noticeType')}"
                    f"&publishStatus={raw.get('publishStatus')}"
                )
                item.update({"title": row["title"], "url": row["url"],
                             "date": row["publish_date"], "content": content,
                             "actual_detail_url": actual_url,
                             "detail_attachments": attachments,
                             "detail_api_id": str(raw.get("id", "")),
                             "detail_notice_type": raw.get("noticeType"),
                             "detail_publish_status": raw.get("publishStatus")})
                item["tenderer"] = raw.get("tendererName") or item.get("tenderer", "")
                _update(db, row, item); ok += 1
            except Exception as exc:
                print(f"[中粮] {row['title']} 回填失败: {exc}"); failed += 1
            time.sleep(DETAIL_DELAY)
    finally:
        scraper.close(); db.close()
    return {"target": len(rows), "updated": ok, "not_found": missing, "failed": failed}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--site", choices=["all", "faw", "cofco"], default="all")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    if args.site in ("all", "faw"): print("一汽:", backfill_faw(args.force))
    if args.site in ("all", "cofco"): print("中粮:", backfill_cofco(args.force))


if __name__ == "__main__": main()
