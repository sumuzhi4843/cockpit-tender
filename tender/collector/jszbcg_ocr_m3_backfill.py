#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""jszbcg_ocr_m3_backfill.py — 江苏招采平台本地 PDF 提发包人（2026-09-08 老板批准）

范围：data/jszbcg_need_ocr.json 里 68 条（本地有 PDF、文本层 enrich 未命中、tenderer 空）。
策略：
  1. PDF 有文字层（get_text >= 200 字）→ 直接喂 M3（跳过 OCR）
  2. 扫描件 → tesseract 本地 OCR → 喂 M3
  3. M3 = openclaw infer model run --model minimax-portal/MiniMax-M3（organize 脚本的旧别名已失效）
  4. purchaser 经 sanitize 后仅回填空 tenderer；m3_extracted 存 raw_json 备查
失败单条不阻塞；结果写 data/jszbcg_ocr_m3_report.json（回滚按其 id 列表）。
"""
import json
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from enrich_engine import sanitize_purchaser  # noqa: E402
from organize_jszbcg_ocr_with_m3 import PROMPT, parse_model_json, validate  # noqa: E402

DB = HERE / "data" / "tender.db"
PLAN = HERE / "data" / "jszbcg_need_ocr.json"
REPORT = HERE / "data" / "jszbcg_ocr_m3_report.json"
OCR_ROOT = HERE / "data" / "jszbcg_ocr"
MODEL = "minimax-portal/MiniMax-M3"


def pdf_text(path):
    import fitz
    try:
        doc = fitz.open(path)
        return "\n".join(page.get_text() for page in doc)
    except Exception as exc:
        print(f"  pdf_text fail: {exc}")
        return ""


def ocr_pdf(path, cache_id):
    import fitz
    work = OCR_ROOT / cache_id
    work.mkdir(parents=True, exist_ok=True)
    texts = []
    doc = fitz.open(path)
    for i, page in enumerate(doc, 1):
        tp = work / f"page_{i:03d}.txt"
        if tp.exists():
            texts.append(tp.read_text(encoding="utf-8"))
            continue
        ip = work / f"page_{i:03d}.png"
        page.get_pixmap(matrix=fitz.Matrix(2.5, 2.5), alpha=False).save(ip)
        proc = subprocess.run(
            ["tesseract", str(ip), "stdout", "-l", "chi_sim+eng", "--psm", "6",
             "-c", "preserve_interword_spaces=1"],
            capture_output=True, text=True, timeout=180)
        t = proc.stdout.strip()
        tp.write_text(t, encoding="utf-8")
        texts.append(t)
    return "\n".join(texts)


def call_m3(prompt):
    cmd = ["openclaw", "infer", "model", "run", "--model", MODEL,
           "--prompt", prompt, "--json"]
    last = ""
    for attempt in range(1, 4):
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=240)
        if proc.returncode == 0:
            try:
                env = json.loads(proc.stdout)
                outs = env.get("outputs") or []
                if outs and outs[0].get("text"):
                    return parse_model_json(outs[0]["text"])
            except (json.JSONDecodeError, ValueError) as exc:
                last = str(exc)
        else:
            last = (proc.stderr or proc.stdout)[-400:]
        time.sleep(attempt * 3)
    raise RuntimeError(last or "M3 no output")


def build_auto_plan(cap):
    """--auto：动态构造待 OCR→M3 清单——tenderer 空 ∧ 本地有 PDF（按 source_id 匹配文件名）
    ∧ 未经 m3。由 jszbcg_batch_cron.sh 每日调用。"""
    import glob as _glob
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    pdf_by_sid = {}
    for f in _glob.glob(str(HERE / "data" / "jszbcg_pdfs" / "*")):
        parts = os.path.basename(f).split("_", 2)
        if len(parts) >= 2:
            pdf_by_sid[parts[1]] = f
    plan = []
    # 2026-09-20 收紧计划范围（老板批准方案落地）：只有「正文仍是列表摘要(<220)且有本地 PDF」的行才进队。
    # 全文已在而发包人为空 = 公告里本来就没写采购人（招标常态），反复重抽恒无命中白烧 cap 名额；
    # 这类交给相1假缺轮（content 命中正则才会试，正则不命中即静默），不再进 OCR→M3 队列。
    for r in conn.execute("""SELECT json_extract(raw_json,'$.source_id') sid, id,
            COALESCE(json_extract(raw_json,'$.m3_extracted'),'') mx,
            COALESCE(json_extract(raw_json,'$.ocr_injected'),'') inj,
            length(COALESCE(json_extract(raw_json,'$.content'),'')) clen
            FROM notices
            WHERE site='江苏招标采购服务平台'
              AND length(COALESCE(json_extract(raw_json,'$.content'),'')) < 220"""):
        if not r["sid"]:
            continue
        f = pdf_by_sid.get(str(r["sid"]))
        if not f:
            continue
        # 已抽字段且已注入（或无正文可注入的极端短文本行由主循环 no_text 分支自然跳过）
        if r["mx"] and r["inj"]:
            continue
        plan.append({"id": r["id"], "pdf": f})
        if len(plan) >= cap:
            break
    conn.close()
    return plan


def main():
    if "--auto" in sys.argv:
        cap = 999
        for a in sys.argv:
            if a.startswith("--cap="):
                cap = int(a.split("=", 1)[1])
        plan = build_auto_plan(cap)
    else:
        plan = json.load(open(PLAN, encoding="utf-8"))
    # 2026-09-20：m3_daily 第二相用 --max-runtime 限时，给后续调度窗留量
    maxrt = 0
    for a in sys.argv:
        if a.startswith("--max-runtime="):
            maxrt = int(a.split("=", 1)[1])
    t0 = time.time()
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Connection.row_factory  # keep default
    conn.row_factory = sqlite3.Row
    done, results = 0, []
    for i, item in enumerate(plan, 1):
        if maxrt and time.time() - t0 > maxrt:
            print(f"⏱ 达 max-runtime {maxrt}s 收尾，剩余明日自动续跑", flush=True)
            break
        nid, pdf = item["id"], item["pdf"]
        rec = {"id": nid, "pdf": pdf, "status": "error"}
        try:
            row = conn.execute(
                "SELECT title, notice_type, tenderer, raw_json FROM notices WHERE id=?",
                (nid,)).fetchone()
            if not row:
                rec["status"] = "skip"
            else:
                raw = json.loads(row["raw_json"] or "{}")
                # 两相需求判定：发包人未抽 / 列表摘要正文待回灌（注入后假缺轮可抽金额/真开标）
                need_extract = not (row["tenderer"] or "").strip() and not raw.get("m3_extracted")
                need_inject = not raw.get("ocr_injected") and len(raw.get("content") or "") < 220
                if not (need_extract or need_inject):
                    rec["status"] = "skip"
                else:
                    text = pdf_text(pdf)
                    via = "text"
                    if len(text.strip()) < 200:
                        text = ocr_pdf(pdf, nid)
                        via = "ocr"
                    if len(text.strip()) < 50:
                        rec.update(status="no_text")
                    else:
                        if need_extract:
                            # 2026-09-20：长 OCR 文本（>3k）易致 M3 输出 JSON 截断（"Expecting ',' delimiter"），
                            # 失败则缩窗口重试一次（发包人/关键字段基本在前半部分）
                            try:
                                data = call_m3(PROMPT.format(title=row["title"], ocr=text[:9000]))
                            except Exception:
                                data = call_m3(PROMPT.format(title=row["title"], ocr=text[:2500]))
                            validate(data)
                            purchaser = sanitize_purchaser((data.get("purchaser") or "").strip())
                            raw["m3_extracted"] = data
                            raw["m3_via"] = via
                            raw["pdf_processing"] = f"{via}+m3"
                            if purchaser:
                                conn.execute("UPDATE notices SET tenderer=? WHERE id=? AND (tenderer IS NULL OR tenderer='')",
                                             (purchaser, nid))
                            rec.update(purchaser=purchaser,
                                       winner=(data.get("winner") or "")[:40])
                        if need_inject and len(text.strip()) >= 400:
                            raw["content"] = text[:6000]
                            raw["ocr_injected"] = via
                            rec["injected"] = True
                        conn.execute("UPDATE notices SET raw_json=? WHERE id=?",
                                     (json.dumps(raw, ensure_ascii=False), nid))
                        conn.commit()
                        rec.update(status="ok" if need_extract else "injected", via=via)
                        done += 1
        except Exception as exc:
            rec["error"] = str(exc)[:300]
        results.append(rec)
        print(f"[{i}/{len(plan)}] {rec['status']} {rec.get('purchaser', rec.get('error',''))[:50]}", flush=True)
        payload = {"ok": done, "items": results}
        try:  # 跨天累积报告，不覆盖历史
            _seen = {r["id"]: r for r in json.load(open(REPORT, encoding="utf-8"))["items"]}
            _seen.update({r["id"]: r for r in results})
            payload["items"] = list(_seen.values())
        except (OSError, KeyError, ValueError, json.JSONDecodeError):
            pass
        REPORT.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
        conn.commit()
        time.sleep(0.5)
    conn.close()
    stats = {}
    for r in results:
        stats[r["status"]] = stats.get(r["status"], 0) + 1
    print("SUMMARY", stats)


if __name__ == "__main__":
    main()
