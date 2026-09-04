"""苏服采分页抓取账本与原始项目索引。"""

from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any


DDL = """
CREATE TABLE IF NOT EXISTS su_fucai_collection_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    endpoint TEXT NOT NULL,
    notice_type TEXT NOT NULL DEFAULT '',
    area_code TEXT NOT NULL DEFAULT '',
    api_total INTEGER,
    fetched_count INTEGER NOT NULL DEFAULT 0,
    page_count INTEGER NOT NULL DEFAULT 0,
    page_size INTEGER NOT NULL DEFAULT 20,
    max_pages INTEGER NOT NULL DEFAULT 1000,
    cutoff_date TEXT,
    status TEXT NOT NULL DEFAULT 'ok',
    started_at TEXT NOT NULL,
    finished_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS su_fucai_source_items (
    source_id TEXT PRIMARY KEY,
    endpoint TEXT NOT NULL,
    project_key TEXT NOT NULL DEFAULT '',
    notice_type TEXT NOT NULL DEFAULT '',
    title TEXT NOT NULL DEFAULT '',
    url TEXT NOT NULL DEFAULT '',
    raw_json TEXT NOT NULL DEFAULT '{}',
    fetched_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sufucai_source_project
    ON su_fucai_source_items(project_key);
"""


class SufucaiLedger:
    def __init__(self, db_path: str | Path):
        self.db_path = str(db_path)
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(DDL)
        self.conn.commit()

    def record_scrape(self, scraper: Any) -> None:
        if not getattr(scraper, "fetch_stats", None):
            return
        grouped: dict[tuple[str, str, str], dict[str, Any]] = {}
        for stat in scraper.fetch_stats:
            raw_area = stat.get("area_code", "")
            if isinstance(raw_area, (list, tuple)):
                area_code = str(raw_area[0]) if len(raw_area) == 1 else json.dumps(raw_area, ensure_ascii=False)
            else:
                area_code = str(raw_area or "")
            key = (stat.get("endpoint", ""), str(stat.get("type", "")), area_code)
            row = grouped.setdefault(key, {
                "api_total": None,
                "fetched_count": 0,
                "source_ids": set(),
                "page_count": 0,
                "pages": [],
                "error": None,
            })
            row["fetched_count"] += int(stat.get("rows") or 0)
            row["source_ids"].update(stat.get("source_ids") or [])
            row["page_count"] += 1
            row["pages"].append(stat)
            if stat.get("total") is not None:
                row["api_total"] = int(stat["total"])
            if stat.get("error"):
                row["error"] = stat["error"]
        started = datetime.now().isoformat(timespec="seconds")
        for (endpoint, notice_type, area_code), data in grouped.items():
            status = "error" if data["error"] else "ok"
            self.conn.execute(
                """INSERT INTO su_fucai_collection_runs
                   (endpoint, notice_type, area_code, api_total, fetched_count,
                    page_count, page_size, max_pages, cutoff_date, status,
                    started_at, finished_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    endpoint,
                    notice_type,
                    area_code,
                    data["api_total"],
                    len(data["source_ids"]) if data["source_ids"] else data["fetched_count"],
                    data["page_count"],
                    data["pages"][-1].get("page_size", 20),
                    getattr(scraper, "max_pages", 1000),
                    getattr(scraper, "cutoff_date", None),
                    status,
                    started,
                    datetime.now().isoformat(timespec="seconds"),
                ),
            )
        self.conn.commit()

    def reset_source_index(self) -> None:
        self.conn.execute("DELETE FROM su_fucai_source_items")
        self.conn.commit()

    def record_source_items(self, items: list[dict]) -> None:
        fetched_at = datetime.now().isoformat(timespec="seconds")
        for item in items:
            source_id = str(item.get("source_key") or item.get("source_id") or "")
            if not source_id:
                continue
            raw = {
                "source_key": source_id,
                "title": item.get("title", ""),
                "url": item.get("url", ""),
                "date": item.get("date", ""),
                "content": item.get("content", ""),
                "tenderer": item.get("tenderer", ""),
                "amount": item.get("amount", ""),
                "amount_value": item.get("amount_value"),
                "bid_deadline": item.get("bid_deadline", ""),
                "winner": item.get("winner", ""),
                "winning_amount": item.get("winning_amount", ""),
                "project_key": item.get("project_key", ""),
                "item_no": item.get("item_no", ""),
                "item_name": item.get("item_name", ""),
                "contact_person": item.get("contact_person", ""),
                "contact_phone": item.get("contact_phone", ""),
                "area_code": item.get("area_code", ""),
            }
            self.conn.execute(
                """INSERT INTO su_fucai_source_items
                   (source_id, endpoint, project_key, notice_type, title, url, raw_json, fetched_at)
                   VALUES (?,?,?,?,?,?,?,?)
                   ON CONFLICT(source_id) DO UPDATE SET
                   endpoint=excluded.endpoint,
                   project_key=excluded.project_key,
                   notice_type=excluded.notice_type,
                   title=excluded.title,
                   url=excluded.url,
                   raw_json=excluded.raw_json,
                   fetched_at=excluded.fetched_at""",
                (
                    source_id,
                    "sufucai",
                    raw["project_key"],
                    item.get("notice_type", ""),
                    raw["title"],
                    raw["url"],
                    json.dumps(raw, ensure_ascii=False),
                    fetched_at,
                ),
            )
        self.conn.commit()

    def record_existing_source_items(self) -> None:
        """把历史已入库的苏服采公告也登记到原始项目索引。"""
        rows = self.conn.execute("""
            SELECT id, raw_json FROM notices WHERE site='苏服采'
        """).fetchall()
        items = []
        for row in rows:
            try:
                raw = json.loads(row["raw_json"] or "{}")
            except Exception:
                raw = {}
            items.append({
                "source_id": row["id"],
                "source_key": f"{raw.get('source_id') or row['id']}|{raw.get('project_key', '')}",
                "project_key": raw.get("project_key", ""),
                "notice_type": "",
                "title": raw.get("title", ""),
                "url": raw.get("url", ""),
                "content": raw.get("content", ""),
                "tenderer": raw.get("tenderer", ""),
                "amount": raw.get("amount", ""),
                "amount_value": raw.get("amount_value"),
                "bid_deadline": raw.get("bid_deadline", ""),
                "winner": raw.get("winner", ""),
                "winning_amount": raw.get("winning_amount", ""),
                "item_no": raw.get("item_no", ""),
                "item_name": raw.get("item_name", ""),
                "contact_person": raw.get("contact_person", ""),
                "contact_phone": raw.get("contact_phone", ""),
                "area_code": raw.get("area_code", ""),
            })
        self.record_source_items(items)

    def close(self) -> None:
        self.conn.close()
