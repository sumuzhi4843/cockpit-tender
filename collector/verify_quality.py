# -*- coding: utf-8 -*-
"""数据质量校验（Phase 5）— 适配本地单库 tender.db

每次 run.py 后自动跑，检查富化字段覆盖率是否低于基线。
低于基线 = 有解析器/采集器回归，需排查。

设计：两级校验
  - 富化字段（金额/开标/区县/行业）只对【盐城本地 5 站】校验——那里富化能力强
  - 外部平台（API/列表性质）只查记录数，不查富化字段（天然低，属预期）
  - 江苏政府采购网历史数据（406 条）排除在外

用法:
  python3 verify_quality.py      # 返回 0=通过 1=有不达标
"""
import sqlite3
import sys
from pathlib import Path

DB_PATH = Path(__file__).parent / "data" / "tender.db"

# 盐城本地 5 站（富化能力强，作严格校验）
LOCAL_SITES = [
    "盐城市大数据集团", "江苏世纪新城", "盐城市东方集团",
    "盐城市都市建设投资集团有限公司", "经开城发集团",
]

# 排除江苏政府采购网历史数据（数据体量远高于其余站点，单独统计）
EXCLUDED_SITES = ["江苏政府采购网"]

# 富化字段基线（仅对 LOCAL_SITES 生效）。critical=低于即FAIL，warn=预警。
BASELINES = {
    "amount":        {"nt": "tender", "critical": 0.50, "warn": 0.45},
    "bid_deadline":  {"nt": "tender", "critical": 0.70, "warn": 0.65},
    "std_district":  {"nt": None, "critical": 0.90, "warn": 0.85},
    "proj_major_cat": {"nt": None, "critical": 0.30, "warn": 0.25},
}


def check(min_sufucai_run_at=None):
    if not DB_PATH.exists():
        print(f"❌ {DB_PATH} 不存在，请先运行 run.py 采集")
        return 1

    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row

    failures = []
    warnings = []

    # ── 0. 未来日期校验（2026-08-06）：publish_date 晚于今天 = 采集 bug，直接 FAIL ──
    from datetime import date
    today = date.today().isoformat()
    future_rows = conn.execute("""
        SELECT site, COUNT(*) n FROM notices
        WHERE publish_date > ? GROUP BY site ORDER BY n DESC
    """, (today,)).fetchall()
    if future_rows:
        detail = ", ".join(f"{r['site']} {r['n']}" for r in future_rows)
        failures.append(f"FAIL {sum(r['n'] for r in future_rows)} 条公告发布日期晚于今天（{today}）：{detail}")

    # ── 0.1 苏服采分页完整性账本：12 区域码 × 5 接口 = 60 分桶 ──
    from config import SITES
    sufucai_area_codes = [str(x) for x in SITES["苏服采"].get("area_codes", [])]
    expected_sufucai_buckets = {
        (endpoint, notice_type, area_code)
        for area_code in sufucai_area_codes
        for endpoint, notice_type in [
            ("/page", ""),
            ("/announcementResultList", "2"),
            ("/announcementResultList", "3"),
            ("/announcementResultList", "4"),
            ("/announcementResultList", "5"),
        ]
    }
    try:
        latest_collection_rows = conn.execute("""
            SELECT endpoint, notice_type, area_code, api_total, fetched_count, page_count,
                   page_size, max_pages, status, started_at
            FROM su_fucai_collection_runs
            WHERE started_at = (SELECT MAX(started_at) FROM su_fucai_collection_runs)
            ORDER BY endpoint, notice_type, area_code
        """).fetchall()
    except sqlite3.Error:
        latest_collection_rows = []

    latest_buckets = {(r["endpoint"], r["notice_type"], r["area_code"]) for r in latest_collection_rows}
    missing_buckets = expected_sufucai_buckets - latest_buckets
    if missing_buckets:
        detail = ", ".join(
            f"{endpoint}/type={notice_type}/area={area_code}"
            for endpoint, notice_type, area_code in sorted(missing_buckets)
        )
        failures.append(f"FAIL 苏服采分页账本缺少分桶：{detail}")

    if min_sufucai_run_at:
        stale_rows = [r for r in latest_collection_rows if r["started_at"] < min_sufucai_run_at]
        if stale_rows:
            detail = ", ".join(
                f"{r['endpoint']}/type={r['notice_type']}/area={r['area_code']} started={r['started_at']}"
                for r in stale_rows
            )
            failures.append(f"FAIL 苏服采未写入本轮分页账本：{detail}")

    collection_gaps = [
        r for r in latest_collection_rows
        if r["status"] != "ok"
        or r["api_total"] is None
        or r["fetched_count"] < r["api_total"]
        or (r["api_total"] > 0 and r["page_count"] >= r["max_pages"]
            and r["fetched_count"] < r["api_total"])
    ]
    if collection_gaps:
        detail = ", ".join(
            f"{r['endpoint']}/type={r['notice_type']}/area={r['area_code']} "
            f"total={r['api_total']} fetched={r['fetched_count']} status={r['status']}"
            for r in collection_gaps
        )
        failures.append(f"FAIL 苏服采分页抓取不完整（最新一次）：{detail}")

    valid_area_codes = set(sufucai_area_codes)
    bad_area_rows = conn.execute("""
        SELECT id, title,
               COALESCE(json_extract(raw_json, '$.area_code'),
                        json_extract(raw_json, '$.raw_row.areaCode'), '') AS area_code
        FROM notices
        WHERE site='苏服采'
          AND COALESCE(json_extract(raw_json, '$.area_code'),
                       json_extract(raw_json, '$.raw_row.areaCode'), '') NOT IN ({})
    """.format(",".join("?" * len(valid_area_codes))), tuple(sorted(valid_area_codes))).fetchall()
    if bad_area_rows:
        failures.append(
            f"FAIL 苏服采存在 {len(bad_area_rows)} 条缺失/无效 areaCode，"
            f"示例：{bad_area_rows[0]['title']}({bad_area_rows[0]['area_code'] or 'missing'})"
        )

    # 2026-08-24：target_region 已废弃（前端只认 std_district），改用 std_district 统计
    try:
        target_stats = conn.execute("""
            SELECT std_district, COUNT(*) n
            FROM notices WHERE site='苏服采' GROUP BY std_district ORDER BY n DESC
        """).fetchall()
    except sqlite3.Error:
        target_stats = []
    if target_stats:
        target_detail = ", ".join(f"{r['std_district']}={r['n']}" for r in target_stats)
        warnings.append(f"WARN 苏服采区县分布：{target_detail}")

    # ── 1. 富化字段：只对本地 5 站 ──
    local_ph = ",".join("?" * len(LOCAL_SITES))
    for col, cfg in BASELINES.items():
        nt = cfg["nt"]
        if nt:
            denom = conn.execute(
                f"SELECT COUNT(*) FROM notices WHERE site IN ({local_ph}) AND notice_type=?",
                LOCAL_SITES + [nt]).fetchone()[0]
            numer = conn.execute(
                f"SELECT COUNT(*) FROM notices WHERE site IN ({local_ph}) "
                f"AND notice_type=? AND {col} IS NOT NULL AND {col}!=''",
                LOCAL_SITES + [nt]).fetchone()[0]
        else:
            denom = conn.execute(
                f"SELECT COUNT(*) FROM notices WHERE site IN ({local_ph})",
                LOCAL_SITES).fetchone()[0]
            numer = conn.execute(
                f"SELECT COUNT(*) FROM notices WHERE site IN ({local_ph}) "
                f"AND {col} IS NOT NULL AND {col}!=''",
                LOCAL_SITES).fetchone()[0]

        ratio = numer / denom if denom else 0
        critical = cfg["critical"]
        warn = cfg.get("warn", max(0, critical - 0.05))
        label = f"{col}[{nt}]" if nt else col

        if ratio < critical:
            failures.append(
                f"FAIL {label}: {ratio:.1%} < critical {critical:.0%} ({numer}/{denom})")
        elif ratio < warn:
            warnings.append(
                f"WARN {label}: {ratio:.1%} < warn {warn:.0%} ({numer}/{denom})")

    # ── 2. 站点记录数：所有站点（排除江苏政府采购网）─
    excl_ph = ",".join("?" * len(EXCLUDED_SITES))
    site_counts = conn.execute(
        f"SELECT site, COUNT(*) n FROM notices WHERE site NOT IN ({excl_ph}) GROUP BY site",
        EXCLUDED_SITES).fetchall()
    total = sum(r["n"] for r in site_counts)
    min_site = min((r["n"] for r in site_counts), default=0)
    # 站点数
    n_sites = conn.execute(
        f"SELECT COUNT(DISTINCT site) FROM notices WHERE site NOT IN ({excl_ph})",
        EXCLUDED_SITES).fetchone()[0]
    if n_sites < 19 or n_sites > 22:
        failures.append(f"FAIL 站点数 {n_sites} 异常（预期 19-22）")

    # 类型分布
    types = {r["notice_type"]: r["n"] for r in conn.execute(
        "SELECT notice_type, COUNT(*) n FROM notices GROUP BY notice_type")}

    # ── 3. 公告分类与项目生命周期（Schema v2）──
    subtype_unknown = conn.execute(
        "SELECT COUNT(*) FROM notices WHERE notice_subtype IS NULL OR notice_subtype=''"
    ).fetchone()[0]
    if subtype_unknown:
        failures.append(f"FAIL {subtype_unknown} 条公告缺少 notice_subtype")

    # 江苏招标采购服务平台的中标详情为 PDF；苏服采公开结果 API 不返回成交供应商。
    # 江苏政府采购网包含大量未做 winner 回填的历史公告，且已在全局质量统计中排除。
    # 三者不参与阻断发布的 winner 覆盖率，分别通过站点专项检查/预警跟踪。
    winner_excluded_sites = [*EXCLUDED_SITES, "江苏招标采购服务平台", "苏服采"]
    winner_excl_ph = ",".join("?" * len(winner_excluded_sites))
    final_total, final_winner = conn.execute(f"""
        SELECT COUNT(*), SUM(CASE WHEN winner IS NOT NULL AND winner!='' THEN 1 ELSE 0 END)
        FROM notices WHERE notice_subtype='award_final'
          AND site NOT IN ({winner_excl_ph})
    """, winner_excluded_sites).fetchone()
    if final_total and (final_winner or 0) / final_total < 0.80:
        failures.append(
            f"FAIL 最终中标公告中标单位覆盖率 {(final_winner or 0)/final_total:.1%} < 80% "
            f"({final_winner or 0}/{final_total})")

    pdf_awards = conn.execute("""
        SELECT COUNT(*) FROM notices
        WHERE notice_subtype='award_final' AND site='江苏招标采购服务平台'
          AND (winner IS NULL OR winner='')
    """).fetchone()[0]
    missing_pdf = conn.execute("""
        SELECT COUNT(*) FROM notices
        WHERE notice_subtype='award_final' AND site='江苏招标采购服务平台'
          AND (winner IS NULL OR winner='')
          AND raw_json NOT LIKE '%"signed_pdf_url"%'
    """).fetchone()[0]
    complex_pdf = pdf_awards - missing_pdf
    if missing_pdf:
        warnings.append(
            f"WARN 江苏招标采购服务平台 {missing_pdf} 条中标公告未提供 PDF，无法提取中标单位")
    if complex_pdf:
        warnings.append(
            f"WARN 江苏招标采购服务平台 {complex_pdf} 条 PDF 公告含多采购包，未写入单一中标单位")

    sufucai_awards = conn.execute("""
        SELECT COUNT(*) FROM notices
        WHERE notice_subtype='award_final' AND site='苏服采'
          AND (winner IS NULL OR winner='')
    """).fetchone()[0]
    if sufucai_awards:
        warnings.append(
            f"WARN 苏服采 {sufucai_awards} 条成交公告的公开 API 未返回成交供应商")

    contract_misclassified = conn.execute("""
        SELECT COUNT(*) FROM notices
        WHERE notice_subtype IN ('award_final','award_candidate')
          AND title LIKE '%合同%'
          AND title NOT LIKE '%中标%' AND title NOT LIKE '%成交%' AND title NOT LIKE '%中选%'
    """).fetchone()[0]
    if contract_misclassified:
        failures.append(f"FAIL {contract_misclassified} 条普通合同标题被误判为中标")

    has_projects = conn.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='projects'"
    ).fetchone()[0]
    if has_projects:
        project_count = conn.execute("SELECT COUNT(*) FROM projects").fetchone()[0]
        if project_count == 0:
            failures.append("FAIL projects 项目表为空")
        impossible = conn.execute("""
            SELECT COUNT(*) FROM projects
            WHERE project_stage='awarded' AND (winner IS NULL OR winner='')
        """).fetchone()[0]
        if impossible:
            warnings.append(f"WARN {impossible} 个已中标项目缺少中标单位")

    has_links = conn.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='project_links'"
    ).fetchone()[0]
    if has_links:
        low_auto = conn.execute("""
            SELECT COUNT(*) FROM project_links
            WHERE tender_id IS NOT NULL AND match_confidence='low'
        """).fetchone()[0]
        if low_auto:
            warnings.append(f"WARN {low_auto} 条低可信关联，应人工复核且不用于确定性折扣率")
    conn.close()

    print("=" * 60)
    print(f"数据质量校验 | 有效记录 {total} 条（{n_sites} 站，排除江苏政府采购网）")
    print(f"类型分布: {types}")
    print("=" * 60)

    if warnings:
        print(f"\n⚠️  {len(warnings)} 项预警（低于 warn 但不 critical）：")
        for w in warnings:
            print(f"  {w}")

    if failures:
        print(f"\n❌ {len(failures)} 项不达标：")
        for f in failures:
            print(f"  {f}")
        print()
        return 1

    print("\n✅ 全部通过（本地 5 站富化字段 + 站点数均高于基线）")
    return 0


if __name__ == "__main__":
    sys.exit(check())
