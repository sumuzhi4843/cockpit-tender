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

启动：bash api_service.sh start   （nohup 常驻，写 api.log，端口 8002）
"""
import os
import re
import json
import sqlite3
import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(HERE, "data")
DB_PATH = os.path.join(HERE, "collector", "data", "tender.db")
PORT = 8002

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
VALID_USERS = ("user1", "user2", "user3", "user4")  # 2026-08-19 首批账号
DEFAULT_USER = "user1"  # 旧收藏迁移目标（老数据归属该用户）


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
    conn.close()


def load_overrides():
    """读全部修订 → {project_id: district}"""
    ensure_table()
    conn = _conn()
    rows = conn.execute("SELECT project_id, district FROM district_overrides").fetchall()
    conn.close()
    return {r["project_id"]: r["district"] for r in rows}


def save_override(project_id, district):
    """写修订；district='' 表示删除（恢复自动）。返回 (ok, existed)"""
    ensure_table()
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = _conn()
    if district == "":
        cur = conn.execute("DELETE FROM district_overrides WHERE project_id=?", (project_id,))
        existed = cur.rowcount > 0
        conn.commit()
        conn.close()
        return True, existed
    existed = conn.execute(
        "SELECT 1 FROM district_overrides WHERE project_id=?", (project_id,)).fetchone() is not None
    if existed:
        conn.execute(
            "UPDATE district_overrides SET district=?, updated_at=? WHERE project_id=?",
            (district, now, project_id))
    else:
        conn.execute(
            "INSERT INTO district_overrides (project_id, district, created_at, updated_at) VALUES (?,?,?,?)",
            (project_id, district, now, now))
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

        if path == "/api/tender/district_overrides":
            self._send(200, load_overrides())
            return

        if path == "/api/tender/favorites":
            user = self._get_user()
            self._send(200, {**load_favorites(user), "_user": user})
            return

        self._send(404, {"error": "not found"})

    def do_PUT(self):
        url = urlparse(self.path)
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


def main():
    ensure_table()
    ensure_fav_table()
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"招投标区县修订 API 已启动（SQLite 存储）→ http://127.0.0.1:{PORT}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
