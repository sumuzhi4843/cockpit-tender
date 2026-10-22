# -*- coding: utf-8 -*-
"""SQLite 存储层 — Phase 2（2026-08-07 并入 store.py 去重逻辑）

本地招标监控的历史数据持久化。单库 data/tender.db，notices 表存全量历史 +
富化字段 + 4 类公告类型。

设计（对比 Phase 1 的 store.json 仅 MD5 去重）：
- store.json 继续负责去重（控制报告推送，FIFO 5000）
- tender.db 存全量历史（可回溯、可按类型/站点/日期查询）
- 按 detail_url 去重（upsert），同公告重复采集只更新不重复插入

对外接口：
    TenderDB()                        # 单例，自动建表
    db.upsert(site, item) -> bool     # 插入/更新一条公告，返回 True=新增
    db.count(notice_type=None) -> int
    db.count_by_type() -> dict
    db.query(site=None, notice_type=None, start=None, end=None, limit=100)
    db.close()

去重（原 store.py，2026-08-07 并入）：
    load_store() / save_store(store)
    item_hash(item, site)             # md5(site|url|title|date)
    is_pushed(item, store, site)      # 是否已推送
    mark_pushed(items, store, site)   # 标记推送 + FIFO 裁剪
"""
import hashlib
import json
import os
import sqlite3
from datetime import datetime

from config import STORE_FILE, STORE_MAX_ENTRIES, DATE_UNSTABLE_SITES
from enrich_engine import classify_item
from site_names import stable_site_name

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
DB_PATH = os.path.join(DATA_DIR, "tender.db")

# 2026-09-28：列表日期不可靠站点集合见 config.DATE_UNSTABLE_SITES。防护两层：
#   ① id 含日期，日期漂移会命中不到原行、再插一条新行（裂行重复）——同 site+url
#      已有行时按 URL 复用更新，禁止新插；
#   ② publish_date 只允许被详情页“发布时间”提取值（item["date_source"]=="detail"）
#      改写，列表页猜测日期不得顶掉库内已确认值。


class TenderDB:
    SCHEMA = """
    CREATE TABLE IF NOT EXISTS notices (
        id              TEXT PRIMARY KEY,
        site            TEXT NOT NULL,
        notice_type     TEXT NOT NULL,   -- tender/intention/award/other
        notice_subtype  TEXT,            -- tender_notice/award_candidate/award_final/...
        classification_confidence REAL,
        classification_reason TEXT,
        classification_version TEXT,
        title           TEXT NOT NULL,
        url             TEXT,
        publish_date    TEXT,            -- 列表页日期 YYYY-MM-DD
        crawl_time      TEXT DEFAULT (datetime('now', 'localtime')),  -- 首次入库时间（不再随更新变化）
        last_seen       TEXT,            -- 最近一次在列表/API 中抓到的时间（2026-08-06）
        tenderer        TEXT,            -- 发包人
        amount          TEXT,            -- 金额字符串（如 21.44万元）
        amount_value    REAL,            -- 金额数值（元）
        amount_unit     TEXT,
        bid_deadline    TEXT,            -- 开标时间
        deadline        TEXT,            -- 报名截止
        winner          TEXT,            -- 中标单位（award）
        winning_amount  TEXT,            -- 中标金额
        ict_keywords    TEXT,            -- JSON 数组，ICT 命中关键词
        tags            TEXT,            -- JSON 数组，统一标签（区县/行业/ICT/中小微/金额档位等，2026-08-04）
        raw_json        TEXT             -- 原始抓取数据备份
    );
    CREATE INDEX IF NOT EXISTS idx_site      ON notices(site);
    CREATE INDEX IF NOT EXISTS idx_type      ON notices(notice_type);
    CREATE INDEX IF NOT EXISTS idx_publish   ON notices(publish_date);
    CREATE INDEX IF NOT EXISTS idx_url       ON notices(url);
    """

    def __init__(self, db_path=None):
        self.db_path = db_path or DB_PATH
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._conn = sqlite3.connect(self.db_path)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute('PRAGMA journal_mode=WAL')
        self._conn.execute('PRAGMA busy_timeout=5000')
        self._init_schema()

    def _init_schema(self):
        for stmt in self.SCHEMA.strip().split(";"):
            stmt = stmt.strip()
            if stmt:
                self._conn.execute(stmt)
        # 幂等补列：兼容旧库增量迁移
        cols = [r[1] for r in self._conn.execute("PRAGMA table_info(notices)")]
        migrations = {
            "tags": "TEXT",
            "notice_subtype": "TEXT",
            "classification_confidence": "REAL",
            "classification_reason": "TEXT",
            "classification_version": "TEXT",
            "last_seen": "TEXT",
            # 2026-09-13 方案3（增量富化）：内容有变化才重新富化的水位列
            "needs_enrich": "INTEGER DEFAULT 1",
            "enrich_ver": "INTEGER DEFAULT 0",
            "enrich_src_len": "INTEGER",
        }
        for name, col_type in migrations.items():
            if name not in cols:
                self._conn.execute(f"ALTER TABLE notices ADD COLUMN {name} {col_type}")
        self._conn.commit()
        # 区县人工修订表（2026-08-16：独立持久层，不随 projects 重建清空）
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS district_overrides (
                project_id  TEXT PRIMARY KEY,
                district    TEXT NOT NULL,
                created_at  TEXT NOT NULL,
                updated_at  TEXT NOT NULL,
                remark      TEXT DEFAULT ''
            )
        """)
        self._conn.commit()

    def _make_id(self, item, site):
        """按 (site, url, title, date) 生成稳定 ID。"""
        raw = f"{stable_site_name(site)}|{item.get('url','')}|{item.get('title','')}|{item.get('date','')}"
        return hashlib.md5(raw.encode("utf-8")).hexdigest()

    def upsert(self, site, item, ict_keywords=None, tags=None):
        """插入或更新一条公告。返回 True=新增，False=更新。
        tags: JSON 数组字符串或列表（2026-08-04）"""
        nid = self._make_id(item, site)
        existing = self._conn.execute(
            "SELECT id,raw_json,winner,winning_amount,bid_deadline,deadline,ict_keywords,tags,tenderer,amount,amount_value,amount_unit,needs_enrich,publish_date FROM notices WHERE id=?", (nid,)).fetchone()
        # 日期不可靠站防裂行：id 未命中时按 站点+URL 找既有行复用
        # （详情 URL 一篇公告一个，URL 级去重符合本模块“按 detail_url 去重”的设计初衷）
        if existing is None and site in DATE_UNSTABLE_SITES and item.get("url"):
            existing = self._conn.execute(
                "SELECT id,raw_json,winner,winning_amount,bid_deadline,deadline,ict_keywords,tags,tenderer,amount,amount_value,amount_unit,needs_enrich,publish_date"
                " FROM notices WHERE site=? AND url=? AND url<>'' ORDER BY crawl_time ASC LIMIT 1",
                (site, item["url"])).fetchone()
            if existing:
                nid = existing["id"]

        # 金额数值解析（从 amount 字符串）
        amount_value = None
        amount_unit = ""
        amount_str = item.get("amount", "") or ""
        classification = classify_item(item)
        if amount_str:
            try:
                if "亿" in amount_str:
                    amount_value = float(amount_str.replace("亿元", "")) * 1e8
                    amount_unit = "元"
                elif "万" in amount_str:
                    amount_value = float(amount_str.replace("万元", "").replace("万", "")) * 1e4
                    amount_unit = "元"
                else:
                    amount_value = float(amount_str.replace("元", ""))
                    amount_unit = "元"
            except (ValueError, TypeError):
                amount_value = None

        raw_data = dict(item)
        # 2026-08-21：合入 API 原始 row（包含 areaCode、itemNo 等原始字段名），
        # 让 SUFUCAI_AREA_CODE_MAP 能从 raw_json 直接拿到 API 原始 areaCode 做分类。
        raw_row = item.get("raw_row") or {}
        if raw_row:
            raw_data["raw_row"] = dict(raw_row)
        if existing:
            try:
                existing_raw = json.loads(existing["raw_json"] or "{}")
            except (TypeError, json.JSONDecodeError):
                existing_raw = {}
            # 列表重抓只更新本轮有值的字段，保留 PDF/OCR/M3 回填元数据。
            raw_data = {**existing_raw, **{k: v for k, v in raw_data.items() if v not in (None, "", [], {})}}

        # —— 金额三件套保护（2026-09-19）：列表重抓通常不返金额，直接覆盖会把 PDF/OCR/M3 回填值刷空
        # （tenderer/winner 历史已踩此坑并加保护，amount 是唯一漏网字段，致 M3 回填被反复抹掉重回假缺池）。
        # 新数据有金额时三字段整体取新，无金额时整体保留库内，避免 amount/amount_value 错位。——
        if amount_str:
            amount_final, amount_value_final, amount_unit_final = amount_str, amount_value, amount_unit
        elif existing:
            amount_final, amount_value_final, amount_unit_final = (
                existing["amount"] or "", existing["amount_value"], existing["amount_unit"] or "")
        else:
            amount_final, amount_value_final, amount_unit_final = "", None, ""

        record = {
            "id": nid,
            "site": site,
            "notice_type": classification.notice_type,
            "notice_subtype": classification.notice_subtype,
            "classification_confidence": classification.confidence,
            "classification_reason": classification.reason,
            "classification_version": classification.version,
            "title": item.get("title", ""),
            "url": item.get("url", ""),
            "publish_date": item.get("date", ""),
            # 2026-09-08：tenderer 同保护策略——列表接口通常不返回发包人，直接覆盖会把
            # 详情富化/回灌所得的值刷空（江苏政采历史教训），有值才覆盖
            "tenderer": item.get("tenderer") or (existing["tenderer"] if existing else "") or "",
            "amount": amount_final,
            "amount_value": amount_value_final,
            "amount_unit": amount_unit_final,
            # 2026-08-07：bid_deadline/deadline 也加保护——重抓列表接口通常不返回开标时间，
            # 若已有值（PDF补抓/富化所得）则保留，避免覆盖丢失（与 winner 同策略）
            "bid_deadline": item.get("bid_deadline") or (existing["bid_deadline"] if existing else "") or "",
            "deadline": item.get("deadline") or (existing["deadline"] if existing else "") or "",
            "winner": item.get("winner") or (existing["winner"] if existing else "") or "",
            "winning_amount": item.get("winning_amount") or (existing["winning_amount"] if existing else "") or "",
            # 2026-08-09：ict_keywords/tags 同保护策略——调用方未传（空列表）时
            # 保留 DB 已有值（deep_refresher 只抓详情不跑 analyzer，避免清空历史 ICT 标记）
            "ict_keywords": json.dumps(ict_keywords or [], ensure_ascii=False) if ict_keywords else (existing["ict_keywords"] if existing and existing["ict_keywords"] else "[]"),
            "tags": json.dumps(tags or [], ensure_ascii=False) if tags else (existing["tags"] if existing and existing["tags"] else "[]"),
            "raw_json": json.dumps(raw_data, ensure_ascii=False),
        }

        # 日期不可靠站点：非“详情页发布时间”来源的日期不许顶掉库内已有日期
        if existing and site in DATE_UNSTABLE_SITES and (item.get("date_source") != "detail" or not item.get("date")):
            record["publish_date"] = existing["publish_date"] or record["publish_date"]

        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        record["last_seen"] = now

        if existing:
            # 2026-09-13 方案3（增量富化）：只有 raw_json 实际变化才打回待富化，
            # 否则保留原标记（避免每轮列表重抓把全库都打回，增量失去意义）
            record["needs_enrich"] = 1 if record["raw_json"] != (existing["raw_json"] or "") else (existing["needs_enrich"] or 0)
            # crawl_time 保持首次入库不变；last_seen 每次抓到都刷新（站点活跃度监控用）
            self._conn.execute("""
                UPDATE notices SET notice_type=:notice_type, notice_subtype=:notice_subtype,
                    classification_confidence=:classification_confidence,
                    classification_reason=:classification_reason,
                    classification_version=:classification_version, title=:title,
                    url=:url, publish_date=:publish_date, tenderer=:tenderer,
                    amount=:amount, amount_value=:amount_value, amount_unit=:amount_unit,
                    bid_deadline=:bid_deadline, deadline=:deadline, winner=:winner,
                    winning_amount=:winning_amount, ict_keywords=:ict_keywords,
                    tags=:tags, raw_json=:raw_json, last_seen=:last_seen,
                    needs_enrich=:needs_enrich
                WHERE id=:id
            """, record)
            self._conn.commit()
            return False
        else:
            record["crawl_time"] = now
            record["needs_enrich"] = 1
            self._conn.execute("""
                INSERT INTO notices (id, site, notice_type, notice_subtype,
                    classification_confidence, classification_reason, classification_version,
                    title, url, publish_date, crawl_time, last_seen,
                    tenderer, amount, amount_value, amount_unit, bid_deadline, deadline,
                    winner, winning_amount, ict_keywords, tags, raw_json, needs_enrich)
                VALUES (:id, :site, :notice_type, :notice_subtype,
                    :classification_confidence, :classification_reason, :classification_version,
                    :title, :url, :publish_date, :crawl_time, :last_seen,
                    :tenderer, :amount, :amount_value, :amount_unit, :bid_deadline, :deadline,
                    :winner, :winning_amount, :ict_keywords, :tags, :raw_json, :needs_enrich)
            """, record)
            self._conn.commit()
            return True

    def count(self, notice_type=None):
        if notice_type:
            return self._conn.execute(
                "SELECT COUNT(*) FROM notices WHERE notice_type=?", (notice_type,)).fetchone()[0]
        return self._conn.execute("SELECT COUNT(*) FROM notices").fetchone()[0]

    def count_by_type(self):
        rows = self._conn.execute(
            "SELECT notice_type, COUNT(*) FROM notices GROUP BY notice_type").fetchall()
        return {r["notice_type"]: r[1] for r in rows}

    def count_by_site(self):
        rows = self._conn.execute(
            "SELECT site, COUNT(*) FROM notices GROUP BY site").fetchall()
        return {r["site"]: r[1] for r in rows}

    def query(self, site=None, notice_type=None, start=None, end=None, limit=100):
        """通用查询。"""
        sql = "SELECT * FROM notices WHERE 1=1"
        params = []
        if site:
            sql += " AND site=?"
            params.append(site)
        if notice_type:
            sql += " AND notice_type=?"
            params.append(notice_type)
        if start:
            sql += " AND publish_date>=?"
            params.append(start)
        if end:
            sql += " AND publish_date<=?"
            params.append(end)
        sql += " ORDER BY publish_date DESC, crawl_time DESC LIMIT ?"
        params.append(limit)
        rows = self._conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def site_activity(self, since=None):
        """站点活跃度：每站最近 last_seen + 今日抓到的公告数（2026-08-06）。
        since: 'YYYY-MM-DD'，缺省为今天。
        """
        since = since or datetime.now().strftime("%Y-%m-%d")
        rows = self._conn.execute("""
            SELECT site, COUNT(*) AS today_count, MAX(last_seen) AS last_seen
            FROM notices
            WHERE last_seen >= ?
            GROUP BY site ORDER BY site
        """, (since,)).fetchall()
        return [dict(r) for r in rows]

    def close(self):
        if self._conn:
            self._conn.close()
            self._conn = None


# ─────────────────────────────────────────────
# 去重存储（2026-08-07 并入：原 store.py）
# 仅控制报告推送；数据出口是 SQLite→8002 API（tender.json 已于 2026-09-29 下线）
# ─────────────────────────────────────────────

def load_store():
    """加载去重记录"""
    if os.path.exists(STORE_FILE):
        try:
            with open(STORE_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            # 文件损坏则重建
            return {"pushed": []}
    return {"pushed": []}


def save_store(store):
    """保存去重记录"""
    os.makedirs(os.path.dirname(STORE_FILE) or '.', exist_ok=True)
    with open(STORE_FILE, 'w', encoding='utf-8') as f:
        json.dump(store, f, ensure_ascii=False, indent=2)


def item_hash(item, site=""):
    """根据 site+标题+URL+日期生成 hash（与 _make_id 同算法，2026-08-04）"""
    raw = f"{stable_site_name(site)}|{item.get('url', '')}|{item.get('title', '')}|{item.get('date', '')}"
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


def is_pushed(item, store, site=""):
    """判断是否已推送过"""
    return item_hash(item, site) in store["pushed"]


def mark_pushed(items, store, site=""):
    """标记为已推送，超限时裁剪旧条目"""
    for item in items:
        h = item_hash(item, site)
        if h not in store["pushed"]:
            store["pushed"].append(h)

    # 裁剪：保留最新的 STORE_MAX_ENTRIES 条
    if len(store["pushed"]) > STORE_MAX_ENTRIES:
        store["pushed"] = store["pushed"][-STORE_MAX_ENTRIES:]

    save_store(store)


if __name__ == "__main__":
    # 自检：建表 + 空库统计
    db = TenderDB()
    print("DB 路径:", db.db_path)
    print("表已建，总数:", db.count())
    print("按类型:", db.count_by_type())
    db.close()
