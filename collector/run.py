# -*- coding: utf-8 -*-
"""招标监控主入口

稳定性设计：
- 单站抓取异常不中断整体流程
- tender.json 输出全量最新数据（非仅新增）
- store.json 去重：标记本次新增（is_new），写入 tender.json 供前端展示 NEW（2026-08-04）
- scraper session 用完即关
"""
import time
import os
import sys
import traceback
import signal
import threading
import uuid
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import json
import sqlite3

from scrapers import get_all_scrapers
from analyzer import analyze_items
from store_db import load_store, is_pushed, mark_pushed
from store_db import TenderDB
from config import REQUEST_DELAY, SITES
from sufucai_ledger import SufucaiLedger

STATUS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs", "crawl_status.json")
FAILED_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs", "last_failed_sites.json")
DEPLOY_STATUS_FILE = os.path.join(os.path.dirname(STATUS_FILE), "deploy_status.json")
DEFAULT_SITE_TIMEOUT = int(os.environ.get("CRAWL_SITE_TIMEOUT", "1800"))
SITE_TIMEOUTS = {
    # 江苏站页面多且有验证码，但不能无限占用主任务；超时后交给凌晨补偿。
    "江苏政府采购网": int(os.environ.get("CCGP_SITE_TIMEOUT", "4500")),
}
# 已退役来源：停止抓取和补抓，历史 DB 记录保留但不再导出到驾驶舱。
RETIRED_SITES = {"瑞康医院"}


class SiteTimeoutError(TimeoutError):
    pass


def _alarm_handler(signum, frame):
    raise SiteTimeoutError("站点抓取超过时限")


def _atomic_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _write_failed(run_id, run_started_at, failed_sites, site_states):
    details = {
        name: {
            "status": state.get("status", "failed"),
            "failed_scopes": state.get("failed_scopes", []),
            "error": state.get("error", ""),
        }
        for name, state in site_states.items()
        if state.get("status") in ("partial", "failed", "timeout")
    }
    _atomic_json(FAILED_FILE, {
        "run_id": run_id,
        "started_at": run_started_at,
        "collected_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "failed": list(failed_sites),
        "details": details,
    })


def _heartbeat_loop(stop_event, run_state):
    """独立写轻量心跳，长站点抓取期间也能判断进程是否存活。"""
    heartbeat_file = os.path.join(os.path.dirname(STATUS_FILE), "crawl_heartbeat.json")
    while not stop_event.wait(60):
        try:
            _atomic_json(heartbeat_file, {
                "run_id": run_state.get("run_id", ""),
                "pid": os.getpid(),
                "status": run_state.get("status", "running"),
                "current_site": run_state.get("current_site", ""),
                "heartbeat_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            })
        except Exception as e:
            print(f"  [抓取心跳] 写入失败: {e}")

# 招投标板块数据目录（2026-08-09 架构重构：输出到 tender/data/）
COCKPIT_DATA_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "..", "data"
)


def main():
    run_started_at = time.strftime('%Y-%m-%dT%H:%M:%S')
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:8]
    os.environ["COCKPIT_CRAWL_RUN_ID"] = run_id
    print(f"=== 招标监控启动 {time.strftime('%Y-%m-%d %H:%M:%S')} ===\n")

    store = load_store()
    results = {}

    scrapers = get_all_scrapers()
    failed_sites = []   # 异常/失败的站点（供 deep_refresher 凌晨补偿，2026-08-09）
    site_states = {}
    run_state = {
        "run_id": run_id,
        "started_at": run_started_at,
        "heartbeat_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "status": "running",
        "current_site": "",
        "sites": site_states,
    }
    _atomic_json(STATUS_FILE, run_state)
    # deploy.sh 在 run.py 启动前只能先写阶段；这里回填本轮真实 run_id，避免短暂沿用上轮 id。
    try:
        deploy_state = {}
        if os.path.exists(DEPLOY_STATUS_FILE):
            with open(DEPLOY_STATUS_FILE, encoding="utf-8") as f:
                deploy_state = json.load(f)
        deploy_state.update({
            "run_id": run_id,
            "status": "running",
            "phase": "collect",
            "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        })
        _atomic_json(DEPLOY_STATUS_FILE, deploy_state)
    except Exception as e:
        print(f"  [部署状态] run_id 回填失败: {e}")
    _atomic_json(os.path.join(os.path.dirname(STATUS_FILE), "crawl_heartbeat.json"), {
        "run_id": run_id,
        "pid": os.getpid(),
        "status": "running",
        "current_site": "",
        "heartbeat_at": run_state["heartbeat_at"],
    })
    _write_failed(run_id, run_started_at, failed_sites, site_states)
    heartbeat_stop = threading.Event()
    heartbeat_thread = threading.Thread(
        target=_heartbeat_loop, args=(heartbeat_stop, run_state), daemon=True,
    )
    heartbeat_thread.start()
    for name, scraper in scrapers.items():
        all_items = []
        site_started = time.time()
        timeout_seconds = SITE_TIMEOUTS.get(name, DEFAULT_SITE_TIMEOUT)
        site_states[name] = {
            "status": "running",
            "started_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "timeout_seconds": timeout_seconds,
        }
        run_state["current_site"] = name
        run_state["heartbeat_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        _atomic_json(STATUS_FILE, run_state)
        try:
            # 江苏站内部按分页边界检查 deadline，能保留已抓列表并标记剩余缺口。
            if name == "江苏政府采购网" and hasattr(scraper, "deadline"):
                scraper.deadline = time.monotonic() + timeout_seconds
            elif hasattr(signal, "SIGALRM"):
                signal.signal(signal.SIGALRM, _alarm_handler)
                signal.alarm(timeout_seconds)
            # 江苏政府采购网先完整落列表；详情/PDF/OCR 由 deploy.sh 后续富化链路处理。
            if name == "江苏政府采购网":
                all_items = scraper.scrape(fetch_details=False, ict_only=False)
            else:
                all_items = scraper.scrape()
            # 区分新增和已读，但 results 存全量
            new_items = [item for item in all_items if not is_pushed(item, store, name)]
            results[name] = {"items": all_items, "new_items": new_items}
            print(f"  [{name}] 总 {len(all_items)} 条，新增 {len(new_items)} 条")
            health = scraper.health() if hasattr(scraper, "health") else {"status": "success"}
            site_states[name].update(health)
            if health.get("status") != "success" and name not in failed_sites:
                failed_sites.append(name)
                print(f"  [{name}] 抓取状态={health.get('status')}，已进入凌晨补偿队列")
        except SiteTimeoutError as e:
            print(f"  [{name}] 抓取超时（{timeout_seconds}s），停止该站并进入补偿: {e}")
            results[name] = {"items": [], "new_items": []}
            if name not in failed_sites:
                failed_sites.append(name)
            site_states[name].update({"status": "timeout", "error": str(e)})
        except Exception as e:
            print(f"  [{name}] 抓取异常，跳过: {e}")
            traceback.print_exc()
            results[name] = {"items": [], "new_items": []}
            failed_sites.append(name)
            site_states[name].update({"status": "failed", "error": str(e)})
        finally:
            if hasattr(signal, "SIGALRM"):
                signal.alarm(0)
            scraper.close()
            if name == "苏服采":
                try:
                    ledger_path = os.path.join(
                        os.path.dirname(os.path.abspath(__file__)),
                        "data", "tender.db",   # 2026-08-20 修复：主库在 collector/data/，此前误指 ../data/tender.db（1.5MB 旧库）导致“no such table: notices”
                    )
                    ledger = SufucaiLedger(ledger_path)
                    ledger.reset_source_index()
                    ledger.record_scrape(scraper)
                    ledger.record_source_items(all_items)
                    ledger.record_existing_source_items()
                    ledger.close()
                    print(f"  [苏服采] 分页账本与原始项目索引已写入 {ledger_path}")
                except Exception as e:
                    print(f"  [苏服采账本] 写入失败: {e}")
                    if name not in failed_sites:
                        failed_sites.append(name)
                    site_states[name].update({"status": "partial", "error": f"苏服采账本写入失败: {e}"})

            site_states[name]["finished_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            site_states[name]["elapsed_seconds"] = round(time.time() - site_started, 1)
            site_states[name]["fetched_count"] = len(all_items)
            run_state["heartbeat_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            _atomic_json(STATUS_FILE, run_state)
            _write_failed(run_id, run_started_at, failed_sites, site_states)

        time.sleep(REQUEST_DELAY)

    # 记录失败站点（供 deep_refresher.py 凌晨补偿，2026-08-09）
    # 空结果（0 条）也可能是静默失败，但部分站正常也可能 0 条；
    # 因此只把「抛异常」的站写入 failed，空结果仅记日志由人工观察。
    try:
        import json as _json
        from datetime import datetime as _dt
        _write_failed(run_id, run_started_at, failed_sites, site_states)
        if failed_sites:
            print(f"  [失败站记录] 写入 {failed_sites}（deep_refresher 将凌晨补偿）")
    except Exception as e:
        print(f"  [失败站记录] 写入失败: {e}")

    # ICT 筛选（对全量数据筛选）
    print("\n=== ICT 筛选 ===")
    total_all = 0
    total_ict = 0
    for name in results:
        all_items = results[name]["items"]
        ict, non_ict = analyze_items(all_items)
        results[name]["ict_items"] = ict
        total_all += len(all_items)
        total_ict += len(ict)
        if ict:
            print(f"  [{name}] ICT 相关 {len(ict)} 条")
            for item in ict:
                print(f"    - {item['title']}")

    # 写入 SQLite 历史库（Phase 2）
    print("\n=== 写入 SQLite 存储层 ===")
    db = TenderDB()
    db_new = 0
    db_upd = 0
    try:
        for name in results:
            for item in results[name]["items"]:
                kw = item.get("ict_keywords", [])
                is_new = db.upsert(name, item, kw)
                if is_new:
                    db_new += 1
                else:
                    db_upd += 1
        print(f"  SQLite 新增 {db_new} 条，更新 {db_upd} 条，总计 {db.count()} 条")
        print(f"  按类型: {db.count_by_type()}")
        # 站点活跃度（2026-08-06）：今日抓到公告数 + 最近 last_seen
        try:
            activity = db.site_activity()
            if activity:
                total_today = sum(a["today_count"] for a in activity)
                print(f"  今日活跃站点 {len(activity)} 个，共抓到 {total_today} 条（last_seen 今日刷新）")
        except Exception as e:
            print(f"  [站点活跃度] 输出失败: {e}")
    except Exception as e:
        print(f"  [SQLite] 写入异常: {e}")
        traceback.print_exc()
    finally:
        db.close()

    # 元数据富化：区县 / 行业分类 / 中小微（Phase 5）
    try:
        from enrich_meta import enrich
        enrich(stats_only=False)
        print("  元数据富化完成（区县/行业/中小微）")
    except Exception as e:
        print(f"  [元数据富化] 失败: {e}")

    # 公告重分类 + 可信关联 + 项目生命周期（Schema v2）
    try:
        from project_engine import rebuild as rebuild_projects
        rebuild_projects()
    except Exception as e:
        print(f"  [项目生命周期] 构建失败: {e}")
        traceback.print_exc()
        raise

    # 数据质量校验（Phase 5）：不通过时由 deploy.sh 保留线上旧数据
    try:
        from verify_quality import check as qcheck
        if qcheck(min_sufucai_run_at=run_started_at) != 0:
            raise RuntimeError("数据质量校验未通过，停止生成线上 JSON")
    except Exception as e:
        print(f"  [数据质量校验] 失败: {e}")
        raise

    # 标记本次新增（2026-08-04：原日报推送改用于前端 is_new 标记）
    all_new = []
    new_ids = []
    for name, data in results.items():
        all_new.extend(data["new_items"])
        mark_pushed(data["new_items"], store, name)
        # 本次新增的 db id（与 store_db._make_id 同算法）
        from store_db import item_hash
        new_ids.extend(item_hash(it, name) for it in data["new_items"])

    # 输出 JSON 供 cockpit 消费：从 tender.db 直接导出（2026-08-04 架构优化）
    export_tender_json(new_ids)

    run_state["status"] = "completed_with_gaps" if failed_sites else "success"
    run_state["current_site"] = ""
    run_state["finished_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    run_state["heartbeat_at"] = run_state["finished_at"]
    _atomic_json(STATUS_FILE, run_state)
    _atomic_json(os.path.join(os.path.dirname(STATUS_FILE), "crawl_heartbeat.json"), {
        "run_id": run_id,
        "pid": os.getpid(),
        "status": run_state["status"],
        "current_site": "",
        "heartbeat_at": run_state["finished_at"],
        "finished_at": run_state["finished_at"],
    })
    heartbeat_stop.set()
    heartbeat_thread.join(timeout=2)

    print(f"=== 完成，新增 {len(all_new)} 条，全量已输出 ===")


def export_tender_json(new_ids=None):
    """从 tender.db 直接导出 tender.json（单一事实源，消除手拼字段漏字段风险）

    结构：
      {
        generated_at, schema_version, summary:{total,projects,ict,sites},
        sites: {站名: {total, ict, items[], ict_items[]}},
        projects[], ict_projects[], links[], unmatched_notices[],
        new_ids: [md5...]   // 本次新增（2026-08-04）
      }
    """
    conn = sqlite3.connect(os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "tender.db"))
    conn.row_factory = sqlite3.Row

    # 站点级统计：数据起止（最早/最新公告日期）/ 当月采集 / 最近抓取时间（2026-08-12 起止）
    month_prefix = time.strftime("%Y-%m")
    site_stats = {}
    try:
        for r in conn.execute("""
            SELECT site,
                   MIN(NULLIF(publish_date, '')) AS data_start,
                   MAX(NULLIF(publish_date, '')) AS data_until,
                   MAX(last_seen)    AS last_seen,
                   SUM(CASE WHEN publish_date LIKE ? THEN 1 ELSE 0 END) AS monthly_count
            FROM notices
            WHERE site NOT IN ({})
            GROUP BY site
        """.format(",".join("?" * len(RETIRED_SITES))),
            (month_prefix + "%", *sorted(RETIRED_SITES))).fetchall():
            site_stats[r["site"]] = {
                "data_start": r["data_start"] or "",
                "data_until": r["data_until"] or "",
                "last_seen": r["last_seen"] or "",
                "monthly_count": r["monthly_count"] or 0,
            }
    except Exception as e:
        print(f"  [站点统计] 聚合失败: {e}")

    # 全量数据
    retired_ph = ",".join("?" * len(RETIRED_SITES))
    rows = conn.execute(
        f"SELECT * FROM notices WHERE site NOT IN ({retired_ph}) ORDER BY publish_date DESC",
        sorted(RETIRED_SITES),
    ).fetchall()

    # 本次新增 id 集合（2026-08-04）
    new_ids = set(new_ids or [])

    sites = {}
    ict_projects = []
    total_all = 0
    total_ict = 0

    def _to_item(r):
        """db 行 → 前端条目（含 tags / is_new）"""
        try:
            raw = json.loads(r["raw_json"] or "{}")
        except Exception:
            raw = {}
        try:
            tags = json.loads(r["tags"] or "[]")
        except Exception:
            tags = []
        try:
            ict_kws = json.loads(r["ict_keywords"] or "[]")
        except Exception:
            ict_kws = []
        effective_url = raw.get("actual_detail_url") or r["url"]
        source_cfg = SITES.get(r["site"], {})
        # 2026-08-21：苏服采 ledger 内部使用 source_area_code 写溯源展示，
        # 优先读 raw_row.areaCode（API 原始字段），与 classify_district 内的快捷映射一致。
        source_area_code = (
            (raw.get("raw_row") or {}).get("areaCode")
            or raw.get("areaCode")
            or raw.get("area_code")
            or source_cfg.get("area_code", "") or ""
        )
        return {
            "title": r["title"],
            "url": effective_url,
            "date": r["publish_date"] or "",
            "tenderer": r["tenderer"] or "",
            "amount": r["amount"] or "",
            "amount_value": r["amount_value"],
            "bid_deadline": r["bid_deadline"] or "",
            "notice_type": r["notice_type"] or "tender",
            "notice_subtype": r["notice_subtype"] or "tender_notice",
            "classification_confidence": r["classification_confidence"],
            "classification_reason": r["classification_reason"] or "",
            "winner": r["winner"] or "",
            "winning_amount": r["winning_amount"] or "",
            "project_key": raw.get("project_key") or raw.get("purchases_need_id") or "",
            "purchases_need_id": raw.get("purchases_need_id") or "",
            "item_no": raw.get("item_no") or "",
            "item_name": raw.get("item_name") or "",
            "contact_person": r["contact_person"] or raw.get("contact_person") or "",
            "contact_phone": r["contact_phone"] or raw.get("contact_phone") or "",
            "tenderer_address": r["tenderer_address"] or "",
            "agency": r["agency"] or "",
            "agency_address": r["agency_address"] or "",
            "agency_contact": r["agency_contact"] or "",
            "agency_phone": r["agency_phone"] or "",
            "supervision_phone": r["supervision_phone"] or "",
            "email": r["email"] or "",
            "postal_code": r["postal_code"] or "",
            "area_code": source_area_code,
            "tender_start_time": raw.get("tender_start_time") or "",
            "transaction_discount": raw.get("transaction_discount") or "",
            "reason": raw.get("reason") or "",
            "version": raw.get("version") or "",
            "termination_status": raw.get("title_status") or "",
            "bidding_status": raw.get("bidding_status") or "",
            "std_district": r["std_district"] or "",
            "proj_major_cat": r["proj_major_cat"] or "",
            "proj_minor_cat": r["proj_minor_cat"] or "",
            "sme_target": r["sme_target"] or "",
            "tags": tags,
            "is_new": r["id"] in new_ids,
        }

    for r in rows:
        site = r["site"]
        if site not in sites:
            cfg = SITES.get(site, {})
            st = site_stats.get(site, {})
            sites[site] = {"total": 0, "ict": 0, "items": [], "ict_items": [],
                           "category": cfg.get("category", ""),
                           "scrape_method": cfg.get("scrape_method", ""),
                           "retention": cfg.get("retention", ""),
                           "scope": cfg.get("scope", ""),
                           # 2026-08-12：平台弹窗字段（名称软链接 / 数据起止 / 当月采集 / 最近抓取）
                           "url": cfg.get("display_url") or cfg.get("url") or "",
                           "data_start": st.get("data_start", ""),
                           "data_until": st.get("data_until", ""),
                           "last_seen": st.get("last_seen", ""),
                           "monthly_count": st.get("monthly_count", 0)}
        item = _to_item(r)
        # is_ict：ict_keywords JSON 解析后非空（'[]' 空数组也算非ICT）
        try:
            _kws = json.loads(r["ict_keywords"] or "[]")
            is_ict = bool(_kws)
        except Exception:
            is_ict = False
        sites[site]["items"].append(item)
        sites[site]["total"] += 1
        if is_ict:
            sites[site]["ict"] += 1
            sites[site]["ict_items"].append(item)
            ict_projects.append({**item, "site": site})
        total_all += 1
        if is_ict:
            total_ict += 1

    # 项目级视图（Schema v2）：页面主列表以项目为单位，sites 继续保留用于公告审计。
    project_rows = conn.execute(
        f"SELECT * FROM projects WHERE site NOT IN ({retired_ph}) ORDER BY latest_notice_date DESC",
        sorted(RETIRED_SITES),
    ).fetchall()
    projects = []
    for p in project_rows:
        notices = conn.execute("""
            SELECT n.id,n.title,n.url,n.raw_json,n.publish_date,n.notice_type,n.notice_subtype,
                   n.ict_keywords,n.tags,n.site,pn.relation_role,pn.match_score,
                   pn.match_confidence,pn.match_reasons,n.last_seen,
                   n.tenderer_address,n.agency,n.agency_address,n.contact_person,n.contact_phone,
                   n.agency_contact,n.agency_phone,n.supervision_phone,n.email,n.postal_code
            FROM project_notices pn JOIN notices n ON n.id=pn.notice_id
            WHERE pn.project_id=? ORDER BY n.publish_date,n.id
        """, (p["id"],)).fetchall()
        notice_list, keywords, tags = [], set(), set()
        tender_url = award_url = ""
        tender_date = award_date = None
        for n in notices:
            try:
                notice_raw = json.loads(n["raw_json"] or "{}")
            except Exception:
                notice_raw = {}
            notice_url = notice_raw.get("actual_detail_url") or n["url"]
            try: keywords.update(json.loads(n["ict_keywords"] or "[]"))
            except Exception: pass
            try: tags.update(json.loads(n["tags"] or "[]"))
            except Exception: pass
            if n["relation_role"] in ("tender_primary", "tender_reopen"):
                tender_url = notice_url or tender_url
                tender_date = n["publish_date"] or tender_date
            if n["relation_role"] == "award_final":
                award_url = notice_url or award_url
                award_date = n["publish_date"] or award_date
            notice_list.append({
                "id": n["id"], "title": n["title"], "url": notice_url,
                "date": n["publish_date"], "notice_type": n["notice_type"],
                "notice_subtype": n["notice_subtype"], "role": n["relation_role"],
                "match_score": n["match_score"], "match_confidence": n["match_confidence"],
                "match_reasons": n["match_reasons"] or "",
                "project_key": notice_raw.get("project_key") or notice_raw.get("purchases_need_id") or "",
                "purchases_need_id": notice_raw.get("purchases_need_id") or "",
                "item_no": notice_raw.get("item_no") or "",
                "item_name": notice_raw.get("item_name") or "",
                "contact_person": n["contact_person"] or notice_raw.get("contact_person") or "",
                "contact_phone": n["contact_phone"] or notice_raw.get("contact_phone") or "",
                "tenderer_address": n["tenderer_address"] or "",
                "agency": n["agency"] or "",
                "agency_address": n["agency_address"] or "",
                "agency_contact": n["agency_contact"] or "",
                "agency_phone": n["agency_phone"] or "",
                "supervision_phone": n["supervision_phone"] or "",
                "email": n["email"] or "",
                "postal_code": n["postal_code"] or "",
                "area_code": notice_raw.get("area_code") or notice_raw.get("areaCode") or SITES.get(n["site"], {}).get("area_code", ""),
                "tender_start_time": notice_raw.get("tender_start_time") or "",
                "transaction_discount": notice_raw.get("transaction_discount") or "",
                "reason": notice_raw.get("reason") or "",
                "version": notice_raw.get("version") or "",
                "termination_status": notice_raw.get("title_status") or "",
                "bidding_status": notice_raw.get("bidding_status") or "",
            })
        cycle_days = None
        if tender_date and award_date:
            try:
                from datetime import datetime
                cycle_days = (datetime.strptime(award_date[:10], "%Y-%m-%d") -
                              datetime.strptime(tender_date[:10], "%Y-%m-%d")).days
            except (ValueError, TypeError):
                pass
        projects.append({
            "id": p["id"], "name": p["display_name"], "canonical_name": p["canonical_name"],
            "stage": p["project_stage"], "stage_reason": p["stage_reason"],
            "purchaser": p["purchaser"] or "", "site": p["site"] or "",
            "site_category": SITES.get(p["site"] or "", {}).get("category", ""),
            "district": p["district"] or "", "category": p["major_category"] or "",
            "minor_category": p["minor_category"] or "", "open_date": p["open_date"] or "",
            "first_notice_date": p["first_notice_date"] or "",
            "latest_notice_date": p["latest_notice_date"] or "",
            "budget": p["budget_value"], "budget_type": p["budget_type"] or "", "winner": p["winner"] or "",
            "winning_amount": p["winning_amount"], "link_confidence": p["link_confidence"] or "none",
            "cycle_days": cycle_days,
            "tender_url": tender_url, "award_url": award_url,
            "keywords": sorted(keywords), "tags": sorted(tags),
            "is_ict": bool(keywords), "is_new": any(n["id"] in new_ids for n in notices),
            "notices": notice_list,
            # 2026-08-16：项目最后见到时间（数据源最后一次抓到该项目的公告）
            "last_seen": max((n["last_seen"] or "" for n in notices), default="") if notices else "",
        })

    tender_json = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M"),
        "schema_version": 2,
        "summary": {"total": total_all, "projects": len(projects), "ict": total_ict, "sites": len(sites)},
        "sites": sites,
        "projects": projects,
        "ict_projects": ict_projects,
        "links": [],
        "unmatched_notices": [],
        "new_ids": list(new_ids),
    }

    # 读取关联分析结果（project_chain 视图）
    try:
        links = conn.execute("""
            SELECT tender_name, award_name, site_name, match_type, match_score,
                   match_confidence, match_reasons, cycle_days, winner, winning_amount, budget
            FROM project_chain
            WHERE site_name NOT IN ({})
            ORDER BY cycle_days""".format(retired_ph), sorted(RETIRED_SITES)).fetchall()
        for r in links:
            tender_json["links"].append({
                "tender_name": r["tender_name"], "award_name": r["award_name"],
                "site": r["site_name"], "match_type": r["match_type"],
                "match_score": r["match_score"], "match_confidence": r["match_confidence"],
                "match_reasons": r["match_reasons"],
                "cycle_days": r["cycle_days"], "winner": r["winner"],
                "winning_amount": r["winning_amount"], "budget": r["budget"],
            })
    except Exception as e:
        print(f"  [关联数据] 读取失败: {e}")

    try:
        unmatched = conn.execute("""
            SELECT n.id,n.title,n.url,n.site,n.publish_date,n.notice_subtype,
                   pl.match_score,pl.unmatched_reason
            FROM project_links pl JOIN notices n ON n.id=pl.award_id
            WHERE pl.tender_id IS NULL AND n.site NOT IN ({})
            ORDER BY n.publish_date DESC
        """.format(retired_ph), sorted(RETIRED_SITES)).fetchall()
        tender_json["unmatched_notices"] = [dict(r) for r in unmatched]
    except Exception as e:
        print(f"  [未匹配数据] 读取失败: {e}")

    conn.close()

    os.makedirs(COCKPIT_DATA_DIR, exist_ok=True)
    json_path = os.path.join(COCKPIT_DATA_DIR, "tender.json")
    temp_path = json_path + ".tmp"
    with open(temp_path, "w", encoding="utf-8") as f:
        json.dump(tender_json, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(temp_path, json_path)
    print(f"JSON 已输出到 {json_path}（{total_all} 条，ICT {total_ict} 条）")


if __name__ == "__main__":
    try:
        main()
    except BaseException as e:
        # deploy.sh 仍负责最终失败状态；这里保证采集层不会遗留 running 心跳。
        try:
            previous = {}
            if os.path.exists(STATUS_FILE):
                with open(STATUS_FILE, encoding="utf-8") as f:
                    previous = json.load(f)
            finished_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            previous.update({
                "status": "failed",
                "current_site": previous.get("current_site", ""),
                "finished_at": finished_at,
                "heartbeat_at": finished_at,
                "error": f"{type(e).__name__}: {e}",
            })
            _atomic_json(STATUS_FILE, previous)
            _atomic_json(os.path.join(os.path.dirname(STATUS_FILE), "crawl_heartbeat.json"), {
                "run_id": previous.get("run_id", ""),
                "pid": os.getpid(),
                "status": "failed",
                "current_site": previous.get("current_site", ""),
                "heartbeat_at": finished_at,
                "finished_at": finished_at,
                "error": previous["error"],
            })
        except Exception:
            pass
        raise
