# -*- coding: utf-8 -*-
"""国投集团存量回填（2026-08-07）

背景：原抓取器详情页 URL 拼错（只传 guid=，实际需 gcGuid+ggGuid 双参数），
导致 31 条存量公告详情全部 403，正文是错误 JSON，发包人/金额/开标时间全空。

本脚本：
1. POST 翻列表页收集 ggGuid→gcGuid 映射（最多 10 页）
2. 存量公告按 ggGuid 匹配，URL 修正为双参数格式（id 同步重算，保证后续去重）
3. 抓详情页正文，enrich_detail 提取字段，只补空字段（不覆盖已有值）
"""
import hashlib
import json
import re
import sys
import time

sys.path.insert(0, ".")
import requests
from config import REQUEST_HEADERS
from enrich_engine import enrich_detail
from site_names import stable_site_name
from store_db import TenderDB

BASE = "https://www.sdicc.com.cn"
LIST_URL = BASE + "/cgxx/ggList"


def collect_pairs(max_pages=10):
    """翻列表页收集 (ggGuid, gcGuid) 映射"""
    s = requests.Session()
    s.headers.update(REQUEST_HEADERS)
    pairs = {}
    for page in range(1, max_pages + 1):
        try:
            r = s.post(LIST_URL, data={"currentPage": str(page)}, timeout=20)
            found = re.findall(r"urlChange\('([^']+)'\s*,\s*'([^']+)'", r.text)
            if not found:
                break
            new = 0
            for gg, gc in found:
                if gg not in pairs:
                    pairs[gg] = gc
                    new += 1
            print(f"  列表第 {page} 页: {len(found)} 条（新增 {new}）")
            if new == 0:
                break  # 翻页已无新内容
            time.sleep(1)
        except Exception as e:
            print(f"  列表第 {page} 页失败: {e}")
            break
    return pairs


def main():
    db = TenderDB()
    conn = db._conn

    print("① 收集列表页 ggGuid→gcGuid 映射…")
    pairs = collect_pairs()
    print(f"  共收集 {len(pairs)} 个映射")

    rows = conn.execute(
        "SELECT id, title, url, publish_date, notice_type, tenderer, amount, bid_deadline, raw_json "
        "FROM notices WHERE site='国投集团'").fetchall()
    print(f"\n② 存量国投公告 {len(rows)} 条，开始修正…")

    # 阶段 A：先修 URL（不请求详情页，限流期也能完成）
    url_map = {}  # old_id -> (new_id, new_url)
    for r in rows:
        m = re.search(r'(?:ggGuid|guid)=([a-f0-9-]+)', r["url"])
        if not m or "gcGuid=" in r["url"]:
            continue
        gc = pairs.get(m.group(1))
        if not gc:
            continue
        new_url = f"{BASE}/cgxx/ggDetail?gcGuid={gc}&ggGuid={m.group(1)}"
        raw_key = f"{stable_site_name('国投集团')}|{new_url}|{r['title']}|{r['publish_date'] or ''}"
        url_map[r["id"]] = (hashlib.md5(raw_key.encode("utf-8")).hexdigest(), new_url)
    for old_id, (new_id, new_url) in url_map.items():
        conn.execute("UPDATE notices SET id=?, url=? WHERE id=?", (new_id, new_url, old_id))
    conn.commit()
    print(f"  URL 修正 {len(url_map)} 条")

    # 阶段 B：抓详情正文 + 补字段（间隔 2s 防限流）
    s = requests.Session()
    s.headers.update(REQUEST_HEADERS)

    stats = {"content_ok": 0, "tenderer": 0, "amount": 0,
             "deadline": 0, "no_match": 0, "detail_fail": 0}

    # 重新读取（URL 已更新）
    rows = conn.execute(
        "SELECT id, title, url, publish_date, notice_type, tenderer, amount, bid_deadline, raw_json "
        "FROM notices WHERE site='国投集团'").fetchall()

    for r in rows:
        try:
            raw = json.loads(r["raw_json"] or "{}")
        except Exception:
            raw = {}
        if raw.get("url_fixed_20260807"):
            continue  # 已补过详情
        if "gcGuid=" not in r["url"]:
            stats["no_match"] += 1
            print(f"  [未匹配] {r['title'][:30]}")
            continue

        time.sleep(2)
        try:
            resp = s.get(r["url"], timeout=25)
            resp.encoding = resp.apparent_encoding or resp.encoding
            html = resp.text if resp.status_code == 200 else ""
        except Exception:
            html = ""
        if not html or "违反安全规则" in html:
            stats["detail_fail"] += 1
            print(f"  [详情失败] {r['title'][:30]}")
            if stats["detail_fail"] >= 3 and stats["content_ok"] == 0:
                print("  连续失败，疑似限流，本轮停止（URL 已修，下轮重试详情）")
                break
            continue

        # 提正文（去标签）
        text = re.sub(r"<script[\s\S]*?</script>", " ", html)
        text = re.sub(r"<style[\s\S]*?</style>", " ", text)
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"\s+", " ", text).strip()
        stats["content_ok"] += 1

        # enrich 提取
        fields = enrich_detail(text, r["notice_type"] or "tender")

        raw["content"] = text[:8000]
        raw["url_fixed_20260807"] = True

        # 只补空字段
        tenderer = r["tenderer"] or (fields.get("tenderer") or "")
        amount = r["amount"] or (fields.get("amount") or "")
        bid_deadline = r["bid_deadline"] or (fields.get("bid_deadline") or "")
        if fields.get("tenderer") and not r["tenderer"]:
            stats["tenderer"] += 1
        if fields.get("amount") and not r["amount"]:
            stats["amount"] += 1
        if fields.get("bid_deadline") and not r["bid_deadline"]:
            stats["deadline"] += 1

        amount_value = None
        if amount and not r["amount"]:
            amount_value = fields.get("amount_value")
        elif amount:
            amount_value = conn.execute(
                "SELECT amount_value FROM notices WHERE id=?", (r["id"],)).fetchone()["amount_value"]

        # 2026-09-21 竞态修复：写前从库里重读合并（deploy 并行补抓组同时写 raw_json）
        try:
            _f = conn.execute("SELECT raw_json FROM notices WHERE id=?", (r["id"],)).fetchone()
            _fresh = json.loads((_f[0] if _f else "") or "{}")
            _fresh.update(raw)
            raw = _fresh
        except Exception:
            pass
        conn.execute("""
            UPDATE notices SET tenderer=?, amount=?, amount_value=?,
                               bid_deadline=?, raw_json=? WHERE id=?
        """, (tenderer or None, amount or None, amount_value,
              bid_deadline or None, json.dumps(raw, ensure_ascii=False), r["id"]))
        conn.commit()
        print(f"  [OK] {r['title'][:28]} | 发包人:{(tenderer or '空')[:14]} | 金额:{amount or '空'} | 开标:{bid_deadline[:10] or '空'}")

    print(f"\n③ 完成: 正文OK {stats['content_ok']} | "
          f"补发包人 {stats['tenderer']} | 补金额 {stats['amount']} | 补开标 {stats['deadline']} | "
          f"未匹配 {stats['no_match']} | 详情失败 {stats['detail_fail']}")
    db.close()


if __name__ == "__main__":
    main()
