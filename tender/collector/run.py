# -*- coding: utf-8 -*-
"""招标监控主入口

稳定性设计：
- 单站抓取异常不中断整体流程
- SQLite tender.db 是唯一事实源，8002 API 是唯一项目数据出口
- store.json 仅用于报告推送去重
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
from experience_auto import install as install_l2_experience  # 2026-10-01：L2 经验自动套用
install_l2_experience()  # 幂等，将 BaseScraper.scrape 补上经验库套用逻辑
from analyzer import analyze_items
from store_db import load_store, is_pushed, mark_pushed
from store_db import TenderDB
from config import REQUEST_DELAY, SITES, CRAWL_CONCURRENCY, CRAWL_SLOW_SITES
from concurrent.futures import ThreadPoolExecutor, as_completed
from sufucai_ledger import SufucaiLedger
from run_state import get as get_run_state, update as update_run_state

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
    # 2026-09-07 H1：tmp 文件名加线程 id —— 多线程并发写同一状态文件时
    # 共享 .tmp 会互相截断/覆盖，导致 json.dump 抛异常或写坏文件。
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.{threading.get_ident()}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
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
    update_run_state("failures", {
        "run_id": run_id,
        "started_at": run_started_at,
        "collected_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "failed": list(failed_sites),
        "details": details,
    })


def _heartbeat_loop(stop_event, run_state):
    """独立写轻量心跳，长站点抓取期间也能判断进程是否存活。"""
    while not stop_event.wait(60):
        try:
            update_run_state("heartbeat", {
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
    update_run_state("crawl", run_state)
    # deploy.sh 在 run.py 启动前只能先写阶段；这里回填本轮真实 run_id，避免短暂沿用上轮 id。
    try:
        deploy_state = get_run_state("deployment") or {}
        deploy_state.update({
            "run_id": run_id,
            "status": "running",
            "phase": "collect",
            "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        })
        update_run_state("deployment", deploy_state)
    except Exception as e:
        print(f"  [部署状态] run_id 回填失败: {e}")
    update_run_state("heartbeat", {
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
    # ── 2026-09-07 H1 采集并发化 ──
    # 慢站（CDP/长超时）：主线程串行 + SIGALRM 超时（信号仅主线程可用）；
    # 普通站：线程池并发（每站独立 scraper 实例/session，无共享状态）。
    # 共享写（site_states/failed_sites/run_state）用 _state_lock 保护。
    _state_lock = threading.Lock()

    def _scrape_site(name, scraper, use_alarm=True):
        # use_alarm=False（线程池站）：不用 SIGALRM（信号仅主线程可用），
        # 超时由外层 fut.result(timeout) 控制。
        """单站抓取（原串行循环体抽函数）。返回 (all_items, new_items, health, error)。"""
        all_items = []
        site_started = time.time()
        timeout_seconds = SITE_TIMEOUTS.get(name, DEFAULT_SITE_TIMEOUT)
        with _state_lock:
            site_states[name] = {
                "status": "running",
                "started_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "timeout_seconds": timeout_seconds,
            }
            run_state["current_site"] = name
            run_state["heartbeat_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        update_run_state("crawl", run_state)
        error = None
        try:
            # 江苏站内部按分页边界检查 deadline，能保留已抓列表并标记剩余缺口。
            if name == "江苏政府采购网" and hasattr(scraper, "deadline"):
                scraper.deadline = time.monotonic() + timeout_seconds
                # 2026-09-07 补 SIGALRM 兜底：站内 deadline 分页自检可能失效
                # （16:16 事故：响水县翻页超时未触发自检，跑了 80+ 分钟），
                # alarm 到点强制抛 SiteTimeoutError，保留已抓数据走发布。
            if use_alarm and hasattr(signal, "SIGALRM"):
                signal.signal(signal.SIGALRM, _alarm_handler)
                signal.alarm(timeout_seconds)
            # 江苏政府采购网先完整落列表；详情/PDF/OCR 由 deploy.sh 后续富化链路处理。
            if name == "江苏政府采购网":
                all_items = scraper.scrape(fetch_details=False, ict_only=False)
            else:
                all_items = scraper.scrape()
            health = scraper.health() if hasattr(scraper, "health") else {"status": "success"}
        except SiteTimeoutError as e:
            error = f"timeout: {e}"
            health = {"status": "timeout", "error": str(e)}
        except Exception as e:
            error = str(e)
            traceback.print_exc()
            health = {"status": "failed", "error": str(e)}
        finally:
            if use_alarm and hasattr(signal, "SIGALRM"):
                signal.alarm(0)
            scraper.close()
            if name == "苏服采":
                try:
                    ledger_path = os.path.join(
                        os.path.dirname(os.path.abspath(__file__)),
                        "data", "tender.db",
                    )
                    ledger = SufucaiLedger(ledger_path)
                    ledger.reset_source_index()
                    ledger.record_scrape(scraper)
                    ledger.record_source_items(all_items)
                    ledger.record_existing_source_items()
                    ledger.close()
                except Exception as e:
                    print(f"  [苏服采账本] 写入失败: {e}")
                    if name not in failed_sites:
                        failed_sites.append(name)
                    health = {"status": "partial", "error": f"苏服采账本写入失败: {e}"}

        # 新增判定（与原串行一致）
        new_items = [item for item in all_items if not is_pushed(item, store, name)]
        with _state_lock:
            site_states[name].update(health)
            if error:
                site_states[name].update({"status": health.get("status", "failed"), "error": error})
            if health.get("status") != "success" and name not in failed_sites:
                failed_sites.append(name)
            site_states[name]["finished_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            site_states[name]["elapsed_seconds"] = round(time.time() - site_started, 1)
            site_states[name]["fetched_count"] = len(all_items)
            run_state["heartbeat_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        update_run_state("crawl", run_state)
        _write_failed(run_id, run_started_at, failed_sites, site_states)
        return all_items, new_items, health, error

    def _run_site_task(name, scraper, use_alarm=True):
        """线程池任务：单站抓取 + 日志。异常已在 _scrape_site 内消化（返回 error）。"""
        all_items, new_items, health, error = _scrape_site(name, scraper, use_alarm)
        if error:
            print(f"  [{name}] 抓取失败（{error[:80]}），已进入补偿队列")
        else:
            print(f"  [{name}] 总 {len(all_items)} 条，新增 {len(new_items)} 条")
        return name, {"items": all_items, "new_items": new_items}

    slow_sites = {k: v for k, v in scrapers.items() if k in CRAWL_SLOW_SITES}
    fast_sites = {k: v for k, v in scrapers.items() if k not in CRAWL_SLOW_SITES}

    # ── 2026-09-07 H1：慢站（主线程，SIGALRM 超时）与快站（线程池）同时启动 ──
    # 两组并行：总时长 = max(慢站, 快站) 而非相加。
    if CRAWL_CONCURRENCY > 1 and len(fast_sites) > 0:
        print(f"  [并发] 普通站 {len(fast_sites)} 个，并发数 {CRAWL_CONCURRENCY}；慢站 {len(slow_sites)} 个主线程串行")
        with ThreadPoolExecutor(max_workers=CRAWL_CONCURRENCY) as pool:
            futures = {pool.submit(_run_site_task, n, sc, False): n for n, sc in fast_sites.items()}
            # 慢站在主线程跑（SIGALRM 只在主线程生效），与快站线程池并行
            for name, scraper in slow_sites.items():
                name_r, r = _run_site_task(name, scraper)
                results[name_r] = r
                time.sleep(REQUEST_DELAY)
            for fut in as_completed(futures):
                try:
                    name_r, r = fut.result(timeout=SITE_TIMEOUTS.get(futures[fut], DEFAULT_SITE_TIMEOUT) + 60)
                    results[name_r] = r
                except Exception as e:
                    n = futures.get(fut, "?")
                    print(f"  [{n}] 并发任务异常: {e}")
                    results[n] = {"items": [], "new_items": []}
                    if n not in failed_sites:
                        failed_sites.append(n)
    else:
        for name, scraper in scrapers.items():
            name_r, r = _run_site_task(name, scraper)
            results[name_r] = r
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

    run_state["status"] = "completed_with_gaps" if failed_sites else "success"
    run_state["current_site"] = ""
    run_state["finished_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    run_state["heartbeat_at"] = run_state["finished_at"]
    update_run_state("crawl", run_state)
    update_run_state("heartbeat", {
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



if __name__ == "__main__":
    try:
        main()
    except BaseException as e:
        # deploy.sh 仍负责最终失败状态；这里保证采集层不会遗留 running 心跳。
        try:
            previous = {}
            previous = get_run_state("crawl") or {}
            finished_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            previous.update({
                "status": "failed",
                "current_site": previous.get("current_site", ""),
                "finished_at": finished_at,
                "heartbeat_at": finished_at,
                "error": f"{type(e).__name__}: {e}",
            })
            update_run_state("crawl", previous)
            update_run_state("heartbeat", {
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
