# -*- coding: utf-8 -*-
"""重试 M3 输出格式失败项，改用分隔符协议。"""
from __future__ import annotations

import json
import re
import sqlite3
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OCR_MANIFEST = ROOT / "data" / "jszbcg_ocr_manifest.json"
M3_MANIFEST = ROOT / "data" / "jszbcg_m3_manifest.json"
DB_PATH = ROOT / "data" / "tender.db"
MD_DIR = ROOT / "reports" / "jszbcg_pdf_markdown"

PROMPT = """你是中文招投标公告整理器。以下是扫描 PDF OCR 原文。校正明显 OCR 错误但不要编造。严格按以下纯文本格式输出，字段分隔符必须独占一行，不要代码围栏：
@@TITLE@@
公告标题
@@WINNER@@
中标单位，无法确认则空行
@@AMOUNT_YUAN@@
中标金额人民币元，只写数字；无法确认则空行；万元乘10000
@@PURCHASER@@
采购人或招标人
@@PROJECT_CODE@@
项目编号或招标编号
@@PUBLISH_DATE@@
YYYY-MM-DD，无法确认则空行
@@CONFIDENCE@@
0到1的小数
@@NOTES@@
识别说明
@@MARKDOWN@@
结构清晰的完整中文 Markdown，保留项目、中标、采购人、代理机构、联系方式和关键事实
@@END@@

公告标题：{title}

OCR 原文：
{ocr}
"""


def extract(text, key, next_key):
    m = re.search(rf"@@{key}@@\s*(.*?)\s*@@{next_key}@@", text, re.S)
    return m.group(1).strip() if m else ""


def run():
    m3 = json.loads(M3_MANIFEST.read_text(encoding="utf-8"))
    failed = {x["source_id"]: x for x in m3["items"] if x["status"] == "error"}
    ocr = json.loads(OCR_MANIFEST.read_text(encoding="utf-8"))
    by_id = {x["source_id"]: x for x in ocr["items"]}
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    for source_id, record in failed.items():
        item = by_id[source_id]
        prompt = PROMPT.format(title=item["title"], ocr=Path(item["ocr_raw_path"]).read_text(encoding="utf-8"))
        proc = subprocess.run(["openclaw", "infer", "model", "run", "--model", "minimax-m3",
                               "--prompt", prompt, "--json"], capture_output=True, text=True, timeout=240)
        if proc.returncode:
            print("ERROR", item["title"], proc.stderr[-1000:])
            continue
        env = json.loads(proc.stdout)
        text = env["outputs"][0]["text"]
        fields = {
            "title": extract(text, "TITLE", "WINNER"),
            "winner": extract(text, "WINNER", "AMOUNT_YUAN"),
            "amount": extract(text, "AMOUNT_YUAN", "PURCHASER"),
            "purchaser": extract(text, "PURCHASER", "PROJECT_CODE"),
            "project_code": extract(text, "PROJECT_CODE", "PUBLISH_DATE"),
            "publish_date": extract(text, "PUBLISH_DATE", "CONFIDENCE"),
            "confidence": extract(text, "CONFIDENCE", "NOTES"),
            "notes": extract(text, "NOTES", "MARKDOWN"),
            "markdown": extract(text, "MARKDOWN", "END"),
        }
        if len(fields["markdown"]) < 80:
            print("ERROR short markdown", item["title"])
            continue
        amount = None
        if fields["amount"]:
            try: amount = float(fields["amount"].replace(",", ""))
            except ValueError: pass
        try: confidence = float(fields["confidence"])
        except ValueError: confidence = 0.5
        md_path = MD_DIR / (Path(item["pdf_path"]).stem + ".md")
        body = fields["markdown"]
        if body.startswith("# "): body = "\n".join(body.splitlines()[1:]).lstrip()
        source_url = conn.execute(
            "SELECT url FROM notices WHERE id=?", (item["id"],)
        ).fetchone()[0]
        md_path.write_text(
            f"# {fields['title'] or item['title']}\n\n- 来源页面：{source_url}\n- PDF：{item.get('pdf_url','')}\n- 整理模型：MiniMax-M3\n- 置信度：{confidence}\n\n---\n\n{body}\n",
            encoding="utf-8")
        row = conn.execute("SELECT raw_json,winner,winning_amount FROM notices WHERE id=?", (item["id"],)).fetchone()
        raw = json.loads(row["raw_json"] or "{}")
        extracted = {"title": fields["title"], "markdown": fields["markdown"],
                     "winner": fields["winner"], "winning_amount_yuan": amount,
                     "purchaser": fields["purchaser"], "project_code": fields["project_code"],
                     "publish_date": fields["publish_date"], "confidence": confidence, "notes": fields["notes"]}
        raw.update({"ocr_raw_path": item["ocr_raw_path"], "m3_markdown_path": str(md_path),
                    "m3_extracted": extracted, "pdf_processing": "ocr+m3"})
        winner = fields["winner"] or row["winner"] or ""
        winning_amount = str(amount) if amount is not None else row["winning_amount"] or ""
        conn.execute("UPDATE notices SET winner=?,winning_amount=?,raw_json=? WHERE id=?",
                     (winner, winning_amount, json.dumps(raw, ensure_ascii=False), item["id"]))
        conn.commit()
        record.clear(); record.update({"id": item["id"], "source_id": source_id, "title": item["title"],
                                       "status": "ok", "markdown_path": str(md_path), "winner": winner,
                                       "winning_amount_yuan": amount, "confidence": confidence, "notes": fields["notes"]})
        print("OK", confidence, winner, amount, item["title"])
    conn.close()
    summary = {"total": len(m3["items"]), "ok": sum(x["status"] == "ok" for x in m3["items"]),
               "errors": sum(x["status"] == "error" for x in m3["items"]),
               "winner_extracted": sum(bool(x.get("winner")) for x in m3["items"]),
               "amount_extracted": sum(x.get("winning_amount_yuan") is not None for x in m3["items"])}
    M3_MANIFEST.write_text(json.dumps({"summary": summary, "items": m3["items"]}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    run()
