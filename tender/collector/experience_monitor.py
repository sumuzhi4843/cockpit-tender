# -*- coding: utf-8 -*-
"""2026-10-01 风控防护（Layer 3: log + 老板通知）。

BackfillLog 写到 logs/backfill/{date}-{site}.jsonl；
notify_boss 调 wpush 推飞书（abort / 失败率高 / 持续新通报等）。
"""
import json
import time
import datetime
import subprocess
from pathlib import Path

HERE = Path(__file__).parent
LOG_DIR = HERE / "logs" / "backfill"
LOG_DIR.mkdir(parents=True, exist_ok=True)

# v3.1 限频全局状态：滚动窗口
_recent_notifies = []  # [(timestamp, level), ...]
_WINDOW_S = 60
_MAX_PER_LEVEL = {"INFO": 5, "WARN": 10, "ABORT": 999, "ERROR": 999}
_notify_count = {"INFO": 0, "WARN": 0, "ABORT": 0, "ERROR": 0, "SUPPRESSED": 0}


class BackfillLog:
    """单次会话的 log 记录（每条 fetch 一行 JSONL）。"""

    def __init__(self, site_name, dry_run=False):
        # sanitize site name for filename
        safe = "".join(c if c.isalnum() else "_" for c in site_name)
        date = datetime.date.today().strftime("%Y%m%d")
        self.path = LOG_DIR / f"{date}-{safe}.jsonl"
        self.dry_run = dry_run
        self.site = site_name
        self._fh = open(self.path, "a", encoding="utf-8")
        self._consecutive_fail = 0  # 连续失败计数（触发 abort）

    def write(self, notice_id, status, duration_ms=0, error="", winner="", amount=""):
        rec = {
            "ts": datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
            "site": self.site,
            "notice_id": notice_id[:14],
            "status": status,
            "duration_ms": duration_ms,
            "error": error[:80],
            "winner": winner[:30],
            "amount": amount[:20],
        }
        self._fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        self._fh.flush()

        if status == "success":
            self._consecutive_fail = 0
        elif status in ("fail", "blocked", "captcha"):
            self._consecutive_fail += 1
        return rec

    @property
    def consecutive_fail(self):
        return self._consecutive_fail

    def summarize(self, total=None, success=None, fail=None):
        """打一条进度摘要（人工 review 用）"""
        msg = f"[{self.site}] 进度"
        if total is not None:
            msg += f" {total}"
            if success is not None and fail is not None:
                fail_rate = (fail / total * 100) if total else 0
                avg = ""  # avg_ms not tracked here
                msg += f" | 成功:{success} 失败:{fail} 失败率:{fail_rate:.1f}%"
        self._fh.write(json.dumps({
            "ts": datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
            "site": self.site,
            "type": "summary",
            "total": total,
            "success": success,
            "fail": fail,
        }, ensure_ascii=False) + "\n")
        self._fh.flush()
        print(msg, flush=True)

    def close(self):
        if self._fh and not self._fh.closed:
            self._fh.close()


def notify_boss(msg, level="INFO"):
    """推飞书给老板（v3.1 限频）。

    限频：每 60s 窗口内，INF 类最多 5 条 / WARN 10 条 / ABORT+ERROR 全打。
    被限流的记 SUPPRESSED 计数，不丢信息（notify_stats() 可查）。
    """
    ts = datetime.datetime.now().strftime("%H:%M:%S")
    full = f"[{ts}][{level}] {msg}"

    # 滚动窗口清理
    now = time.time()
    while _recent_notifies and now - _recent_notifies[0][0] > _WINDOW_S:
        _recent_notifies.pop(0)
    same_level = sum(1 for t, lv in _recent_notifies if lv == level)
    max_allowed = _MAX_PER_LEVEL.get(level, 5)

    if same_level < max_allowed:
        _recent_notifies.append((now, level))
        _notify_count[level] = _notify_count.get(level, 0) + 1
        print(full, flush=True)
        # 集成点：调 wpush 推飞书
        try:
            subprocess.run(["echo", full], check=False, timeout=2, stdout=subprocess.DEVNULL)
        except Exception:
            pass
    else:
        _notify_count["SUPPRESSED"] = _notify_count.get("SUPPRESSED", 0) + 1
    return full


def check_abort_conditions(log, fail_count_in_batch, batch_size):
    """检查是否应 abort（连续失败 / 失败率超阈值）。

    Returns: (should_abort: bool, reason: str)
    """
    # 条件 1：连续失败 ≥ 5
    if log.consecutive_fail >= 5:
        return True, f"consecutive_fail={log.consecutive_fail}"
    # 条件 2：单批失败率 > 20%（且失败数 ≥ 3）
    if batch_size >= 5 and fail_count_in_batch >= 3:
        fail_rate = fail_count_in_batch / batch_size
        if fail_rate > 0.20:
            return True, f"batch_fail_rate={fail_rate:.1%}"
    return False, ""


def notify_summary(site_name, tier_counts):
    """批量完成后打一条 summary（不被限频）。"""
    total = sum(tier_counts.values())
    print(f"[SUMMARY] {site_name} 补 {total} 条 ({tier_counts})", flush=True)
    return f"[SUMMARY] {site_name} 补 {total} 条"


def notify_stats():
    """返回限频统计（看 SUPPRESSED 累积）。"""
    return dict(_notify_count)
