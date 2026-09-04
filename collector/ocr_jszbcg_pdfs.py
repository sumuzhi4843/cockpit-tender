# -*- coding: utf-8 -*-
"""对 jszbcg 扫描 PDF 执行本地中文 OCR，保留逐页图片和原始文本。"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import fitz

ROOT = Path(__file__).resolve().parent
MANIFEST = ROOT / "data" / "jszbcg_pdf_manifest.json"
OCR_ROOT = ROOT / "data" / "jszbcg_ocr"
OCR_MANIFEST = ROOT / "data" / "jszbcg_ocr_manifest.json"


def run():
    source = json.loads(MANIFEST.read_text(encoding="utf-8"))
    items = [x for x in source["items"] if x.get("status") == "needs_ocr"]
    OCR_ROOT.mkdir(parents=True, exist_ok=True)
    results = []
    for index, item in enumerate(items, 1):
        source_id = item["source_id"]
        work = OCR_ROOT / source_id
        work.mkdir(parents=True, exist_ok=True)
        doc = fitz.open(item["pdf_path"])
        pages = []
        for page_index, page in enumerate(doc, 1):
            image_path = work / f"page_{page_index:03d}.png"
            text_path = work / f"page_{page_index:03d}.txt"
            if not image_path.exists():
                pix = page.get_pixmap(matrix=fitz.Matrix(2.5, 2.5), alpha=False)
                pix.save(image_path)
            cmd = [
                "tesseract", str(image_path), "stdout", "-l", "chi_sim+eng",
                "--psm", "6", "-c", "preserve_interword_spaces=1",
            ]
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
            text = proc.stdout.strip()
            text_path.write_text(text, encoding="utf-8")
            pages.append({
                "page": page_index, "image": str(image_path), "text_path": str(text_path),
                "chars": len(text), "text": text,
                "stderr": proc.stderr[-1000:] if proc.returncode else "",
            })
        doc.close()
        joined = "\n\n".join(f"--- 第 {p['page']} 页 ---\n{p['text']}" for p in pages)
        joined_path = work / "ocr_raw.txt"
        joined_path.write_text(joined, encoding="utf-8")
        result = {**item, "ocr_status": "done", "ocr_dir": str(work),
                  "ocr_raw_path": str(joined_path), "ocr_chars": len(joined), "ocr_pages": pages}
        results.append(result)
        print(f"[{index}/{len(items)}] pages={len(pages)} chars={len(joined)} {item['title'][:50]}")
    summary = {"files": len(results), "pages": sum(len(x["ocr_pages"]) for x in results),
               "chars": sum(x["ocr_chars"] for x in results)}
    OCR_MANIFEST.write_text(json.dumps({"summary": summary, "items": results}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(OCR_MANIFEST)


if __name__ == "__main__":
    run()
