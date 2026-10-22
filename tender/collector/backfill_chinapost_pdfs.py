# -*- coding: utf-8 -*-
"""中国邮政 PDF 补抓工具（2026-08-06）

背景：中国邮政详情页正文为 PDF 内嵌（pdfjs viewer），HTML 无文字，
导致开标时间等字段缺失、页面显示"时间待确认"。

本工具：下载 PDF → 文本层提取（无 OCR）→ 富化引擎提取开标/截止时间
→ 回填 SQLite。用法：

    python3 backfill_chinapost_pdfs.py              # 补抓缺开标时间的公告
    python3 backfill_chinapost_pdfs.py --url <url>  # 只补指定一条（验证用）
    python3 backfill_chinapost_pdfs.py --all        # 全部存量强制刷新
"""
import argparse
import json
import os
import re
import sqlite3
import sys
import time
from urllib.parse import unquote

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import REQUEST_HEADERS, REQUEST_TIMEOUT
from enrich_engine import parse_datetime, _extract_budget  # noqa: E402

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "tender.db")
PDF_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "chinapost_pdfs")

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"


def extract_pdf_url(html):
    """从详情页 HTML 提取 PDF 文件 URL（pdfjs iframe 的 file= 参数）。"""
    m = re.findall(r"file=([^\"']+)", html)
    if not m:
        return None
    raw = m[0]
    # 页面里是 openFileById%26id%3D...，解码为 openFileById&id=...
    decoded = unquote(raw)
    return decoded


def download_pdf(url, out_path):
    s = requests.Session()
    s.verify = False
    s.headers.update({
        "User-Agent": UA,
        "Referer": "https://cg.11185.cn/",
        "Accept": "application/pdf,*/*",
    })
    r = s.get(url, timeout=REQUEST_TIMEOUT)
    if r.status_code != 200 or r.content[:4] != b"%PDF":
        return False
    with open(out_path, "wb") as f:
        f.write(r.content)
    return True


def pdf_text(path):
    """提取 PDF 文本层（无 OCR）。返回文本或 None。"""
    try:
        import pdfplumber
        with pdfplumber.open(path) as pdf:
            parts = []
            for pg in pdf.pages:
                t = pg.extract_text() or ""
                parts.append(t)
            text = "\n".join(parts)
            return text if text.strip() else None
    except Exception:
        return None


# 预算提取（2026-08-07 扩展）：数字前 40 字窗口内出现预算/限价类关键词才接受；
# 拒绝分包金额（数字前紧跟"标包X/包X"）和单价（元/张、元/月…）。
_BUDGET_NUM_RE = re.compile(
    r"([\d][\d,]*(?:\.\d+)?)\s*(万元|亿元|元)")
_BUDGET_CONTEXT_KW = re.compile(r"预算|限价|控制价|概算|估算|总投资|标底")
_BREAKDOWN_PREFIX_RE = re.compile(r"(?:标包|包|第)[一二三四五六七八九十\d]{1,3}(?:标段)?\s*[：:]?\s*$")
_UNIT_PRICE_RE = re.compile(r"元\s*[/每]\s*[张月年天次人席位项]")


def parse_budget(text):
    """从 PDF 文本提取项目预算。返回 (amount_str, amount_value, amount_unit) 或 None。

    严格策略（宁缺毋滥）：
    - 数字前 40 字窗口必须含 预算/限价/控制价/概算/估算/总投资/标底
    - 数字紧跟在"标包X/包X/第X标段："后 → 拒提（分包金额）
    - 单位后是"元/张、元/月"类单价 → 拒提
    - 数值范围 100 元 ~ 500 亿元
    """
    if not text:
        return None
    # pdfplumber 提取的文本常带空格，压缩后匹配（保留中文字符间距信息不重要）
    t = re.sub(r"[ \t\u3000]+", "", text)
    for m in _BUDGET_NUM_RE.finditer(t):
        start = m.start()
        pre = t[max(0, start - 40):start]
        post = t[m.end():m.end() + 6]
        # 单价拒提：元/张、元/月…
        if m.group(2) == "元" and _UNIT_PRICE_RE.search(m.group(0) + post):
            continue
        # 分包拒提：数字前紧跟 标包X/包X/第X标段
        if _BREAKDOWN_PREFIX_RE.search(pre):
            continue
        # 上下文必须含预算类关键词
        if not _BUDGET_CONTEXT_KW.search(pre):
            continue
        try:
            value = float(m.group(1).replace(",", ""))
        except ValueError:
            continue
        unit = m.group(2)
        if unit == "万元":
            value *= 1e4
        elif unit == "亿元":
            value *= 1e8
        if not (100 <= value <= 5e10):
            continue
        # 格式化
        if value >= 1e8 and value % 1e8 == 0:
            amount_str = f"{value / 1e8:.0f}亿元"
        elif value >= 1e4:
            wan = value / 1e4
            amount_str = (f"{wan:.2f}".rstrip('0').rstrip('.')) + "万元"
        else:
            amount_str = f"{value:.0f}元"
        return amount_str, value, "元"
    return None


def parse_times(text):
    """从 PDF 文本提取开标时间 / 截止时间。返回 (bid_deadline, deadline)。
    兼容格式：2026年08月18日14:30:00 / 2026 年 08 月 26 日上午 11:00:00
    / 2026年8月10日上午09:30 / 09：30（全角冒号）/ 开标时间2026年08月18日14:30
    """
    bid = deadline = None
    if not text:
        return bid, deadline
    # 通用时间正则：允许年月日间有空格、可带 上午/下午、冒号半角/全角、秒可缺
    _dt = (r"(20\d{2})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日"
           r"(?:\s*(?:上午|下午|中午|晚上)?\s*(\d{1,2})\s*[：:]\s*(\d{2})\s*(?::\s*(\d{2}))?)?")
    def _fmt(m):
        hh = int(m.group(4) or 0)
        # 下午/晚上 12 小时制 → 24 小时制
        marker = (m.group(0) or '')
        if re.search(r'下午|晚上|中午', marker) and hh < 12:
            hh += 12
        ss = m.group(6) or '00'
        if m.group(4):
            return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d} {hh:02d}:{m.group(5)}:{ss}"
        return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    # 开标时间：优先找「开标时间」附近
    for m in re.finditer(r"开标\s*时间?\s*[：:]?\s*" + _dt, text):
        bid = _fmt(m)
        break
    if not bid:
        # 兜底：开标章节内（八、开标）找第一个日期
        sec = re.search(r"八、?\s*开标[\s\S]{0,400}", text)
        if sec:
            m = re.search(_dt, sec.group(0))
            if m:
                bid = _fmt(m)
    # 截止时间：投标文件递交截止（兼容“截止时间”“递交的截止时间”“递交截止时间”）
    for m in re.finditer(r"(?:递交)?(?:文件)?(?:的)?(?:截止|递交)\s*时间\s*(?:均为|为)?\s*[：:]?\s*" + _dt, text):
        deadline = _fmt(m)
        break
    if not deadline:
        # 兜底：响应文件递交截止
        for m in re.finditer(r"响应文件递交\s*的?\s*截止时间\s*(?:均为|为)?\s*[：:]?\s*" + _dt, text):
            deadline = _fmt(m)
            break
    # 兜底：解密截止时间（无独立开标时间时近似）
    if not bid:
        for m in re.finditer(r"解密截止时间\s*(?:为|：|:)?\s*" + _dt, text):
            bid = _fmt(m)
            break
    return bid, deadline


def process_notice(row, force=False):
    """处理单条公告：下载 PDF → 提取 → 返回 (bid_deadline, deadline, budget_tuple, msg)。
    budget_tuple: (amount_str, amount_value, amount_unit) 或 None（2026-08-07 扩展）。
    缓存复用：PDF 已下载过则直接读缓存（不再请求网络）。"""
    nid = row["id"]
    url = row["url"]
    bid_old = row["bid_deadline"] or ""
    amt_old = row["amount"] or ""
    need_time = not bid_old or force
    need_amt = not amt_old or force
    if not need_time and not need_amt:
        return None, None, None, "字段已齐，跳过"

    os.makedirs(PDF_DIR, exist_ok=True)
    pdf_path = os.path.join(PDF_DIR, f"{nid}.pdf")

    # 缓存复用：已有 PDF 直接用（2026-08-07）
    if not os.path.exists(pdf_path):
        # 1. 抓详情页
        try:
            r = requests.get(url, headers={"User-Agent": UA, "Referer": "https://cg.11185.cn/"},
                             timeout=REQUEST_TIMEOUT, verify=False)
            html = r.text
        except Exception as e:
            return None, None, None, f"详情页失败: {str(e)[:60]}"

        # 2. 找 PDF
        pdf_url = extract_pdf_url(html)
        if not pdf_url:
            return None, None, None, "无 PDF 附件"
        if not download_pdf(pdf_url, pdf_path):
            return None, None, None, "PDF 下载失败"

    # 3. 提取文本
    text = pdf_text(pdf_path)
    if not text:
        return None, None, None, "PDF 无文本层（需 OCR，跳过）"

    # 4. 解析时间 + 预算
    bid, deadline = (parse_times(text) if need_time else (None, None))
    budget = parse_budget(text) if need_amt else None
    parts = []
    if bid or deadline:
        parts.append(f"开标={bid} 截止={deadline}")
    if budget:
        parts.append(f"预算={budget[0]}")
    return bid, deadline, budget, ("提取到 " + " ".join(parts) if parts else "无新字段")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", help="只处理指定 URL 的公告")
    ap.add_argument("--all", action="store_true", help="全部存量强制刷新")
    args = ap.parse_args()

    # 2026-09-16：本脚本与其余 5 组补抓并行跑（deploy.sh BF_PIDS），写锁偶发被长事务占用；
    # Python 默认 timeout=5s 不够，当日 locked 崩在 UPDATE。提到 60s + 行级重试双保险。
    conn = sqlite3.connect(DB_PATH, timeout=60)
    conn.row_factory = sqlite3.Row

    if args.url:
        rows = conn.execute("SELECT * FROM notices WHERE url=?", (args.url,)).fetchall()
        force = True
    elif args.all:
        rows = conn.execute(
            "SELECT * FROM notices WHERE site='中国邮政'").fetchall()
        force = True
    else:
        # 2026-08-07：缺开标时间或缺金额都处理
        rows = conn.execute(
            "SELECT * FROM notices WHERE site='中国邮政' "
            "AND ((bid_deadline IS NULL OR bid_deadline='') "
            "     OR (amount IS NULL OR amount=''))").fetchall()
        force = False

    print(f"待处理 {len(rows)} 条")
    updated = 0
    for row in rows:
        bid, deadline, budget, msg = process_notice(row, force=force)
        if bid or deadline or budget:
            # 行级重试：locked 时指数退避 5/10s，避免整轮崩掉丢弃剩余行
            for attempt in range(3):
                try:
                    conn.execute("""
                        UPDATE notices SET bid_deadline=COALESCE(?, bid_deadline),
                                           deadline=COALESCE(?, deadline),
                                           amount=COALESCE(?, amount),
                                           amount_value=COALESCE(?, amount_value),
                                           amount_unit=COALESCE(?, amount_unit)
                        WHERE id=?
                    """, (bid or None, deadline or None,
                          budget[0] if budget else None,
                          budget[1] if budget else None,
                          budget[2] if budget else None,
                          row["id"]))
                    conn.commit()
                    break
                except sqlite3.OperationalError as e:
                    if "locked" not in str(e).lower() or attempt == 2:
                        raise
                    time.sleep(5 * (2 ** attempt))
            updated += 1
        print(f"  {row['title'][:35]} | {msg}")

    print(f"\n完成，更新 {updated} 条")
    conn.close()


if __name__ == "__main__":
    main()
