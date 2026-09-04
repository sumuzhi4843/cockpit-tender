# -*- coding: utf-8 -*-
"""抓取运行账本。

独立 SQLite 文件，避免抓取状态记录与业务主库互相影响。每个站点/分页范围
一行，可用于判断 success / partial / failed / timeout 以及定位缺失页面。
"""
import json
import os
import sqlite3
from datetime import datetime


DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "crawl_ledger.db")


def record(run_id, site, status, *, scope="", expected_count=None,
           fetched_count=0, successful_pages=0, expected_pages=None,
           failed_pages=None, error="", started_at="", finished_at=""):
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = sqlite3.connect(DB_PATH, timeout=10)
    try:
        conn.execute("PRAGMA busy_timeout=10000")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS crawl_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                site TEXT NOT NULL,
                scope TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL,
                expected_count INTEGER,
                fetched_count INTEGER NOT NULL DEFAULT 0,
                successful_pages INTEGER NOT NULL DEFAULT 0,
                expected_pages INTEGER,
                failed_pages TEXT NOT NULL DEFAULT '[]',
                error TEXT NOT NULL DEFAULT '',
                started_at TEXT NOT NULL DEFAULT '',
                finished_at TEXT NOT NULL DEFAULT '',
                recorded_at TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_crawl_runs_lookup
            ON crawl_runs(run_id, site, scope)
        """)
        conn.execute("""
            INSERT INTO crawl_runs (
                run_id, site, scope, status, expected_count, fetched_count,
                successful_pages, expected_pages, failed_pages, error,
                started_at, finished_at, recorded_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            run_id, site, scope, status, expected_count, fetched_count,
            successful_pages, expected_pages,
            json.dumps(failed_pages or [], ensure_ascii=False), error or "",
            started_at or "", finished_at or "", now,
        ))
        conn.commit()
    finally:
        conn.close()


def prune(keep_days=90):
    conn = sqlite3.connect(DB_PATH, timeout=10)
    try:
        conn.execute(
            "DELETE FROM crawl_runs WHERE recorded_at < datetime('now', 'localtime', ?)",
            (f"-{int(keep_days)} days",),
        )
        conn.commit()
    finally:
        conn.close()
