#!/usr/bin/env python3
"""江苏招标采购服务平台详情补抓（2026-08-17 4 大头之一）

背景：jszbtb.com API 列表只返回 content 74-83 字（公告摘要），详情页才能拿到完整正文+发包人。
库内 749 条 tender 类公告，content 都很短 + 大量金额/发包人/开标字段空。

安全策略（2026-08-17 老板反爬顾虑）：
- 每天最多 30 条（防被识别）
- 单条间隔 3-5 秒
- 失败 3 次连续报错立即停止
- 进度写 cron.log

API（实测）：列表 API 已返回 content；详情页用 URL 单独抓，提取"招标人/采购人/金额/开标时间"等。
"""
import argparse
import json
import os
import re
import sqlite3
import sys
import time
from pathlib import Path
from urllib.parse import urljoin

import requests
import urllib3
urllib3.disable_warnings()

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import REQUEST_HEADERS, REQUEST_TIMEOUT  # noqa: E402

DB_PATH = Path(__file__).parent / "data" / "tender.db"
MIN_INTERVAL = 4.0  # 4 秒（江苏招标平台反爬中等）
MAX_LIMIT = 30
MAX_CONSECUTIVE_FAIL = 3
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"


def get_session():
    s = requests.Session()
    s.headers.update({"User-Agent": UA, **REQUEST_HEADERS})
    s.verify = False
    return s


def fetch_detail(session, url):
    r = session.get(url, timeout=REQUEST_TIMEOUT)
    r.raise_for_status()
    r.encoding = r.apparent_encoding or r.encoding
    return r.text


def parse_detail_html(html):
    """从详情页 HTML 提取正文 + 字段"""
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, 'lxml')
    # 正文：移除 script/style 后取 text
    for s in soup.find_all(['script', 'style']):
        s.decompose()
    content = soup.get_text(' ', strip=True)[:15000]
    result = {"content": content}
    # 金额
    pat_amt = re.compile(
        r'(?:预算金额|项目预算|控制价|最高限价|标底|金额|限价|采购预算)[为：:是\s]{0,8}([\d,.]+)\s*(万元|亿元|元)'
    )
    m_amt = pat_amt.search(content)
    if m_amt:
        try:
            num = float(m_amt.group(1).replace(",", ""))
            unit = m_amt.group(2)
            if unit == "万元": num *= 10000
            elif unit == "亿元": num *= 1e8
            result["amount_value"] = num
            result["amount"] = f"{num/10000:.2f}万元" if num >= 10000 else f"{num:.2f}元"
        except (ValueError, TypeError):
            pass
    # 开标时间
    from enrich_engine import parse_datetime
    m_bid = re.search(r"开标时间[：:]\s*(\d{4}[\s年/-]\d{1,2}[\s月/-]\d{1,2}[^\n]{0,25})", content)
    if m_bid:
        dt = parse_datetime(m_bid.group(1).strip())
        if dt:
            result["bid_deadline"] = dt
    # 发包人（严格 ORG_TAIL）
    _STRICT = re.compile(
        r'(?:有限责任|股份有限|集团有限|公司|集团|局|委员会|管委会|医院|学校|事务所|研究院|研究所|中心|管理站|工作站)'
    )
    for kw in ("采购人", "招标人", "发包人", "采购单位", "建设单位", "业主单位"):
        m2 = re.search(rf"{kw}[：:是为\s]+(.{{3,55}}?)(?=\s|\n|，|。|（|（|$)", content)
        if m2:
            tail_matches = list(_STRICT.finditer(m2.group(1)))
            if not tail_matches:
                continue
            end_pos = max(tm.end() for tm in tail_matches)
            v = m2.group(1)[:end_pos].strip()
            if v.endswith(("有限责任", "股份有限", "集团有限", "有限")) and not v.endswith("公司"):
                v += "公司"
            if 4 <= len(v) <= 50 and "代理" not in v:
                result["tenderer"] = v
                break
    return result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=MAX_LIMIT)
    ap.add_argument("--url", help="单 URL 验证")
    args = ap.parse_args()

    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    session = get_session()

    if args.url:
        try:
            html = fetch_detail(session, args.url)
        except Exception as e:
            print(f"  详情页抓取失败: {e}")
            return
        fields = parse_detail_html(html)
        print(f"  提取字段: {fields}")
        return

    targets = conn.execute("""
        SELECT id, title, raw_json FROM notices
        WHERE site='江苏招标采购服务平台' AND notice_type='tender'
          AND (
            json_extract(raw_json, '$.content') IS NULL
            OR LENGTH(json_extract(raw_json, '$.content')) < 200
            OR amount_value IS NULL OR amount_value <= 0
            OR bid_deadline IS NULL OR bid_deadline=''
            OR tenderer IS NULL OR tenderer=''
          )
        ORDER BY publish_date DESC
        LIMIT ?
    """, (args.limit,)).fetchall()
    print(f"[江苏招标] 待补 {len(targets)} 条 (上限 {args.limit})")
    if not targets:
        return

    succ = fail = 0
    consecutive_fail = 0
    for i, r in enumerate(targets, 1):
        nid = r["id"]
        raw = json.loads(r["raw_json"] or "{}")
        url = raw.get("url", "")
        if not url:
            fail += 1
            continue
        try:
            html = fetch_detail(session, url)
        except Exception as e:
            print(f"  [{nid[:8]}] 详情页失败: {e}")
            fail += 1
            consecutive_fail += 1
            if consecutive_fail >= MAX_CONSECUTIVE_FAIL:
                print(f"  连续失败停止")
                break
            time.sleep(MIN_INTERVAL)
            continue
        consecutive_fail = 0
        fields = parse_detail_html(html)
        if fields.get("content"):
            new_raw = dict(raw)
            old_content = new_raw.get("content", "") or ""
            if fields["content"] not in old_content:
                new_raw["content"] = (old_content + "\n\n[2026-08-17 详情补抓]\n" + fields["content"])[:15000]
            conn.execute("UPDATE notices SET raw_json=? WHERE id=?", (json.dumps(new_raw, ensure_ascii=False), nid))
        # 字段
        set_parts = []
        set_vals = []
        for k in ("amount_value", "amount", "bid_deadline", "tenderer"):
            if fields.get(k):
                set_parts.append(f"{k}=?")
                set_vals.append(fields[k])
        if set_parts:
            set_vals.append(nid)
            conn.execute(f"UPDATE notices SET {', '.join(set_parts)} WHERE id=?", set_vals)
        conn.commit()
        succ += 1
        extracted = {k: fields[k] for k in ("amount", "bid_deadline", "tenderer") if fields.get(k)}
        print(f"  [{nid[:8]}] {extracted if extracted else 'PDF补抓 (text落库)'}")
        time.sleep(MIN_INTERVAL)
        if i % 10 == 0:
            print(f"  进度: {i}/{len(targets)} 成功{succ} 失败{fail}")

    print(f"\n[江苏招标] 完成: 成功{succ} 失败{fail}")
    conn.close()


if __name__ == "__main__":
    main()