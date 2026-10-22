#!/usr/bin/env python3
"""
health_snapshot.py — 招投标系统确定性健康快照

职责：
  - 读 L1 心跳 / 状态 / 锁 / 日志 → 给出运行态结论
  - 读 L4 服务（Gateway / Docker cockpit / API）→ 给出服务态结论
  - 读 L5 SQLite/API 事实源 → 给出数据态结论
  - 读主机资源 → 给出资源态结论
  - 写到 logs/health_snapshot.json（被 OpenClaw 心跳读取）

输出：JSON；同时 --watch 模式持续打印摘要；非零退出仅表示 IO/解析异常，不表示健康异常。
正常 / 异常在 JSON 字段里表达。

不执行：kill、rm、重启、推送、付费 API。
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

# —— 路径常量 ——
ROOT = Path("/root/.openclaw/workspace/agents/yktender")
COLL = ROOT / "projects/cockpit/tender/collector"
LOGS = COLL / "logs"
RUN_STATE = LOGS / "run_state.json"
ALERT_STATE = LOGS / "health_alert_state.json"
LOCK = LOGS / "cron.lock"
CRON_LOG = COLL / "cron.log"
TENDER_DB = COLL / "data/tender.db"
RETIRED_SITES = {"瑞康医院"}
sys.path.insert(0, str(COLL))

LOCAL_TZ = timezone(timedelta(hours=8))


def now_str() -> str:
    return datetime.now(LOCAL_TZ).strftime("%Y-%m-%d %H:%M:%S")


def iso_str(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return dt.astimezone(LOCAL_TZ).strftime("%Y-%m-%d %H:%M:%S")


def parse_dt(s: str | None) -> datetime | None:
    if not s:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=LOCAL_TZ)
        except ValueError:
            continue
    return None


def read_json(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        with path.open(encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        return {"__read_error__": str(e)}


def cmd_exists(cmd: str) -> bool:
    return shutil.which(cmd) is not None


def safe_run(args, timeout=8) -> str:
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False)
        return (r.stdout or "").strip() + ((r.stderr or "").strip() if r.returncode != 0 else "")
    except Exception as e:
        return f"<err:{type(e).__name__}:{e}>"


def proc_cmdline(pid: int) -> str | None:
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            return f.read().replace(b"\x00", b" ").decode("utf-8", "ignore")
    except Exception:
        return None


def proc_alive(pid: int) -> bool:
    if pid is None or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def flock_test(lock: Path) -> str:
    """Returns one of: 'unlocked', 'held', 'unknown'."""
    if not lock.exists():
        return "no_lock_file"
    if not cmd_exists("flock"):
        return "unknown"
    try:
        r = subprocess.run(
            ["flock", "-n", str(lock), "-c", "echo OK"],
            capture_output=True, text=True, timeout=3,
        )
        if r.returncode == 0:
            return "unlocked"
        return "held"
    except Exception:
        return "unknown"


def log_growth(path: Path) -> dict:
    """最近 5 分钟日志是否有增长。"""
    if not path.exists():
        return {"exists": False}
    st = path.stat()
    mtime = datetime.fromtimestamp(st.st_mtime, tz=LOCAL_TZ)
    age_sec = (datetime.now(LOCAL_TZ) - mtime).total_seconds()
    return {
        "exists": True,
        "size_bytes": st.st_size,
        "mtime_local": iso_str(mtime),
        "age_seconds": round(age_sec, 1),
        "growing_5m": age_sec <= 300,
    }


def disk_usage() -> dict:
    total, used, free = shutil.disk_usage("/")
    return {
        "path": "/",
        "total_gb": round(total / 1024 ** 3, 1),
        "used_gb": round(used / 1024 ** 3, 1),
        "free_gb": round(free / 1024 ** 3, 1),
        "percent": round(used / total * 100, 1),
    }


def service_checks() -> dict:
    out: dict = {}

    # 1. Gateway：cron 环境没有 systemd user bus；CLI 在并发时也可能超时。
    # 监控用本地 HTTP + 监听端口双证据，CLI 留给人工排查。
    gw_http = safe_run(
        ["curl", "-sS", "-o", "/dev/null", "-w", "%{http_code}",
         "--max-time", "4", "http://127.0.0.1:65025/health"], timeout=8,
    )
    gw_listen = safe_run(["ss", "-ltn", "sport", "=", ":65025"], timeout=5)
    out["gateway"] = {
        "healthy": gw_http == "200" and ":65025" in gw_listen,
        "http_code": gw_http or "unknown",
        "listening": ":65025" in gw_listen,
    }

    # 2. cockpit docker container
    dock = safe_run(
        ["docker", "inspect", "-f", "{{.State.Status}} {{.State.Health.Status}}", "cockpit"],
        timeout=8,
    )
    out["cockpit_container"] = {"inspect": dock or "unknown"}


    # 2b. cockpit-ent / cockpit-follow 容器 (2026-09-06 三栏目拆分)
    out["cockpit_ent_container"] = {"inspect": safe_run(
        ["docker", "inspect", "-f", "{{.State.Status}} {{.State.Health.Status}}", "cockpit-ent"],
        timeout=8) or "unknown"}
    out["cockpit_ent_summary"] = {"size_bytes": safe_run(
        ["docker", "exec", "cockpit-ent", "stat", "-c", "%s",
         "/usr/share/nginx/html/data/enterprise/summary.json"], timeout=8)}
    out["cockpit_follow_container"] = {"inspect": safe_run(
        ["docker", "inspect", "-f", "{{.State.Status}} {{.State.Health.Status}}", "cockpit-follow"],
        timeout=8) or "unknown"}
    out["cockpit_follow_json"] = {"size_bytes": safe_run(
        ["docker", "exec", "cockpit-follow", "stat", "-c", "%s",
         "/usr/share/nginx/html/data/experience.json"], timeout=8)}

    # 3. Tender API (端口 8002)
    out["tender_api"] = {"probe": safe_run(
        ["curl", "-sS", "-o", "/dev/null", "-w", "%{http_code}", "--max-time", "4",
         "http://127.0.0.1:8002/api/tender/health"], timeout=8)}

    return out


def db_summary() -> dict:
    if not TENDER_DB.exists():
        return {"exists": False}
    try:
        conn = sqlite3.connect(str(TENDER_DB))
        ph = ",".join("?" * len(RETIRED_SITES))
        retired = sorted(RETIRED_SITES)
        notices = conn.execute(
            f"SELECT COUNT(*) FROM notices WHERE site NOT IN ({ph})", retired
        ).fetchone()[0]
        projects = conn.execute(
            f"SELECT COUNT(*) FROM projects WHERE site NOT IN ({ph})", retired
        ).fetchone()[0]
        conn.close()
        return {"exists": True, "notices": notices, "projects": projects}
    except Exception as e:
        return {"exists": True, "__read_error__": str(e)}


def update_alert_state(alerts: list[dict]) -> dict:
    """跨快照保存首次发现、连续次数与恢复时间，避免模型重复报同一异常。"""
    now = now_str()
    previous = read_json(ALERT_STATE) or {}
    active_prev = previous.get("active", {}) if isinstance(previous, dict) else {}
    active_now: dict[str, dict] = {}
    resolved = list(previous.get("resolved", []))[-50:] if isinstance(previous, dict) else []

    for alert in alerts:
        code = alert["code"]
        old = active_prev.get(code, {})
        active_now[code] = {
            **alert,
            "first_seen": old.get("first_seen", now),
            "last_seen": now,
            "count": int(old.get("count", 0)) + 1,
        }
        alert["first_seen"] = active_now[code]["first_seen"]
        alert["count"] = active_now[code]["count"]

    for code, old in active_prev.items():
        if code not in active_now:
            resolved.append({
                "code": code,
                "first_seen": old.get("first_seen"),
                "resolved_at": now,
                "last_message": old.get("msg", ""),
            })

    state = {"updated_at": now, "active": active_now, "resolved": resolved[-50:]}
    ALERT_STATE.parent.mkdir(parents=True, exist_ok=True)
    tmp = ALERT_STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, ALERT_STATE)
    return state


def collect() -> dict:
    snap: dict = {
        "snapshot_at": now_str(),
        "tz": "Asia/Shanghai",
        "crawl": {},
        "services": {},
        "data": {},
        "resources": {},
        "alerts": [],
    }

    # —— 1. crawl 状态 ——
    state = read_json(RUN_STATE) or {}
    hb = state.get("heartbeat", {})
    cs = state.get("crawl", {})
    fa = state.get("failures", {})
    ds = state.get("deployment", {})
    manifest = state.get("manifest", {})

    pid = hb.get("pid") if isinstance(hb, dict) else None
    hb_at = parse_dt(hb.get("heartbeat_at")) if isinstance(hb, dict) else None
    hb_age = round((datetime.now(LOCAL_TZ) - hb_at).total_seconds(), 1) if hb_at else None

    pid_alive = proc_alive(pid) if pid else False
    cmdline = proc_cmdline(pid) if pid_alive else None
    deploy_pid = ds.get("pid") if isinstance(ds, dict) else None
    deploy_pid_alive = proc_alive(deploy_pid) if deploy_pid else False
    deploy_cmdline = proc_cmdline(deploy_pid) if deploy_pid_alive else None

    # 站点进度（来自 run_state.json crawl.sites）
    cur_site = (cs or {}).get("current_site") if isinstance(cs, dict) else None
    sites = (cs or {}).get("sites", {}) if isinstance(cs, dict) else {}
    cur_info = sites.get(cur_site, {}) if cur_site else {}
    cur_started = parse_dt(cur_info.get("started_at"))
    cur_elapsed = (
        round((datetime.now(LOCAL_TZ) - cur_started).total_seconds(), 1)
        if cur_started else None
    )
    cur_timeout = cur_info.get("timeout_seconds")

    snap["crawl"] = {
        "run_id": (cs or {}).get("run_id") if isinstance(cs, dict) else None,
        "status": (cs or {}).get("status") if isinstance(cs, dict) else None,
        "started_at": (cs or {}).get("started_at") if isinstance(cs, dict) else None,
        "finished_at": (cs or {}).get("finished_at") if isinstance(cs, dict) else None,
        "heartbeat": {
            "at": hb.get("heartbeat_at") if isinstance(hb, dict) else None,
            "age_seconds": hb_age,
            "fresh": hb_age is not None and hb_age <= 120,
        },
        "pid": pid,
        "pid_alive": pid_alive,
        "cmdline": cmdline,
        "lock_state": flock_test(LOCK),
        "current_site": cur_site,
        "current_site_started_at": cur_info.get("started_at"),
        "current_site_elapsed_seconds": cur_elapsed,
        "current_site_timeout_seconds": cur_timeout,
        # 仅主抓进程存活时才判单站超时：run.py 失败路径会保留 current_site（便于定位失败点），
        # 进程已死时按 started_at 累算会误报「站点还在跑」（2026-09-14 实证误报 27 次）。
        "current_site_overtime": (
            pid_alive
            and cur_elapsed is not None and cur_timeout is not None and cur_elapsed > cur_timeout
        ),
        "cron_log": log_growth(CRON_LOG),
        "failed_sites": (
            [site for site in (fa or {}).get("failed", []) if site not in RETIRED_SITES]
            if isinstance(fa, dict) and (fa or {}).get("run_id") == (cs or {}).get("run_id")
            else []
        ),
        "deployment": {
            "run_id": ds.get("run_id") if isinstance(ds, dict) else None,
            "pid": deploy_pid,
            "pid_alive": deploy_pid_alive,
            "cmdline": deploy_cmdline,
            "status": ds.get("status") if isinstance(ds, dict) else None,
            "phase": ds.get("phase") if isinstance(ds, dict) else None,
            "started_at": ds.get("started_at") if isinstance(ds, dict) else None,
            "updated_at": ds.get("updated_at") if isinstance(ds, dict) else None,
            "finished_at": ds.get("finished_at") if isinstance(ds, dict) else None,
            "message": ds.get("message") if isinstance(ds, dict) else None,
        },
    }

    # —— 2. 服务 ——
    snap["services"] = service_checks()

    # —— 3. 数据 ——
    snap["data"] = {
        "database": db_summary(),
        "deployment_manifest": manifest,
    }

    # —— 4. 资源 ——
    snap["resources"] = {
        "disk": disk_usage(),
    }

    # —— 5. 告警生成 ——
    alerts: list[dict] = []

    c = snap["crawl"]
    # 锁 vs PID 互证
    deployment = c["deployment"]
    pipeline_alive = c["pid_alive"] or deployment["pid_alive"]
    if c["lock_state"] == "held":
        if not pipeline_alive:
            alerts.append({
                "level": "high",
                "code": "stale_lock",
                "msg": "cron.lock 被持有，但采集 PID 与部署 PID 均已消失，疑似陈旧锁",
                "evidence": {"crawl_pid": c["pid"], "deploy_pid": deployment["pid"]},
            })
        elif (c["pid_alive"] and c["cmdline"]
              and "run.py" not in c["cmdline"] and "deploy.sh" not in c["cmdline"]):
            alerts.append({
                "level": "high",
                "code": "lock_holder_mismatch",
                "msg": f"锁占用者不是主抓进程：{c['cmdline'][:80]}",
                "evidence": {"cmdline": c["cmdline"][:200]},
            })
    elif pipeline_alive and c["lock_state"] == "unlocked":
        alerts.append({
            "level": "high",
            "code": "pipeline_without_lock",
            "msg": "采集或部署进程仍在运行，但 cron.lock 可被获取，互斥保护失效",
            "evidence": {"crawl_pid": c["pid"], "deploy_pid": deployment["pid"]},
        })

    if deployment.get("status") == "running" and not deployment.get("pid_alive"):
        alerts.append({
            "level": "high",
            "code": "deployment_orphaned",
            "msg": f"部署状态停在 {deployment.get('phase') or 'unknown'}，但部署 PID 已消失",
            "evidence": {"deploy_pid": deployment.get("pid"), "updated_at": deployment.get("updated_at")},
        })

    # PID 存在却心跳过久
    # 仅 collect 阶段要求 run.py 的 60 秒心跳；backfill/publish 阶段由 deploy_status 接管。
    if (c["pid_alive"] and deployment.get("phase") in (None, "starting", "collect")
            and not c["heartbeat"]["fresh"]):
        alerts.append({
            "level": "high",
            "code": "heartbeat_stale",
            "msg": f"主抓 PID 存活但心跳 {c['heartbeat']['age_seconds']}s 未更新，疑似卡死",
            "evidence": {
                "pid": c["pid"], "heartbeat_age": c["heartbeat"]["age_seconds"],
                "current_site": c["current_site"],
            },
        })

    # 单站超时（不杀进程）
    if c["current_site_overtime"]:
        alerts.append({
            "level": "high",
            "code": "site_overtime",
            "msg": f"站点 {c['current_site']} 已运行 {c['current_site_elapsed_seconds']}s，"
                   f"超过上限 {c['current_site_timeout_seconds']}s",
            "evidence": {
                "site": c["current_site"],
                "elapsed": c["current_site_elapsed_seconds"],
                "timeout": c["current_site_timeout_seconds"],
            },
        })

    if c.get("status") in ("completed_with_gaps", "success", "failed") and c.get("failed_sites"):
        alerts.append({
            "level": "warn",
            "code": "site_failures",
            "msg": "本轮存在失败/超时站点：" + "、".join(c["failed_sites"]),
            "evidence": {"run_id": c.get("run_id"), "sites": c["failed_sites"]},
        })

    # 站点静默漏抓告警（2026-09-08 v2 口径）：
    # 站点改版选择器失效时不抛异常（failed_sites 只记抛异常站），列表页抓 0 条静默漏抓。
    # 信号：本轮 fetched_count==0 且上一轮也是 0（连续两轮，排除偶发）→ warn。
    # 正常站列表页永远有历史公告（fetched>0），不受补抓批次/水位增量影响。
    # 历史存 run_state.json fetch_history（每站最近 10 轮，按 run_id 去重追加）。
    try:
        cs_sites = (cs or {}).get("sites", {}) if isinstance(cs, dict) else {}
        hist = state.get("fetch_history", {}) or {}
        cur_run = (cs or {}).get("run_id") or ""
        zero_pairs = []
        for sname, sinfo in cs_sites.items():
            if not isinstance(sinfo, dict):
                continue
            fc = sinfo.get("fetched_count")
            if fc is None:
                continue
            prev = [x for x in (hist.get(sname) or []) if isinstance(x, int)]
            if fc == 0 and prev and prev[-1] == 0:
                zero_pairs.append(sname)
        if zero_pairs:
            alerts.append({
                "level": "warn",
                "code": "site_drop",
                "msg": "连续两轮列表抓取 0 条（疑似选择器失效/反爬静默漏抓）：" + "、".join(zero_pairs),
                "evidence": {"sites": zero_pairs, "run_id": cur_run},
            })
        # 按 run_id 去重追加历史（心跳每 5 分钟跑，同轮只记一次）
        if cur_run and hist.get("_run_id") != cur_run:
            for sname, sinfo in cs_sites.items():
                if isinstance(sinfo, dict) and sinfo.get("fetched_count") is not None:
                    prev = [x for x in (hist.get(sname) or []) if isinstance(x, int)]
                    hist[sname] = (prev + [sinfo["fetched_count"]])[-10:]
            hist["_run_id"] = cur_run
            from run_state import update as update_run_state
            update_run_state("fetch_history", hist)
    except Exception:
        pass

    # 日志停滞
    cron_log = c.get("cron_log", {})
    if (pipeline_alive
            and cron_log.get("exists")
            and not cron_log.get("growing_5m")
            and not (deployment.get("phase") == "collect" and c["heartbeat"]["fresh"])):
        alerts.append({
            "level": "warn",
            "code": "cron_log_stalled",
            "msg": f"主抓 PID 存活但 cron.log 已 {cron_log.get('age_seconds')}s 无增长",
        })

    # 08/12/18 主抓缺失
    now = datetime.now(LOCAL_TZ)
    if now.hour in (8, 12, 18) and now.minute >= 10:
        scheduled = now.replace(minute=0, second=0, microsecond=0)
        deploy_started = parse_dt(deployment.get("started_at"))
        if deploy_started is None or deploy_started < scheduled:
            alerts.append({
                "level": "high",
                "code": "missing_scheduled_run",
                "msg": f"{now.hour}:00 主抓在 10 分钟宽限后仍无对应 deployment 状态",
                "evidence": {"expected_after": iso_str(scheduled), "last_started": deployment.get("started_at")},
            })

    # L6 主抓耗时告警（2026-09-07）：正常 1.5-2h（并发化后预期更短），
    # 超 3h 疑似单站卡死（如江苏政采翻页不收敛）或网络恶化。
    if deployment.get("status") == "running" and deployment.get("started_at"):
        dep_start = parse_dt(deployment["started_at"])
        if dep_start:
            elapsed_h = (datetime.now(LOCAL_TZ) - dep_start).total_seconds() / 3600
            if elapsed_h > 3:
                alerts.append({
                    "level": "high",
                    "code": "crawl_overrun",
                    "msg": f"主抓已运行 {elapsed_h:.1f} 小时（阈值 3h），疑似卡死；当前站：{c.get('current_site') or '未知'}",
                    "evidence": {"started_at": deployment.get("started_at"),
                                 "elapsed_hours": round(elapsed_h, 1),
                                 "current_site": c.get("current_site")},
                })

    # 磁盘阈值
    du = snap["resources"]["disk"]
    if du["percent"] >= 90:
        alerts.append({"level": "high", "code": "disk_critical", "msg": f"磁盘 {du['percent']}%"})
    elif du["percent"] >= 80:
        alerts.append({"level": "warn", "code": "disk_warning", "msg": f"磁盘 {du['percent']}%"})

    # 服务可用性
    sv = snap["services"]
    if not sv.get("gateway", {}).get("healthy", False):
        alerts.append({"level": "high", "code": "gateway_down",
                       "msg": "OpenClaw Gateway 健康探针失败",
                       "evidence": sv.get("gateway", {})})
    if "running" not in sv.get("cockpit_container", {}).get("inspect", ""):
        alerts.append({"level": "high", "code": "cockpit_down",
                       "msg": "cockpit 容器状态异常",
                       "evidence": sv.get("cockpit_container", {})})

    for name in ("tender_api",):
        probe = sv.get(name, {}).get("probe", "")
        if probe != "200":
            alerts.append({"level": "high", "code": f"{name}_down",
                           "msg": f"{name} 健康检查返回 {probe or 'empty'}"})

    # 成功发布必须有经过 SQLite quick_check 验证的轻量发布清单。
    if deployment.get("status") == "success":
        if not isinstance(manifest, dict) or not manifest.get("verified"):
            alerts.append({"level": "high", "code": "manifest_missing",
                           "msg": "部署成功但 run_state 缺少有效 manifest"})
        elif manifest.get("published_at") and deployment.get("started_at"):
            published_at = parse_dt(manifest.get("published_at"))
            deploy_started = parse_dt(deployment.get("started_at"))
            if published_at and deploy_started and published_at < deploy_started:
                alerts.append({"level": "high", "code": "manifest_stale",
                               "msg": "发布清单早于本轮部署启动，疑似旧清单"})

    alert_state = update_alert_state(alerts)
    snap["alerts"] = alerts
    snap["alert_state"] = {
        "active_count": len(alert_state.get("active", {})),
        "last_resolved": alert_state.get("resolved", [])[-5:],
    }
    snap["severity"] = (
        "high" if any(a["level"] == "high" for a in alerts) else
        "warn" if any(a["level"] == "warn" for a in alerts) else
        "ok"
    )
    return snap


def write_snapshot(snap: dict, out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(snap, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, out_path)


def summarize(snap: dict) -> str:
    c = snap["crawl"]
    msg = (f"severity={snap['severity']} run={c.get('run_id') or '-'} "
           f"status={c.get('status') or '-'} "
           f"site={c.get('current_site') or '-'} "
           f"pid={c.get('pid') or '-'} alive={c.get('pid_alive')} "
           f"hb_age={c.get('heartbeat',{}).get('age_seconds')}s "
           f"lock={c.get('lock_state')} alerts={len(snap['alerts'])}")
    return msg


def main() -> int:
    ap = argparse.ArgumentParser(description="招投标系统确定性健康快照")
    ap.add_argument("--once", action="store_true", help="一次性产出快照并写盘")
    ap.add_argument("--watch", type=int, default=0, metavar="SECONDS",
                    help="以 SECONDS 秒为间隔持续输出")
    ap.add_argument("--out", default=str(LOGS / "health_snapshot.json"),
                    help="快照 JSON 输出路径")
    ap.add_argument("--print", action="store_true", help="同时打印到 stdout")
    args = ap.parse_args()

    out_path = Path(args.out)

    if args.watch > 0:
        while True:
            try:
                snap = collect()
                write_snapshot(snap, out_path)
                if args.print:
                    print(summarize(snap))
                sys.stdout.flush()
            except Exception as e:
                print(f"snapshot_error: {type(e).__name__}: {e}", file=sys.stderr)
            time.sleep(args.watch)
    else:
        snap = collect()
        write_snapshot(snap, out_path)
        if args.print:
            print(json.dumps(snap, ensure_ascii=False, indent=2))
        else:
            print(summarize(snap))
    return 0


if __name__ == "__main__":
    sys.exit(main())
