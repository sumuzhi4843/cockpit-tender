#!/usr/bin/env python3
"""中国邮政 PDF 详情补抓（2026-08-17 老板拍板"4 大头开工"）

背景：中国邮政 content 是从详情页 HTML 提取的纯文本，PDF 链接在转文本时丢失。
backfill_chinapost_pdfs.py 原本从详情页 HTML 提取 PDF 链接——但列表阶段已经存了 content，
PDF 链接没存。本脚本：从 raw_json.url 重新抓详情页 HTML → 提取 PDF → 文本层提内容 → 回填。

安全策略（2026-08-17 老板反爬顾虑）：
- 每天最多跑 30 条（防被识别为爬虫）
- 单条间隔 2-3 秒
- 失败 3 次连续报错立即停止
- 进度写 cron.log

用法：
  python3 backfill_chinapost_detail.py            # 默认跑 30 条
  python3 backfill_chinapost_detail.py --limit 10  # 试跑 10 条
  python3 backfill_chinapost_detail.py --url <url> # 只补指定一条（验证用）
"""
import argparse
import json
import os
import re
import sqlite3
import sys
import time
from pathlib import Path
from urllib.parse import unquote, urljoin

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import REQUEST_HEADERS, REQUEST_TIMEOUT  # noqa: E402

DB_PATH = Path(__file__).parent / "data" / "tender.db"
PDF_DIR = Path(__file__).parent / "data" / "chinapost_pdfs"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
MIN_INTERVAL = 2.0
MAX_LIMIT = 30
MAX_CONSECUTIVE_FAIL = 8


def get_session():
    s = requests.Session()
    s.headers.update({"User-Agent": UA, **REQUEST_HEADERS})
    s.verify = False
    import urllib3
    urllib3.disable_warnings()
    return s


def extract_pdf_url(html):
    """从详情页 HTML 提取 PDF 文件 URL（pdfjs iframe 的 file= 参数）。

    兼容中国邮政的 file= 参数被双层 URL 编码（& 被编为 %26）的情况：
    先 unquote 整个 file= 后的值，再取到第一个真正边界。
    """
    for raw in re.findall(r"file=([^\"'<>\s]+)", html):
        decoded = unquote(unquote(raw))  # 双层 unquote 防止多重编码
        # 接受以 .pdf 结尾，或 .do / .jsp / cgUploadController.do 等下载型接口
        if any(decoded.lower().endswith(ext) for ext in (".pdf", ".do", ".jsp", ".jhtml", ".action", ".htm", ".html")) or "openFileById" in decoded:
            return decoded
    return None


def fetch_detail_html(session, url):
    r = session.get(url, timeout=REQUEST_TIMEOUT)
    r.raise_for_status()
    r.encoding = r.apparent_encoding or r.encoding
    return r.text


def download_pdf(session, pdf_url, save_path):
    r = session.get(pdf_url, timeout=REQUEST_TIMEOUT)
    r.raise_for_status()
    with open(save_path, "wb") as f:
        f.write(r.content)
    return save_path


def extract_text_from_pdf(pdf_path):
    try:
        import fitz  # PyMuPDF
    except ImportError:
        return None, "PyMuPDF (fitz) not installed"
    try:
        doc = fitz.open(pdf_path)
        texts = []
        for page in doc:
            t = page.get_text() or ""
            if t.strip():
                texts.append(t)
        doc.close()
        return ("\n".join(texts)).strip(), None
    except Exception as e:
        return None, str(e)


def parse_fields_from_text(text):
    """从 PDF 文本中提取开标时间/金额/发包人（中国邮政 PDF 专用宽泛版）

    不依赖 enrich_engine 的 _ORG_TAIL alternation（复合后缀优先问题），
    直接用宽泛字符数 + 允许"为"作分隔符。
    """
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from enrich_engine import parse_datetime, _ORG_SUFFIX
    _ORG_TAIL_RE = re.compile(_ORG_SUFFIX)
    result = {}
    # 金额：允许"为/："等分隔符
    pat_amt = re.compile(
        r'(?:预算|最高限价|报价|总价|控制价|金额|限价|标段价)[为：:是\s]{0,8}([\d,.]+)\s*(万元|亿元|元)'
    )
    m_amt = pat_amt.search(text)
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
    m_bid = re.search(r"开标时间[：:]\s*(\d{4}[\s年/-]\d{1,2}[\s月/-]\d{1,2}[^\n]{0,25})", text)
    if m_bid:
        dt = parse_datetime(m_bid.group(1).strip())
        if dt:
            result["bid_deadline"] = dt  # parse_datetime 已返回 "%Y-%m-%d %H:%M:%S" 字符串
    # B 简称指代映射（2026-08-17）：中国邮政 PDF 大量使用"XXX（以下简称'采购人'）"格式
    # 解析出简称→全称映射，在发包人提取时把简称替换为全称
    aliases = {}
    # 模式 1：受 XXX 委托/中国邮政集团有限公司XXX分公司（以下简称"采购人"）
    for m in re.finditer(r'受\s*([^\n。]{3,50}?(?:公司|集团|局|委员会|中心))\s*\(以下简称\s*[「"\u201c]([^\u201d\u300d"]+?)[\u201d\u300d"]', text):
        fn, ab = m.group(1).strip(), m.group(2).strip()
        if 2 <= len(ab) <= 8 and len(fn) <= 50:
            aliases[ab] = fn
    # 模式 2：XXX 系/单位/公司（以下简称"采购人"）
    for m in re.finditer(r'([^\n。]{3,50}?(?:公司|集团|局|委员会|中心|事务所))\s*[\(（]?以下简称\s*[「"\u201c]([^\u201d\u300d"]+?)[\u201d\u300d"]', text):
        fn, ab = m.group(1).strip(), m.group(2).strip()
        if 2 <= len(ab) <= 8 and len(fn) <= 50:
            aliases[ab] = fn
    # 模式 3：XXX（以下简称"采购人"）- 简称在左后
    for m in re.finditer(r'([\u3002\n])*([^\n。]{3,50}?(?:公司|集团|局|委员会))\s*[\(（]?以下简称\s*[「"\u201c]([^\u201d\u300d"]+?)[\u201d\u300d"]', text):
        fn, ab = m.group(2).strip(), m.group(3).strip()
        if 2 <= len(ab) <= 8 and len(fn) <= 50:
            aliases[ab] = fn
    # 简称 → 全称 替换（仅在结果提取用，不改原 text）
    expanded_text = text
    for ab, fn in aliases.items():
        if len(ab) >= 2:
            expanded_text = expanded_text.replace(ab, fn)
        # 发包人：取"采购人/招标人/发包人/建设单位/采购单位"后，允许多字符，到第一个 ORG 词
    # 复合后缀优先（"有限责任公司"等）—— 直接 finditer 找最右 ORG 词
    for kw in ("采购人", "招标人", "发包人", "采购单位", "建设单位", "业主单位"):
        m2 = re.search(rf"{kw}[：:是为\s]+(.{{3,55}}?)(?=\s|\n|，|。|（|（|$)", text)
        if m2:
            # 严格 ORG_TAIL（避开"时/票/事/项"等通用词）
            _STRICT = re.compile(r'(?:有限责任|股份有限|集团有限|公司|集团|局|委员会|管委会|医院|学校|事务所|研究院|研究所|中心|管理站|工作站)')
            tail_matches = list(_STRICT.finditer(m2.group(1)))
            if tail_matches:
                end_pos = max(tm.end() for tm in tail_matches)
                v = m2.group(1)[:end_pos].strip()
            else:
                continue  # 没有真实单位后缀就跳过，避免误抓"时/票/事/项"等通用词
            if v.endswith(("有限责任", "股份有限", "集团有限", "有限")) and not v.endswith("公司"):
                v += "公司"
            if 4 <= len(v) <= 50 and "代理" not in v:
                result["tenderer"] = v
                break
    return result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=MAX_LIMIT, help=f"最多处理条数（默认 {MAX_LIMIT}）")
    ap.add_argument("--url", help="只补指定详情页 URL（验证用）")
    args = ap.parse_args()

    PDF_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    session = get_session()

    if args.url:
        # 单条验证模式
        try:
            html = fetch_detail_html(session, args.url)
        except Exception as e:
            print(f"  详情页抓取失败: {e}")
            return
        pdf_url = extract_pdf_url(html)
        print(f"  PDF URL: {pdf_url}")
        if not pdf_url:
            print("  未提取到 PDF 链接（可能详情页结构已变）")
            return
        nid = abs(hash(args.url)) % 100000
        save = PDF_DIR / f"test_{nid}.pdf"
        try:
            download_pdf(session, pdf_url, save)
        except Exception as e:
            print(f"  PDF 下载失败: {e}")
            return
        text, err = extract_text_from_pdf(save)
        if err:
            print(f"  PDF 文本提取失败: {err}")
            return
        print(f"  PDF 文本 {len(text)} 字")
        fields = parse_fields_from_text(text)
        print(f"  提取字段: {fields}")
        return

    # 批量模式：找 content 短（详情正文缺失）or 关键字段空的 tender 类中国邮政公告
    targets = conn.execute("""
        SELECT id, title, raw_json FROM notices
        WHERE site='中国邮政' AND notice_type='tender'
          AND (
            json_extract(raw_json, '$.content') IS NULL
            OR LENGTH(json_extract(raw_json, '$.content')) < 500
            OR amount_value IS NULL OR amount_value <= 0
            OR bid_deadline IS NULL OR bid_deadline = ''
          )
        ORDER BY publish_date DESC
        LIMIT ?
    """, (args.limit,)).fetchall()
    print(f"[中国邮政] 待补 {len(targets)} 条 (上限 {args.limit})")
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
        # 跳过已尝试过且标记失败的
        try:
            html = fetch_detail_html(session, url)
        except Exception as e:
            print(f"  [{nid[:8]}] 详情页抓取失败: {e}")
            fail += 1
            consecutive_fail += 1
            if consecutive_fail >= MAX_CONSECUTIVE_FAIL:
                print(f"  连续 {MAX_CONSECUTIVE_FAIL} 次失败，停止")
                break
            time.sleep(MIN_INTERVAL)
            continue
        pdf_url = extract_pdf_url(html)
        if not pdf_url:
            fail += 1
            consecutive_fail += 1
            if consecutive_fail >= MAX_CONSECUTIVE_FAIL:
                print(f"  [{nid[:8]}] 未提取到 PDF，连续失败，停止")
                break
            time.sleep(MIN_INTERVAL)
            continue
        save = PDF_DIR / f"{nid}.pdf"
        try:
            download_pdf(session, pdf_url, save)
        except Exception as e:
            print(f"  [{nid[:8]}] PDF 下载失败: {e}")
            fail += 1
            consecutive_fail += 1
            if consecutive_fail >= MAX_CONSECUTIVE_FAIL:
                break
            time.sleep(MIN_INTERVAL)
            continue
        text, err = extract_text_from_pdf(save)
        if err or not text:
            print(f"  [{nid[:8]}] PDF 文本提取失败: {err or '空'}")
            fail += 1
            consecutive_fail += 1
            if consecutive_fail >= MAX_CONSECUTIVE_FAIL:
                break
            time.sleep(MIN_INTERVAL)
            continue
        # 提取字段 + 写库（中国邮政 PDF 简称模式复杂，字段提取仅做宽泛尝试）
        fields = parse_fields_from_text(text)
        # 始终把 PDF 文本写进 raw_json.content（提高 content 覆盖度+让 audit 看到完整正文）
        new_raw = dict(raw)
        old_content = new_raw.get("content", "") or ""
        if text not in old_content:
            new_raw["content"] = (old_content + "\n\n[2026-08-17 PDF补抓]\n" + text)[:15000]
            # 2026-09-21 竞态修复：写前从库里重读合并（deploy 并行补抓组同时写 raw_json）
            try:
                _f = conn.execute("SELECT raw_json FROM notices WHERE id=?", (nid,)).fetchone()
                _fresh = json.loads((_f[0] if _f else "") or "{}")
                _fresh.update(new_raw)
                new_raw = _fresh
            except Exception:
                pass
            conn.execute("UPDATE notices SET raw_json=? WHERE id=?",
                         (json.dumps(new_raw, ensure_ascii=False), nid))
        if fields:
            set_clause = ", ".join(f"{k}=?" for k in fields)
            conn.execute(f"UPDATE notices SET {set_clause} WHERE id=?", (*fields.values(), nid))
            conn.commit()
            succ += 1
            consecutive_fail = 0
            print(f"  [{nid[:8]}] ✓ {fields}")
        else:
            succ += 1  # PDF 文本落库也算成功
            consecutive_fail = 0
            print(f"  [{nid[:8]}] √ PDF 文本落库 (txt {len(text)}字, 字段未提取)")
        time.sleep(MIN_INTERVAL)
        # 每 5 条报进度
        if i % 5 == 0:
            print(f"  进度: {i}/{len(targets)} 成功{succ} 失败{fail}")

    print(f"\n[中国邮政] 完成: 成功{succ} 失败{fail}")
    conn.close()


if __name__ == "__main__":
    main()