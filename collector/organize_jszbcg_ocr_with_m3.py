# -*- coding: utf-8 -*-
"""用 MiniMax-M3 校正 jszbcg OCR 文本、生成 Markdown 并回填结构化字段。"""
from __future__ import annotations

import json
import re
import sqlite3
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "data" / "tender.db"
OCR_MANIFEST = ROOT / "data" / "jszbcg_ocr_manifest.json"
RESULT_MANIFEST = ROOT / "data" / "jszbcg_m3_manifest.json"
MD_DIR = ROOT / "reports" / "jszbcg_pdf_markdown"
MODEL = "minimax-m3"

PROMPT = """你是中文招投标公告整理器。以下是扫描 PDF 的 OCR 原文，可能有错字、错行和页脚乱码。请结合公告标题校正明显 OCR 错误，但不要编造；无法确认的字段留空。仅输出一个 JSON 对象，不要代码围栏，不要任何额外文字。字段必须为：title, markdown, winner, winning_amount_yuan, purchaser, project_code, publish_date, confidence, notes。要求：markdown 使用结构清晰的中文 Markdown，保留公告中的项目名称、中标人、中标金额、采购人/招标人、代理机构、联系方式和其他关键事实；winner 无法确认填空字符串；winning_amount_yuan 必须是数字或 null，万元应乘以 10000；confidence 为 0 到 1；publish_date 无法确认填空字符串。

公告标题：{title}

OCR 原文：
{ocr}
"""


def parse_model_json(text: str):
    text = (text or "").strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            return json.loads(text[start:end + 1])
        raise


def call_m3(prompt: str):
    cmd = ["openclaw", "infer", "model", "run", "--model", MODEL,
           "--prompt", prompt, "--json"]
    last_error = ""
    for attempt in range(1, 4):
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        if proc.returncode == 0:
            envelope = json.loads(proc.stdout)
            outputs = envelope.get("outputs") or []
            if outputs and outputs[0].get("text"):
                return parse_model_json(outputs[0]["text"]), envelope
        last_error = (proc.stderr or proc.stdout)[-2000:]
        time.sleep(attempt * 2)
    raise RuntimeError(last_error or "MiniMax-M3 returned no output")


def validate(data: dict):
    required = {"title", "markdown", "winner", "winning_amount_yuan", "purchaser",
                "project_code", "publish_date", "confidence", "notes"}
    missing = required - set(data)
    if missing:
        raise ValueError(f"missing fields: {sorted(missing)}")
    if not isinstance(data["markdown"], str) or len(data["markdown"]) < 80:
        raise ValueError("markdown too short")
    amount = data["winning_amount_yuan"]
    if amount is not None and not isinstance(amount, (int, float)):
        raise ValueError("winning_amount_yuan must be number or null")
    confidence = data["confidence"]
    if not isinstance(confidence, (int, float)) or not 0 <= confidence <= 1:
        raise ValueError("confidence out of range")


def run():
    source = json.loads(OCR_MANIFEST.read_text(encoding="utf-8"))
    items = source["items"]
    MD_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    results = []
    for index, item in enumerate(items, 1):
        record = {"id": item["id"], "source_id": item["source_id"], "title": item["title"]}
        try:
            ocr = Path(item["ocr_raw_path"]).read_text(encoding="utf-8")
            data, envelope = call_m3(PROMPT.format(title=item["title"], ocr=ocr))
            validate(data)
            md_path = MD_DIR / (Path(item["pdf_path"]).stem + ".md")
            source_url = item.get("url") or conn.execute(
                "SELECT url FROM notices WHERE id=?", (item["id"],)
            ).fetchone()[0]
            header = [
                f"# {data['title'] or item['title']}", "",
                f"- 来源页面：{source_url}",
                f"- PDF：{item.get('pdf_url', '')}",
                f"- 整理模型：MiniMax-M3", f"- 置信度：{data['confidence']}", "", "---", "",
            ]
            body = data["markdown"].strip()
            if body.startswith("# "):
                body = "\n".join(body.splitlines()[1:]).lstrip()
            md_path.write_text("\n".join(header) + body + "\n", encoding="utf-8")

            row = conn.execute("SELECT raw_json,winner,winning_amount FROM notices WHERE id=?", (item["id"],)).fetchone()
            if not row:
                raise ValueError("notice missing in database")
            raw = json.loads(row["raw_json"] or "{}")
            raw.update({
                "ocr_raw_path": item["ocr_raw_path"], "m3_markdown_path": str(md_path),
                "m3_extracted": data, "pdf_processing": "ocr+m3",
            })
            winner = (data.get("winner") or row["winner"] or "").strip()
            amount = data.get("winning_amount_yuan")
            winning_amount = str(amount) if amount is not None else (row["winning_amount"] or "")
            conn.execute("UPDATE notices SET winner=?,winning_amount=?,raw_json=? WHERE id=?",
                         (winner, winning_amount, json.dumps(raw, ensure_ascii=False), item["id"]))
            conn.commit()
            record.update(status="ok", markdown_path=str(md_path), winner=winner,
                          winning_amount_yuan=amount, confidence=data["confidence"], notes=data["notes"])
            print(f"[{index}/{len(items)}] OK conf={data['confidence']} winner={winner[:30]} {item['title'][:45]}")
        except Exception as exc:
            record.update(status="error", error=str(exc))
            print(f"[{index}/{len(items)}] ERROR {item['title'][:45]}: {exc}")
        results.append(record)
        RESULT_MANIFEST.write_text(json.dumps({"items": results}, ensure_ascii=False, indent=2), encoding="utf-8")
        time.sleep(0.25)
    conn.close()
    summary = {
        "total": len(results), "ok": sum(x["status"] == "ok" for x in results),
        "errors": sum(x["status"] == "error" for x in results),
        "winner_extracted": sum(bool(x.get("winner")) for x in results),
        "amount_extracted": sum(x.get("winning_amount_yuan") is not None for x in results),
    }
    RESULT_MANIFEST.write_text(json.dumps({"summary": summary, "items": results}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(RESULT_MANIFEST)


if __name__ == "__main__":
    run()
