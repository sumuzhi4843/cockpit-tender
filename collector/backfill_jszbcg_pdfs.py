# -*- coding: utf-8 -*-
"""江苏招标采购服务平台 PDF 回填与 Markdown 归档。

流程：
1. 读取 tender.db 中该平台公告的 source_id；
2. 查询详情 API 获取 signedPdfUrl；
3. 下载 PDF，优先使用文本层转 Markdown；
4. 用现有富化引擎回填中标单位/中标金额；
5. 输出需 OCR/视觉模型识别的清单，不自动调用付费模型。
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import time
from pathlib import Path

import fitz
import requests

from enrich_engine import enrich_detail

ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "data" / "tender.db"
PDF_DIR = ROOT / "data" / "jszbcg_pdfs"
MD_DIR = ROOT / "reports" / "jszbcg_pdf_markdown"
MANIFEST = ROOT / "data" / "jszbcg_pdf_manifest.json"
DETAIL_API = "https://api.jszbtb.com/DataGatewayApi/PublishBulletin/BulletinType/{bulletin_type}/ID/{source_id}"
HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Origin": "https://www.jszbcg.com",
    "Referer": "https://www.jszbcg.com/",
}


def safe_name(value: str) -> str:
    value = re.sub(r"[\\/:*?\"<>|]+", "_", value or "")
    value = re.sub(r"\s+", " ", value).strip(" ._")
    # ext4 单个文件名上限 255 bytes；中文通常每字 3 bytes，保守截断。
    return value[:55] or "untitled"


def pdf_to_text(path: Path):
    doc = fitz.open(path)
    pages = []
    for idx, page in enumerate(doc):
        text = page.get_text("text").strip()
        pages.append({"page": idx + 1, "text": text, "chars": len(text)})
    page_count = len(doc)
    doc.close()
    joined = "\n\n".join(p["text"] for p in pages if p["text"])
    return joined, pages, page_count


def markdown_document(title: str, source_url: str, pdf_url: str, text: str, pages: list[dict]) -> str:
    chunks = [
        f"# {title}", "", f"- 来源页面：{source_url}", f"- PDF：{pdf_url}",
        f"- 页数：{len(pages)}", "", "---", "",
    ]
    for page in pages:
        chunks.extend([f"## 第 {page['page']} 页", "", page["text"] or "[无可提取文本]", ""])
    return "\n".join(chunks)


def update_notice(conn, row, raw, pdf_url, pdf_path, md_path, text, fields):
    attachments = raw.get("detail_attachments") or []
    if not isinstance(attachments, list):
        attachments = []
    entry = {"name": pdf_path.name, "url": pdf_url, "local_path": str(pdf_path), "markdown_path": str(md_path)}
    if not any(isinstance(x, dict) and x.get("url") == pdf_url for x in attachments):
        attachments.append(entry)
    raw["detail_attachments"] = attachments
    raw["signed_pdf_url"] = pdf_url
    raw["pdf_local_path"] = str(pdf_path)
    raw["pdf_markdown_path"] = str(md_path)
    if text:
        raw["pdf_text"] = text[:50000]
        current = raw.get("content") or ""
        if len(text) > len(current):
            raw["content"] = text[:50000]

    winner = fields.get("winner") or row["winner"] or ""
    winning_amount = fields.get("winning_amount") or row["winning_amount"] or ""
    conn.execute(
        "UPDATE notices SET winner=?,winning_amount=?,raw_json=? WHERE id=?",
        (winner, str(winning_amount) if winning_amount not in (None, "") else "",
         json.dumps(raw, ensure_ascii=False), row["id"]),
    )


def run(limit=0, all_types=False, days=0):
    PDF_DIR.mkdir(parents=True, exist_ok=True)
    MD_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    where = "site='江苏招标采购服务平台'"
    if not all_types:
        where += " AND notice_subtype='award_final'"
    where += " AND (winner IS NULL OR winner='')"
    if days > 0:
        # 只处理最近 N 天（定时任务增量补抓，2026-08-06）
        where += f" AND publish_date >= date('now','localtime','-{days} days')"
    rows = conn.execute(f"SELECT * FROM notices WHERE {where} ORDER BY publish_date DESC,id").fetchall()
    if limit:
        rows = rows[:limit]

    session = requests.Session()
    session.headers.update(HEADERS)
    results = []
    for index, row in enumerate(rows, 1):
        try:
            raw = json.loads(row["raw_json"] or "{}")
        except Exception:
            raw = {}
        # 增量保护（2026-08-06）：已下载 PDF 且已提取文本的，跳过，避免定时任务重复处理
        if raw.get("pdf_local_path") and raw.get("pdf_text"):
            continue
        source_id = str(raw.get("source_id") or "")
        bulletin_type = 3 if row["notice_subtype"] == "award_final" else 1
        result = {
            "id": row["id"], "source_id": source_id, "title": row["title"],
            "url": row["url"], "status": "pending",
        }
        if not source_id:
            result.update(status="missing_source_id")
            results.append(result)
            continue
        try:
            detail = session.get(
                DETAIL_API.format(bulletin_type=bulletin_type, source_id=source_id), timeout=20
            ).json()
            data = detail.get("data") or {}
            pdf_url = data.get("signedPdfUrl") or raw.get("signed_pdf_url") or ""
            if not pdf_url:
                result.update(status="missing_pdf_url")
                results.append(result)
                continue

            filename = f"{row['publish_date'] or 'unknown'}_{source_id}_{safe_name(row['title'])}.pdf"
            pdf_path = PDF_DIR / filename
            if not pdf_path.exists() or pdf_path.stat().st_size < 1000:
                resp = session.get(pdf_url, timeout=60)
                resp.raise_for_status()
                pdf_path.write_bytes(resp.content)
            text, pages, page_count = pdf_to_text(pdf_path)
            md_path = MD_DIR / (pdf_path.stem + ".md")
            md_path.write_text(markdown_document(row["title"], row["url"], pdf_url, text, pages), encoding="utf-8")

            fields = enrich_detail(text, "award") if text else {}
            update_notice(conn, row, raw, pdf_url, pdf_path, md_path, text, fields)
            conn.commit()

            nonempty_pages = sum(1 for p in pages if p["chars"] >= 30)
            total_chars = len(text)
            needs_ocr = total_chars < 150 or nonempty_pages < max(1, page_count // 2)
            result.update(
                status="needs_ocr" if needs_ocr else "text_extracted",
                pdf_url=pdf_url, pdf_path=str(pdf_path), markdown_path=str(md_path),
                pages=page_count, text_chars=total_chars, nonempty_pages=nonempty_pages,
                winner=fields.get("winner") or "", winning_amount=fields.get("winning_amount") or "",
            )
            print(f"[{index}/{len(rows)}] {result['status']} pages={page_count} chars={total_chars} {row['title'][:45]}")
        except Exception as exc:
            result.update(status="error", error=str(exc))
            print(f"[{index}/{len(rows)}] ERROR {row['title'][:45]}: {exc}")
        results.append(result)
        time.sleep(0.15)

    conn.close()
    summary = {
        "total": len(results),
        "text_extracted": sum(r["status"] == "text_extracted" for r in results),
        "needs_ocr": sum(r["status"] == "needs_ocr" for r in results),
        "missing_pdf_url": sum(r["status"] == "missing_pdf_url" for r in results),
        "errors": sum(r["status"] == "error" for r in results),
        "pages": sum(r.get("pages", 0) for r in results),
        "ocr_pages": sum(r.get("pages", 0) for r in results if r["status"] == "needs_ocr"),
        "winner_extracted": sum(bool(r.get("winner")) for r in results),
        "winning_amount_extracted": sum(bool(r.get("winning_amount")) for r in results),
    }
    MANIFEST.write_text(json.dumps({"summary": summary, "items": results}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"manifest: {MANIFEST}")
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--all-types", action="store_true")
    parser.add_argument("--days", type=int, default=0,
                        help="只处理最近 N 天新增（定时任务增量用，2026-08-06）")
    args = parser.parse_args()
    run(limit=args.limit, all_types=args.all_types, days=args.days)
