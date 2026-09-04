#!/usr/bin/env python3
"""全站漏缺项目信息自检（2026-08-17；v2 老板研判后改进）

目的：
- 周/每次采集后自动跑一遍（只读 DB，无外网/抓取），输出各站各字段覆盖率
- 与上一次报告对比，**缺口率恶化 > 5pp 且缺失条数实际增加 ≥5** 时告警
- 区分"假缺（content 有但没提取）vs 真缺" → 产出可补清单
- 按公告类型分层计算（中标/终止公告本无开标 → 不算缺失）
- 项目级覆盖（页面实际展示层）

P0 改进：
  1. 分型口径：金额仅 tender+intention（排除合同预估）；开标仅 tender；中标单位/中标金额仅 award
  2. 假缺清单：content 含金额/发包人/开标关键词但字段空 → 输出可补条数 + 落盘 fixable_<ts>.json
  3. 告警修稳：n<30 豁免 + 双条件（pct 下降 + 缺失条数实际增加）
P1 改进：
  4. 项目级审计：projects 表的预算/中标方/开标覆盖（对齐页面体验）
  5. 采集总量骤降告警：某站总数比上次降 >30% → 哨兵（疑似停更/反爬封禁）

用法：
  python3 audit_coverage.py            # 默认输出 + 写日志 + 写历史报告
  python3 audit_coverage.py --quiet   # 仅打印告警
"""
import argparse
import json
import os
import re
import sqlite3
import sys
import time
from pathlib import Path
from collections import OrderedDict

HERE = Path(__file__).parent
DB_PATH = HERE / "data" / "tender.db"
AUDIT_DIR = HERE / "logs" / "audit"
AUDIT_LOG = HERE / "audit.log"
FIXABLE_DIR = HERE / "logs" / "audit" / "fixable"
ALERT_PCT_DELTA = -5       # pct 下降超过 5pp 才计告警（注意方向：下降 = 恶化）
ALERT_ABS_DELTA = 5         # 缺失条数实际增加 ≥5 才计告警
MIN_SAMPLE_FOR_ALERT = 30   # 样本 <30 豁免告警（避免小样本抖误报）
TOTAL_DROP_RATIO = 0.7      # 站总数降至上次 70% 以下 → 总量告警（疑似停更/反爬）

sys.path.insert(0, str(HERE))
from config import SITES  # noqa: E402

# ── 字段-适用公告类型映射（2026-08-17 老板研判改进 P0-1）──
# 只统计该字段语义存在的公告类型，避免混算导致失真
APPLICABLE_NOTICE_TYPES = {
    # 字段名 → [(notice_types, has_fn)]
    "amount_value": (
        {"tender", "intention"},
        lambda r: r["amount_value"] is not None and r["amount_value"] > 0
                  and (r["budget_type"] or "") != "contract_estimate",  # 合同预估页面不展示
    ),
    "bid_deadline": (
        {"tender"},  # 中标/终止/意向公告本无开标时间
        lambda r: bool((r["bid_deadline"] or "").strip()),
    ),
    "tenderer": (
        {"tender", "intention", "other"},
        lambda r: bool((r["tenderer"] or "").strip()),
    ),
    "winner": (
        {"award"},
        lambda r: bool((r["winner"] or "").strip()),
    ),
    "winning_amount": (
        {"award"},
        lambda r: bool((r["winning_amount"] or "") and str(r["winning_amount"]).strip()),
    ),
}

# ── 假缺检测模式（content 里有但字段空 → 可补抓的"假缺"）──
FALSE_MISS_PATTERNS = {
    "amount_value": [
        re.compile(r'(?:预算金额|项目预算|采购预算|控制价|最高限价|合同估算价|合同预估金额)[^。\n]{0,30}?[\d,.]+\s*(?:万\s*元|万\s*%|万元|亿\s*元|亿元|元)'),
        re.compile(r'(?:预算金额|项目预算|采购预算|控制价|最高限价|合同估算价|合同预估金额)[：:]\s*[\d,.]+'),
    ],
    "tenderer": [
        re.compile(r'(?:采购人|招标人|采购单位|采购代理机构|发包人)[：:]\s*\S{3,60}'),
    ],
    "bid_deadline": [
        re.compile(r'(?:开标时间|投标截止时间|递交截止时间|截止时间)[：:]?\s*\d{4}[\s年/-]\d{1,2}[\s月/-]\d{1,2}'),
    ],
}

# 项目级审计字段-适用项目状态映射
PROJECT_APPLICABLE = {
    # 字段名 → {(project_stage), has_fn}
    "budget_value": (
        lambda p: True,  # 所有项目都应有预算展示（合同预估型不展示，下面过滤）
        lambda r: r["budget_value"] is not None and r["budget_value"] > 0
                  and (r["budget_type"] or "") != "contract_estimate",
    ),
    "winner": (
        {"awarded", "contract"},
        lambda r: bool((r["winner"] or "").strip()),
    ),
    "open_date": (
        {"upcoming", "upcoming_unknown_time", "opening_today", "awarded"},
        lambda r: bool((r["open_date"] or "").strip()),
    ),
    "purchaser": (
        lambda p: True,
        lambda r: bool((r["purchaser"] or "").strip()),
    ),
}


def get_content(raw_json_str):
    try:
        return json.loads(raw_json_str or "{}").get("content") or ""
    except Exception:
        return ""


def collect_notice(conn, sites):
    """公告级：按适用公告类型分层计算覆盖率"""
    conn.row_factory = sqlite3.Row
    out = OrderedDict()
    for site in sites:
        rows = conn.execute(
            "SELECT notice_type, amount_value, bid_deadline, tenderer, winner, winning_amount, budget_type FROM notices WHERE site=?",
            (site,),
        ).fetchall()
        n_total = len(rows)
        fields = OrderedDict()
        if n_total == 0:
            out[site] = {"n": 0, "fields": {}, "n_applicable_total": 0}
            continue
        for fname, (applicable_types, has_fn) in APPLICABLE_NOTICE_TYPES.items():
            applicable = [r for r in rows if (r["notice_type"] or "") in applicable_types]
            n_app = len(applicable)
            n_have = sum(1 for r in applicable if has_fn(r))
            fields[fname] = {
                "applicable_n": n_app,
                "have": n_have,
                "missing": n_app - n_have,
                "pct": round(n_have * 100 / n_app, 1) if n_app else None,
            }
        out[site] = {
            "n": n_total,
            "fields": fields,
            "n_applicable_total": sum(f["applicable_n"] for f in fields.values()),
        }
    return out


def collect_project(conn):
    """项目级：projects 表（页面实际展示层）"""
    conn.row_factory = sqlite3.Row
    sites = [r[0] for r in conn.execute("SELECT DISTINCT site FROM projects WHERE site IS NOT NULL AND site!=''")]
    out = OrderedDict()
    for site in sites:
        rows = conn.execute(
            "SELECT project_stage, budget_value, winner, open_date, purchaser, budget_type FROM projects WHERE site=?",
            (site,),
        ).fetchall()
        n_total = len(rows)
        fields = OrderedDict()
        for fname, (stage_pred, has_fn) in PROJECT_APPLICABLE.items():
            if callable(stage_pred):
                applicable = [r for r in rows if stage_pred(r)]
            else:
                applicable = [r for r in rows if (r["project_stage"] or "") in stage_pred]
            n_app = len(applicable)
            n_have = sum(1 for r in applicable if has_fn(r))
            fields[fname] = {
                "applicable_n": n_app,
                "have": n_have,
                "missing": n_app - n_have,
                "pct": round(n_have * 100 / n_app, 1) if n_app else None,
            }
        out[site] = {"n": n_total, "fields": fields}
    return out


def collect_false_missing(conn, sites):
    """假缺清单：content 含关键词但字段空 → 可补条数 + 标题清单"""
    conn.row_factory = sqlite3.Row
    out = OrderedDict()
    for site in sites:
        # 只看 tender/intention 类（按公告类型分层后）
        rows = conn.execute(
            "SELECT id, title, amount_value, tenderer, bid_deadline, budget_type, raw_json FROM notices WHERE site=? AND notice_type IN ('tender','intention')",
            (site,),
        ).fetchall()
        if not rows:
            out[site] = {"items": []}
            continue
        items = []
        for r in rows:
            c = get_content(r["raw_json"])
            if not c:
                continue
            for fname, patterns in FALSE_MISS_PATTERNS.items():
                # 跳过合同预估金额（页面不展示，不算假缺）
                if fname == "amount_value" and (r["budget_type"] or "") == "contract_estimate":
                    continue
                if not any(p.search(c) for p in patterns):
                    continue
                # content 含该字段关键词 → 检查是否已入库
                if fname == "amount_value":
                    has_field = r["amount_value"] is not None and r["amount_value"] > 0
                else:
                    has_field = bool((r[fname] or "").strip())
                if not has_field:
                    items.append({"id": r["id"], "title": (r["title"] or "")[:80], "field": fname})
        out[site] = {"items": items, "total": len(items)}
    return out


def previous_report():
    if not AUDIT_DIR.exists():
        return None
    files = sorted(AUDIT_DIR.glob("audit_*.json"), key=lambda p: p.stat().st_mtime)
    return json.load(open(files[-1])) if files else None


def compare_alerts(curr_notice, prev_notice, curr_total_drop=None):
    """双条件告警：pct 下降 >5pp 且缺失条数实际增加 ≥5；n<30 豁免"""
    alerts = []
    if not prev_notice:
        return alerts
    for site, sdata in curr_notice.items():
        if sdata["n"] < MIN_SAMPLE_FOR_ALERT:
            continue
        prev_data = prev_notice.get(site)
        if not prev_data:
            continue
        prev_fields = prev_data.get("fields", {})
        for fname, fdata in sdata.get("fields", {}).items():
            prev_f = prev_fields.get(fname, {})
            prev_pct = prev_f.get("pct")
            curr_pct = fdata["pct"]
            if prev_pct is None or curr_pct is None:
                continue
            pct_drop = curr_pct - prev_pct  # 负数 = 恶化
            missing_now = fdata["missing"]
            missing_prev = prev_f.get("missing", 0)
            missing_increase = missing_now - missing_prev
            if pct_drop <= ALERT_PCT_DELTA and missing_increase >= ALERT_ABS_DELTA:
                alerts.append({
                    "site": site, "field": fname,
                    "prev_pct": prev_pct, "curr_pct": curr_pct,
                    "missing_prev": missing_prev, "missing_now": missing_now,
                    "missing_increase": missing_increase,
                })
    return alerts


def compare_total_drop(curr_notice, prev_notice):
    """采集总量骤降告警：某站总数降至上次 70% 以下 → 疑似停更/反爬"""
    alerts = []
    if not prev_notice:
        return alerts
    for site, sdata in curr_notice.items():
        prev = prev_notice.get(site)
        if not prev or prev["n"] < 20:  # 基数太小不看
            continue
        if sdata["n"] < prev["n"] * TOTAL_DROP_RATIO:
            alerts.append({
                "site": site,
                "prev_n": prev["n"], "curr_n": sdata["n"],
                "drop_pct": round((prev["n"] - sdata["n"]) * 100 / prev["n"], 1),
            })
    return alerts


def format_report(curr_notice, curr_proj, false_missing, coverage_alerts, total_alerts):
    lines = []
    lines.append(f"\n== 漏缺自检 v2（{time.strftime('%Y-%m-%d %H:%M:%S')}）==")
    # 公告级（按适用类型分层）
    lines.append("\n【公告级：按适用类型分层】")
    header = f"{'网站':<20} {'总数':>5} {'金额%':>7} {'开标%':>7} {'发包人%':>8} {'中标方%':>8} {'中标额%':>8}"
    lines.append(header)
    lines.append("-" * len(header))
    for site, sdata in curr_notice.items():
        n = sdata["n"]
        if n == 0:
            lines.append(f"{site:<20} {0:>5} (无数据)")
            continue
        f = sdata.get("fields", {})
        def pct(name):
            d = f.get(name)
            if not d or d["pct"] is None: return "-"
            return d["pct"]
        lines.append(f"{site:<20} {n:>5} {pct('amount_value'):>6}% {pct('bid_deadline'):>6}% {pct('tenderer'):>7}% {pct('winner'):>7}% {pct('winning_amount'):>7}%")
    # 项目级
    lines.append("\n【项目级：页面实际展示层】")
    header2 = f"{'网站':<20} {'项目数':>6} {'预算%':>7} {'中标方%':>8} {'开标%':>7} {'发包人%':>9}"
    lines.append(header2)
    lines.append("-" * len(header2))
    for site, sdata in curr_proj.items():
        n = sdata["n"]
        if n == 0:
            continue
        f = sdata.get("fields", {})
        def pctp(name):
            d = f.get(name)
            if not d or d["pct"] is None: return "-"
            return d["pct"]
        lines.append(f"{site:<20} {n:>6} {pctp('budget_value'):>6}% {pctp('winner'):>7}% {pctp('open_date'):>6}% {pctp('purchaser'):>8}%")
    # 假缺清单
    lines.append("\n【假缺清单：content 有但未提取（可立即补）】")
    has_any = False
    for site, d in false_missing.items():
        if d["total"] > 0:
            has_any = True
            lines.append(f"  {site}: {d['total']} 条")
    if not has_any:
        lines.append("  （无）")
    # 告警
    lines.append("")
    if coverage_alerts:
        lines.append(f"⚠️ 覆盖率恶化（n≥{MIN_SAMPLE_FOR_ALERT} 且 pct↓{abs(ALERT_PCT_DELTA)}pp + 缺失≥{ALERT_ABS_DELTA}）：")
        for a in coverage_alerts:
            lines.append(f"  - {a['site']} / {a['field']}: {a['prev_pct']}%→{a['curr_pct']}% (Δ={a['missing_increase']:+d}条)")
    else:
        lines.append("✅ 各站覆盖率无明显恶化")
    if total_alerts:
        lines.append(f"\n⚠️ 采集总量骤降（降至上次 <{int(TOTAL_DROP_RATIO*100)}%，疑似停更/反爬）：")
        for a in total_alerts:
            lines.append(f"  - {a['site']}: {a['prev_n']}→{a['curr_n']} (↓{a['drop_pct']}%)")
    else:
        lines.append("✅ 各站采集量无骤降")
    return "\n".join(lines)


def save_report(curr_notice, curr_proj, false_missing, coverage_alerts, total_alerts):
    AUDIT_DIR.mkdir(parents=True, exist_ok=True)
    FIXABLE_DIR.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    # 主报告
    path = AUDIT_DIR / f"audit_{stamp}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump({
            "ts": stamp,
            "version": 2,
            "alerts": {"coverage": coverage_alerts, "total_drop": total_alerts},
            "notice_coverage": curr_notice,
            "project_coverage": curr_proj,
        }, f, ensure_ascii=False, indent=2)
    # 假缺清单（独立落盘便于直接行动）
    fix_path = FIXABLE_DIR / f"fixable_{stamp}.json"
    with open(fix_path, "w", encoding="utf-8") as f:
        json.dump({"ts": stamp, "by_site": {s: d for s, d in false_missing.items() if d["total"] > 0}}, f, ensure_ascii=False, indent=2)
    # 最小备份策略：审计与可修复清单各保留最近 7 份
    for old in sorted(AUDIT_DIR.glob("audit_*.json"), key=lambda p: p.stat().st_mtime)[:-7]:
        try:
            old.unlink()
        except OSError:
            pass
    for old in sorted(FIXABLE_DIR.glob("fixable_*.json"), key=lambda p: p.stat().st_mtime)[:-7]:
        try:
            old.unlink()
        except OSError:
            pass
    # 日志行
    with open(AUDIT_LOG, "a", encoding="utf-8") as f:
        n_fix = sum(d["total"] for d in false_missing.values())
        f.write(f"[{stamp}] v2 自检: {len(coverage_alerts)} 覆盖率告警, {len(total_alerts)} 总量告警, {n_fix} 条假缺可补\n")
    return path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--quiet", action="store_true", help="只打印告警与汇总")
    args = ap.parse_args()

    conn = sqlite3.connect(str(DB_PATH))
    sites = list(SITES.keys())
    curr_notice = collect_notice(conn, sites)
    curr_proj = collect_project(conn)
    false_missing = collect_false_missing(conn, sites)
    conn.close()

    prev = previous_report()
    coverage_alerts = compare_alerts(curr_notice, prev)
    total_alerts = compare_total_drop(curr_notice, prev)

    if not args.quiet:
        print(format_report(curr_notice, curr_proj, false_missing, coverage_alerts, total_alerts))

    # 告警打印（即使 --quiet 也打）
    for a in coverage_alerts:
        print(f"⚠️ 覆盖: {a['site']}/{a['field']} {a['prev_pct']}%→{a['curr_pct']}%")
    for a in total_alerts:
        print(f"⚠️ 总量: {a['site']} {a['prev_n']}→{a['curr_n']}")

    save_report(curr_notice, curr_proj, false_missing, coverage_alerts, total_alerts)
    return 1 if (coverage_alerts or total_alerts) else 0


if __name__ == "__main__":
    sys.exit(main())
