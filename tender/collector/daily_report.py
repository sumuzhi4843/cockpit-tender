#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""daily_report.py — 盐开区招投标日报生成器（HTML + PDF）

口径：publish_date ∈ [昨天, 今天]（防遗漏重叠一天）；范围 std_district ∈ (盐南,经开)=盐开区。
样式单源：collector/daily_report.css —— 老板改版式只改这个文件（打印规则在 @media print 段）。
       （2026-09-12 预览页 preview.html 已下线，样式已迁入本 css）
产出（2026-09-26 调整 v3）：site/daily/latest.html + latest.pdf + {YYYY-MM-DD}.html（不再产 {date}.pdf）
       —— 双写：latest.* 常态覆盖（站内默认入口），{date}.* 按日归档（PROTOCOL-v0.3 永久链接，
       QR 码 + 推送都用它，避免 latest.html 次日被覆盖导致 yk 校验 404）。
       —— PDF 只留 latest.pdf 一份（老板 14:03 拍板：省 PDF 归档，只写 HTML 归档；
       PDF 在 HTML 页内有「下载为 PDF」按钮现生成，不需归档）。
       —— 当日 deploy（8/12/18）多次跑时，{date}.html 始终覆盖为当日最新一版；跨日时 {date}.html 不动。
       --no-pdf 跳过 PDF 生成（仅 HTML）；--no-deploy 跳过 docker cp。
三页 A4 横版（2026-09-23 调整）：P1=ICT 命中明细 + 盐开区细分条，P2=新增招标（含摘要卡 stats），
       P3=中标公示 + 金额 Top5。**无节选上限**（2026-09-23 老板拍板）：招标 / 中标 / ICT 全量展示，
       数据多时自动追加下一页，不再截断。
用法：python3 daily_report.py [--date YYYY-MM-DD] [--no-pdf] [--no-deploy]
退出码：0=成功；末尾输出 JSON 摘要（自动化消费）。
 — yktender
"""
import argparse, html, json, re, sqlite3, subprocess, sys
from datetime import datetime, timedelta, date as ddate
from pathlib import Path

HERE = Path(__file__).resolve().parent
DB_PATH = HERE / "data" / "tender.db"
SITE_DAILY = HERE.parents[1] / "site" / "daily"
STYLE_CSS = HERE / "daily_report.css"
QR_LIB = "qrcode.min.js"
DISTRICTS = ("盐南", "经开")
DISTRICT_LABEL = {"盐南": "盐南高新区", "经开": "经开区"}
WEEK_CN = "一二三四五六日"  # 与 date.weekday() 对齐：0=一 … 5=六 6=日（2026-09-12 修 off-by-one）

# 来源简写表：日报来源列窄，长名换行溢格（2026-09-12 老板拍板用简写）；未收录名走 short_site() 通用压缩
SITE_SHORT = {
    "盐城市公共资源交易网": "盐城交易网",
    "城南新区公共资源交易网": "城南交易网",
    "开发区公共资源交易网": "经开交易网",
    "盐城市都市建设投资集团有限公司": "都市建投集团",
    "中国政府采购网": "中国政采网",
    "盐城市政府采购网": "盐城政采网",
    "江苏政府采购网": "江苏政采网",
    "江苏招标采购服务平台": "江苏招采平台",
    "盐城市人民政府网": "盐城市政府网",
    "盐城市大数据集团": "盐城大数据集团",
    "盐城市东方集团": "盐城东方集团",
    "江苏世纪新城": "世纪新城",
    "经开城发集团": "经开城发",
}

# 强词集合（样式区分红/蓝用；判定口径以库内 ict_keywords 为准）
try:
    sys.path.insert(0, str(HERE))
    from config import ICT_STRONG_KEYWORDS as STRONG_KW
except Exception:
    STRONG_KW = set()
STRONG_KW = set(STRONG_KW)


def esc(s):
    return html.escape(str(s or "").strip(), quote=True)


def short_site(site):
    """来源列简写：先查表；未命中做通用压缩（去"市/有限公司"后缀、交易网/政采网缩略），压到 8 字内。"""
    s = str(site or "").strip()
    if s in SITE_SHORT:
        return SITE_SHORT[s]
    s = s.replace("公共资源交易网", "交易网").replace("政府采购网", "政采网")
    s = re.sub(r"(股份有限公司|集团有限公司|有限责任公司)$", "", s)
    s = re.sub(r"^盐城市", "盐城", s)
    return s if len(s) <= 8 else s[:7] + "…"


def safe_url(u):
    u = (u or "").strip()
    return u if u.startswith("http://") or u.startswith("https://") else ""


def fmt_amount(v):
    try:
        v = float(v)
    except Exception:
        return "—"
    if v >= 1e8:
        return f"{v/1e8:.2f} 亿"
    if v >= 1e4:
        return f"{v/1e4:.2f} 万"
    return f"{v:.0f} 元"


def kw_chips(kws, cap=4):
    if not kws:
        return ""
    ordered = sorted(kws, key=lambda k: (k not in STRONG_KW,))
    shown = ordered[:cap]
    more = len(ordered) - len(shown)
    chips = "".join(
        f'<span class="ict-chip{" strong" if k in STRONG_KW else ""}">{esc(k)}</span>' for k in shown)
    if more > 0:
        chips += f'<span class="ict-chip more" title="其余命中：{esc("、".join(ordered[cap:]))}">+{more}</span>'
    return f'<span class="ict-hit">{chips}</span>'


def load_style():
    """样式单源：daily_report.css（老板改版式直接改这个文件）。"""
    css = STYLE_CSS.read_text(encoding="utf-8")
    if len(css.strip()) < 500:
        raise RuntimeError("daily_report.css 缺失或过短（样式单源异常）")
    return css


def q(conn, sql, args=()):
    cur = conn.execute(sql, args)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def dedupe(rows):
    seen, out = set(), []
    for r in rows:
        t = re.sub(r"\s+", "", r["title"] or "")
        if t and t not in seen:
            seen.add(t)
            out.append(r)
    return out


def collect(day):
    """day=date对象(报告日)；窗口=昨天~今天。返回渲染所需全部数据。"""
    yest = day - timedelta(days=1)
    lo, hi = yest.isoformat(), day.isoformat()
    con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    base = "publish_date>=? AND publish_date<=? AND std_district IN ('盐南','经开')"

    tenders = q(con, f"""SELECT publish_date, std_district d, site, title, url,
        COALESCE(json_extract(raw_json,'$.actual_detail_url'), url) AS eff_url,
        amount, amount_value av, notice_type nt, COALESCE(ict_keywords,'[]') kw
        FROM notices WHERE {base} AND notice_type IN ('tender','intention')
        ORDER BY publish_date DESC, id DESC""", (lo, hi))
    awards = q(con, f"""SELECT publish_date, std_district d, site, title, winner, winning_amount,
        COALESCE(ict_keywords,'[]') kw
        FROM notices WHERE {base} AND notice_type='award'
        ORDER BY publish_date DESC, id DESC""", (lo, hi))
    ict_rows = dedupe(q(con, f"""SELECT publish_date, std_district d, site, title,
        COALESCE(ict_keywords,'[]') kw FROM notices
        WHERE {base} AND COALESCE(ict_keywords,'') NOT IN ('','[]')
        ORDER BY publish_date DESC, id DESC""", (lo, hi)))
    # 2026-09-28：更正/澄清公告单独成段。之前 notice_type IN ('tender','intention') 与 award 把更正版全部过滤掉，
    # 导致项目阶段不变但 latest_notice_date 被更正公告顶到最新，前端显示为「招标 9/20」误导为又推了一遍。
    # 现在仅按 notice_subtype='correction' 取，包含 9-20 补发的更正版（原招标仍有效）。
    corrections = dedupe(q(con, f"""SELECT publish_date, std_district d, site, title, url,
        COALESCE(json_extract(raw_json,'$.actual_detail_url'), url) AS eff_url,
        notice_subtype nt
        FROM notices WHERE {base} AND notice_subtype='correction'
        ORDER BY publish_date DESC, id DESC""", (lo, hi)))
    total_projects = q(con, "SELECT COUNT(*) n FROM projects WHERE district IN ('盐南','经开')")[0]["n"]
    con.close()

    tenders, awards = dedupe(tenders), dedupe(awards)
    dist_cnt = {d: sum(1 for t in tenders if t["d"] == d) for d in DISTRICTS}
    top5 = sorted([t for t in tenders if (t["av"] or 0) > 0], key=lambda x: -x["av"])[:5]
    sites = {t["site"] for t in tenders} | {a["site"] for a in awards}
    sites |= {c["site"] for c in corrections}
    return dict(day=day, lo=lo, hi=hi, tenders=tenders, awards=awards, ict=ict_rows,
                corrections=corrections,
                total_projects=total_projects, dist_cnt=dist_cnt, top5=top5, n_sites=len(sites))


def tender_rows(items):
    """2026-09-23：老板取消节选上限，全部展示；多了追加下一页。"""
    if not items:
        return '<tr><td colspan="5" style="text-align:center;color:var(--color-text-muted);padding:12px">本期无新增招标公告</td></tr>'
    out = []
    for t in items:
        kws = json.loads(t["kw"] or "[]")
        pill = "intention" if t["nt"] == "intention" else "tender"
        pill_txt = "意向" if t["nt"] == "intention" else "招标"
        u = safe_url(t["eff_url"] or t["url"])
        title_html = f'<a href="{esc(u)}" target="_blank" rel="noopener">{esc(t["title"])}</a>' if u else esc(t["title"])
        out.append(
            f'<tr><td class="col-time">{esc(t["publish_date"][5:])}</td>'
            f'<td class="col-district">{esc(DISTRICT_LABEL.get(t["d"], t["d"]))}</td>'
            f'<td class="col-src">{esc(short_site(t["site"]))}</td>'
            f'<td class="col-title"><span class="tag-pill {pill}">{pill_txt}</span>{kw_chips(kws)}{title_html}</td>'
            f'<td class="amount col-num">{esc(t["amount"] or "—")}</td></tr>')
    return "".join(out)


def award_rows(items):
    """2026-09-23：老板取消节选上限，全部展示；多了追加下一页。"""
    if not items:
        return '<tr><td colspan="6" style="text-align:center;color:var(--color-text-muted);padding:12px">本期无中标结果公告</td></tr>'
    out = []
    for a in items:
        kws = json.loads(a["kw"] or "[]")
        amt = a["winning_amount"] or "—"
        try:
            amt = fmt_amount(float(str(amt).replace(",", "")))
        except Exception:
            pass
        out.append(
            f'<tr><td class="col-time">{esc(a["publish_date"][5:])}</td>'
            f'<td class="col-district">{esc(DISTRICT_LABEL.get(a["d"], a["d"]))}</td>'
            f'<td class="col-src">{esc(short_site(a["site"]))}</td>'
            f'<td class="col-title">{kw_chips(kws)}{esc(a["title"])}</td>'
            f'<td class="col-title">{esc(a["winner"] or "—")}</td>'
            f'<td class="amount col-num">{esc(amt)}</td></tr>')
    return "".join(out)


def top5_items(items):
    if not items:
        return '<li style="grid-template-columns:auto"><span style="grid-column:2/-1;color:var(--color-text-muted)">本期无预算金额可排序</span></li>'
    return "".join(
        f'<li><span>{esc(DISTRICT_LABEL.get(t["d"], t["d"]))} <b>{esc(t["title"][:42])}</b> · {esc(short_site(t["site"]))}</span>'
        f'<span class="top-amt">{fmt_amount(t["av"])}元</span></li>'.replace("元元", "元")
        for t in items)


def dist_rows(cnt):
    mx = max(list(cnt.values()) + [1])
    out = []
    for d in DISTRICTS:
        n = cnt.get(d, 0)
        w = int(n * 100 / mx) if n else 2
        out.append(f'<div class="dist-row"><div class="dist-name">{DISTRICT_LABEL[d]}</div>'
                   f'<div class="dist-bar-wrap"><div class="dist-bar" style="width:{w}%"></div></div>'
                   f'<div class="dist-cnt">{n}</div></div>')
    return "".join(out)


def ict_rows(items):
    """2026-09-23：老板取消节选上限，全部展示；多了追加下一页。"""
    if not items:
        return '<tr><td colspan="4" style="text-align:center;color:var(--color-text-muted);padding:12px">本期无 ICT 命中项目</td></tr>'
    out = []
    for t in items:
        kws = json.loads(t["kw"] or "[]")
        out.append(f'<tr><td class="col-time">{esc(t["publish_date"][5:])}</td>'
                   f'<td class="col-district">{esc(DISTRICT_LABEL.get(t["d"], t["d"]))}</td>'
                   f'<td class="col-src">{esc(short_site(t["site"]))}</td>'
                   f'<td class="col-title">{kw_chips(kws)}<b>{esc(t["title"])}</b></td></tr>')
    return "".join(out)


def correction_rows(items):
    """2026-09-28：更正/澄清公告渲染。原招标仍有效，此段只是补充告知。
    复用 'other' CSS（色块），不取金额。"""
    if not items:
        return '<tr><td colspan="4" style="text-align:center;color:var(--color-text-muted);padding:12px">本期无更正/澄清公告</td></tr>'
    out = []
    for t in items:
        u = safe_url(t["eff_url"] or t["url"])
        title_html = f'<a href="{esc(u)}" target="_blank" rel="noopener">{esc(t["title"])}</a>' if u else esc(t["title"])
        out.append(
            f'<tr><td class="col-time">{esc(t["publish_date"][5:])}</td>'
            f'<td class="col-district">{esc(DISTRICT_LABEL.get(t["d"], t["d"]))}</td>'
            f'<td class="col-src">{esc(short_site(t["site"]))}</td>'
            f'<td class="col-title"><span class="tag-pill other">更正/澄清</span>{title_html}'
            f'<span style="color:var(--color-text-muted);font-size:11px;margin-left:6px">（原招标仍有效）</span></td></tr>')
    return "".join(out)


def render(data, qr_url):
    day = data["day"]
    date_cn = f"{day.year} 年 {day.month} 月 {day.day} 日（周{WEEK_CN[day.weekday()]}）"
    rid = f"DAILY-{day:%Y%m%d}-YK"
    window = f"{data['lo']} 00:00 – {data['hi']} 23:59"
    n_t, n_a, n_i = len(data["tenders"]), len(data["awards"]), len(data["ict"])
    n_c = len(data.get("corrections") or [])
    sec_tender = f"盐开区 · 共 {n_t} 条 · 全部展示"
    sec_award = f"盐开区 · 共 {n_a} 条 · 全部展示"
    sec_ict = f"盐开区 · 共 {n_i} 项 · 命中词可见 · 全部展示"
    sec_correction = f"盐开区 · 共 {n_c} 条 · 全部展示"
    style = load_style()
    gen_time = datetime.now().strftime("%H:%M:%S")

    page = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>招投标信息日报 · 盐开区 · {day:%m-%d}</title>
<style>{style}</style>
<script>window.QR_URL = {json.dumps(qr_url)};</script>
</head><body>
<div class="daily-toolbar">
  <span class="note">⇣ 一键下载 PDF</span>
  <button type="button" onclick="window.print()" title="调用浏览器打印对话框，选「另存为 PDF」">下载为 PDF</button>
  <span class="note">盐开区</span>
</div>
<div class="daily-qrcode" aria-label="扫码查看实时日报">
  <div id="qrcode-target"></div>
  <div class="daily-qrcode__caption">扫码查看实时日报</div>
</div>

<section class="daily-page">
  <header class="daily-head">
    <div><h1>招投标信息日报 · 盐开区（ICT 页）</h1>
      <div class="daily-date">{date_cn}<span class="sample-tag" style="background:#e8f5e9;color:var(--color-success);border-color:var(--color-success)">真实数据</span></div></div>
    <div class="daily-meta">编号：<strong>{rid}-01</strong><br>
      范围：<strong>盐开区（盐南 + 经开）</strong><br>窗口：<strong>{window}</strong><br>生成：<strong>{gen_time}</strong> · yktender 维护</div>
  </header>
  <div class="daily-section"><h2>本期新增 · 盐开区细分 <span class="sec-count">按招标公告区县口径</span></h2>
    <div class="dist-bars">{dist_rows(data['dist_cnt'])}</div>
    <p style="font-size:11px;color:var(--color-text-muted);margin-top:8px">口径：公告 std_district ∈ {{盐南, 经开}}；与站内筛选「盐开区」一致。</p></div>
  <div class="daily-section"><h2>本期新增 · ICT 命中明细 <span class="sec-count">{sec_ict}</span></h2>
    <table class="daily-table"><thead><tr>
      <th class="col-time">日期</th><th class="col-district">区县</th><th class="col-src">来源</th><th class="col-title">项目名 + 命中词</th>
    </tr></thead><tbody>{ict_rows(data['ict'])}</tbody></table>
    <p style="font-size:11px;color:var(--color-text-muted);margin-top:8px">命中词按现行 analyzer.py 规则（强词红 / 弱词蓝），与站内「悬停 ●ICT」同口径。</p></div>
  <footer class="daily-foot">
    <div class="foot-left"><strong>口径说明</strong>：窗口=昨日+今日两日发布（防遗漏）；命中词取自库内 ict_keywords 字段。</div>
    <div class="foot-right">页 1 · yktender</div>
  </footer>
</section>

<section class="daily-page">
  <header class="daily-head">
    <div><h1>招投标信息日报 · 盐开区</h1>
      <div class="daily-date">{date_cn}<span class="sample-tag" style="background:#e8f5e9;color:var(--color-success);border-color:var(--color-success)">真实数据 · {rid}</span></div></div>
    <div class="daily-meta">编号：<strong>{rid}-02</strong><br>
      范围：<strong>盐开区（盐南 + 经开）</strong><br>窗口：<strong>{window}</strong><br>生成：<strong>{gen_time}</strong> · yktender 维护</div>
  </header>
  <div class="daily-stats">
    <div class="stat-card stat-total"><div class="stat-label">累计收录 · 盐开区</div><div class="stat-value">{data['total_projects']:,}</div></div>
    <div class="stat-card"><div class="stat-label">本期新增 · 招标+意向</div><div class="stat-value">{n_t}</div></div>
    <div class="stat-card stat-ict"><div class="stat-label">本期新增 · ICT 命中</div><div class="stat-value">{n_i}</div></div>
    <div class="stat-card stat-award"><div class="stat-label">本期新增 · 中标结果</div><div class="stat-value">{n_a}</div></div>
    <div class="stat-card stat-correction"><div class="stat-label">本期新增 · 更正/澄清</div><div class="stat-value">{n_c}</div></div>
  </div>
  <div class="daily-section"><h2>新增招标公告 <span class="sec-count">{sec_tender}</span></h2>
    <table class="daily-table"><thead><tr>
      <th class="col-time">日期</th><th class="col-district">区县</th><th class="col-src">来源</th><th class="col-title">项目名称</th><th class="col-num">金额</th>
    </tr></thead><tbody>{tender_rows(data['tenders'])}</tbody></table></div>
  <div class="daily-section"><h2>更正/澄清公告 <span class="sec-count">{sec_correction}</span></h2>
    <table class="daily-table"><thead><tr>
      <th class="col-time">日期</th><th class="col-district">区县</th><th class="col-src">来源</th><th class="col-title">公告名称</th>
    </tr></thead><tbody>{correction_rows(data.get('corrections') or [])}</tbody></table>
    <p style="font-size:11px;color:var(--color-text-muted);margin-top:8px">口径：notice_subtype='correction'（分类置信 0.98）；原招标仍有效，仅补充告知。</p></div>
  <footer class="daily-foot">
    <div class="foot-left"><strong>范围</strong>：盐开区（盐南高新区 + 经开区）· <strong>来源</strong>：本期覆盖 {data['n_sites']} 个站源</div>
    <div class="foot-right">页 2 · yktender</div>
  </footer>
</section>

<section class="daily-page">
  <header class="daily-head">
    <div><h1>招投标信息日报 · 盐开区（中标页）</h1>
      <div class="daily-date">{date_cn}<span class="sample-tag" style="background:#e8f5e9;color:var(--color-success);border-color:var(--color-success)">真实数据</span></div></div>
    <div class="daily-meta">编号：<strong>{rid}-03</strong><br>窗口：<strong>{window}</strong></div>
  </header>
  <div class="daily-section"><h2>中标结果公示 <span class="sec-count">{sec_award}</span></h2>
    <table class="daily-table"><thead><tr>
      <th class="col-time">日期</th><th class="col-district">区县</th><th class="col-src">来源</th><th class="col-title">中标项目</th><th class="col-title">中标单位</th><th class="col-num">中标金额</th>
    </tr></thead><tbody>{award_rows(data['awards'])}</tbody></table></div>
  <div class="daily-section"><h2>本期新增 · 金额 Top 5 <span class="sec-count">盐开区 · 按预算金额</span></h2>
    <ol class="top-list">{top5_items(data['top5'])}</ol></div>
  <footer class="daily-foot">
    <div class="foot-left"><strong>金额口径</strong>：中标金额取公告原文；预算类金额未填则显“—”</div>
    <div class="foot-right">页 3 / 3 · yktender · <a href="/daily/latest.html" style="color:inherit">5310 招投标信息栏</a></div>
  </footer>
</section>

<script src="{QR_LIB}"></script>
<script>
(function () {{
  function renderQR() {{
    var el = document.getElementById('qrcode-target');
    if (!el || typeof QRCode === 'undefined') return;
    el.innerHTML = '';
    new QRCode(el, {{ text: window.QR_URL || window.location.href,
      width: 110, height: 110, colorDark: '#1f2937', colorLight: '#ffffff' }});
  }}
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', renderQR);
  else renderQR();
  window.addEventListener('beforeprint', renderQR);
}})();
</script>
</body></html>"""
    return page


def make_pdf(html_path, pdf_path):
    """snap 版 chromium 读不了 file://（ERR_ACCESS_DENIED，classic confinement），
    改走本地临时 HTTP 服务（与 Playwright 验页同机制，已证实可用）。"""
    import threading
    from functools import partial
    from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
    from playwright.sync_api import sync_playwright

    class Quiet(SimpleHTTPRequestHandler):
        def log_message(self, *a):
            pass
    handler = partial(Quiet, directory=str(html_path.parent))
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        with sync_playwright() as p:
            b = p.chromium.launch(executable_path="/snap/bin/chromium", args=["--no-sandbox"])
            pg = b.new_page(viewport={"width": 1280, "height": 900})
            pg.goto(f"http://127.0.0.1:{port}/{html_path.name}", wait_until="domcontentloaded")
            pg.wait_for_timeout(1500)  # 等 QR canvas 绘制
            pg.pdf(path=str(pdf_path), prefer_css_page_size=True, print_background=True)
            b.close()
    finally:
        srv.shutdown()


def deploy(paths):
    for f in paths:
        subprocess.run(["docker", "cp", str(f), f"cockpit:/usr/share/nginx/html/daily/{f.name}"],
                       check=True, capture_output=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="报告日 YYYY-MM-DD，默认今天")
    ap.add_argument("--no-pdf", action="store_true")
    ap.add_argument("--no-deploy", action="store_true")
    ap.add_argument("--host", default="http://<SERVER_IP>:5310", help="二维码/绝对链接用的站点地址")
    a = ap.parse_args()
    day = datetime.strptime(a.date, "%Y-%m-%d").date() if a.date else datetime.now().date()

    SITE_DAILY.mkdir(parents=True, exist_ok=True)
    data = collect(day)
    # 2026-09-26：PROTOCOL-v0.3 要求永久链接 /daily/{YYYY-MM-DD}.html，QR 也指向归档版避免次日被覆盖
    date_slug = day.isoformat()
    qr_url = f"{a.host}/daily/{date_slug}.html"
    page_html = render(data, qr_url)

    # 双写：latest.*（站内默认入口，常态覆盖）+ {date}.*（PROTOCOL-v0.3 永久归档）
    latest_html = SITE_DAILY / "latest.html"
    archived_html = SITE_DAILY / f"{date_slug}.html"
    latest_html.write_text(page_html, encoding="utf-8")
    archived_html.write_text(page_html, encoding="utf-8")

    latest_pdf = SITE_DAILY / "latest.pdf"
    if not a.no_pdf:
        # 老板 14:03 拍板：PDF 只写 latest.pdf 一份，不再双写 {date}.pdf（HTML 页内可一键下载）
        make_pdf(archived_html, latest_pdf)

    deployed = [latest_html, archived_html]
    if not a.no_pdf:
        deployed += [latest_pdf]
    if not a.no_deploy:
        deploy(deployed)

    print(json.dumps(dict(
        date=date_slug,
        html=str(latest_html), archived_html=str(archived_html),
        pdf=str(latest_pdf) if not a.no_pdf else None,
        stats=dict(total_projects=data["total_projects"], tenders=len(data["tenders"]),
                   awards=len(data["awards"]), ict=len(data["ict"]), sites=data["n_sites"]),
        deployed=not a.no_deploy, qr_url=qr_url,
        public_url=f"{a.host}/daily/{date_slug}.html"), ensure_ascii=False))


if __name__ == "__main__":
    main()
