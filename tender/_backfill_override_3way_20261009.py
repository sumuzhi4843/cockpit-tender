#!/usr/bin/env python3
"""2026-10-09：把 32 条历史 override 不一致记录回灌到 projects.district + notices.std_district。

原因：之前 save_override 只写 district_overrides，没回写 projects/notices，导致 SQL
过滤（基于 p.district）和 override 口径不一致。修完 save_override 后，把历史不一致
一并同步过来。脚本只动"有项目存在且 district 不一致"的记录，孤儿 override 不动
（proj_key 兜底机制已覆盖列表显示）。
"""
import sqlite3
DB = 'collector/data/tender.db'

conn = sqlite3.connect(DB)
cur = conn.cursor()

# 拿所有 override 中 project 仍存在的、且 p.district 与 override 不一致的
cur.execute("""
SELECT o.project_id, o.district, p.district, p.display_name
FROM district_overrides o
INNER JOIN projects p ON p.id = o.project_id
WHERE COALESCE(o.district, '') != COALESCE(p.district, '')
""")
rows = cur.fetchall()
print(f'不一致: {len(rows)} 条')

# 同时统计孤儿
cur.execute("""
SELECT COUNT(*) FROM district_overrides o
LEFT JOIN projects p ON p.id = o.project_id
WHERE p.id IS NULL
""")
orphans = cur.fetchone()[0]
print(f'孤儿 override (project 已不存在): {orphans} 条 — 不动，靠 proj_key 兜底')

ok, skip, fail = 0, 0, 0
for pid, ov_dist, p_dist, name in rows:
    try:
        # 1) projects.district
        cur.execute("UPDATE projects SET district=? WHERE id=?", (ov_dist, pid))
        np = cur.rowcount
        # 2) notices.std_district (项目关联的所有公告)
        cur.execute("""UPDATE notices SET std_district=?
                       WHERE id IN (SELECT notice_id FROM project_notices WHERE project_id=?)""",
                    (ov_dist, pid))
        nn = cur.rowcount
        # 3) 同步快照 original_district（之前是 NULL，现在把当前已被覆盖的 p.district 写回去）
        #    注意：此刻 p.district 还是旧值（上面 UPDATE 还没 commit），所以读到的就是 pre-override 值
        #    用 SAVEPOINT/还原的方式取一下原值
        #    简化：直接用刚才 SELECT 拿到的 p_dist
        if p_dist is not None:
            cur.execute("""UPDATE district_overrides SET original_district=?
                           WHERE project_id=? AND (original_district IS NULL OR original_district='')""",
                        (p_dist, pid))
        ok += 1
        print(f'  [OK] {ov_dist:6s} <- {p_dist or "<NULL>":6s}  projects={np} notices={nn}  {name[:50]}')
    except Exception as e:
        fail += 1
        print(f'  [FAIL] {pid}: {e}')

conn.commit()
print(f'\n回灌 {ok} 条 (skip {skip}, fail {fail})')

# 校验
cur.execute("""
SELECT COUNT(*) FROM district_overrides o
INNER JOIN projects p ON p.id = o.project_id
WHERE COALESCE(o.district, '') != COALESCE(p.district, '')
""")
print(f'回灌后仍不一致: {cur.fetchone()[0]} 条')
conn.close()
