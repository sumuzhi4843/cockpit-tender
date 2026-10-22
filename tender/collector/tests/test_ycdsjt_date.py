#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ycdsjt 日期链路回归（2026-09-28 事故修复）

覆盖三层防线：
1) extract_detail_fields：详情页"发布时间"必须活过新引擎对 fields 的重建（原失效根因）；
2) YcDsjtScraper._infer_date/parse_list：只给"日"的列表按降序回推年月，深页/上月同号不误标；
3) store_db.upsert：同 site+url 防裂行 + 非详情页来源日期不顶库内已确认日期。
"""
import os
import sys
import tempfile
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scrapers import YcDsjtScraper
from store_db import TenderDB

SITE = "盐城市都市建设投资集团有限公司"
CONTENT = ("关于评估楼宇公司电脑的询价公告\n发布时间：2026-08-28\n"
           "招标人：盐城市都市酒店管理有限公司\n预算金额：12.5万元\n")


def _scraper():
    return YcDsjtScraper(SITE, "http://www.ycdsjt.cn/?zhaobiao/", "http://www.ycdsjt.cn")


def _list_html(pairs):
    """pairs: [(day, id)] → 都市集团列表 dl/dt.f18/dd.date>span 结构"""
    rows = []
    for day, i in pairs:
        rows.append(
            '<dl><dt class="f18"><a href="/?zhaobiao/%s.html">项目%s</a></dt>'
            '<dd class="date"><span>%s</span></dd></dl>' % (i, i, day))
    return "<html><body>" + "".join(rows) + "</body></html>"


def test_detail_date_kept():
    """发布时间日期不许被新引擎重建 fields 冲掉（2026-08-11 版即在此失效）"""
    s = _scraper()
    fields = s.extract_detail_fields(CONTENT, "关于评估楼宇公司电脑的询价公告")
    assert fields.get("date") == "2026-08-28", fields
    assert fields.get("date_source") == "detail", fields


def test_no_detail_date_no_override():
    """正文无"发布时间"时不许输出 date 键，列表页猜测值得以保留"""
    s = _scraper()
    fields = s.extract_detail_fields("随便一段没有日期的正文", "测试公告")
    assert "date" not in fields, fields
    assert "date_source" not in fields, fields


def test_descending_backfill():
    """9-28 抓取：首页 28/27/26 → 九月；续页出现 28（上月同号）→ 八月28，5→八月5，30→七月30"""
    s = _scraper()
    s._now_dt = datetime(2026, 9, 28, 12, 0, 0)
    s._list_prev_dt = None
    p1 = s.parse_list(_list_html([(28, 1), (27, 2), (26, 3)]))
    assert [it["date"] for it in p1] == ["2026-09-28", "2026-09-27", "2026-09-26"], p1
    p2 = s.parse_list(_list_html([(28, 4), (5, 5), (30, 6)]))
    assert [it["date"] for it in p2] == ["2026-08-28", "2026-08-05", "2026-07-30"], p2


def test_same_day_same_month():
    """同日多条（列表降序里日期相等合法）→ 不触发跨月回退"""
    s = _scraper()
    s._now_dt = datetime(2026, 9, 28, 12, 0, 0)
    s._list_prev_dt = None
    items = s.parse_list(_list_html([(28, 1), (28, 2), (28, 3)]))
    assert [it["date"] for it in items] == ["2026-09-28"] * 3, items


def test_month_end_clamp():
    """10-3 抓取遇到日号 31：10月31>锚→退9月，9月无31日钳到9月30"""
    s = _scraper()
    s._now_dt = datetime(2026, 10, 3, 12, 0, 0)
    s._list_prev_dt = None
    items = s.parse_list(_list_html([(31, 1), (30, 2), (29, 3)]))
    assert [it["date"] for it in items] == ["2026-09-30", "2026-09-30", "2026-09-29"], items


def test_store_url_guard():
    """同 URL 不许按漂移日期裂新行；猜测日期不许顶掉已确认日期；detail 来源可更正"""
    tmp = os.path.join(tempfile.mkdtemp(), "t_check.db")
    db = TenderDB(tmp)
    try:
        base = {"title": "关于评估楼宇公司电脑的询价公告",
                "url": "http://www.ycdsjt.cn/?zhaobiao/2632.html",
                "content": CONTENT}
        item = dict(base, date="2026-08-28", date_source="detail")
        assert db.upsert(SITE, item) is True, "首抓应新增"

        guess = dict(base, date="2026-09-28")  # 模拟详情抓取失败、列表页猜测日期漂移到今天
        assert db.upsert(SITE, guess) is False, "URL 命中应复用旧行而非新增"
        row = db.query(site=SITE)[0]
        assert db.count() == 1, db.count()
        assert row["publish_date"] == "2026-08-28", row

        fix = dict(base, date="2026-07-28", date_source="detail")  # 详情页真实日期可更正
        assert db.upsert(SITE, fix) is False, "URL 命中应复用旧行"
        assert db.count() == 1, db.count()
        assert db.query(site=SITE)[0]["publish_date"] == "2026-07-28", db.query(site=SITE)[0]
    finally:
        db.close()


def _run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"  PASS {fn.__name__}")
    print(f"全部 {len(fns)} 项通过")


if __name__ == "__main__":
    _run_all()
