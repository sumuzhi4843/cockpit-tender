# -*- coding: utf-8 -*-
"""苏服采详情回填（2026-08-19；2026-08-21 扩展 areaCode/itemNo）

背景：苏服采公开列表接口（/page、/announcementResultList）不返回
单位地址、项目地点、成交供应商；区域归类只能靠企业库/关键词弱证据。

详情接口：GET {api_url}/purchases/tenders/notice/resultAnnouncement/{project_key}
返回（实测）：
  address          单位地址（如「盐城市康居路初级中学」「世纪大道21号」）
  projectLocation  项目地点（如「盐城市盐都区潘黄街道宝才社区…」）
  supplierName     成交供应商（award 公告有值；tender 公告 None）
  areaCode         区县代码（公告接口有；比选接口 /page 无，详情接口也能取到）
  itemNo           项目编号（如「JSZC-320903-FW2026-04566」，含区县级编码）

本脚本按唯一 project_key 批量回填到 notices.raw_json：
  detail_address      单位地址
  detail_project_location  项目地点
  detail_supplier     成交供应商
  raw_row.areaCode    公告接口的区县编码（2026-08-21 新增）
  raw_row.itemNo      项目编号（2026-08-21 新增，便于审计与溯源）
并基于地址/地点做一次区域判定（5 级优先级第一级：详情地址）。

用法:
  python3 backfill_sufucai_detail.py            # 全量回填（跳过已有 detail_address 的）
  python3 backfill_sufucai_detail.py --force    # 强制重抓全部
  python3 backfill_sufucai_detail.py --limit 50 # 只回填前 50 个 project_key（测试）
"""
import argparse
import json
import sqlite3
import sys
import time
import urllib3
from pathlib import Path

import requests

urllib3.disable_warnings()

ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "data" / "tender.db"
API_BASE = "https://js.fwgov.cn:868"
DETAIL_ENDPOINT = "/purchases/tenders/notice/resultAnnouncement"
REQUEST_DELAY = 0.4  # 秒；469 条公告 / 376 唯一 key，约 3 分钟

# 详情地址区域判定：与 enrich_meta.addr_to_district 同源（完整区县解析）
# 优先级：完整区县词 → 盐南/经开高置信路名 → 世纪大道门牌号段
import re as _re


def parse_area_code_from_item_no(item_no: str) -> str:
    """从 itemNo 解析区县级 areaCode（2026-08-21）。

    苏服采 API 的 row/详情 row 都没有 areaCode 字段，但 itemNo 形如
    `JSZC-{6位编码}-...`，前 6 位就是区县级 areaCode（与公告接口 row.areaCode 一致）。

    返回 6 位编码或空字符串（格式不对时）。"""
    if not item_no:
        return ""
    m = _re.match(r'^JSZC-(\d{6})-', item_no)
    return m.group(1) if m else ""


def fetch_detail(session, project_key: str):
    """调详情接口；失败返回 None。"""
    url = f"{API_BASE}{DETAIL_ENDPOINT}/{project_key}"
    try:
        resp = session.get(
            url,
            headers={"Origin": "https://js.fwgov.cn", "Referer": "https://js.fwgov.cn/"},
            timeout=20,
        )
        resp.raise_for_status()
        data = resp.json()
        if not data.get("success"):
            return None
        res = data.get("result") or {}
        item_no = (res.get("itemNo") or "").strip()
        # 2026-08-21：详情接口 row 没有 areaCode 字段，但 itemNo 含 6 位区县级编码；
        # 从 itemNo 解析出 areaCode，让 SUFUCAI_AREA_CODE_MAP 快捷映射生效。
        area_code = parse_area_code_from_item_no(item_no)
        return {
            "address": (res.get("address") or "").strip(),
            "project_location": (res.get("projectLocation") or "").strip(),
            "supplier": (res.get("supplierName") or "").strip(),
            "area_code": area_code,
            "item_no": item_no,
        }
    except Exception as e:
        print(f"  [详情] project_key={project_key} 请求失败: {e}")
        return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true", help="强制重抓全部")
    parser.add_argument("--limit", type=int, default=0, help="只处理前 N 个 project_key（测试）")
    parser.add_argument("--dry-run", action="store_true", help="只统计待回填数量，不请求")
    args = parser.parse_args()

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    # 所有苏服采公告（按 project_key 分组，取最新一条的 raw_json）
    rows = conn.execute("""
        SELECT id, raw_json FROM notices
        WHERE site='苏服采'
        ORDER BY publish_date DESC
    """).fetchall()

    by_key = {}  # project_key -> (notice_id, raw)
    for r in rows:
        try:
            raw = json.loads(r["raw_json"] or "{}")
        except Exception:
            continue
        pk = str(raw.get("project_key") or raw.get("purchases_need_id") or "")
        if not pk:
            continue
        if pk not in by_key:
            by_key[pk] = (r["id"], raw)

    print(f"苏服采公告 {len(rows)} 条 / 唯一 project_key {len(by_key)} 个")

    # 过滤已回填
    if not args.force:
        todo = {}
        for pk, (nid, raw) in by_key.items():
            if not raw.get("detail_address"):
                todo[pk] = (nid, raw)
        print(f"待回填 {len(todo)} 个（已回填 {len(by_key) - len(todo)}）")
    else:
        todo = dict(by_key)
        print(f"强制重抓 {len(todo)} 个")

    if args.limit:
        todo = dict(list(todo.items())[: args.limit])
        print(f"限 {args.limit} 个测试")

    if args.dry_run:
        print("[dry-run] 结束")
        conn.close()
        return

    session = requests.Session()
    session.verify = False

    done = 0
    ok_supplier = 0
    ok_area_code = 0  # 2026-08-21 新增：拿到 areaCode 的计数
    for pk, (nid, raw) in todo.items():
        detail = fetch_detail(session, pk)
        if detail is None:
            continue
        raw["detail_address"] = detail["address"]
        raw["detail_project_location"] = detail["project_location"]
        if detail["supplier"]:
            raw["detail_supplier"] = detail["supplier"]
            ok_supplier += 1
        # 2026-08-21：把详情接口的 areaCode / itemNo 写进 raw_row 子对象，
        # 让 enrich_meta.classify_district 内的 SUFUCAI_AREA_CODE_MAP 快捷路径生效。
        # raw_row 跟 _row_to_item 新抓取时的字段命名一致，便于审计。
        if detail["area_code"] or detail["item_no"]:
            raw_row = raw.get("raw_row") or {}
            if detail["area_code"]:
                raw_row["areaCode"] = detail["area_code"]
                ok_area_code += 1
            if detail["item_no"]:
                raw_row["itemNo"] = detail["item_no"]
            raw["raw_row"] = raw_row
        # 2026-08-24：详情地址区域判定已移除——苏服采只认 API areaCode（classify_district 0 级），
        # target_region 字段整体废弃（前端盐开筛选只认 std_district）。
        conn.execute(
            "UPDATE notices SET raw_json=? WHERE id=?",
            (json.dumps(raw, ensure_ascii=False), nid),
        )
        done += 1
        if done % 50 == 0:
            conn.commit()
            print(f"  已回填 {done}/{len(todo)}…")
        time.sleep(REQUEST_DELAY)

    conn.commit()

    # 2026-08-20：同步 detail 字段到同 project_key 的其他公告（幂等，仅补齐缺失）
    # 同项目多条公告（招标+中标+合同）共享同一地址/供应商，避免重复请求。
    # 2026-08-21：扩展同步 raw_row.areaCode / itemNo（详情接口一次拿全所有项目共用字段）。
    synced = conn.execute("""
        UPDATE notices SET raw_json = json_set(
            raw_json, '$.detail_address',
            (SELECT json_extract(r2.raw_json, '$.detail_address')
             FROM notices r2 WHERE r2.site='苏服采'
               AND json_extract(r2.raw_json, '$.project_key') = json_extract(notices.raw_json, '$.project_key')
               AND json_extract(r2.raw_json, '$.detail_address') IS NOT NULL
               AND json_extract(r2.raw_json, '$.detail_address') != ''
             LIMIT 1),
            '$.detail_project_location',
            (SELECT json_extract(r2.raw_json, '$.detail_project_location')
             FROM notices r2 WHERE r2.site='苏服采'
               AND json_extract(r2.raw_json, '$.project_key') = json_extract(notices.raw_json, '$.project_key')
               AND json_extract(r2.raw_json, '$.detail_project_location') IS NOT NULL
               AND json_extract(r2.raw_json, '$.detail_project_location') != ''
             LIMIT 1),
            '$.detail_supplier',
            (SELECT json_extract(r2.raw_json, '$.detail_supplier')
             FROM notices r2 WHERE r2.site='苏服采'
               AND json_extract(r2.raw_json, '$.project_key') = json_extract(notices.raw_json, '$.project_key')
               AND json_extract(r2.raw_json, '$.detail_supplier') IS NOT NULL
               AND json_extract(r2.raw_json, '$.detail_supplier') != ''
             LIMIT 1),
            '$.raw_row',
            (SELECT json_extract(r2.raw_json, '$.raw_row')
             FROM notices r2 WHERE r2.site='苏服采'
               AND json_extract(r2.raw_json, '$.project_key') = json_extract(notices.raw_json, '$.project_key')
               AND json_extract(r2.raw_json, '$.raw_row') IS NOT NULL
             LIMIT 1)
        )
        WHERE site='苏服采'
          AND (json_extract(raw_json, '$.detail_address') IS NULL OR json_extract(raw_json, '$.detail_address') = '')
    """)
    print(f"  同步同项目公告: {synced.rowcount} 条")
    conn.commit()
    conn.close()
    print(f"\n完成：回填 {done} 个 project_key")
    print(f"  拿到供应商: {ok_supplier} | 拿到 areaCode: {ok_area_code}")

if __name__ == "__main__":
    main()
