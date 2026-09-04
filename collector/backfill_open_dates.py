# -*- coding: utf-8 -*-
"""缺开标时间公告自动补抓（2026-08-06）

deploy.sh 定时任务调用：run.py 抓取后，检测所有"开标时间待确认"公告，
按 PDF 识别等级自动补抓：

  L1 库内已有正文       → 富化引擎直接从 content 提取
  L2 详情页 HTML 有正文 → 抓详情页正文再提取
  L3 PDF 有文本层       → 下载 PDF 提文本层再提取（0 token）
  L4 扫描件 PDF         → Tesseract 中文 OCR → 提取（0 token）
                         → 低置信/无法确认 → MiniMax-M3 校正

用法：
    python3 backfill_open_dates.py            # 增量：近 N 天缺时间的
    python3 backfill_open_dates.py --days 30  # 近 30 天
    python3 backfill_open_dates.py --all      # 全部存量
    python3 backfill_open_dates.py --site 江苏招标采购服务平台
"""
import argparse
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
from urllib.parse import unquote

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import REQUEST_HEADERS, REQUEST_TIMEOUT  # noqa: E402
from enrich_engine import enrich_detail  # noqa: E402

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "tender.db")
PDF_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "auto_pdfs")

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"

# 每站详情页正文选择器（L2）
DETAIL_SELECTORS = {
    "盐城市政府采购网": ["div.content", "div.article", "#zoom", ".TRS_Editor"],
    "盐城市人民政府网": ["div.content", "div.article", "#zoom", ".TRS_Editor"],
    "中国政府采购网": ["div.vF_detail_content", ".vF_detail"],
    "盐城市公共资源交易网": ["div.content", ".detail-body"],
    "开发区公共资源交易网": [".detail", ".content"],
    "城南新区公共资源交易网": [".detail", ".content"],
    "江苏世纪新城": [".detail", ".content", ".article"],
    "盐城市东方集团": [".detail", ".content", ".article"],
    "经开城发集团": [".detail", ".content", ".article"],
    "盐城市大数据集团": [".detail", ".content", ".article"],
    "盐城市都市建设投资集团有限公司": [".detail", ".content", ".article"],
    "悦达阳光": [".detail", ".content"],
    "江苏农华": [".detail", ".content"],
    "国投集团": [".detail", ".content"],
    "三峡集团": [".detail", ".content"],
}


# 不含开标时间的公告子类型（候选公示/结果/更正/终止/意向等）直接跳过
SKIP_SUBTYPES = {
    'award_candidate', 'award_final', 'correction', 'terminated', 'suspended',
    'failed', 'contract', 'intention', 'unknown',
}


def get_missing(conn, days=0, site=None):
    # 只处理招标公告类（tender_notice/tender_reopen），候选/结果/更正/终止等无开标时间语义
    sub_ph = ",".join("?" * len(SKIP_SUBTYPES))
    base = (f"site NOT IN ('江苏政府采购网','中国邮政')"
            f" AND (bid_deadline IS NULL OR bid_deadline='')"
            f" AND (notice_subtype IS NULL OR notice_subtype NOT IN ({sub_ph}))")
    if site:
        where = f"site=? AND " + base
        params = [site] + sorted(SKIP_SUBTYPES)
    else:
        where = base
        params = sorted(SKIP_SUBTYPES)
    if days > 0 and not site:
        where += f" AND publish_date >= date('now','localtime','-{days} days')"
    return conn.execute(f"SELECT * FROM notices WHERE {where}", params).fetchall()


def fetch_html(url, referer=None):
    h = dict(REQUEST_HEADERS, **({"Referer": referer} if referer else {}))
    try:
        r = requests.get(url, headers=h, timeout=REQUEST_TIMEOUT, verify=False)
        return r.text if r.status_code == 200 else ""
    except Exception:
        return ""


def extract_text_from_html(html, selectors):
    """按选择器列表提取正文文本。"""
    from bs4 import BeautifulSoup
    if not html:
        return ""
    soup = BeautifulSoup(html, "lxml")
    best = ""
    for sel in selectors:
        try:
            for el in soup.select(sel):
                t = el.get_text(separator=" ", strip=True)
                if len(t) > len(best):
                    best = t
        except Exception:
            continue
    if not best:
        body = soup.find("body")
        if body:
            best = body.get_text(separator=" ", strip=True)
    return best[:8000]


def pdf_to_text(path):
    """PDF 文本层提取。返回 (text, is_scanned)。"""
    try:
        import fitz
        doc = fitz.open(path)
        parts = []
        for pg in doc:
            parts.append(pg.get_text() or "")
        text = "\n".join(parts)
        return text, len(text.strip()) < 100
    except Exception:
        return "", True


def ocr_pdf(path):
    """扫描件 OCR。返回识别文本。"""
    try:
        import fitz
        doc = fitz.open(path)
        full = []
        for i, page in enumerate(doc, 1):
            pix = page.get_pixmap(matrix=fitz.Matrix(2.5, 2.5), alpha=False)
            img = f"/tmp/auto_ocr_{os.getpid()}_{i}.png"
            pix.save(img)
            proc = subprocess.run(
                ["tesseract", img, "stdout", "-l", "chi_sim+eng", "--psm", "6",
                 "-c", "preserve_interword_spaces=1"],
                capture_output=True, text=True, timeout=180)
            full.append(proc.stdout)
            os.unlink(img)
        return "\n".join(full)
    except Exception:
        return ""


def extract_open_time(text):
    """从文本提取开标时间。返回 (bid_deadline, deadline) 或 (None, None)。"""
    if not text:
        return None, None
    bid = deadline = None
    _dt = (r"(20\d{2})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日"
           r"(?:\s*(?:上午|下午|中午|晚上)?\s*(\d{1,2})\s*[：:]\s*(\d{2})\s*(?::\s*(\d{2}))?)?")
    def _fmt(m):
        hh = int(m.group(4) or 0)
        if re.search(r'下午|晚上|中午', m.group(0) or '') and hh < 12:
            hh += 12
        ss = m.group(6) or '00'
        if m.group(4):
            return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d} {hh:02d}:{m.group(5)}:{ss}"
        return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    for m in re.finditer(r"开标\s*时间?\s*[：:]?\s*" + _dt, text):
        bid = _fmt(m)
        break
    if not bid:
        sec = re.search(r"(?:六|八)[、.]?\s*开标[\s\S]{0,300}", text)
        if sec:
            m = re.search(_dt, sec.group(0))
            if m:
                bid = _fmt(m)
    for m in re.finditer(r"(?:递交)?(?:文件)?(?:的)?(?:截止|递交)\s*时间\s*(?:均为|为)?\s*[：:]?\s*" + _dt, text):
        deadline = _fmt(m)
        break
    return bid, deadline


def process_row(conn, row):
    """处理单条公告，返回 (bid, deadline, level, msg)。"""
    nid = row["id"]
    site = row["site"]
    url = row["url"] or ""
    title = row["title"] or ""
    raw = {}
    try:
        raw = json.loads(row["raw_json"] or "{}")
    except Exception:
        pass

    # 已尝试过（补抓标记）则跳过，避免每轮重复撞墙
    if raw.get("backfill_tried"):
        return None, None, "skip", "已尝试过"

    # L1：库内已有正文
    content = raw.get("content") or ""
    if len(content or "") > 100:
        fields = enrich_detail(content, "tender")
        bid = fields.get("bid_deadline") or fields.get("deadline") or ""
        if bid:
            return bid, bid, "L1", "库内正文提取"

    # L2：详情页 HTML 正文（仅静态 HTML 站）
    if site in DETAIL_SELECTORS and url:
        html = fetch_html(url)
        text = extract_text_from_html(html, DETAIL_SELECTORS[site])
        if len(text) > 100:
            fields = enrich_detail(text, "tender")
            bid = fields.get("bid_deadline") or ""
            if bid:
                return bid, bid, "L2", "详情页正文提取"
        # L3/L4：详情页内嵌 PDF
        pdf_url = extract_pdf_from_html(html)
        if pdf_url:
            bid, deadline = handle_pdf(pdf_url, nid)
            if bid:
                return bid, deadline or bid, "L3/L4", "PDF处理"

    # 江苏招标平台：走详情 API 拿 PDF
    if site == "江苏招标采购服务平台":
        sid = raw.get("source_id", "")
        if sid:
            bid, deadline = jszbcg_pdf_flow(sid, nid)
            if bid:
                return bid, deadline or bid, "L3/L4", "jszbcg PDF处理"

    return None, None, "?", "未找到来源"


def extract_pdf_from_html(html):
    """从详情页 HTML 提取 PDF URL（pdfjs iframe file= 参数）。"""
    if not html:
        return None
    m = re.findall(r"file=([^\"']+)", html)
    if not m:
        return None
    return unquote(m[0])


def handle_pdf(pdf_url, nid):
    """下载 PDF → 文本层 → 扫描件 OCR → 提取时间。"""
    os.makedirs(PDF_DIR, exist_ok=True)
    pdf_path = os.path.join(PDF_DIR, f"{nid}.pdf")
    try:
        r = requests.get(pdf_url, headers={"User-Agent": UA, "Referer": "https://cg.11185.cn/"},
                         timeout=60, verify=False)
        if r.status_code != 200 or r.content[:4] != b"%PDF":
            return None, None
        with open(pdf_path, "wb") as f:
            f.write(r.content)
    except Exception:
        return None, None

    text, scanned = pdf_to_text(pdf_path)
    if scanned:
        text = ocr_pdf(pdf_path)
    if not text.strip():
        return None, None
    bid, deadline = extract_open_time(text)
    return bid, deadline


def jszbcg_pdf_flow(sid, nid):
    """江苏招标平台：详情 API → signedPdfUrl → PDF 处理。"""
    h = dict(REQUEST_HEADERS, Origin="https://www.jszbcg.com", Referer="https://www.jszbcg.com/")
    try:
        api = f"https://api.jszbtb.com/DataGatewayApi/PublishBulletin/BulletinType/1/ID/{sid}"
        d = (requests.get(api, headers=h, timeout=20).json().get("data") or {})
        pdf_url = d.get("signedPdfUrl") or ""
        if not pdf_url:
            return None, None
        return handle_pdf(pdf_url, nid)
    except Exception:
        return None, None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=30, help="近 N 天（默认 30）")
    ap.add_argument("--all", action="store_true", help="全部存量")
    ap.add_argument("--site", default=None, help="只处理指定站点")
    args = ap.parse_args()

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = get_missing(conn, days=0 if args.all else args.days, site=args.site)
    print(f"待补开标时间: {len(rows)} 条")

    updated = 0
    for i, row in enumerate(rows, 1):
        try:
            bid, deadline, level, msg = process_row(conn, row)
            # 写补抓尝试标记（无论成败），避免每轮重复撞墙
            try:
                raw = json.loads(row["raw_json"] or "{}")
            except Exception:
                raw = {}
            raw["backfill_tried"] = True
            raw["backfill_time"] = time.strftime("%Y-%m-%d %H:%M:%S")
            if bid:
                conn.execute("""UPDATE notices SET bid_deadline=COALESCE(?, bid_deadline),
                                deadline=COALESCE(?, deadline), raw_json=? WHERE id=?""",
                             (bid, deadline, json.dumps(raw, ensure_ascii=False), row["id"]))
                conn.commit()
                updated += 1
                print(f"  ✅ [{level}] {row['title'][:32]} | {bid} ({msg})")
            else:
                conn.execute("UPDATE notices SET raw_json=? WHERE id=?",
                             (json.dumps(raw, ensure_ascii=False), row["id"]))
                conn.commit()
                print(f"  ⏭ [{level}] {row['title'][:32]} | {msg}")
        except Exception as e:
            print(f"  ⚠️ {row['title'][:32]} | {str(e)[:80]}")
        if i % 10 == 0:
            time.sleep(1)  # 温和节流

    print(f"\n完成，更新 {updated}/{len(rows)} 条")
    conn.close()


if __name__ == "__main__":
    main()
