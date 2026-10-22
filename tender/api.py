#!/usr/bin/env python3
"""招投标区县人工修订 API — 驾驶舱读写接口（2026-08-16，2026-08-16 晚迁入 SQLite）

让老板在网页上直接修改项目归属区县（修正自动打标错误），
修改持久化到 tender.db 的 district_overrides 独立表（不参与 projects 重建清空，
采集重跑 / 部署重建都不丢；key=项目 id，采集后 id 稳定不变）。

接口：
  GET    /api/tender/district_overrides         # 读全部修订 {project_id: district}
  PUT    /api/tender/district                   # 写修订  body: {"project_id","district"} district 可为 "" 表示不标注
  DELETE /api/tender/district?project_id=xxx    # 撤销修订（恢复自动打标）
  GET    /api/tender/health                     # 健康检查

鉴权：由 nginx 层 Basic Auth 保护（location /api/tender/ 转发到本服务）。

启动：systemctl start tender-district-api.service（systemd 托管，端口 8002）
"""
import os
import re
import json
import sqlite3
import datetime
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(HERE, "data")
DB_PATH = os.path.join(HERE, "collector", "data", "tender.db")
RUN_STATE_PATH = os.path.join(HERE, "collector", "logs", "run_state.json")
PORT = 8002


def data_state():
    """数据状态三态（2026-10-15 stale 软提示版，老板拍板：抓取不打扰、改数据短暂提示）。

    返回 {"data_state": "ok"|"stale"|"blocked", "phase": "...", ...}。
    - ok      正常（无部署/已收工）
    - stale   软维护：DB SELECT 仍可工作（starting/collect/backfill/audit/screen/daily），
              响应附 _meta 让前端展示 banner 而不是清页
    - blocked 硬维护：DB 不可读（rebuild/verify/publish），handler 返 503

    旧 maintenance_state 把 7 个 phase 一刀切，collect 期间也拦 503 导致前端整页空白。
    """
    try:
        with open(RUN_STATE_PATH, encoding="utf-8") as f:
            full = json.load(f)
        dep = full.get("deployment", {})
        if dep.get("status") != "running":
            return {"data_state": "ok", "phase": ""}
        phase = dep.get("phase", "")
        if phase in ("rebuild", "verify", "publish"):
            return {"data_state": "blocked", "phase": phase,
                    "updated_at": dep.get("updated_at", "")}
        if phase in ("starting", "collect", "backfill", "audit", "screen", "daily"):
            return {"data_state": "stale", "phase": phase,
                    "updated_at": dep.get("updated_at", ""),
                    "last_published_at": full.get("manifest", {}).get("generated_at", "")}
    except Exception:
        pass
    return {"data_state": "ok", "phase": ""}

# 合法区县值（与前端 DISTRICTS 的 match 原始值一致；"" = 不标注）
VALID_DISTRICTS = {
    "", "市级", "盐南", "经开", "盐都", "亭湖", "大丰",
    "响水", "滨海", "阜宁", "射阳", "建湖", "东台",
}

# 项目 id 格式：p_<32位hex>（project_engine._pid）
PID_RE = re.compile(r"^p_[0-9a-f]{32}$")

# 收藏桶（2026-08-17 服务端同步）：tender=待投标 / intention=代挂网
VALID_BUCKETS = ("tender", "intention")

# 合法用户（2026-08-19 多账号隔离收藏）：nginx basic auth 通过后透传 X-Remote-User。
# 此处为额外白名单：header 不在列表内 → fallback 到 DEFAULT_USER（保证老前端不报错）。
# 新增账号：先在 nginx htpasswd 添加用户，再把账号同步加到该列表。
VALID_USERS = ("yankai", "liutao", "xuezc", "wangyw")  # 2026-08-19 首批账号
DEFAULT_USER = "yankai"  # 旧收藏迁移目标（老数据归属该用户）

# ── 懒加载查询（2026-09-07 P1）：projects 表只读分页查询 ──
# 前端首页为 API-only 按需查询（2026-09-29 起，全量 tender.json 已下线），本节为服务端。
# 站点 category 映射（type=政府/企业 筛选用；与 config.SITES 同源，读库免依赖）
import sys as _sys
_sys.path.insert(0, os.path.join(HERE, "collector"))
try:
    from config import SITES as _SITES_CFG
except Exception:
    _SITES_CFG = {}

# 金额档位 → budget_value 区间。单位：元。
# 2026-09-08 P0 修复：库内 budget_value 存的是元（实测均价 2114 万元），
# 旧实现按万元直写 (0,10) 等 → 选「10-100万」实际查 10~100 元，命中 4 条。
# key 契约保持不变（与前端 lazy amountOptions 一致），仅修正区间数值。
_AMOUNT_BANDS = {
    "0-10":     (0, 100000),
    "10-100":   (100000, 1000000),
    "100-500":  (1000000, 5000000),
    "500-2000": (5000000, 20000000),
    "2000+":    (20000000, None),
}

# 前端导航入口 → project_stage 集合（与 tender.js getProjectStage/导航映射同口径）
# 2026-09-08 第二批：all=进行中（排除 awarded）与 nav 角标对齐；countdown=7 天开标窗口
#（两入口在 _projects_where 特殊分支处理，不查本表；publicity 保留映射兼容旧调用）
_STAGE_GROUPS = {
    "all":       None,  # 特殊：非 awarded（见 _projects_where）
    "countdown": None,  # 特殊：7 天开标窗口（见 _projects_where）
    "upcoming":  ("upcoming", "upcoming_unknown_time", "opening_today"),
    "intention": ("intention",),
    "awarded":   ("awarded",),
    "pending_result": ("pending_result",),
    "candidate": ("upcoming",),  # 前端 candidate 为动态倒计时，后端给 upcoming 兼容
    "exceptions": ("failed", "terminated", "suspended", "correction", "contract", "unknown"),
    "publicity": ("awarded",),
}


def ensure_indexes():
    """懒加载查询索引（幂等；2026-09-07 P1）。projects 表此前只有主键索引。"""
    conn = _conn()
    for ddl in (
        "CREATE INDEX IF NOT EXISTS idx_projects_stage ON projects(project_stage)",
        "CREATE INDEX IF NOT EXISTS idx_projects_district ON projects(district)",
        "CREATE INDEX IF NOT EXISTS idx_projects_major ON projects(major_category)",
        "CREATE INDEX IF NOT EXISTS idx_projects_latest ON projects(latest_notice_date DESC)",
        "CREATE INDEX IF NOT EXISTS idx_projects_stage_dist ON projects(project_stage, district)",
        "CREATE INDEX IF NOT EXISTS idx_projects_latest_id ON projects(latest_notice_date DESC, id DESC)",
        "CREATE INDEX IF NOT EXISTS idx_pn_project ON project_notices(project_id)",
        "CREATE INDEX IF NOT EXISTS idx_projects_ict ON projects(is_ict)",
    ):
        conn.execute(ddl)
    conn.commit()
    conn.close()


def _site_category(site):
    return (_SITES_CFG.get(site) or {}).get("category", "")


def _projects_where(params):
    """查询参数 → (where_sql, args, sort)。与前端 filterByXxx 同口径。"""
    where = ["site NOT IN ('瑞康医院')"]
    args = []

    def _in_list(col, raw, cast=str):
        # parse_qs 返回 list（逗号串可拆多值）；兼容 str/list 两种入参
        if isinstance(raw, list):
            vals = []
            for item in raw:
                vals.extend(cast(v.strip()) for v in str(item).split(",") if v.strip())
        else:
            vals = [cast(v.strip()) for v in str(raw or "").split(",") if v.strip()]
        vals = list(dict.fromkeys(vals))
        if not vals:
            return
        ph = ",".join("?" * len(vals))
        where.append(f"{col} IN ({ph})")
        args.extend(vals)

    # stage（导航入口组映射；2026-09-08 第二批：all/countdown 特殊分支）
    stage = (params.get("stage") or [""])[0].strip()
    if stage == "countdown":
        # 开标倒计时真语义：未开标且开标日在未来 7 天内（含今天）
        where.append("project_stage IN ('upcoming','upcoming_unknown_time','opening_today') "
                     "AND open_date != '' AND date(open_date) >= date('now') "
                     "AND date(open_date) <= date('now','+7 day')")
    elif stage == "all":
        # 全部=进行中（2026-09-08 老板拍板）：排除已中标，与 nav 角标同口径
        where.append("project_stage != 'awarded'")
    elif stage and stage in _STAGE_GROUPS and _STAGE_GROUPS[stage]:
        sts = _STAGE_GROUPS[stage]
        ph = ",".join("?" * len(sts))
        where.append(f"project_stage IN ({ph})")
        args.extend(sts)
    # 区县（2026-09-07：支持逗号多值——盐开区=盐南,经开；none=非盐城项目）
    district = (params.get("district") or [""])[0].strip()
    if district == "none":
        where.append("(district IS NULL OR district='')")
    elif district:
        dvals = [v.strip() for v in district.split(",") if v.strip()]
        if dvals:
            ph = ",".join("?" * len(dvals))
            where.append(f"district IN ({ph})")
            args.extend(dvals)
    # 行业多选（2026-09-08 饼图跳转支持：「未分类」哨兵 → major_category 空/NULL）
    cat_vals = []
    raw_cat = params.get("cat") or []
    for item in (raw_cat if isinstance(raw_cat, list) else [raw_cat]):
        cat_vals.extend(v.strip() for v in str(item).split(",") if v.strip())
    cat_vals = list(dict.fromkeys(cat_vals))
    cat_real = [v for v in cat_vals if v != "未分类"]
    if cat_real:
        ph = ",".join("?" * len(cat_real))
        if "未分类" in cat_vals:
            where.append(f"(major_category IN ({ph}) OR major_category IS NULL OR major_category='')")
        else:
            where.append(f"major_category IN ({ph})")
        args.extend(cat_real)
    elif "未分类" in cat_vals:
        where.append("(major_category IS NULL OR major_category='')")
    # 站点多选
    _in_list("site", params.get("site"))
    # 政府/企业
    tp = (params.get("type") or [""])[0].strip()
    if tp:
        like_vals = [s for s in _SITES_CFG if _SITES_CFG[s].get("category", "") == tp]
        if like_vals:
            ph = ",".join("?" * len(like_vals))
            where.append(f"site IN ({ph})")
            args.extend(like_vals)
    # ICT（2026-09-09 性能优化：改用 projects.is_ict 物化列，与旧 EXISTS 口径
    # 已全组合对账一致；project_engine.rebuild 同步写列。旧 EXISTS 全表 120ms→6ms）
    ict = (params.get("ict") or [""])[0].strip()
    if ict == "1":
        where.append("p.is_ict = 1")
    elif ict == "0":
        where.append("COALESCE(p.is_ict, 0) = 0")
    # 中小微（any 子公告 sme_target 命中）
    sme = (params.get("sme") or [""])[0].strip()
    if sme:
        where.append("EXISTS (SELECT 1 FROM project_notices pn JOIN notices n ON n.id=pn.notice_id "
                     "WHERE pn.project_id=p.id AND n.sme_target=?)")
        args.append(sme)
    # 金额档位
    amount = (params.get("amount") or [""])[0].strip()
    if amount in _AMOUNT_BANDS:
        lo, hi = _AMOUNT_BANDS[amount]
        where.append("budget_value IS NOT NULL AND budget_value >= ?")
        args.append(lo)
        if hi is not None:
            where[-1] += " AND budget_value < ?"
            args.append(hi)
    # 发布日期（latest_notice_date）
    df = (params.get("date_from") or [""])[0].strip()
    dt = (params.get("date_to") or [""])[0].strip()
    if df:
        where.append("latest_notice_date >= ?"); args.append(df)
    if dt:
        where.append("latest_notice_date <= ?"); args.append(dt + "~")
    # 开标时间
    of = (params.get("open_from") or [""])[0].strip()
    ot = (params.get("open_to") or [""])[0].strip()
    if of:
        where.append("open_date >= ?"); args.append(of)
    if ot:
        where.append("open_date <= ?"); args.append(ot + "~")
    # 关键词（标题/发包人）
    q = (params.get("q") or [""])[0].strip()
    if q:
        where.append("(display_name LIKE ? OR purchaser LIKE ?)")
        args.extend([f"%{q}%", f"%{q}%"])

    order = "latest_notice_date DESC, id DESC"
    sort = (params.get("sort") or [""])[0].strip()
    if sort == "open":
        order = "open_date IS NULL, open_date ASC, latest_notice_date DESC"
    elif sort == "budget":
        order = "budget_value IS NULL, budget_value DESC"
    return " AND ".join(where), args, order


def _row_to_item(r):
    """projects 行 → 前端 item（字段名与 collectAllItems 对齐，渲染层零改动）。
    tender_url/award_url/cycle_days 列在旧 schema 可能不存在，安全取值。"""
    def _g(key, default=""):
        try:
            v = r[key]
            return v if v is not None else default
        except (IndexError, KeyError):
            return default
    return {
        "id": r["id"], "title": r["display_name"], "canonical_name": r["canonical_name"],
        # 2026-09-08 前端断层修复：双键名——渲染层消费 project_stage/tenderer，
        # 旧键 stage/purchaser 保留兼容（详情接口/外部调用方在用）
        "stage": r["project_stage"], "project_stage": r["project_stage"],
        "stage_reason": r["stage_reason"],
        "purchaser": r["purchaser"] or "", "tenderer": r["purchaser"] or "",
        "site": r["site"] or "",
        "siteCategory": _site_category(r["site"] or ""),
        "std_district": r["district"] or "", "district_raw": r["district"] or "",
        "district_label": r["district"] or "未划分",
        "proj_major_cat": r["major_category"] or "", "proj_minor_cat": r["minor_category"] or "",
        "bid_deadline": r["open_date"] or "", "budget": r["budget_value"],
        "budget_type": r["budget_type"] or "", "winner": r["winner"] or "",
        "winning_amount": r["winning_amount"], "link_confidence": r["link_confidence"] or "none",
        "date": r["latest_notice_date"] or "", "first_date": r["first_notice_date"] or "",
        "latest_date": r["latest_notice_date"] or "",
        "tender_url": _g("tender_url"), "award_url": _g("award_url"),
        # 2026-09-09 修复：award/tender 均无时回退最新公告 url（对齐全量 JSON
        # notices.at(-1).url 口径）。此前空 url 会让前端渲染 <a href="">，
        # 点击被浏览器解析为当前页 → 触发登录守卫跳 login。
        "url": _g("award_url") or _g("tender_url") or _g("fallback_url"),
        # 2026-09-29：last_seen 对齐全量 JSON 口径（抓取命中时间）；无子查询列的调用方
        # （如 query_updates 纯 p.* 行）退回 updated_at，该端点不消费此字段，无影响。
        "cycle_days": _g("cycle_days"),
        "last_seen": _g("proj_last_seen") or r["updated_at"] or "",
    }


def _url_subqueries():
    """tender/award URL 动态子查询（与 run.py export 口径一致：tender_primary 最早一条 / award_final 最新一条）。"""
    tender_url = """(SELECT COALESCE(json_extract(n2.raw_json,'$.actual_detail_url'), n2.url)
        FROM project_notices pn2 JOIN notices n2 ON n2.id=pn2.notice_id
        WHERE pn2.project_id=p.id AND pn2.relation_role IN ('tender_primary','tender_reopen')
        ORDER BY n2.publish_date ASC, n2.id ASC LIMIT 1) AS tender_url"""
    award_url = """(SELECT COALESCE(json_extract(n2.raw_json,'$.actual_detail_url'), n2.url)
        FROM project_notices pn2 JOIN notices n2 ON n2.id=pn2.notice_id
        WHERE pn2.project_id=p.id AND pn2.relation_role='award_final'
        ORDER BY n2.publish_date DESC, n2.id DESC LIMIT 1) AS award_url"""
    cycle = """(SELECT CAST(ROUND(julianday(a.publish_date) - julianday(t.publish_date)) AS INTEGER)
        FROM project_notices pnt JOIN notices t ON t.id=pnt.notice_id AND pnt.relation_role IN ('tender_primary','tender_reopen'),
             project_notices pna JOIN notices a ON a.id=pna.notice_id AND pna.relation_role='award_final'
        WHERE pnt.project_id=p.id AND pna.project_id=p.id LIMIT 1) AS cycle_days"""
    # 兜底 url：无 tender/award 角色公告的项目（仅更正/合同/意向等），取最新一条公告原文
    fallback = """(SELECT COALESCE(json_extract(n2.raw_json,'$.actual_detail_url'), n2.url)
        FROM project_notices pn2 JOIN notices n2 ON n2.id=pn2.notice_id
        WHERE pn2.project_id=p.id AND COALESCE(json_extract(n2.raw_json,'$.actual_detail_url'), n2.url, '') <> ''
        ORDER BY n2.publish_date DESC, n2.id DESC LIMIT 1) AS fallback_url"""
    # 2026-09-29 双链路对齐：项目 last_seen = 公告最近一次被列表/API 命中的抓取时间
    # （与 run.py 导出 max(notices.last_seen) 同口径；旧版 _row_to_item 用 projects.updated_at
    # 属项目重建时间，弹窗「系统核对至」随取数路径漂移）
    seen = """(SELECT MAX(n2.last_seen) FROM project_notices pn2 JOIN notices n2 ON n2.id=pn2.notice_id
        WHERE pn2.project_id=p.id) AS proj_last_seen"""
    return tender_url, award_url, cycle, fallback, seen


def _enrich_items(conn, rows):
    """2026-09-09 性能优化：把每行 2 次相关子查询（subtype + ict）改为按页批量。
    返回 items：含 notice_subtype / ict（读 is_ict 物化列）/ update_notice（内联，
    形状同 /updates 单条，前端有则跳过二次请求）/ 区县人工修订覆盖（同 detail 口径）。
    一次 join 查询拿齐页内全部公告时间线，python 按项目分组计算。"""
    ids = [r["id"] for r in rows]
    if not ids:
        return []
    ph = ",".join("?" * len(ids))
    notes = conn.execute(
        f"""SELECT pn.project_id, n.publish_date, n.notice_subtype, n.notice_type,
                   n.title, pn.relation_role, n.ict_keywords,
                   COALESCE(json_extract(n.raw_json,'$.actual_detail_url'), n.url) AS eff_url
            FROM project_notices pn JOIN notices n ON n.id=pn.notice_id
            WHERE pn.project_id IN ({ph})
            ORDER BY pn.project_id, n.publish_date ASC, n.id ASC""", ids).fetchall()
    by_pid = {}
    for nr in notes:
        by_pid.setdefault(nr["project_id"], []).append(nr)
    ov = load_overrides()
    ovk = load_overrides_by_key()
    items = []
    for r in rows:
        it = _row_to_item(r)
        plist = by_pid.get(r["id"]) or []
        # subtype：最新一条公告（同旧 ORDER BY publish_date DESC, id DESC LIMIT 1）
        it["notice_subtype"] = plist[-1]["notice_subtype"] if plist else ""
        # 2026-09-28：前端角标兜底用——最新一条公告是 correction 时，列表角标改成「更正/澄清」
        # 避免「又推送了一遍招标公告」的误读（项目阶段仍是 upcoming，但最新公告是更正）
        it["latest_notice_type"] = plist[-1]["notice_type"] if plist else ""
        it["latest_notice_subtype"] = plist[-1]["notice_subtype"] if plist else ""
        # ICT 标记：读物化列（旧每行 EXISTS 子查询），保持前端 ict===1 消费不变
        it["ict"] = r["is_ict"] or 0
        # 2026-09-10 修复：ICT 关键词（前端标题旁「ICT 命中：…」tooltip + 卡片关键词行）。
        # 口径同全量导出：关联公告 ict_keywords 并集、sorted。此前 lazy 不返回该字段，
        # 前端 it.keywords = it.keywords || [] 恒为空 → tooltip 只有前缀没内容。
        kws = set()
        for nr in plist:
            try:
                kws.update(json.loads(nr["ict_keywords"] or "[]"))
            except Exception:
                pass
        it["keywords"] = sorted(kws)
        # 更新角标内联（同 query_updates 语义：主公告 = tender_primary/reopen 最早，
        # 更新 = 其后非 tender 公告取最新；url 用 eff_url 对齐全量导出/新路由口径）
        main = next((nr["publish_date"] for nr in plist
                     if nr["relation_role"] in ("tender_primary", "tender_reopen")), None)
        if main:
            upd = None
            for nr in plist:
                if (nr["relation_role"] not in ("tender_primary", "tender_reopen")
                        and nr["notice_type"] != "tender"
                        and (nr["publish_date"] or "") > main):
                    upd = {"title": nr["title"], "url": nr["eff_url"] or "",
                           "date": nr["publish_date"], "notice_type": nr["notice_type"],
                           "notice_subtype": nr["notice_subtype"]}
            if upd:
                it["update_notice"] = upd
        # 区县人工修订（同 detail 端点口径；label 由前端据 std_district 重算）
        # 2026-09-11：按 id 命中优先，否则按归一项目名兜底（免疫项目 id 漂移）
        _ovv = ov.get(it["id"])
        if _ovv is None:
            _ovv = ovk.get(_norm_name(it.get("title") or ""))
        if _ovv:
            it["std_district"] = _ovv
            it["district_raw"] = _ovv
            it["district_overridden"] = True
        items.append(it)
    return items


def query_projects(params):
    """分页查询 → {total, page, size, items}。items 含 latest_subtype 供列表 badge。"""
    conn = _conn()
    tu, au, cd, fu, seen = _url_subqueries()
    where, args, order = _projects_where(params)
    sort_used = order
    total = conn.execute(
        f"SELECT COUNT(*) FROM projects p WHERE {where}", args).fetchone()[0]
    page = max(1, int((params.get("page") or ["1"])[0] or 1))
    size = min(200, max(1, int((params.get("size") or ["40"])[0] or 40)))
    # 2026-09-07 M4 深翻页游标化：cursor="<latest_notice_date>|<id>" 走 keyset 分页
    # （WHERE (latest_notice_date,id) < (?,?) ORDER BY DESC LIMIT），避免 OFFSET 深翻
    # 扫掉前 N 万行（压测 page=2000 曾 1.5s）。仅默认排序（latest）可用；页码跳转
    # 仍走 page/OFFSET（低频，可接受）。
    cursor = (params.get("cursor") or [""])[0].strip()
    if cursor and "|" in cursor and sort_used == "latest_notice_date DESC, id DESC":
        c_date, c_id = cursor.split("|", 1)
        rows = conn.execute(
            f"SELECT p.*,{tu},{au},{cd},{fu},{seen} FROM projects p WHERE {where} "
            "AND (latest_notice_date, id) < (?, ?) "
            f"ORDER BY {order} LIMIT ?",
            args + [c_date, c_id, size]).fetchall()
        page = page  # 游标模式 page 仅用于响应回显
    else:
        offset = (page - 1) * size
        rows = conn.execute(
            f"SELECT p.*,{tu},{au},{cd},{fu},{seen} FROM projects p WHERE {where} ORDER BY {order} LIMIT ? OFFSET ?",
            args + [size, offset]).fetchall()
    items = _enrich_items(conn, rows)
    conn.close()
    return {"total": total, "page": page, "size": size, "items": items}


# ── 收藏夹视图批量查询（2026-09-08 P0 修复）──
# 旧前端实现对每条收藏（≤200）各发一次 q=LIKE 查询（串行 N+1），且 lazy 化后
# 依赖恒空的 _allItems → 收藏视图永远 0 条。改为前端 POST keys（归一化项目名），
# 后端单遍全表扫描归一匹配，返回与 /projects 同 shape 的 items。
_FAV_JS_SUFFIX = (r"(?:采购|招标|询价|竞争性磋商|比选)?"
                  r"(?:公告|结果公示|中标结果公示|中标候选人公示|成交公告|成交结果公示)$")

# 归一名索引缓存（2026-09-08 性能修复）：收藏匹配每次全表正则归一要 ~220ms，
# 而库只在主抓（3h 一轮）时变，故缓存。
# 2026-09-10 失效机制修复：旧实现按请求新建连接取 PRAGMA data_version 再与缓存比，
# 但 data_version 只在**同一连接内**可比（同一连接才见外部提交递增）；新建连接恒返回
# 初值 2 → 比较永远相等 → 缓存从服务启动起再不重建。症状：主抓后新建项目的收藏
# （待投标/待挂网）匹配静默返回 0、不展示。改为长期存活探针连接取变更号 + TTL 兜底。
_NORM_CACHE = {"dv": None, "ts": 0.0, "map": {}}
_NORM_CACHE_TTL = 300.0   # 秒；兜底防探针连接失活/库文件被替换等极端情况
_DV_CONN = {"conn": None}


def _db_change_token():
    """库变更号：必须用长期存活连接读取（见上方注释）。取不到返回 None（调用方退化为 TTL）。"""
    try:
        c = _DV_CONN["conn"]
        if c is None:
            c = sqlite3.connect(DB_PATH)
            _DV_CONN["conn"] = c
        return c.execute("PRAGMA data_version").fetchone()[0]
    except Exception:
        try:
            if _DV_CONN["conn"] is not None:
                _DV_CONN["conn"].close()
        except Exception:
            pass
        _DV_CONN["conn"] = None
        return None


def _norm_index(conn):
    dv = _db_change_token()
    now = time.time()
    changed = dv is not None and _NORM_CACHE["dv"] != dv
    expired = (now - (_NORM_CACHE["ts"] or 0.0)) > _NORM_CACHE_TTL
    if not _NORM_CACHE["map"] or changed or expired:
        m = {}
        for r in conn.execute("SELECT id, display_name, latest_notice_date FROM projects "
                              "WHERE site NOT IN ('瑞康医院')"):
            k = _norm_name(r["display_name"])
            cur = m.get(k)
            if cur is None or (r["latest_notice_date"] or "") > cur[0]:
                m[k] = ((r["latest_notice_date"] or ""), r["id"])
        _NORM_CACHE.update(dv=dv, ts=now, map=m)
    return _NORM_CACHE["map"]


def _norm_name(name):
    """与前端 tender.js normalizeProjectName 同一套正则（去括号/去公告后缀/去空白）。"""
    s = re.sub(r"[【】\[\]()（）]", "", str(name or ""))
    s = re.sub(_FAV_JS_SUFFIX, "", s)
    return re.sub(r"\s+", "", s).strip()


def query_fav_projects(payload):
    keys, seen_k = [], set()
    for k in (payload.get("keys") or []):
        k = str(k).strip()
        if k and k not in seen_k:
            seen_k.add(k)
            keys.append(k)
    keys = keys[:500]
    if not keys:
        return {"items": [], "matched": 0}
    conn = _conn()
    # Stage 1：命中查询走进程内归一名索引（data_version 变化才重建）
    idx = _norm_index(conn)
    best = {}
    for k in keys:
        v = idx.get(k)
        if v:
            best[k] = v
    ids = [v[1] for v in best.values()]
    if not ids:
        conn.close()
        return {"items": [], "matched": 0}
    # Stage 2：仅对命中的 ≤500 个 id 取完整行 + subtype/ICT/角标/修订富化（批量）
    tu, au, cd, fu, seen = _url_subqueries()
    ph = ",".join("?" * len(ids))
    rows = conn.execute(
        f"SELECT p.*,{tu},{au},{cd},{fu},{seen} FROM projects p WHERE p.id IN ({ph})", ids).fetchall()
    items = _enrich_items(conn, rows)
    conn.close()
    return {"items": items, "matched": len(best)}


def query_ict_dist(params):
    """2026-09-08 ICT 饼图弹窗：ict=1 + 与列表同款 where（district 等）→ 行业 GROUP BY。
    返回 {total, cats:{行业:条数}}，空行业计入「未分类」。全库实测 0.1s。"""
    p = dict(params)
    p["ict"] = ["1"]
    p.pop("cat", None)  # 饼图展示全部行业分布，忽略行业自身筛选
    where, args, _order = _projects_where(p)
    conn = _conn()
    rows = conn.execute(
        f"SELECT COALESCE(major_category,'') m, COUNT(*) c FROM projects p "
        f"WHERE {where} GROUP BY m", args).fetchall()
    conn.close()
    cats = {}
    for r in rows:
        key = r["m"] or "未分类"
        cats[key] = cats.get(key, 0) + r["c"]
    return {"total": sum(cats.values()), "cats": cats}


def query_meta():
    """首屏元数据：summary + 区县/阶段/行业/站点计数（一次返回，替代前端内存遍历计数）。"""
    conn = _conn()
    summary = {
        "total": conn.execute("SELECT COUNT(*) FROM notices WHERE site NOT IN ('瑞康医院')").fetchone()[0],
        "projects": conn.execute("SELECT COUNT(*) FROM projects WHERE site NOT IN ('瑞康医院')").fetchone()[0],
        # 2026-09-09：改读 is_ict 物化列（与旧 DISTINCT join 口径已对账，3901=3901）
        "ict": conn.execute("SELECT COUNT(*) FROM projects WHERE COALESCE(is_ict,0)=1 "
                            "AND site NOT IN ('瑞康医院')").fetchone()[0],
        "sites": conn.execute("SELECT COUNT(DISTINCT site) FROM projects "
                              "WHERE site NOT IN ('瑞康医院')").fetchone()[0],
    }
    district_counts = {r[0] or "": r[1] for r in conn.execute(
        "SELECT COALESCE(district,'') d, COUNT(*) FROM projects WHERE site NOT IN ('瑞康医院') GROUP BY d")}
    stage_counts = {r[0]: r[1] for r in conn.execute(
        "SELECT project_stage, COUNT(*) FROM projects WHERE site NOT IN ('瑞康医院') GROUP BY project_stage")}
    # 2026-09-30：补 publicity（中标公示）计数——isPublicity() 判定：project_stage='awarded' 且
    # display_name 含"中标公示/结果公示/成交公示"。publicity 是 awarded 的子集，前端两按钮分开列表。
    # 前端原使用 sc.awarded 代替导致两按钮数字恒等（Bug 2）；补独立计数后修复。
    stage_counts["publicity"] = conn.execute(
        "SELECT COUNT(*) FROM projects WHERE site NOT IN ('瑞康医院') "
        "AND project_stage='awarded' "
        "AND (display_name LIKE '%中标公示%' OR display_name LIKE '%结果公示%' OR display_name LIKE '%成交公示%')"
    ).fetchone()[0]
    # 2026-09-08 第二批：倒计时（未来 7 天开标，与 /projects?stage=countdown 同口径）单独计，
    # 不入 stage_counts（避免前端求和重复计数）
    special_counts = {"countdown": conn.execute(
        "SELECT COUNT(*) FROM projects WHERE site NOT IN ('瑞康医院') "
        "AND project_stage IN ('upcoming','upcoming_unknown_time','opening_today') "
        "AND open_date != '' AND date(open_date) >= date('now') "
        "AND date(open_date) <= date('now','+7 day')").fetchone()[0]}
    cat_counts = {r[0] or "未分类": r[1] for r in conn.execute(
        "SELECT COALESCE(major_category,'') m, COUNT(*) FROM projects WHERE site NOT IN ('瑞康医院') GROUP BY m")}
    site_meta = []
    for r in conn.execute("""SELECT p.site, COUNT(*) total,
                SUM(CASE WHEN p.district IN ('盐南','经开') THEN 1 ELSE 0 END) yankai,
                SUM(CASE WHEN COALESCE(p.is_ict,0)=1 THEN 1 ELSE 0 END) ict,
                MIN(p.first_notice_date) data_start, MAX(p.latest_notice_date) data_until,
                SUM(CASE WHEN p.latest_notice_date >= date('now','start of month') THEN 1 ELSE 0 END) monthly,
                (SELECT MAX(n.last_seen) FROM notices n WHERE n.site=p.site) last_seen
                FROM projects p WHERE p.site NOT IN ('瑞康医院') GROUP BY p.site ORDER BY total DESC"""):
        cfg = _SITES_CFG.get(r["site"]) or {}
        site_meta.append({
            "name": r["site"], "total": r["total"], "yankai": r["yankai"], "ict": r["ict"] or 0,
            "data_start": r["data_start"] or "", "data_until": r["data_until"] or "",
            "monthly_count": r["monthly"] or 0, "last_seen": r["last_seen"] or "",
            "category": cfg.get("category", ""), "scrape_method": cfg.get("scrape_method", ""),
            "retention": cfg.get("retention", ""), "url": cfg.get("display_url") or cfg.get("url") or "",
        })
    gen = conn.execute("SELECT MAX(updated_at) FROM projects").fetchone()[0] or ""
    summary["generated_at"] = gen[:16]   # "YYYY-MM-DD HH:MM"（rebuild 完成时刻，数据更新卡片消费）
    conn.close()
    return {"summary": summary, "district_counts": district_counts,
            "stage_counts": stage_counts, "special_counts": special_counts,
        "cat_counts": cat_counts, "site_meta": site_meta}


_META_CACHE = {"t": 0.0, "data": None}

def query_meta_cached():
    """meta 60s 进程内缓存（2026-09-07 H3）：数据 3h 才变一次，缓存后首屏 624ms→<10ms。"""
    now = time.time()
    if _META_CACHE["data"] is not None and now - _META_CACHE["t"] < 60:
        return _META_CACHE["data"]
    d = query_meta()
    _META_CACHE["t"] = now
    _META_CACHE["data"] = d
    return d


def get_project_detail(pid):
    """单项目详情：完整字段 + notices 时间线（7 字段精简版，与列表项同口径）。"""
    if not PID_RE.match(pid or ""):
        return None
    conn = _conn()
    tu, au, cd, fu, seen = _url_subqueries()
    r = conn.execute(f"SELECT p.*,{tu},{au},{cd},{fu},{seen} FROM projects p WHERE p.id=?", (pid,)).fetchone()
    if not r:
        conn.close()
        return None
    item = _row_to_item(r)
    item["notices"] = []
    for n in conn.execute("""
            SELECT n.id, n.title, n.url, n.publish_date, n.notice_type, n.notice_subtype,
                   pn.relation_role
            FROM project_notices pn JOIN notices n ON n.id=pn.notice_id
            WHERE pn.project_id=? ORDER BY n.publish_date, n.id""", (pid,)):
        item["notices"].append({
            "id": n["id"], "title": n["title"], "url": n["url"],
            "date": n["publish_date"], "notice_type": n["notice_type"],
            "notice_subtype": n["notice_subtype"], "role": n["relation_role"],
        })
    # 人工区县修订覆盖
    ov = conn.execute("SELECT district FROM district_overrides WHERE project_id=?", (pid,)).fetchone()
    if not ov:
        # 2026-09-11：按归一项目名兜底（免疫项目 id 漂移）
        _k = _norm_name(item.get("title") or "")
        if _k:
            ov = conn.execute("SELECT district FROM district_overrides WHERE proj_key=?", (_k,)).fetchone()
    if ov:
        item["std_district"] = ov["district"]
        item["district_raw"] = ov["district"]
        item["district_overridden"] = True
    conn.close()
    return item


def query_updates(params):
    """更新角标批量数据：列表页各项目的最新更新公告（date 晚于主招标公告）。
    2026-09-09：复用 _enrich_items 批量实现（旧版每 pid 各 1 次子查询），
    端点保留兼容老前端缓存；返回结构不变 {updates:{pid:{...}}}。"""
    ids = [v.strip() for v in (params.get("ids") or [""])[0].split(",") if v.strip()]
    if not ids:
        return {"updates": {}}
    conn = _conn()
    ph = ",".join("?" * len(ids))
    rows = conn.execute(
        f"SELECT p.* FROM projects p WHERE p.id IN ({ph})", ids).fetchall()
    updates = {}
    for it in _enrich_items(conn, rows):
        if it.get("update_notice"):
            updates[it["id"]] = it["update_notice"]
    conn.close()
    return {"updates": updates}



def _conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def ensure_table():
    conn = _conn()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS district_overrides (
            project_id  TEXT PRIMARY KEY,
            district    TEXT NOT NULL,
            created_at  TEXT NOT NULL,
            updated_at  TEXT NOT NULL,
            remark      TEXT DEFAULT ''
        )
    """)
    conn.commit()
    # 2026-09-11 加固：增加 proj_key（归一项目名）。项目重建会让 id 漂移，只按 id 存
    # 会让人工修订静默失效（存量 37 条中 25 条已因历史漂移变孤儿）。按名兼容后免疫。
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(district_overrides)")}
    if "proj_key" not in cols:
        conn.execute("ALTER TABLE district_overrides ADD COLUMN proj_key TEXT")
        conn.commit()
    # 2026-10-09 修复：增加 original_district 快照列。撤销 override 时按此还原
    # projects.district/notices.std_district 到 auto 状态；老数据此列为 NULL
    # （无快照），撤销时只删 override 记录、保持原 auto 不可知时的现状。
    if "original_district" not in cols:
        conn.execute("ALTER TABLE district_overrides ADD COLUMN original_district TEXT")
        conn.commit()
    rows = conn.execute("""SELECT o.project_id, o.proj_key, p.display_name
        FROM district_overrides o LEFT JOIN projects p ON p.id=o.project_id""").fetchall()
    for r in rows:
        want = _norm_name(r["display_name"]) if r["display_name"] else None
        if want and r["proj_key"] != want:
            conn.execute("UPDATE district_overrides SET proj_key=? WHERE project_id=?",
                         (want, r["project_id"]))
    conn.commit()
    conn.close()


def load_overrides():
    """读全部修订 → {project_id: district}（兼容旧调用）"""
    ensure_table()
    conn = _conn()
    rows = conn.execute("SELECT project_id, district FROM district_overrides").fetchall()
    conn.close()
    return {r["project_id"]: r["district"] for r in rows}


def load_overrides_by_key():
    """读全部修订 → {归一项目名: district}（2026-09-11：免疫项目 id 漂移）"""
    ensure_table()
    conn = _conn()
    rows = conn.execute("SELECT proj_key, district FROM district_overrides "
                        "WHERE proj_key IS NOT NULL AND proj_key<>''").fetchall()
    conn.close()
    return {r["proj_key"]: r["district"] for r in rows}


def save_override(project_id, district):
    """写修订；district='' 表示删除（恢复自动）。返回 (ok, existed)

    2026-10-09 修复：写 override 时同步更新 projects.district 和 notices.std_district，
    让 SQL 过滤（基于 p.district）口径与 override 一致（之前只写 district_overrides，
    列表筛选项里看不到已修改的区县——前端显示对、过滤错）。撤销时按 original_district
    快照回滚 projects.district 和 notices.std_district。
    """
    ensure_table()
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = _conn()
    if district == "":
        # 撤销：先读 original_district 快照，按它回滚 projects/notices
        _ovrow = conn.execute(
            "SELECT original_district FROM district_overrides WHERE project_id=?",
            (project_id,)).fetchone()
        cur = conn.execute("DELETE FROM district_overrides WHERE project_id=?", (project_id,))
        existed = cur.rowcount > 0
        if existed and _ovrow and _ovrow["original_district"]:
            _orig = _ovrow["original_district"]
            conn.execute("UPDATE projects SET district=? WHERE id=?", (_orig, project_id))
            conn.execute("""UPDATE notices SET std_district=?
                            WHERE id IN (SELECT notice_id FROM project_notices WHERE project_id=?)""",
                         (_orig, project_id))
        # 无快照（老数据）：保持 projects.district 不动，勿回退到空串（前端会丢）
        conn.commit()
        conn.close()
        return True, existed
    existed = conn.execute(
        "SELECT 1 FROM district_overrides WHERE project_id=?", (project_id,)).fetchone() is not None
    # 2026-09-11：同时写归一项目名（proj_key），项目 id 漂移后仍能命中
    _prow = conn.execute(
        "SELECT display_name, district FROM projects WHERE id=?", (project_id,)).fetchone()
    _pkey = _norm_name(_prow["display_name"]) if _prow else None
    _orig = _prow["district"] if _prow else None
    if existed:
        # 已存在 override：不覆盖 original_district（保留首次快照）
        conn.execute(
            "UPDATE district_overrides SET district=?, updated_at=?, proj_key=COALESCE(?, proj_key) WHERE project_id=?",
            (district, now, _pkey, project_id))
    else:
        # 首次写入：快照当前 p.district 作为 original_district（撤销时回滚用）
        conn.execute(
            "INSERT INTO district_overrides (project_id, district, created_at, updated_at, proj_key, original_district) "
            "VALUES (?,?,?,?,?,?)",
            (project_id, district, now, now, _pkey, _orig))
    # 同步 projects.district 和 notices.std_district——让 SQL 过滤（p.district）口径与 override 一致
    if _prow is not None:
        conn.execute("UPDATE projects SET district=? WHERE id=?", (district, project_id))
        conn.execute("""UPDATE notices SET std_district=?
                        WHERE id IN (SELECT notice_id FROM project_notices WHERE project_id=?)""",
                     (district, project_id))
    conn.commit()
    conn.close()
    return True, existed


# ── 收藏服务端同步（2026-08-17）：favorites 表 + GET/PUT 全量接口 ──
# 2026-08-19：多账号隔离，加 user_id 列 + PK 含 user；老表自动迁移为 user=DEFAULT_USER

def ensure_fav_table():
    conn = _conn()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS favorites (
            user_id     TEXT NOT NULL DEFAULT 'yankai',
            bucket      TEXT NOT NULL,
            fav_key     TEXT NOT NULL,
            created_at  TEXT NOT NULL,
            updated_at  TEXT NOT NULL,
            PRIMARY KEY (user_id, bucket, fav_key)
        )
    """)
    conn.commit()
    # 老表迁移：检测 user_id 列是否存在；不存在则 ALTER + 补齐 DEFAULT_USER
    cols = [r["name"] for r in conn.execute("PRAGMA table_info(favorites)").fetchall()]
    if "user_id" not in cols:
        conn.execute(f"ALTER TABLE favorites ADD COLUMN user_id TEXT NOT NULL DEFAULT '{DEFAULT_USER}'")
        conn.commit()
        # PK 修正：SQLite 不支持直接改 PK；但我们表里没有重复 (bucket, fav_key) 的数据
        # （之前是单用户、全表 DELETE+INSERT，不会有跨用户的重复）。如发生冲突则去重保留 latest。
        dups = conn.execute("""
            SELECT bucket, fav_key, COUNT(*) c FROM favorites
            GROUP BY bucket, fav_key HAVING c > 1
        """).fetchall()
        for d in dups:
            rows = conn.execute(
                "SELECT user_id, created_at, updated_at FROM favorites WHERE bucket=? AND fav_key=? ORDER BY updated_at DESC, created_at DESC",
                (d["bucket"], d["fav_key"]),
            ).fetchall()
            keep = rows[0]
            conn.execute(
                "DELETE FROM favorites WHERE bucket=? AND fav_key=? AND (user_id != ? OR updated_at != ? OR created_at != ?)",
                (d["bucket"], d["fav_key"], keep["user_id"], keep["updated_at"], keep["created_at"]),
            )
        conn.commit()
    # PK 检查 + 重建（防 CREATE IF NOT EXISTS 跳过老表后 PK 未更新）
    pk_cols = [r["name"] for r in conn.execute("PRAGMA table_info(favorites)").fetchall()
               if r["pk"] > 0]
    expected_pk = ["user_id", "bucket", "fav_key"]
    if pk_cols != expected_pk:
        # SQLite 不支持直接改 PK，必须重建表
        print(f"[ensure_fav_table] PK 错误 ({pk_cols}) → 重建为 {expected_pk}", flush=True)
        conn.executescript("""
        BEGIN;
        ALTER TABLE favorites RENAME TO favorites_old;
        CREATE TABLE favorites (
            user_id     TEXT NOT NULL DEFAULT 'yankai',
            bucket      TEXT NOT NULL,
            fav_key     TEXT NOT NULL,
            created_at  TEXT NOT NULL,
            updated_at  TEXT NOT NULL,
            PRIMARY KEY (user_id, bucket, fav_key)
        );
        INSERT INTO favorites(user_id,bucket,fav_key,created_at,updated_at)
        SELECT user_id,bucket,fav_key,created_at,updated_at FROM favorites_old;
        DROP TABLE favorites_old;
        COMMIT;
        """)
    conn.close()


def load_favorites(user):
    """读指定用户的收藏 → {bucket: [keys]}（新收藏在前）"""
    ensure_fav_table()
    conn = _conn()
    rows = conn.execute(
        "SELECT bucket, fav_key FROM favorites WHERE user_id=? ORDER BY updated_at DESC, created_at DESC",
        (user,),
    ).fetchall()
    conn.close()
    out = {"tender": [], "intention": []}
    for r in rows:
        if r["bucket"] in VALID_BUCKETS:
            out[r["bucket"]].append(r["fav_key"])
    return out


def save_favorites(user, payload):
    """全量覆盖指定用户的收藏。payload: {bucket: [keys]} → (ok, err)"""
    ensure_fav_table()
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    buckets = {}
    for b in VALID_BUCKETS:
        arr = payload.get(b)
        if not isinstance(arr, list):
            return False, f"{b} 必须是数组"
        seen = set()
        clean = []
        for k in arr:
            k = str(k or "").strip()
            if not k or k in seen:
                continue
            seen.add(k)
            clean.append(k)
        buckets[b] = clean
    conn = _conn()
    try:
        conn.execute("BEGIN")
        # 只删该用户的收藏，不动别人
        conn.execute("DELETE FROM favorites WHERE user_id=?", (user,))
        for b, keys in buckets.items():
            for k in keys:
                conn.execute(
                    "INSERT INTO favorites (user_id, bucket, fav_key, created_at, updated_at) VALUES (?,?,?,?,?)",
                    (user, b, k, now, now))
        conn.commit()
    except Exception as e:
        conn.rollback()
        conn.close()
        print(f"[fav-write-err] user={user} buckets={ {b: len(v) for b,v in buckets.items()} } err={type(e).__name__}: {e}", flush=True)
        return False, "写入失败"
    conn.close()
    return True, ""


class Handler(BaseHTTPRequestHandler):
    server_version = "TenderDistrictAPI/1.0"

    def _send(self, code, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self):
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length <= 0:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8") or "{}")
        except (json.JSONDecodeError, UnicodeDecodeError):
            return {}

    def _get_user(self):
        """从 nginx 转发的 X-Remote-User 取当前用户。未传 → fallback DEFAULT_USER。
        白名单外的用户（理论上不会发生，nginx 已 Basic Auth）也 fallback，保证老前端不报错。"""
        u = (self.headers.get("X-Remote-User") or "").strip()
        if u in VALID_USERS:
            return u
        return DEFAULT_USER

    def do_GET(self):
        url = urlparse(self.path)
        path = url.path
        params = parse_qs(url.query)

        if path == "/api/tender/health":
            self._send(200, {"ok": True, "overrides": len(load_overrides())})
            return

        # 2026-10-15：轻量 state 探针，永远 200，给前端 stale 软提示用轮询
        if path == "/api/tender/state":
            self._send(200, data_state())
            return

        state = data_state()
        self._currentDataState = state
        if state["data_state"] == "blocked":
            self._send(503, {"error": "maintenance", **state})
            return

        # ── 懒加载查询端点（2026-09-07 P1）──
        if path == "/api/tender/meta":
            self._send(200, self._annotate_meta(query_meta_cached()))
            return
        if path == "/api/tender/projects":
            self._send(200, self._annotate_meta(query_projects(params)))
            return
        if path == "/api/tender/updates":
            self._send(200, self._annotate_meta(query_updates(params)))
            return
        if path == "/api/tender/ict_dist":
            self._send(200, self._annotate_meta(query_ict_dist(params)))
            return
        m = re.match(r"^/api/tender/projects/(.+)$", path)  # 格式校验交给 PID_RE（get_project_detail 内）
        if m:
            item = get_project_detail(m.group(1))
            if item is None:
                self._send(404, {"error": "project not found"})
            else:
                self._send(200, self._annotate_meta(item))
            return

        if path == "/api/tender/district_overrides":
            self._send(200, load_overrides())
            return

        if path == "/api/tender/favorites":
            user = self._get_user()
            self._send(200, {**load_favorites(user), "_user": user})
            return

        self._send(404, {"error": "not found"})

    def do_POST(self):
        url = urlparse(self.path)
        state = data_state()
        self._currentDataState = state
        if state["data_state"] == "blocked":
            self._send(503, {"error": "maintenance", **state})
            return
        if url.path == "/api/tender/fav_projects":
            self._send(200, self._annotate_meta(query_fav_projects(self._read_body())))
            return
        self._send(404, {"error": "not found"})

    def do_PUT(self):
        url = urlparse(self.path)
        state = data_state()
        self._currentDataState = state
        if state["data_state"] == "blocked":
            self._send(503, {"error": "maintenance", **state})
            return
        if url.path == "/api/tender/favorites":
            body = self._read_body()
            user = self._get_user()
            ok, err = save_favorites(user, body)
            if not ok:
                self._send(400, {"error": err})
                return
            data = load_favorites(user)
            total = sum(len(v) for v in data.values())
            self._send(200, {"ok": True, "total": total, "user": user, **data})
            print(f"[{datetime.datetime.now():%Y-%m-%d %H:%M:%S}] PUT favorites user={user} total={total}", flush=True)
            return
        if url.path != "/api/tender/district":
            self._send(404, {"error": "not found"})
            return

        body = self._read_body()
        pid = str(body.get("project_id") or "").strip()
        district = str(body.get("district") or "").strip()

        if not PID_RE.match(pid):
            self._send(400, {"error": "project_id 格式不正确", "project_id": pid})
            return
        if district not in VALID_DISTRICTS:
            self._send(400, {"error": "district 值不合法", "district": district,
                             "valid": sorted(v for v in VALID_DISTRICTS if v)})
            return

        ok, existed = save_override(pid, district)
        if not ok:
            self._send(500, {"error": "保存失败"})
            return

        self._send(200, {"ok": True, "project_id": pid, "district": district,
                         "total": len(load_overrides())})
        print(f"[{datetime.datetime.now():%Y-%m-%d %H:%M:%S}] PUT {pid} -> {district or '(撤销)'} "
              f"total={len(load_overrides())}", flush=True)

    def do_DELETE(self):
        url = urlparse(self.path)
        state = data_state()
        self._currentDataState = state
        if state["data_state"] == "blocked":
            self._send(503, {"error": "maintenance", **state})
            return
        if url.path != "/api/tender/district":
            self._send(404, {"error": "not found"})
            return
        params = parse_qs(url.query)
        pid = (params.get("project_id") or [""])[0].strip()
        if not PID_RE.match(pid):
            self._send(400, {"error": "project_id 格式不正确"})
            return

        existed = pid in load_overrides()
        if existed:
            save_override(pid, "")
        self._send(200, {"ok": True, "existed": existed, "project_id": pid,
                         "total": len(load_overrides())})
        print(f"[{datetime.datetime.now():%Y-%m-%d %H:%M:%S}] DELETE {pid} "
              f"(existed={existed}) total={len(load_overrides())}", flush=True)

    def log_message(self, fmt, *args):
        pass  # 静默 access log，错误走 print

    def _annotate_meta(self, data):
        """stale 期间给响应附 _meta 段；blocked 期间不会调用此方法（已提前 503 退出）。"""
        state = getattr(self, "_currentDataState", None)
        if state and state.get("data_state") == "stale" and isinstance(data, dict):
            return {**data, "_meta": state}
        return data


def main():
    ensure_table()
    ensure_fav_table()
    ensure_indexes()
    # 2026-09-08 第三批：不再绑 0.0.0.0（此前裸暴露靠云防火墙挡）。双监听：
    #   127.0.0.1   → 宿主本机调试/脚本
    #   172.18.0.1  → cockpit 容器 nginx 反代入口（与 qa :8003 同款收口）
    # 若 docker 网桥 IP 变更，这里会告警且 nginx 代理同时失效（nginx.conf 也硬绑 172.18.0.1）。
    import threading
    bind_hosts = ("127.0.0.1", os.environ.get("TENDER_API_BIND", "172.18.0.1"))
    servers = []
    for host in bind_hosts:
        try:
            servers.append(ThreadingHTTPServer((host, PORT), Handler))
        except OSError as e:
            print(f"[warn] bind {host}:{PORT} failed: {e}", flush=True)
    if not servers:
        raise SystemExit("no listener")
    for extra in servers[1:]:
        threading.Thread(target=extra.serve_forever, daemon=True).start()

    # 2026-09-09 A3 预热：重启后首个用户请求不再吃冷 SQL（meta 全量统计 ~180ms）。
    def _warmup():
        try:
            qconn = _conn()
            _norm_index(qconn)
            qconn.close()
            query_meta_cached()
            query_projects({"page": ["1"], "size": ["20"], "stage": ["all"]})
            print("[warmup] meta/列表/归一名索引预热完成", flush=True)
        except Exception as e:
            print(f"[warmup] 预热失败（不影响服务）: {e}", flush=True)
    threading.Thread(target=_warmup, daemon=True).start()
    print(f"招投标区县修订 API 已启动（SQLite 存储）→ "
          f"{[f'{s.server_address[0]}:{PORT}' for s in servers]}", flush=True)
    servers[0].serve_forever()


if __name__ == "__main__":
    main()
