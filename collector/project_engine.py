# -*- coding: utf-8 -*-
"""项目生命周期层：公告重分类、项目归并、状态计算与项目视图。"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from datetime import date, datetime
from pathlib import Path

from notice_classifier import classify_notice
from site_names import stable_site_name

DB_PATH = Path(__file__).parent / "data" / "tender.db"
OVERRIDES_PATH = Path(__file__).parent / "manual_overrides.json"
SCHEMA_VERSION = 2

DDL = """
CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY,
    canonical_name TEXT NOT NULL,
    display_name TEXT NOT NULL,
    purchaser TEXT,
    site TEXT,
    district TEXT,
    major_category TEXT,
    minor_category TEXT,
    project_stage TEXT NOT NULL,
    stage_reason TEXT,
    first_notice_date TEXT,
    latest_notice_date TEXT,
    open_date TEXT,
    budget_value REAL,
    budget_type TEXT,
    winner TEXT,
    winning_amount REAL,
    link_confidence TEXT,
    updated_at TEXT
);
CREATE TABLE IF NOT EXISTS project_notices (
    project_id TEXT NOT NULL REFERENCES projects(id),
    notice_id TEXT NOT NULL REFERENCES notices(id),
    relation_role TEXT NOT NULL,
    match_method TEXT,
    match_score INTEGER,
    match_confidence TEXT,
    match_reasons TEXT,
    is_manual INTEGER DEFAULT 0,
    PRIMARY KEY(project_id, notice_id)
);
CREATE INDEX IF NOT EXISTS pn_notice ON project_notices(notice_id);
CREATE INDEX IF NOT EXISTS pn_project ON project_notices(project_id);
"""

_SUFFIX = re.compile(
    r"(?:招标|采购|竞争性谈判|竞争性磋商|询价|比选)?公告\s*$"
    r"|中标结果(?:公告|公示)\s*$|中标候选人(?:公告|公示)\s*$"
    r"|成交结果(?:公告|公示)\s*$|成交公告\s*$|中选(?:公告|公示|结果公示)\s*$"
    r"|询价结果(?:公告|公示)\s*$|比选结果(?:公告|公示)\s*$|结果公示\s*$"
    r"|(?:更正|澄清|补充|变更|终止|暂停|流标|废标)(?:公告|公示)\s*$"
    # 2026-08-11：补「最高限价/控制价/拦标价」类价格公示后缀（伍佑派出所案例）
    r"|(?:最高(?:投标)?限价|控制价|拦标价)(?:公告|公示)\s*$"
)


def norm_name(name: str) -> str:
    value = re.sub(r"^(?:【[^】]+】\s*)+(?:关于\s*)?", "", (name or "").strip())
    value = _SUFFIX.sub("", value).strip()
    return re.sub(r"[\s　]+", "", value)


def _project_key(row):
    """从原始采集字段提取跨公告类型稳定的项目 ID。"""
    try:
        raw = json.loads(row["raw_json"] or "{}")
    except Exception:
        raw = {}
    value = raw.get("project_key") or raw.get("purchases_need_id") or raw.get("pneed_id")
    return str(value or "").strip()


def _pid(seed: str) -> str:
    return "p_" + hashlib.md5(seed.encode("utf-8")).hexdigest()


def ensure_notice_columns(conn):
    cols = {r[1] for r in conn.execute("PRAGMA table_info(notices)")}
    for name, typ in {
        "notice_subtype": "TEXT", "classification_confidence": "REAL",
        "classification_reason": "TEXT", "classification_version": "TEXT",
    }.items():
        if name not in cols:
            conn.execute(f"ALTER TABLE notices ADD COLUMN {name} {typ}")
    conn.commit()


def reclassify_all(conn):
    ensure_notice_columns(conn)
    try:
        overrides = json.loads(OVERRIDES_PATH.read_text(encoding="utf-8")).get("notice_overrides", {})
    except Exception:
        overrides = {}
    rows = conn.execute("SELECT id,title,url,raw_json FROM notices").fetchall()
    counts = {}
    for row in rows:
        try:
            raw = json.loads(row["raw_json"] or "{}")
        except Exception:
            raw = {}
        result = classify_notice(row["title"], raw.get("content", ""))
        override = overrides.get(row["id"]) or overrides.get(row["url"] or "")
        if override:
            notice_type = override.get("notice_type", result.notice_type)
            subtype = override.get("notice_subtype", result.notice_subtype)
            confidence = 1.0
            reason = "人工校正：" + override.get("reason", "未填写原因")
            version = result.version + "+manual"
        else:
            notice_type, subtype, confidence = result.notice_type, result.notice_subtype, result.confidence
            reason, version = result.reason, result.version
        conn.execute("""
            UPDATE notices SET notice_type=?,notice_subtype=?,classification_confidence=?,
                classification_reason=?,classification_version=? WHERE id=?
        """, (notice_type, subtype, confidence, reason, version, row["id"]))
        counts[subtype] = counts.get(subtype, 0) + 1
    conn.commit()
    return counts


def _parse_day(value: str):
    if not value: return None
    m = re.match(r"(\d{4})[-/年](\d{1,2})[-/月](\d{1,2})", value)
    if not m: return None
    try: return date(*map(int, m.groups()))
    except ValueError: return None


def _stage(notices):
    subtypes = {n["notice_subtype"] for n in notices}
    if "terminated" in subtypes: return "terminated", "存在终止公告"
    if "award_final" in subtypes: return "awarded", "存在最终中标/成交公告"
    if "award_candidate" in subtypes: return "awarded", "存在中标候选人公示（尚无最终结果）"
    if "failed" in subtypes and "tender_reopen" not in subtypes: return "failed", "本轮流标/废标且未发现重新采购"

    tenders = [n for n in notices if n["notice_type"] == "tender"]
    if tenders:
        latest = max(tenders, key=lambda n: (n["publish_date"] or "", n["id"]))
        open_day = _parse_day(latest["bid_deadline"])
        today = date.today()
        if open_day == today: return "opening_today", "最新招标公告开标日为今天"
        if open_day and open_day > today: return "upcoming", "最新招标公告开标日在未来"
        if open_day and open_day < today: return "pending_result", "开标已过，未发现候选或中标结果"
        pub = _parse_day(latest["publish_date"])
        if pub and (today - pub).days > 30:
            return "pending_result", "缺少开标时间且公告已超过30天"
        return "upcoming_unknown_time", "缺少开标时间，暂归待开标"
    if "intention" in subtypes: return "intention", "仅发现采购意向/计划"
    if "suspended" in subtypes: return "suspended", "存在暂停公告"
    if "correction" in subtypes: return "correction", "仅发现更正/澄清公告"
    if "contract" in subtypes: return "contract", "仅发现合同公告"
    return "unknown", "公告尚未归并到明确项目阶段"


def _role(subtype):
    return {
        "tender_notice": "tender_primary", "tender_reopen": "tender_reopen",
        "award_candidate": "candidate", "award_final": "award_final",
        "intention": "intention", "correction": "correction", "failed": "failed",
        "terminated": "terminated", "suspended": "suspended", "contract": "contract",
    }.get(subtype, "other")


def _pick_base(tenders):
    """挑真正的招标公告作为项目基底（避免「最高限价公示」这类也被归为 tender 的子公告抢占 display_name）。"""
    if not tenders:
        return None
    for t in tenders:
        if any(k in t["title"] for k in
               ("招标公告", "采购公告", "竞争性磋商", "竞争性谈判",
                "询价公告", "比选公告", "征集公告", "谈判公告", "磋商公告")):
            return t
    return tenders[-1]


def build_projects(conn):
    conn.executescript(DDL)
    # 幂等迁移：已存在的 projects 表补 budget_type 列（2026-08-17）
    cols = [r[1] for r in conn.execute("PRAGMA table_info(projects)")]
    if "budget_type" not in cols:
        conn.execute("ALTER TABLE projects ADD COLUMN budget_type TEXT")
    conn.execute("DELETE FROM project_notices")
    conn.execute("DELETE FROM projects")
    notices = conn.execute("SELECT * FROM notices ORDER BY publish_date,id").fetchall()
    links = {r["award_id"]: r for r in conn.execute("SELECT * FROM project_links")}

    groups = {}
    notice_project = {}
    tender_key_to_pid = {}
    # 招标公告优先按同站+项目 ID 聚合；历史数据没有 project_key 时再回退标题。
    for n in notices:
        if n["notice_type"] != "tender": continue
        project_key = _project_key(n)
        if project_key:
            key = f"key:{stable_site_name(n['site'])}|{project_key}"
            map_key = (stable_site_name(n["site"]), project_key)
            pid = tender_key_to_pid.get(map_key)
            if not pid:
                pid = _pid(key)
                tender_key_to_pid[map_key] = pid
        else:
            key = f"name:{stable_site_name(n['site'])}|{norm_name(n['title'])}"
            pid = _pid(key)
        groups.setdefault(pid, []).append(n)
        notice_project[n["id"]] = pid

    # 中标公告优先跟随可信关联；无项目 ID 时再回退标题。
    for n in notices:
        if n["notice_type"] != "award": continue
        link = links.get(n["id"])
        pid = notice_project.get(link["tender_id"]) if link and link["tender_id"] else None
        if not pid:
            project_key = _project_key(n)
            if project_key:
                pid = tender_key_to_pid.get((stable_site_name(n["site"]), project_key))
        if not pid:
            pid = _pid(f"{stable_site_name(n['site'])}|{norm_name(n['title'])}")
        groups.setdefault(pid, []).append(n); notice_project[n["id"]] = pid

    # 意向和异常公告优先按项目 ID 挂接，再回退到标题。
    by_key = {f"{stable_site_name(n['site'])}|{norm_name(n['title'])}": notice_project[n["id"]]
              for n in notices if n["id"] in notice_project}
    by_project_key = {
        (stable_site_name(n["site"]), _project_key(n)): notice_project[n["id"]]
        for n in notices if n["id"] in notice_project and _project_key(n)
    }
    for n in notices:
        if n["id"] in notice_project: continue
        project_key = _project_key(n)
        if project_key:
            pid = by_project_key.get((stable_site_name(n["site"]), project_key))
        else:
            pid = None
        if not pid:
            key = f"{stable_site_name(n['site'])}|{norm_name(n['title'])}"
            pid = by_key.get(key) or _pid(key)
        groups.setdefault(pid, []).append(n); notice_project[n["id"]] = pid

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    for pid, rows in groups.items():
        rows = sorted(rows, key=lambda r: (r["publish_date"] or "", r["id"]))
        tenders = [r for r in rows if r["notice_type"] == "tender"]
        awards = [r for r in rows if r["notice_subtype"] == "award_final"]
        base = _pick_base(tenders) or rows[-1]
        final = awards[-1] if awards else None
        stage, reason = _stage(rows)
        link = links.get(final["id"]) if final else None
        if final:
            confidence = link["match_confidence"] if link and link["tender_id"] else "standalone"
        else:
            confidence = "none"
        conn.execute("""
            INSERT INTO projects (id, canonical_name, display_name, purchaser, site, district,
                                  major_category, minor_category, project_stage, stage_reason,
                                  first_notice_date, latest_notice_date, open_date, budget_value,
                                  budget_type, winner, winning_amount, link_confidence, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            pid, norm_name(base["title"]), base["title"], base["tenderer"], base["site"],
            base["std_district"], base["proj_major_cat"], base["proj_minor_cat"], stage,
            reason, rows[0]["publish_date"], rows[-1]["publish_date"], base["bid_deadline"],
            base["amount_value"], base["budget_type"], (final["winner"] if final else ""),
            (float(final["winning_amount"]) if final and str(final["winning_amount"] or "").replace('.','',1).isdigit() else None),
            confidence, now,
        ))
        for n in rows:
            link = links.get(n["id"])
            conn.execute("""
                INSERT INTO project_notices VALUES (?,?,?,?,?,?,?,0)
            """, (pid, n["id"], _role(n["notice_subtype"]),
                  link["match_type"] if link else None, link["match_score"] if link else None,
                  link["match_confidence"] if link else None, link["match_reasons"] if link else None))
    conn.commit()
    return len(groups)


def rebuild(db_path=DB_PATH):
    conn = sqlite3.connect(str(db_path)); conn.row_factory = sqlite3.Row
    counts = reclassify_all(conn)
    conn.close()
    # 关联器需在重分类后单独重建（2026-08-07 起 build 已并入本文件，直接调用）
    build(False)
    conn = sqlite3.connect(str(db_path)); conn.row_factory = sqlite3.Row
    project_count = build_projects(conn)
    stages = dict(conn.execute("SELECT project_stage,COUNT(*) FROM projects GROUP BY project_stage").fetchall())
    conn.close()
    print("公告子类型:", counts)
    print("项目数:", project_count, "项目状态:", stages)
    return {"subtypes": counts, "projects": project_count, "stages": stages}




# ─────────────────────────────────────────────
# 可信关联层（2026-08-07 并入：原 build_project_links.py）
# ─────────────────────────────────────────────
# -*- coding: utf-8 -*-
"""公告关联 v2：输出匹配分数、可信度、理由与未匹配原因。"""

import argparse
import json
import re
import sqlite3
from collections import defaultdict
from pathlib import Path

LINK_DATA_DIR = Path(__file__).parent / "data"
LINK_DB_PATH = LINK_DATA_DIR / "tender.db"
LINK_OVERRIDES_PATH = Path(__file__).parent / "manual_overrides.json"

DDL_PROJECT_LINKS = """
CREATE TABLE IF NOT EXISTS project_links (
    award_id        TEXT PRIMARY KEY REFERENCES notices(id),
    tender_id       TEXT REFERENCES notices(id),
    canonical_name  TEXT,
    match_type      TEXT,
    amendment_count INTEGER DEFAULT 0,
    match_score     INTEGER,
    match_confidence TEXT,
    match_reasons   TEXT,
    unmatched_reason TEXT
)
"""

DDL_VIEW = """
CREATE VIEW IF NOT EXISTS project_chain AS
SELECT
    t.id AS tender_id, t.title AS tender_name, t.site AS site_name,
    t.tenderer AS purchaser, t.amount_value AS budget,
    t.bid_deadline AS open_date, t.publish_date AS tender_date,
    a.id AS award_id, a.title AS award_name, a.winner,
    a.winning_amount, a.publish_date AS award_date,
    a.notice_subtype AS award_subtype,
    pl.match_type, pl.match_score, pl.match_confidence, pl.match_reasons,
    pl.amendment_count,
    CAST(ROUND(julianday(a.publish_date) - julianday(t.publish_date)) AS INTEGER) AS cycle_days
FROM project_links pl
JOIN notices t ON t.id=pl.tender_id AND t.notice_type='tender'
JOIN notices a ON a.id=pl.award_id AND a.notice_type='award'
WHERE pl.tender_id IS NOT NULL
"""

_LINK_PREFIX = re.compile(r"^(?:【[^】]*】\s*)+(?:关于\s*)?")
_LINK_SUFFIX = re.compile(
    r"(?:招标|采购|竞争性谈判|竞争性磋商|询价|比选)?公告\s*$"
    r"|中标结果(?:公告|公示)\s*$|中标候选人(?:公告|公示)\s*$"
    r"|成交结果(?:公告|公示)\s*$|成交公告\s*$|中选(?:公告|公示|结果公示)\s*$"
    r"|询价结果(?:公告|公示)\s*$|比选结果(?:公告|公示)\s*$|结果公示\s*$"
)
_LINK_BATCH_RE = re.compile(r"(?:第)?([一二三四五六七八九十\d]+)(?:次|标段|包)")


def _link_norm(name: str) -> str:
    n = _LINK_PREFIX.sub("", (name or "").strip()).strip()
    # 2026-08-11：先剥括号（含「（N次公示/公告）」，如“评标结果公示（二次公示）”），
    # 再剥公告类型后缀；否则括号阻挡后缀正则的 $ 锚定，导致名称超长、关联打分不足不合并。
    n = re.sub(r"[（(](?:一|二|三|四|五|六|七|八|九|十|\d+)[次批]?(?:(?:中标|成交|候选)?结果)?(?:公示|公告)?[）)]", "", n)
    n = _LINK_SUFFIX.sub("", n).strip()
    return re.sub(r"[\s　]+", "", n)


def _link_batch(name: str) -> str:
    m = _LINK_BATCH_RE.search(name or "")
    return m.group(1) if m else ""


def _link_ensure_schema(conn):
    conn.execute(DDL_PROJECT_LINKS)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(project_links)")}
    for name, typ in {
        "match_score": "INTEGER", "match_confidence": "TEXT",
        "match_reasons": "TEXT", "unmatched_reason": "TEXT",
    }.items():
        if name not in cols:
            conn.execute(f"ALTER TABLE project_links ADD COLUMN {name} {typ}")
    conn.execute("CREATE INDEX IF NOT EXISTS pl_tender ON project_links(tender_id)")
    conn.execute("DROP VIEW IF EXISTS project_chain")
    conn.execute(DDL_VIEW)
    conn.commit()


def _link_score(award, tender):
    ak, tk = _link_norm(award["title"]), _link_norm(tender["title"])
    reasons, score, mtype = [], 0, "none"
    if ak == tk:
        score += 55; reasons.append("项目名完全一致"); mtype = "exact"
    elif len(ak) >= 8 and len(tk) >= 8 and (ak in tk or tk in ak):
        score += 35; reasons.append("项目名子串匹配"); mtype = "substring"
    else:
        # 长名称相似度：公共字符二元组召回，避免完全依赖子串。
        ab = {ak[i:i+2] for i in range(max(0, len(ak)-1))}
        tb = {tk[i:i+2] for i in range(max(0, len(tk)-1))}
        sim = len(ab & tb) / max(1, len(ab | tb))
        if sim >= .72:
            score += 30; reasons.append(f"项目名相似度{sim:.0%}"); mtype = "similar"
        else:
            return 0, "none", [], "NO_NAME_CANDIDATE"

    if award["site"] == tender["site"]:
        score += 15; reasons.append("同一平台")
    if award["tenderer"] and tender["tenderer"] and award["tenderer"] == tender["tenderer"]:
        score += 15; reasons.append("采购单位一致")
    if (tender["publish_date"] or "") <= (award["publish_date"] or ""):
        score += 10; reasons.append("公告时间顺序合理")
    else:
        return 0, "none", reasons, "DATE_CONFLICT"
    if award["std_district"] and award["std_district"] == tender["std_district"]:
        score += 5; reasons.append("区县一致")

    abatch, tbatch = _link_batch(award["title"]), _link_batch(tender["title"])
    if abatch and tbatch and abatch != tbatch:
        score -= 30; reasons.append("采购批次冲突")

    return score, mtype, reasons, None


def _link_confidence(score: int) -> str:
    if score >= 85: return "high"
    if score >= 70: return "medium"
    if score >= 60: return "low"
    return "none"


def build(stats_only: bool = False):
    if not LINK_DB_PATH.exists():
        print("tender.db 不存在，请先运行 run.py")
        return
    conn = sqlite3.connect(str(LINK_DB_PATH)); conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL"); _link_ensure_schema(conn)

    tenders = conn.execute("""
        SELECT id,title,url,site,publish_date,tenderer,std_district,raw_json
        FROM notices WHERE notice_type='tender'
    """).fetchall()
    awards = conn.execute("""
        SELECT id,title,url,site,publish_date,tenderer,std_district,raw_json
        FROM notices WHERE notice_type='award'
    """).fetchall()
    try:
        overrides = json.loads(LINK_OVERRIDES_PATH.read_text(encoding="utf-8")).get("link_overrides", {})
    except Exception:
        overrides = {}
    tender_by_id = {t["id"]: t for t in tenders}
    tender_by_url = {r["url"]: r for r in conn.execute("SELECT * FROM notices WHERE notice_type='tender'") if r["url"]}
    tender_by_project_key = {}
    for tender in tenders:
        project_key = _project_key(tender)
        if project_key:
            tender_by_project_key[(stable_site_name(tender["site"]), project_key)] = tender

    by_prefix = defaultdict(list)
    for t in tenders:
        k = _link_norm(t["title"])
        if len(k) >= 4: by_prefix[k[:4]].append(t)

    output, stats = [], defaultdict(int)
    for award in awards:
        ak = _link_norm(award["title"])
        override = overrides.get(award["id"]) or overrides.get(award["url"] or "")
        if override:
            target = tender_by_id.get(override.get("tender_id")) or tender_by_url.get(override.get("tender_url"))
            if target:
                output.append((award["id"], target["id"], ak, "manual", 0, 100, "manual",
                               "人工确认：" + override.get("reason", "未填写原因"), None))
                stats["linked:manual"] += 1
            else:
                output.append((award["id"], None, ak, "manual", 0, 0, "none", "", "MANUAL_TARGET_NOT_FOUND"))
                stats["unmatched:MANUAL_TARGET_NOT_FOUND"] += 1
            continue

        # 苏服采两路 API 有 purchasesNeedId / pneedId；项目 ID 匹配优先于标题猜测。
        award_project_key = _project_key(award)
        if award_project_key:
            target = tender_by_project_key.get((stable_site_name(award["site"]), award_project_key))
            if target:
                output.append((award["id"], target["id"], ak, "project_key", 0, 100, "high",
                               "项目ID一致：" + award_project_key + "；采购单位/站点按项目ID归属", None))
                stats["linked:project_key"] += 1
                continue

        candidates = by_prefix.get(ak[:4], tenders)
        scored = []
        fail_reasons = []
        for tender in candidates:
            score, mtype, reasons, fail = _link_score(award, tender)
            if fail: fail_reasons.append(fail)
            if score > 0: scored.append((score, tender, mtype, reasons))
        scored.sort(key=lambda x: (x[0], x[1]["publish_date"] or ""), reverse=True)

        if not scored or scored[0][0] < 70:
            reason = "LOW_CONFIDENCE" if scored else (fail_reasons[0] if fail_reasons else "NO_NAME_CANDIDATE")
            output.append((award["id"], None, ak, "none", 0, scored[0][0] if scored else 0,
                           "none", "", reason))
            stats[f"unmatched:{reason}"] += 1
            continue

        score, tender, mtype, reasons = scored[0]
        if len(scored) > 1 and scored[1][0] >= score - 5 and scored[1][1]["id"] != tender["id"]:
            output.append((award["id"], None, ak, "none", 0, score, "none",
                           "；".join(reasons), "MULTIPLE_CANDIDATES"))
            stats["unmatched:MULTIPLE_CANDIDATES"] += 1
            continue

        conf = _link_confidence(score)
        output.append((award["id"], tender["id"], ak, mtype, 0, score, conf,
                       "；".join(reasons), None))
        stats[f"linked:{conf}"] += 1

    print(f"award 总数: {len(awards)}")
    for k in sorted(stats): print(f"  {k}: {stats[k]}")
    if stats_only:
        conn.close(); return

    conn.execute("DELETE FROM project_links")
    conn.executemany("""
        INSERT INTO project_links
        (award_id,tender_id,canonical_name,match_type,amendment_count,
         match_score,match_confidence,match_reasons,unmatched_reason)
        VALUES (?,?,?,?,?,?,?,?,?)
    """, output)
    conn.commit(); conn.close()
    print(f"project_links 写入 {len(output)} 条（含未匹配记录）")


def report():
    conn = sqlite3.connect(str(LINK_DB_PATH)); conn.row_factory = sqlite3.Row; _link_ensure_schema(conn)
    for row in conn.execute("""
        SELECT COALESCE(match_confidence,'none') confidence, COUNT(*) n
        FROM project_links GROUP BY COALESCE(match_confidence,'none') ORDER BY n DESC
    """): print(f"{row['confidence']}: {row['n']}")
    conn.close()


def main():
    p=argparse.ArgumentParser(); p.add_argument("--stats",action="store_true"); p.add_argument("--report",action="store_true"); a=p.parse_args()
    report() if a.report else build(a.stats)

if __name__ == "__main__": rebuild()
