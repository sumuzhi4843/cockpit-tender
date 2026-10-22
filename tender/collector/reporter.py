# -*- coding: utf-8 -*-
"""Markdown 日报生成"""
from datetime import datetime


def generate_report(results):
    """生成 Markdown 日报

    results: {site_name: {"items": [...], "ict_items": [...]}}
    """
    today = datetime.now().strftime("%Y年%m月%d日")

    lines = [f"# 招标日报 - {today}\n"]

    # 各平台汇总
    lines.append("## 一、五家平台公司招标公告\n")

    total_ict = 0
    total_all = 0

    for site_name, data in results.items():
        items = data.get("items", [])
        ict_items = data.get("ict_items", [])
        total_ict += len(ict_items)
        total_all += len(items)

        lines.append(
            f"### {site_name}（共 {len(items)} 条，ICT 相关 {len(ict_items)} 条）\n")

        if not items:
            lines.append("暂无公告\n")
            continue

        if ict_items:
            lines.append("| 项目名称 | 发布日期 | 发包人 | 金额 | 开标时间 | ICT 关键词 |")
            lines.append("|---|---|---|---|---|---|")
            for item in ict_items:
                title = item["title"]
                date = item.get("date", "—")
                tenderer = item.get("tenderer", "—")
                amount = item.get("amount", "—")
                deadline = item.get("bid_deadline", "—")
                keywords = ", ".join(item.get("ict_keywords", []))
                lines.append(f"| {title} | {date} | {tenderer} | {amount} | {deadline} | {keywords} |")
            lines.append("")

        # 非 ICT 项目
        non_ict = [i for i in items if i not in ict_items]
        if non_ict:
            lines.append(
                "<details><summary>非 ICT 项目（点击展开）</summary>\n")
            lines.append("| 项目名称 | 发布日期 | 发包人 | 金额 |")
            lines.append("|---|---|---|---|")
            for item in non_ict:
                title = item["title"]
                date = item.get("date", "—")
                tenderer = item.get("tenderer", "—")
                amount = item.get("amount", "—")
                lines.append(f"| {title} | {date} | {tenderer} | {amount} |")
            lines.append("\n</details>\n")

    # ICT 项目详情
    lines.append("## 二、ICT 相关项目详情\n")

    all_ict = []
    for site_name, data in results.items():
        for item in data.get("ict_items", []):
            item["site"] = site_name
            all_ict.append(item)

    if all_ict:
        for item in all_ict:
            lines.append(f"### {item['title']}\n")
            lines.append(f"- **来源**: {item['site']}")
            lines.append(f"- **发布日期**: {item.get('date', '—')}")
            lines.append(f"- **发包人**: {item.get('tenderer', '—')}")
            lines.append(f"- **项目金额**: {item.get('amount', '—')}")
            lines.append(f"- **开标时间**: {item.get('bid_deadline', '—')}")
            lines.append(
                f"- **ICT 关键词**: {', '.join(item.get('ict_keywords', []))}")
            lines.append(f"- **链接**: {item['url']}\n")
    else:
        lines.append("今日无新增 ICT 相关项目\n")

    # 统计
    lines.append("---")
    lines.append(
        f"\n**统计**: 共抓取 {total_all} 条公告，其中 ICT 相关 {total_ict} 条。")

    return "\n".join(lines)
