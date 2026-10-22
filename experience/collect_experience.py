#!/usr/bin/env python3
"""项目经验数据采集 — qcc-tender 批量采集（2026-08-13）

策略：按运营商公司名做关键词（精准命中），regions 分组传地市，每次返回 20 条。
预算：2 积分/条，**日额度 200 积分**（老板 08-13 修正）→ 每天更新 80 条 = 160 积分，留冗余。

用法：
  python3 collect_experience.py           # 全量采集（默认 200 条，跑全量用）
  python3 collect_experience.py --limit 80  # 日常更新 80 条
"""
import json
import time
import subprocess
import argparse
import datetime
import os

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "data", "experience.json")

cfg = json.load(open(os.path.expanduser("~/.openclaw/openclaw.json")))
KEY = cfg["mcp"]["servers"]["qcc-tender"]["headers"]["Authorization"]
URL = "https://agent.qcc.com/mcp/tender/stream"

# 运营商 → 关键词（精准公司名）
OPERATORS = [
    ("中国移动", "中国移动通信集团江苏有限公司"),
    ("中国联通", "中国联合网络通信有限公司"),
    ("中国电信", "中国电信股份有限公司"),
    ("中国铁塔", "中国铁塔股份有限公司"),
]

# 江苏 12 地市（除盐城）
CITIES = ["南京", "苏州", "无锡", "常州", "南通", "扬州", "镇江",
          "泰州", "徐州", "淮安", "连云港", "宿迁"]
REGIONS = [f"江苏省{c}市" for c in CITIES]


def mcp_call(req_id, name, arguments):
    payload = {"jsonrpc": "2.0", "id": req_id, "method": "tools/call",
               "params": {"name": name, "arguments": arguments}}
    r = subprocess.run(
        ["curl", "-s", "-m", "90", URL,
         "-X", "POST", "-H", "Content-Type: application/json",
         "-H", "Authorization: " + KEY,
         "-H", "Accept: application/json, text/event-stream",
         "-d", json.dumps(payload, ensure_ascii=False)],
        capture_output=True, text=True, timeout=100)
    for line in r.stdout.splitlines():
        if line.startswith("data: "):
            try:
                d = json.loads(line[6:])
                if "error" in d:
                    print(f"  ⚠️ MCP error: {d['error'].get('message','')}")
                    return None
                for c in d.get("result", {}).get("content", []):
                    if c.get("type") == "text":
                        return json.loads(c["text"])
            except Exception:
                continue
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=200, help="目标条数")
    ap.add_argument("--begin", default="2026-05-01", help="开始日期")
    args = ap.parse_args()

    collected = {}   # bid_id → item
    req_id = 100
    begin = args.begin

    # 查询计划：4 运营商 × 3 组地市（每组 4 市），每组 20 条 → 最多 240 条，去重后取 200
    city_groups = [REGIONS[i:i+4] for i in range(0, len(REGIONS), 4)]

    print(f"== 开始采集（目标 {args.limit} 条，{begin} 起）==")
    print(f"计划：{len(OPERATORS)} 运营商 × {len(city_groups)} 地市组 = {len(OPERATORS)*len(city_groups)} 次查询")
    t0 = time.time()

    for op_name, op_kw in OPERATORS:
        for gi, group in enumerate(city_groups):
            if len(collected) >= args.limit:
                break
            print(f"\n[{op_name}] 地市组{gi+1}: {[g.replace('江苏省','').replace('市','') for g in group]}")
            res = mcp_call(req_id, "search_tenders", {
                "keywords": [op_kw],
                "regions": group,
                "infoTypes": ["中标公告"],
                "beginDate": begin,
                "smartSort": False,   # 按发布时间新→旧
            })
            req_id += 1
            time.sleep(0.5)   # 限速，避免触发限流

            if not res:
                print("  ⚠️ 无返回")
                continue
            total = res.get("查询摘要", {}).get("命中总数", 0)
            print(f"  命中 {total}，本次返回 {len(res.get('标讯列表', []))} 条")
            for it in res.get("标讯列表", []):
                bid_id = it.get("标讯ID")
                if not bid_id or bid_id in collected:
                    continue
                winner = " / ".join(u.get("企业名称", "") for u in it.get("中标单位", [])) or ""
                city_raw = (it.get("省市区", "") or "").replace("江苏省-", "")
                city = city_raw.split("-")[0] if city_raw else ""
                collected[bid_id] = {
                    "title": it.get("标题", ""),
                    "operator": op_name,
                    "city": city,
                    "winner": winner,
                    "amount": it.get("中标金额（元）", ""),
                    "date": (it.get("发布时间", "") or "")[:10],
                    "industry": " / ".join(it.get("标讯行业分类", []) or []),
                    "procurement_unit": " / ".join(u.get("企业名称", "") for u in it.get("招采单位", [])),
                }
            print(f"  累计去重后: {len(collected)} 条")
            if len(collected) >= args.limit:
                break

    items = list(collected.values())[:args.limit]
    items.sort(key=lambda x: x["date"], reverse=True)
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    exp = {
        "meta": {
            "generated_at": now,
            "scope": "江苏省（除盐城外）运营商中标信息",
            "total_records": len(items),
            "source": "qcc-tender (企查查 MCP)",
        },
        "items": items,
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(exp, f, ensure_ascii=False, indent=2)

    cost = len(items) * 2
    print(f"\n✅ 采集完成：{len(items)} 条（去重前 {len(collected)}），写入 {OUT}")
    print(f"   积分估算：{len(items)} × 2 = {cost}（预算 400）")
    print(f"   耗时 {time.time()-t0:.0f}s")
    from collections import Counter
    print("   地市分布:", dict(Counter(i["city"] for i in items).most_common()))
    print("   运营商分布:", dict(Counter(i["operator"] for i in items)))


if __name__ == "__main__":
    main()
