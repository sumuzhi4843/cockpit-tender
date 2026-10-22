# -*- coding: utf-8 -*-
"""假缺清单 M3 回填（2026-09-16，老板授权试点 1000 条）

背景：漏缺自检产出"假缺清单"（content 有但未提取），大头在江苏政采 2760 +
盐城政采 1038。这批正文已在库内，无需重爬，用 M3 从 content 抽取缺失字段回填。

要点：
- 双账号轮换：minimax-portal/MiniMax-M3 与 minimax-portal2/MiniMax-M3 逐条交替，
  分摊限流。注意 openclaw infer CLI 有 state-lifecycle 锁，禁止多进程并发调用。
- 宁缺毋滥：M3 输出经 parse_datetime / sanitize_purchaser / 金额区间校验后才入库；
  只填空字段（CASE WHEN 空 THEN ? ELSE 原值 END），不覆盖已有值。
- 可续跑：目标字段已补上的行自然不再入选。
- 验证前提：per-字段假缺判定复用 audit_coverage.FALSE_MISS_PATTERNS。

用法：
    python3 fake_missing_m3_backfill.py --dry-run      # 只选出清单打印，不调模型不写库
    python3 fake_missing_m3_backfill.py --smoke 6      # 先跑 6 条看质量
    python3 fake_missing_m3_backfill.py                # 全量（江苏 700 + 盐城 300）
"""
import argparse
import json
import re
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from audit_coverage import FALSE_MISS_PATTERNS, get_content  # noqa: E402
from enrich_engine import parse_datetime, sanitize_purchaser  # noqa: E402
from organize_jszbcg_ocr_with_m3 import parse_model_json  # noqa: E402

DB = HERE / "data" / "tender.db"
REPORT = HERE / "data" / "fake_missing_m3_report.json"

# 双 minimax 账号（aliases: minimax-m3 / minimax-m3-alt），逐条轮换
MODELS = ["minimax-portal/MiniMax-M3", "minimax-portal2/MiniMax-M3"]
# 全站覆盖；每站不设上限，按 DB 当前假缺状态全量跑
ALL_SITES_ORDER = [
    "江苏政府采购网", "盐城市政府采购网", "中国政府采购网", "苏服采",
    "江苏招标采购服务平台", "中国邮政", "盐城市都市建设投资集团有限公司",
    "国投集团", "盐城市东方集团", "盐城市公共资源交易网", "江苏世纪新城",
]
CONTENT_MAX = 6000          # 正文截断，预算/开标/采购人基本都在前半部分
AMOUNT_MIN, AMOUNT_MAX = 100.0, 5e10   # 与 parse_budget 同区间（100元 ~ 500亿元）
YEAR_OK = (2024, 2027)

PROMPT = """你是中文招投标公告字段抽取器。从公告正文中抽取以下字段，正文里没有的就填 null，严禁编造、严禁推测：
1. budget_yuan：预算金额/采购预算/最高限价/控制价的金额，统一换算成"元"的纯数字（如 82万元 → 820000）
2. bid_open：开标时间，格式 YYYY-MM-DD HH:MM
3. submit_deadline：投标/响应文件递交截止时间，格式 YYYY-MM-DD HH:MM
4. purchaser：采购人/招标人/采购单位名称（注意：是采购人本身，不是采购代理机构）

仅输出一个 JSON 对象，不要代码围栏，不要任何多余文字：
{{"budget_yuan": null, "bid_open": null, "submit_deadline": null, "purchaser": null}}

公告标题：{title}

公告正文：
{content}
"""


def select_candidates(conn):
    """全站选出假缺行：字段空 ∧ content 命中对应假缺正则。"""
    conn.row_factory = sqlite3.Row
    picked = []
    for site in ALL_SITES_ORDER:
        rows = conn.execute(
            "SELECT id, title, site, amount_value, amount, bid_deadline, deadline, "
            "tenderer, budget_type, raw_json FROM notices "
            "WHERE site=? AND notice_type IN ('tender','intention') ORDER BY id",
            (site,),
        ).fetchall()
        for r in rows:
            content = get_content(r["raw_json"])
            if len(content) < 50:
                continue
            need_amount = (r["amount_value"] is None or not str(r["amount_value"]).strip())
            # 合同预估金额页面不展示，不算假缺（与自检口径一致）
            if need_amount and (r["budget_type"] or "") == "contract_estimate":
                need_amount = False
            if need_amount and not any(p.search(content) for p in FALSE_MISS_PATTERNS["amount_value"]):
                need_amount = False
            # 2026-09-20 收紧（老板批准）：jszbcg 列表摘要页（含“工具发布”行且正文极短）里的
            # “开标时间：”后面跟的是发布时刻而非真开标（真值在详情页 PDF，由 PDF 链路回灌后
            # content 变长自然解除此门控），短摘要不进入 open/deadline 候选，消除恒无命中噪声。
            spa_summary = (site == "江苏招标采购服务平台"
                           and "工具发布" in content and len(content) < 220)
            need_open = (not (r["bid_deadline"] or "").strip()) and not spa_summary and any(
                p.search(content) for p in FALSE_MISS_PATTERNS["bid_deadline"])
            # deadline 为空且正文含截止时间关键词也可补（正则同开标组）
            need_dl = (not (r["deadline"] or "").strip()) and not spa_summary and any(
                p.search(content) for p in FALSE_MISS_PATTERNS["bid_deadline"])
            need_tenderer = (not (r["tenderer"] or "").strip()) and any(
                p.search(content) for p in FALSE_MISS_PATTERNS["tenderer"])
            if not (need_amount or need_open or need_dl or need_tenderer):
                continue
            picked.append({
                "id": r["id"], "site": r["site"], "title": r["title"],
                "need": {"amount": need_amount, "open": need_open,
                         "deadline": need_dl, "tenderer": need_tenderer},
                "content": content[:CONTENT_MAX],
            })
    return picked


def call_model(model, prompt):
    """单次 M3 调用，3 次退避重试；返回 dict 或抛异常。串行使用，勿并发。"""
    cmd = ["openclaw", "infer", "model", "run", "--model", model,
           "--prompt", prompt, "--json"]
    last = ""
    for attempt in range(1, 4):
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=240)
            if proc.returncode == 0:
                env = json.loads(proc.stdout)
                outs = env.get("outputs") or []
                if outs and outs[0].get("text"):
                    return parse_model_json(outs[0]["text"])
                last = "empty output"
            else:
                last = (proc.stderr or proc.stdout)[-300:]
        except (subprocess.TimeoutExpired, json.JSONDecodeError, ValueError) as exc:
            last = str(exc)[:300]
        time.sleep(attempt * 4)
    raise RuntimeError(last or "M3 no output")


def _norm_dt(val):
    """M3 时间串 → 归一化；不在合理年份范围返回 None。"""
    if not val:
        return None
    dt = parse_datetime(str(val))
    if not dt:
        return None
    year = int(dt[:4])
    return dt if YEAR_OK[0] <= year <= YEAR_OK[1] else None


def validate_fields(data, need):
    """M3 输出 → 可入库字段；宁缺毋滥，存疑一律丢弃。"""
    fills = {}
    if data is None or not isinstance(data, dict):
        return fills
    if need["amount"]:
        v = data.get("budget_yuan")
        try:
            v = float(v)
        except (TypeError, ValueError):
            v = None
        if v is not None and AMOUNT_MIN <= v <= AMOUNT_MAX:
            if v >= 1e8:
                disp = f"{v / 1e8:.2f}亿元"
            elif v >= 1e4:
                disp = f"{v / 1e4:.2f}万元"
            else:
                disp = f"{v:.2f}元"
            fills["amount_value"] = v
            fills["amount"] = disp
            fills["amount_unit"] = "元"   # 与存量约定一致：unit 指 value 的单位
    if need["open"]:
        dt = _norm_dt(data.get("bid_open"))
        if dt:
            fills["bid_deadline"] = dt
    if need["deadline"]:
        dt = _norm_dt(data.get("submit_deadline"))
        if dt:
            fills["deadline"] = dt
    if need["tenderer"]:
        name = sanitize_purchaser(str(data.get("purchaser") or ""))
        if len(name) >= 3 and "代理机构" not in name:
            fills["tenderer"] = name
    return fills


def update_row(conn, row_id, fills):
    """只填空字段：CASE WHEN 原值为空才写入。撞锁退避重试。"""
    sets, args = [], []
    for col, val in fills.items():
        sets.append(f"{col}=CASE WHEN COALESCE({col},'')='' THEN ? ELSE {col} END")
        args.append(val)
    args.append(row_id)
    sql = f"UPDATE notices SET {', '.join(sets)} WHERE id=?"
    for attempt in range(3):
        try:
            conn.execute(sql, args)
            conn.commit()
            return
        except sqlite3.OperationalError as exc:
            if "locked" not in str(exc).lower() or attempt == 2:
                raise
            time.sleep(5 * (attempt + 1))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--smoke", type=int, default=0, help="只处理前 N 条（质量试跑）")
    ap.add_argument("--dry-run", action="store_true", help="只选出清单打印，不调模型")
    ap.add_argument("--limit", type=int, default=0, help="最多处理多少条（0=不限）")
    ap.add_argument("--max-runtime", type=int, default=0, help="最多跑多少秒（0=不限）")
    args = ap.parse_args()

    conn = sqlite3.connect(DB, timeout=60)
    cands = select_candidates(conn)
    if args.limit:
        cands = cands[:args.limit]
    total_missing = sum(1 for c in cands for v in c["need"].values() if v)
    print(f"候选 {len(cands)} 条，待补字段 {total_missing} 个"
          + (f"，上限 {args.limit} 条" if args.limit else "")
          + (f"，限时 {args.max_runtime}s" if args.max_runtime else ""), flush=True)
    if args.dry_run:
        from collections import Counter
        cnt = Counter(c["site"] for c in cands)
        print(f"分布: {dict(cnt)}", flush=True)
        for c in cands[:5]:
            print(f"  [{c['site']}] {c['title'][:40]} need={c['need']}", flush=True)
        return

    if args.smoke:
        cands = cands[:args.smoke]

    stats = {"rows": 0, "updated_rows": 0, "fields": {}, "failed": 0, "errors": [], "stopped_reason": ""}
    t0 = time.time()
    for i, c in enumerate(cands):
        # 时间闸：超时则停，给凌晨 site_refresher 留 buffer
        if args.max_runtime and (time.time() - t0) > args.max_runtime:
            stats["stopped_reason"] = f"max_runtime {args.max_runtime}s reached"
            break
        model = MODELS[i % len(MODELS)]
        prompt = PROMPT.format(title=c["title"], content=c["content"])
        try:
            data = call_model(model, prompt)
            fills = validate_fields(data, c["need"])
        except Exception as exc:  # 单条失败不中断整轮
            stats["failed"] += 1
            stats["errors"].append({"id": c["id"], "err": str(exc)[:200]})
            print(f"[{i+1}/{len(cands)}] id={c['id']} {model} 调用失败: {str(exc)[:120]}", flush=True)
            continue
        if fills:
            update_row(conn, c["id"], fills)
            stats["updated_rows"] += 1
            for k in fills:
                stats["fields"][k] = stats["fields"].get(k, 0) + 1
        stats["rows"] += 1
        sample = next(iter(fills.items()), None)
        print(f"[{i+1}/{len(cands)}] id={c['id']} {model.split('/')[0]} "
              f"补 {list(fills.keys()) or '无命中'}"
              + (f" 例:{sample[0]}={str(sample[1])[:40]}" if sample else ""), flush=True)
        time.sleep(1)

    elapsed = time.time() - t0
    stats["elapsed_sec"] = round(elapsed, 1)
    if not stats["stopped_reason"]:
        stats["stopped_reason"] = "completed"
    REPORT.write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n完成：处理 {stats['rows']} 条，补值 {stats['updated_rows']} 条，"
          f"字段 {stats['fields']}，失败 {stats['failed']}，耗时 {elapsed/60:.1f} 分钟 "
          f"({stats['stopped_reason']})", flush=True)


if __name__ == "__main__":
    main()
