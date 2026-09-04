# -*- coding: utf-8 -*-
"""各站点抓取适配器

稳定性设计：
- BaseScraper.fetch() 带 3 次重试 + 指数退避
- parse_list() 内部容错：单条解析异常不中断整站
- 详情页抓取有独立超时 + 异常隔离
- session 用完即关
"""
import os
import re
import time
import requests
import json
from datetime import datetime
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse, parse_qs

from config import (
    REQUEST_HEADERS, REQUEST_TIMEOUT, REQUEST_DELAY,
    NOTICE_TYPES, MAX_RETRIES, RETRY_BACKOFF,
    DETAIL_DELAY, MAX_DETAIL_CHARS,
)

# 详情页正文提取配置
DETAIL_BODY_SELECTORS = [
    {"selector": "div.content", "min_len": 50},
    {"selector": "div.article", "min_len": 50},
    {"selector": "div.detail", "min_len": 50},
    {"selector": "div.news_content", "min_len": 50},
    {"selector": "div.text", "min_len": 50},
    {"selector": "div.tender_detail", "min_len": 50},
    {"selector": "div.info_content", "min_len": 50},
    {"selector": "div.article-content", "min_len": 50},
    # 通配回退
    {"selector": "div", "min_len": 200, "fallback": True},
]

# 正文清理关键词
CONTENT_CUT_KEYWORDS = ['推荐新闻', '友情链接', '相关新闻', '上一篇', '下一篇', '版权所有']


class BaseScraper:
    """基类：抓取列表 + 详情

    翻页能力（P1-3）：
    - 子类可设置 max_pages（默认 2，即抓第 1 页 + 第 2 页）
    - 子类重写 get_page_url(page) 给出第 N 页 URL 模式
    - 不支持翻页的子类保持 max_pages=1，跳过翻页逻辑
    """

    # 默认翻页数；子类按需覆盖
    max_pages = 2

    # 2026-08-10：数据保留线（老板口径：只采到 6 月），太老数据不抓
    CUTOFF_DATE = "2026-06-01"

    def __init__(self, name, url, base_url):
        self.name = name
        self.url = url
        self.base_url = base_url
        self.session = None

    # ---- 翻页层 ----

    def get_page_url(self, page):
        """生成第 N 页 URL。

        page=1 直接返回 self.url；
        page>1 默认抛 NotImplementedError，子类按模式覆盖。
        """
        if page == 1:
            return self.url
        raise NotImplementedError(
            f"{self.__class__.__name__} 不支持第 {page} 页，请设置 max_pages=1 或重写 get_page_url()"
        )

    # ---- HTTP 层 ----

    def _get_session(self):
        """懒初始化 session（每次 scrape 复用一个）"""
        if self.session is None:
            self.session = requests.Session()
            self.session.headers.update(REQUEST_HEADERS)
        return self.session

    def fetch(self, url):
        """抓取页面 HTML，带重试 + 指数退避"""
        session = self._get_session()
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                resp = session.get(url, timeout=REQUEST_TIMEOUT)
                resp.encoding = resp.apparent_encoding or resp.encoding
                if resp.status_code == 200 and resp.text:
                    return resp.text
                # 404/410 是确定性下架，不重试；超时、限流和 5xx 仍按退避策略重试。
                if resp.status_code in (404, 410):
                    print(f"  [{self.name}] HTTP {resp.status_code}，页面不存在或已下架，跳过")
                    return ""
                if attempt < MAX_RETRIES:
                    wait = RETRY_BACKOFF * attempt
                    print(f"  [{self.name}] HTTP {resp.status_code}，{wait}s 后重试 ({attempt}/{MAX_RETRIES})")
                    time.sleep(wait)
            except requests.exceptions.Timeout:
                if attempt < MAX_RETRIES:
                    wait = RETRY_BACKOFF * attempt
                    print(f"  [{self.name}] 超时，{wait}s 后重试 ({attempt}/{MAX_RETRIES})")
                    time.sleep(wait)
                else:
                    print(f"  [{self.name}] 超时，已达最大重试次数 {MAX_RETRIES}")
            except Exception as e:
                if attempt < MAX_RETRIES:
                    wait = RETRY_BACKOFF * attempt
                    print(f"  [{self.name}] 请求异常: {e}，{wait}s 后重试 ({attempt}/{MAX_RETRIES})")
                    time.sleep(wait)
                else:
                    print(f"  [{self.name}] 请求失败，已达最大重试次数: {e}")
        return ""

    def close(self):
        """关闭 session 释放连接"""
        if self.session:
            self.session.close()
            self.session = None

    # ---- 详情页层 ----

    def fetch_detail_content(self, url):
        """抓取详情页正文文本

        通用策略：尝试常见 CSS 选择器，取文本最长且 > min_len 的块。
        回退：取 body 纯文本前 MAX_DETAIL_CHARS 字。
        """
        html = self.fetch(url)
        if not html:
            return ""

        soup = BeautifulSoup(html, 'lxml')
        best_text = ""
        for spec in DETAIL_BODY_SELECTORS:
            try:
                elements = soup.select(spec["selector"])
            except Exception:
                continue
            for el in elements:
                text = el.get_text(separator=" ", strip=True)
                if len(text) > len(best_text) and len(text) >= spec.get("min_len", 0):
                    best_text = text

        if best_text:
            for cut_kw in CONTENT_CUT_KEYWORDS:
                idx = best_text.find(cut_kw)
                if idx >= 0:
                    best_text = best_text[:idx]
            return best_text[:MAX_DETAIL_CHARS]

        # 回退：取 body 全文
        body = soup.find('body')
        if body:
            text = body.get_text(separator=" ", strip=True)
            for cut_kw in CONTENT_CUT_KEYWORDS:
                idx = text.find(cut_kw)
                if idx >= 0:
                    text = text[:idx]
            return text[:MAX_DETAIL_CHARS]

        return ""

    def extract_detail_fields(self, content, title=""):
        """从详情页正文中提取结构化字段：发包人、项目金额、开标时间

        v2: 优先用移植自 Pro 版的富化引擎 (enrich_engine)，失败回退旧正则。
        """
        fields = {}

        # 详情页“发布时间”优先（2026-08-11：都市集团等列表页只给“日”，跨月/晚抓会错标月份；
        # 详情页明文“发布时间：YYYY-MM-DD”最可靠）。仅提取到才返回，避免覆盖列表页猜测值。
        m = re.search(r"发布时间[^0-9年]{0,10}(20\d{2})[-年/.]?(\d{1,2})[-月/.]?(\d{1,2})", content or "")
        if m:
            y, mo, d = m.group(1), m.group(2).zfill(2), m.group(3).zfill(2)
            fields["date"] = f"{y}-{mo}-{d}"

        # 优先新引擎
        try:
            from enrich_engine import enrich_detail, infer_notice_type
            nt = infer_notice_type(title or "")
            enriched = enrich_detail(content, nt)
            fields = {k: v for k, v in enriched.items() if v not in (None, "")}
            fields["notice_type"] = nt
        except Exception as e:
            print(f"  [{self.name}] 富化引擎异常，回退旧正则: {e}")
            fields = {}

        # ---- 旧正则回退（合并策略：新引擎优先，旧引擎补缺）----
        old_fields = self._legacy_extract(content)
        for k in ("tenderer", "amount", "bid_deadline"):
            if not fields.get(k) and old_fields.get(k):
                fields[k] = old_fields[k]
        # 新引擎已提取到关键字段，直接返回
        if fields.get("tenderer") or fields.get("amount") or fields.get("bid_deadline"):
            return fields

        return fields

    def _legacy_extract(self, content):
        """旧版正则提取（独立方法，供合并策略复用）"""
        fields = {}
        for pat in [
            r'招标人\s*[：:]\s*([^\s，。、（]+)',
            r'发包人\s*[：:]\s*([^\s，。、（]+)',
            r'采购人\s*[：:]\s*([^\s，。、（]+)',
            r'招\s*标\s*人\s*[：:]\s*([^\s，。、（]+)',
            r'甲方\s*[：:]\s*([^\s，。、（]+)',
        ]:
            m = re.search(pat, content)
            if m:
                fields["tenderer"] = m.group(1).strip()
                break
        if "tenderer" not in fields:
            m = re.search(r'(盐城[^\s，。、（]{2,15}(?:有限公司|集团有限公司|资产经营管理有限公司))', content)
            if m:
                fields["tenderer"] = m.group(1).strip()

        amount_patterns = [
            (r'(?:预算|控制价|最高限价|项目金额|总投资|投资规模|合同估算价|概算)[^。]*?(\d+(?:\.\d+)?)\s*万\s*元', '万元'),
            (r'(?:总费用|费用|造价|合同金额)[^。]*?(\d+(?:\.\d+)?)\s*万\s*元', '万元'),
            (r'(?:基金规模|规模)[^。]*?(\d+(?:\.\d+)?)\s*亿元', '亿元'),
            # 2026-08-17 修复：原 (\d[\d,]+) 会把 "中标金额：1192108.56元" 的 .56 小数误抓成 56元
            (r'(?:预算|控制价|最高限价|金额|总投资)[^。]*?(\d[\d,]*(?:\.\d+)?)\s*元', '元'),
            (r'中标价[：:]\s*最高投标限价的(\d+(?:\.\d+)?)%', 'percent'),
        ]
        for pat, unit in amount_patterns:
            m = re.search(pat, content)
            if m:
                raw = m.group(1)
                if unit == '万元':
                    fields["amount"] = f"{raw}万元"
                elif unit == '亿元':
                    fields["amount"] = f"{raw}亿元"
                elif unit == '元':
                    try:
                        val = float(raw.replace(',', ''))
                    except ValueError:
                        continue
                    # 2026-08-17：低于 1000 元的"金额"几乎全是小数残留/单价误抓，跳过继续找
                    if val < 1000:
                        continue
                    fields["amount"] = f"{val / 10000:.2f}万元" if val >= 10000 else f"{val:.2f}元"
                elif unit == 'percent':
                    fields["amount"] = f"最高限价的{raw}%"
                break

        date_time_pat = r'\d{4}\s*年[\s_]*\d{1,2}[\s_]*月[\s_]*\d{1,2}[\s_]*日[\s_]*\d{1,2}[\s_]*[：:时][\s_]*\d{0,2}[\s_]*分?'
        date_only_pat = r'\d{4}\s*年[\s_]*\d{1,2}[\s_]*月[\s_]*\d{1,2}[\s_]*日'

        for kw in ['开标时间', '递交截止时间', '投标截止时间', '截止时间']:
            m = re.search(kw + r'\s*[（(]?[^)）]*?[)）]?\s*[：:]\s*(' + date_time_pat + r')', content)
            if m:
                fields["bid_deadline"] = m.group(1).strip()
                break
        if "bid_deadline" not in fields:
            for kw in ['开标时间', '递交截止时间', '投标截止时间', '截止时间']:
                m = re.search(kw + r'\s*[（(]?[^)）]*?[)）]?\s*[：:]\s*(' + date_only_pat + r')', content)
                if m:
                    fields["bid_deadline"] = m.group(1).strip()
                    break
        if "bid_deadline" not in fields:
            m = re.search(r'于\s*(' + date_time_pat + r')\s*(?:前|时)', content)
            if m:
                fields["bid_deadline"] = m.group(1).strip()
        if "bid_deadline" not in fields:
            m = re.search(r'请于\s*(' + date_time_pat + r')\s*前', content)
            if m:
                fields["bid_deadline"] = m.group(1).strip()

        if "bid_deadline" in fields:
            fields["bid_deadline"] = self._normalize_datetime(fields["bid_deadline"])
        return fields

    @staticmethod
    def _normalize_datetime(raw):
        """归一化为 'YYYY年M月D日 HH:MM'"""
        m = re.match(
            r'(\d{4})\s*年[\s_]*(\d{1,2})[\s_]*月[\s_]*(\d{1,2})[\s_]*日'
            r'(?:[\s_]*(\d{1,2})[\s_]*[：:时][\s_]*(\d{0,2})[\s_]*分?)?',
            raw
        )
        if not m:
            return raw
        y, mo, d = m.group(1), int(m.group(2)), int(m.group(3))
        hh, mm = m.group(4), m.group(5)
        result = f"{y}年{mo}月{d}日"
        if hh:
            hh_val, mm_val = int(hh), int(mm) if mm else 0
            result += f" {hh_val:02d}:{mm_val:02d}"
        return result

    # ---- 列表层 ----

    def parse_list(self, html):
        """解析列表页，子类实现"""
        raise NotImplementedError

    def _safe_parse_list(self, html):
        """带异常隔离的列表解析

        单条条目解析异常不影响其他条目；
        parse_list 自身异常返回空列表。
        """
        try:
            return self.parse_list(html)
        except Exception as e:
            print(f"  [{self.name}] 列表解析异常: {e}")
            return []

    def _safe_parse_item(self, parser_fn, *args):
        """单条条目解析的异常隔离"""
        try:
            return parser_fn(*args)
        except Exception as e:
            # 静默跳过单条异常，避免整站中断
            return None

    # ---- 主流程 ----

    def scrape(self):
        """完整抓取流程：列表页（可翻页）→ 详情页正文 + 结构化字段提取

        P1-3 翻页：
        - 遍历 page = 1..max_pages，每页独立抓取 + 解析
        - 跨页用 URL 去重（seen_urls），避免第 1 页与第 2 页头部重复
        - 某页异常隔离：跳过该页，继续后续页
        - 详情页抓取仍走现有流程
        """
        print(f"[{self.name}] 开始抓取 {self.url} (max_pages={self.max_pages})")
        items = []
        seen_urls = set()  # 跨页去重（按详情 URL）
        successful_pages = 0

        for page in range(1, self.max_pages + 1):
            try:
                page_url = self.get_page_url(page)
            except NotImplementedError:
                print(f"  [{self.name}] 不支持第 {page} 页，停止翻页")
                break

            if page > 1:
                # 翻页间隔复用 REQUEST_DELAY
                time.sleep(REQUEST_DELAY)

            html = self.fetch(page_url)
            if not html:
                print(f"  [{self.name}] 第 {page} 页抓取失败，跳过")
                continue

            successful_pages += 1
            page_items = self._safe_parse_list(html)
            # 跨页去重（按 URL）
            added = 0
            for it in page_items:
                u = it.get("url", "")
                if not u or u in seen_urls:
                    continue
                seen_urls.add(u)
                items.append(it)
                added += 1
            print(f"  [{self.name}] 第 {page} 页解析 {len(page_items)} 条，去重后新增 {added} 条，累计 {len(items)} 条")

            # 2026-08-10 日期早停：按当前页最新条目判断（防置顶旧条目误触发）
            page_dates = [it.get("date", "") for it in page_items if it.get("date")]
            if page_dates and max(page_dates) < self.CUTOFF_DATE:
                print(f"  [{self.name}] 已到 {self.CUTOFF_DATE} 保留线，停止翻页")
                break

        if not items:
            if successful_pages == 0:
                raise RuntimeError(f"{self.name} 所有列表页请求均失败")
            print(f"  [{self.name}] 列表为空，跳过详情页抓取")
            return items

        print(f"  [{self.name}] 列表共 {len(items)} 条，开始抓详情页…")

        for i, item in enumerate(items):
            try:
                content = self.fetch_detail_content(item["url"])
                item["content"] = content
                fields = self.extract_detail_fields(content, item.get("title", ""))
                item.update(fields)
            except Exception as e:
                print(f"  [{self.name}] 详情页失败 {item.get('url', '?')}: {e}")
                item.setdefault("content", "")
            time.sleep(DETAIL_DELAY)

        print(f"  [{self.name}] 详情页抓取完成")
        return items


# ==================== 原有盐城本地平台 ====================


class YcDataGroupScraper(BaseScraper):
    """大数据集团 — 标题+摘要混合在 a 文本里，末尾有日期
    翻页：.../news/19.html → .../news/19-N.html
    2026-08-10：原 max_pages=2 太小，放大到 20（日期早停在 6 月线）。
    """

    max_pages = 1000

    def get_page_url(self, page):
        if page == 1:
            return self.url
        # http://www.ycdatagroup.cn/news/19.html → http://www.ycdatagroup.cn/news/19-2.html
        if self.url.endswith('.html'):
            return self.url[:-5] + f'-{page}.html'
        return self.url  # 回退

    def parse_list(self, html):
        soup = BeautifulSoup(html, 'lxml')
        items = []
        seen = set()

        for a in soup.find_all('a', href=True):
            href = a['href']
            if 'show-' not in href:
                continue
            if href in seen:
                continue
            seen.add(href)

            full_url = urljoin(self.base_url, href)
            text = a.get_text(strip=True)
            title = self._extract_title(text)
            date = ""
            # 2026-08-17：日期正则通用化（原写死 2026，支持任意年份）
            # 源站 a 文本末尾格式："…日2026.月" 或 "…日2027.月"
            date_match = re.search(r'(\d{1,2})(20\d{2})\.(\d{2})$', text)
            if date_match:
                date = f"{date_match.group(2)}-{date_match.group(3)}-{date_match.group(1).zfill(2)}"

            if title and len(title) > 2:
                items.append({"title": title, "url": full_url, "date": date})

        return items

    @staticmethod
    def _extract_title(text):
        # 2026-08-17：优先 NOTICE_TYPES 关键词截断；未命中时用"的公告/通知/公示"等词截断，
        # 避免把正文（"为进一步提升…"）带进标题
        for kw in NOTICE_TYPES:
            idx = text.find(kw)
            if idx != -1:
                return text[:idx + len(kw)]
        for kw in ("的公告", "的通知", "的公示", "结果公示", "结果公告"):
            idx = text.find(kw)
            if idx != -1:
                return text[:idx + len(kw)]
        return text[:60]


class YcDsjtScraper(BaseScraper):
    """都市集团 — dt.f18 > a 结构，日期在 dd.date span 里（只有日）
    翻页：...?zhaobiao/ → ...?zhaobiao_N/
    2026-08-10：原 max_pages=2 只抓 2 页，放大到 20。
    """

    max_pages = 20

    def get_page_url(self, page):
        if page == 1:
            return self.url
        # http://www.ycdsjt.cn/?zhaobiao/ → http://www.ycdsjt.cn/?zhaobiao_2/
        if self.url.endswith('/'):
            return self.url[:-1] + f'_{page}/'
        return self.url + f'_{page}/'

    def parse_list(self, html):
        from datetime import datetime
        soup = BeautifulSoup(html, 'lxml')
        items = []
        seen = set()
        now = datetime.now()

        for dt in soup.find_all('dt', class_='f18'):
            a = dt.find('a', href=True)
            if not a:
                continue
            href = a['href']
            if href in seen:
                continue
            seen.add(href)

            full_url = urljoin(self.base_url, href)
            title = a.get_text(strip=True)
            date = ""
            dl = dt.find_parent('dl')
            if dl:
                dd_date = dl.find('dd', class_='date')
                if dd_date:
                    span = dd_date.find('span')
                    if span:
                        day = span.get_text(strip=True)
                        if re.match(r'\d{1,2}$', day):
                            # 列表只给"日"数字，用当前年月补齐；
                            # 跨月保护：若补全后晚于今天，视为上个月（2026-08-06 修复）。
                            day_int = int(day)
                            if day_int <= now.day:
                                date = f"{now.year}-{now.month:02d}-{day_int:02d}"
                            elif now.month == 1:
                                date = f"{now.year - 1}-12-{day_int:02d}"
                            else:
                                date = f"{now.year}-{now.month - 1:02d}-{day_int:02d}"

            if title and len(title) > 2:
                items.append({"title": title, "url": full_url, "date": date})

        return items


class CommonListScraper(BaseScraper):
    """通用列表抓取 — 适用于世纪新城、经开城发、盐东方
    翻页：.../tender/ → .../tender/index_N.html
         .../qzzgs/  → .../qzzgs/index_N.html
         .../zbxx/   → .../zbxx/index_N.html
    2026-08-10：原 max_pages=2 只抓 2 页，放大到 20。
    """

    max_pages = 20

    def get_page_url(self, page):
        if page == 1:
            return self.url
        # URL 以 "/" 结尾 → 插入 "index_N.html"
        # 例：http://www.jscncg.com/tenderLease/tender/ → http://www.jscncg.com/tenderLease/tender/index_2.html
        if self.url.endswith('/'):
            return self.url + f'index_{page}.html'
        # URL 以 ".html" 结尾 → 在文件名后插入 _N
        if self.url.endswith('.html'):
            return self.url[:-5] + f'_{page}.html'
        return self.url

    def parse_list(self, html):
        soup = BeautifulSoup(html, 'lxml')
        items = []
        seen = set()

        for li in soup.find_all('li'):
            a = li.find('a', href=True)
            span = li.find('span')
            if not a or not span:
                continue

            href = a['href']
            if not re.search(r'/\d{4}-\d{2}-\d{2}/', href) and not href.endswith('.html'):
                continue
            if href in seen:
                continue
            seen.add(href)

            full_url = urljoin(self.base_url, href)
            title = a.get('title', '') or a.get_text(strip=True)
            date = span.get_text(strip=True)
            if not re.match(r'\d{4}-\d{2}-\d{2}', date):
                date = ""

            if title and len(title) > 2:
                items.append({"title": title, "url": full_url, "date": date})

        return items


# ==================== 表格补充的盐城公共来源 ====================


def _db_query_all():
    """查询已入库记录的 url → raw_json 映射（详情增量保护用，2026-08-10）"""
    try:
        import sqlite3 as _sqlite3
        db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "tender.db")
        conn = _sqlite3.connect(db_path)
        rows = conn.execute("SELECT url, raw_json FROM notices").fetchall()
        conn.close()
        return [{"url": r[0], "raw_json": r[1]} for r in rows]
    except Exception as e:
        print(f"  [scraper] _db_query_all 失败（增量保护降级）: {type(e).__name__}: {e}")
        return []


class YcGovProcurementScraper(BaseScraper):
    """盐城市政府采购网 — 财政局政府采购聚合页。

    抓取策略 v2：
    1. 聚合页 /col/col2383/index.html 仅展示每个子栏目最新 5 条；
       子栏目数量约 30+ 个（含管理类与项目类）。
    2. 子栏目独立列表页 /col/col{ID}/index.html 在 server 端渲染了首屏
       <record> 块（最多 ~45 条），并提供 totalRecord。
       这才是真正能拿到足够数据的地方。
    3. 排除管理类子栏目（通知公告/诚信平台/办事指南），
       对其余子栏目逐个抓取并聚合。
    """

    # 管理类子栏目：通知公告 / 诚信平台（投诉处理） / 办事指南
    EXCLUDED_COLS = {"2387", "2388", "2407"}

    max_pages = 1  # 聚合页本身不分页；通过子栏目页覆盖数据

    def parse_list(self, html):
        """解析聚合页：找出所有子栏目 ID + 备用最新条目

        主路径会覆盖本方法：直接抓子栏目列表页。
        本方法仍保留，作为聚合页直接命中条目的快速通道（与子栏目页合并去重）。
        """
        soup = BeautifulSoup(html, 'lxml')
        items, seen = [], set()
        col_ids = set()
        for a in soup.find_all('a', href=True):
            href = a.get('href', '')
            m = re.search(r'/art/(\d{4})/(\d{1,2})/(\d{1,2})/art_(\d+)_', href)
            if m:
                col_ids.add(m.group(4))
                if m.group(4) in self.EXCLUDED_COLS:
                    continue
                full_url = urljoin(self.base_url, href)
                if full_url in seen:
                    continue
                seen.add(full_url)
                title = (a.get('title') or a.get_text(' ', strip=True)).strip()
                if title:
                    items.append({
                        "title": title,
                        "url": full_url,
                        "date": f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}",
                        "col_id": m.group(4),
                    })
        return items

    def scrape(self):
        """覆盖基类：先抓聚合页发现子栏目 → 逐个抓子栏目列表页 → 合并去重 → 抓详情"""
        print(f"[{self.name}] 开始抓取 {self.url}")
        all_items = []
        seen_urls = set()

        # ---- 步骤 1：抓聚合页 ----
        html = self.fetch(self.url)
        if not html:
            print(f"  [{self.name}] 聚合页抓取失败")
            return all_items

        index_items = self._safe_parse_list(html)
        print(f"  [{self.name}] 聚合页解析 {len(index_items)} 条")

        # 从聚合页发现子栏目 ID
        col_ids = set()
        for it in index_items:
            cid = it.get("col_id")
            if cid:
                col_ids.add(cid)
        # 也从原始 HTML 提取（兼容聚合页直接展示但 parse_list 跳过的情况）
        col_ids.update(self._discover_col_ids(html))
        col_ids -= self.EXCLUDED_COLS
        print(f"  [{self.name}] 发现 {len(col_ids)} 个项目子栏目：{sorted(col_ids, key=int)[:10]}{'...' if len(col_ids)>10 else ''}")

        # 合并聚合页条目
        for it in index_items:
            u = it.get("url", "")
            if u and u not in seen_urls:
                seen_urls.add(u)
                all_items.append(it)

        # ---- 步骤 2：抓每个子栏目列表页 ----
        for col_id in sorted(col_ids, key=int):
            sub_url = f"{self.base_url}/col/col{col_id}/index.html"
            try:
                sub_html = self.fetch(sub_url)
            except Exception as e:
                print(f"  [{self.name}] 子栏目 col{col_id} 抓取异常: {e}")
                continue

            if not sub_html:
                print(f"  [{self.name}] 子栏目 col{col_id} 抓取失败，跳过")
                continue

            if 'wafblock' in sub_html or '知道创宇' in sub_html:
                print(f"  [{self.name}] 子栏目 col{col_id} 被 WAF 拦截，跳过")
                continue

            sub_items = self._parse_subcolumn_html(sub_html, col_id)
            added = 0
            for it in sub_items:
                u = it.get("url", "")
                if not u or u in seen_urls:
                    continue
                seen_urls.add(u)
                all_items.append(it)
                added += 1

            # ---- jpage 翻页补全（2026-08-10：根治历史缺口）----
            # 原实现只抓首屏（~15-45 条），意向/招标/中标栏目实际 540~2700 条。
            # 翻页接口：POST /module/web/jpage/dataproxy.jsp?startrecord=&endrecord=&perpage=15
            #   参数（页面 script param_xxx 中）：col/appid/webid/path/columnid/sourceContentType/unitid/...
            #   返回 <datastore><totalrecord>N</totalrecord><recordset>...record CDATA...</recordset></datastore>
            # 真实总数以 dataproxy 返回的 totalrecord 为准（页面占位 totalRecord=1000 不可信）。
            try:
                unit_m = re.search(r"unitid:'(\d+)'", sub_html)
                if unit_m:
                    unitid = unit_m.group(1)
                    page_items = self._fetch_jpage(col_id, unitid, total_cap=2500)
                    p_added = 0
                    for it in page_items:
                        u = it.get("url", "")
                        if not u or u in seen_urls:
                            continue
                        seen_urls.add(u)
                        all_items.append(it)
                        p_added += 1
                    if p_added:
                        print(f"  [{self.name}]   col{col_id}: jpage 翻页 +{p_added} 条（累计 {added + p_added}）")
                else:
                    print(f"  [{self.name}]   col{col_id}: 未发现 unitid，跳过 jpage 翻页")
            except Exception as e:
                print(f"  [{self.name}]   col{col_id}: jpage 翻页异常: {e}")
            print(f"  [{self.name}]   col{col_id}: +{added} 条（首屏）")

            # 子栏目间隔
            time.sleep(REQUEST_DELAY * 0.5)

        print(f"  [{self.name}] 列表汇总 {len(all_items)} 条，开始抓详情页…")

        # ---- 步骤 3：抓详情 ----
        # 2026-08-10：增量保护——已入库且已有正文的条目跳过详情抓取，
        # 避免 jpage 翻页补全后每天全量重抓详情拖垮定时任务。
        try:
            existing = {r["url"]: (r["raw_json"] or "") for r in _db_query_all()}
        except Exception as e:
            print(f"  [{getattr(self, 'name', '?')}] 增量保护构建失败，本轮将全量重抓详情: {type(e).__name__}: {e}")
            existing = {}

        def _has_content(url):
            raw = existing.get(url, "")
            try:
                import json as _json
                return bool(_json.loads(raw).get("content"))
            except Exception:
                return False

        detail_count = 0
        for i, item in enumerate(all_items):
            if _has_content(item.get("url", "")):
                continue
            try:
                content = self.fetch_detail_content(item["url"])
                item["content"] = content
                fields = self.extract_detail_fields(content, item.get("title", ""))
                item.update(fields)
                detail_count += 1
            except Exception as e:
                print(f"  [{self.name}] 详情页失败 {item.get('url', '?')}: {e}")
                item.setdefault("content", "")
            time.sleep(DETAIL_DELAY)
            if (i + 1) % 50 == 0:
                print(f"  [{self.name}]   详情进度 {i + 1}/{len(all_items)}（新抓 {detail_count}）")

        print(f"  [{self.name}] 详情页抓取完成：{len(all_items)} 条（本次新抓详情 {detail_count}）")
        return all_items

    def _discover_col_ids(self, html):
        """从聚合页 HTML 中发现所有子栏目 ID（出现在 art_xxxx_ 中）"""
        ids = set()
        for m in re.finditer(r'art_(\d+)_', html):
            ids.add(m.group(1))
        return ids

    def _parse_subcolumn_html(self, html, col_id):
        """解析子栏目列表页：优先解析 server-rendered <record> 块，回退普通 <li>"""
        items, seen = [], set()

        # 策略 A：解析 <record><![CDATA[...]]></record> 块（首屏 server-rendered）
        records = re.findall(r"<record><!\[CDATA\[(.*?)\]\]></record>", html, re.DOTALL)
        for rec_html in records:
            # 每个 record 内是 <li>...</li>
            # 注意：<a> 标签中 title 和 href 顺序不固定，分别独立抽取
            href_m = re.search(r"href=['\"]([^'\"]+)['\"]", rec_html)
            title_m = re.search(r"title=['\"]([^'\"]*)['\"]", rec_html)
            date_match = re.search(r'<span[^>]*bt-list-time[^>]*>([\d\-]+)</span>', rec_html)
            if not href_m:
                continue
            href = href_m.group(1)
            title = (title_m.group(1) if title_m else '').strip()
            # 兜底：从 <a> 文本内容中提取 title
            if not title:
                txt_m = re.search(r'<a[^>]*>([^<]+)</a>', rec_html)
                if txt_m:
                    title = txt_m.group(1).strip()
            # 提取日期（URL 中或 span 中）
            date = None
            url_m = re.search(r'/art/(\d{4})/(\d{1,2})/(\d{1,2})/art_', href)
            if url_m:
                date = f"{url_m.group(1)}-{int(url_m.group(2)):02d}-{int(url_m.group(3)):02d}"
            elif date_match:
                date = date_match.group(1)
            if not title:
                continue
            full_url = urljoin(self.base_url, href)
            if full_url in seen:
                continue
            seen.add(full_url)
            items.append({
                "title": title,
                "url": full_url,
                "date": date or "",
                "col_id": col_id,
            })

        # 策略 B：回退解析普通 <li> 块（有些页可能不用 record 模式）
        if not items:
            soup = BeautifulSoup(html, 'lxml')
            for li in soup.find_all('li', class_=re.compile(r'bt-list|bt-row')):
                a = li.find('a', href=True)
                if not a:
                    continue
                href = a['href']
                title = a.get('title') or a.get_text(strip=True)
                url_m = re.search(r'/art/(\d{4})/(\d{1,2})/(\d{1,2})/art_(\d+)_', href)
                if not url_m:
                    continue
                full_url = urljoin(self.base_url, href)
                if full_url in seen:
                    continue
                seen.add(full_url)
                items.append({
                    "title": title,
                    "url": full_url,
                    "date": f"{url_m.group(1)}-{int(url_m.group(2)):02d}-{int(url_m.group(3)):02d}",
                    "col_id": url_m.group(4),
                })
        return items

    # ---- jpage 翻页（2026-08-10：根治历史缺口）----
    # 数据保留策略（老板 2026-08-10 确认）：只保留 2026-06 及之后的数据。
    # 翻页早停口径见 BaseScraper.CUTOFF_DATE（基类已声明，此处不再重复定义）。

    def _fetch_jpage(self, col_id, unitid, per_page=15, total_cap=2500):
        """用 dataproxy 接口翻页抓全子栏目列表。

        返回去重后的全部条目（含首屏已抓的会由调用方 seen_urls 去重）。
        total_cap：防呆上限，接口异常时避免无限翻页（最大栏目实际 2713 条）。
        """
        proxy_url = f"{self.base_url}/module/web/jpage/dataproxy.jsp"
        items, seen = [], set()
        data = {
            "col": "1", "appid": "1", "webid": "7", "path": "/",
            "columnid": str(col_id), "sourceContentType": "1",
            "unitid": str(unitid), "webname": "盐城市财政局", "permissiontype": "0",
        }
        headers = {
            "Referer": f"{self.base_url}/col/col{col_id}/index.html",
            "X-Requested-With": "XMLHttpRequest",
        }
        start = 1
        total = None
        while True:
            params = {"startrecord": start, "endrecord": start + per_page - 1, "perpage": per_page}
            text = ""
            for attempt in range(1, MAX_RETRIES + 1):
                try:
                    resp = self._get_session().post(proxy_url, params=params, data=data,
                                                    headers=headers, timeout=REQUEST_TIMEOUT)
                    if resp.status_code == 200 and "<datastore>" in resp.text:
                        text = resp.text
                        break
                    time.sleep(RETRY_BACKOFF * attempt)
                except requests.exceptions.RequestException:
                    time.sleep(RETRY_BACKOFF * attempt)
            if not text:
                print(f"  [{self.name}]   col{col_id}: dataproxy 请求失败，停止翻页")
                break

            m = re.search(r"<totalrecord>(\d+)</totalrecord>", text)
            if m:
                total = int(m.group(1))
            page_items = []
            for rec in re.findall(r"<record><!\[CDATA\[(.*?)\]\]></record>", text, re.DOTALL):
                href_m = re.search(r"href=['\"]([^'\"]+)['\"]", rec)
                title_m = re.search(r"title=['\"]([^'\"]*)['\"]", rec)
                if not href_m:
                    continue
                href = href_m.group(1)
                title = (title_m.group(1) or "").strip()
                if not title:
                    txt_m = re.search(r"<a[^>]*>([^<]+)</a>", rec)
                    if txt_m:
                        title = txt_m.group(1).strip()
                if not title:
                    continue
                full_url = urljoin(self.base_url, href)
                if full_url in seen:
                    continue
                seen.add(full_url)
                date = ""
                dm = re.search(r"/art/(\d{4})/(\d{1,2})/(\d{1,2})/art_", href)
                if dm:
                    date = f"{dm.group(1)}-{int(dm.group(2)):02d}-{int(dm.group(3)):02d}"
                else:
                    tm = re.search(r"bt-list-time[^>]*>([\d\-]+)</span>", rec)
                    if tm:
                        date = tm.group(1)
                page_items.append({"title": title, "url": full_url, "date": date, "col_id": col_id})
            items.extend(page_items)

            # 2026-08-10 日期早停：按【当前页】最新条目判断。jpage 严格新→旧，
            # 当前页最新都低于保留线则后续全更旧，可安全停。（不能用累计 items 求 max，
            # 那样永不触发；也不能用 min——会被置顶/补录旧条目误触发漏抓）
            page_dates = [it.get("date") or "" for it in page_items]
            if page_dates and max(page_dates) < self.CUTOFF_DATE:
                print(f"  [{self.name}]   col{col_id}: 已到 {self.CUTOFF_DATE} 保留线，停止翻页")
                break

            if total and len(items) >= total:
                break
            if not total or len(items) >= total_cap:
                break
            start += per_page
            time.sleep(REQUEST_DELAY * 0.3)
        return items


class YanchengGovScraper(BaseScraper):
    """盐城市人民政府网 — 政府采购 iframe 列表。
    2026-08-10 复审：DB 34 条 vs 网站列表量待核；max_pages=2 暂不动。
    """

    max_pages = 2

    def __init__(self, name, url, base_url, list_url):
        super().__init__(name, url, base_url)
        self.list_url = list_url

    def get_page_url(self, page):
        if page == 1:
            return self.list_url
        sep = '&' if '?' in self.list_url else '?'
        return self.list_url + sep + f"sortfield=createdatetime%3A0&currpage={page}"

    def parse_list(self, html):
        soup = BeautifulSoup(html, 'lxml')
        items, seen = [], set()
        for a in soup.find_all('a', href=True):
            href = a.get('href', '')
            m = re.search(r'/art/(\d{4})/(\d{1,2})/(\d{1,2})/art_', href)
            if not m:
                continue
            full_url = urljoin(self.base_url, href.replace('http://', 'https://', 1))
            if full_url in seen:
                continue
            seen.add(full_url)
            title = (a.get('title') or a.get_text(' ', strip=True)).strip()
            if title:
                items.append({
                    "title": title, "url": full_url,
                    "date": f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}",
                })
        return items


class ChinaGovProcurementScraper(BaseScraper):
    """中国政府采购网盐城检索；官方频控时安全返回空列表。

    2026-08-29 修复：
    1. max_pages 1 → 50：原来只抓搜索结果第 1 页（20 条/页），
       盐城当天公告超过 1 页时超出的公告永远轮不到，全区县持续缺数据。
       现改为动态翻页，配合基类 CUTOFF_DATE(2026-06-01) 日期早停。
    2. 过滤误杀修复：原实现只看正文「行政区域」字段，盐城本地公告
       正文该字段写法不一致（如「盐城经济开发区」vs 名单里的
       「盐城经济技术开发区」）会被误排除（例：盐城工职院宿舍管理
       中标公告被误杀）。现改为「标题含盐城关键词 → 直接保留」，
       仅标题无关键词时才用正文行政区域/采购人做二次校验。
    """

    max_pages = 6  # ccgp 单关键词搜索最多返回 120 条（6 页×20），第 7 页起内容重复

    # 标题/正文命中即视为盐城公告的关键词（含区县名，容忍 ccgp 高亮标签产生的空格）
    SALT_CITY_KWS = ('盐城', '亭湖', '盐都', '大丰', '东台', '射阳', '阜宁',
                     '建湖', '响水', '滨海', '盐南', '经开区', '经济开发区')

    # 2026-08-29：单关键词最多返回 120 条（约 6 天窗口），盐城公告日均 15-20 条，
    # 单桶会截断（8 月实测 6 天约 100+ 条纯盐城公告）。改为多关键词分桶：
    # 主桶「盐城」全量 + 区县关键词桶补漏（区县公告标题可能不含“盐城”）。
    SEARCH_KEYWORDS = ('盐城', '亭湖', '盐都', '大丰', '东台', '射阳', '阜宁',
                       '建湖', '响水', '滨海', '盐南')

    # 2026-08-29：ccgp 频控检测基于请求指纹。原全局 REQUEST_HEADERS 缺
    # Accept-Encoding/Sec-Fetch 等头，高频率请求时被判定为爬虫返回「频繁访问」页。
    # 改用完整浏览器头后稳定（实测 7 月时间窗正常返回）。
    CCGP_HEADERS = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Accept-Encoding": "gzip, deflate",  # 不声明 br：服务器若返 br 而未装 brotli 会乱码（2026-08-29 复查修复）
        "Referer": "https://search.ccgp.gov.cn/",
        "Sec-Ch-Ua": '"Not/A)Brand";v="8", "Chromium";v="126", "Google Chrome";v="126"',
        "Sec-Ch-Ua-Mobile": "?0",
        "Sec-Ch-Ua-Platform": '"Windows"',
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "same-origin",
        "Sec-Fetch-User": "?1",
        "Upgrade-Insecure-Requests": "1",
    }

    def _get_session(self):
        """中国政府采购网专用 session：完整浏览器头，规避频控误判。"""
        if self.session is None:
            self.session = requests.Session()
            self.session.headers.update(self.CCGP_HEADERS)
        return self.session

    def get_page_url(self, page):
        """bxsearch 翻页：page_index=N；第 1 页直接用配置 URL。"""
        if page == 1:
            return self.url
        from urllib.parse import urlparse, urlencode, parse_qs, urlunparse
        u = urlparse(self.url)
        qs = parse_qs(u.query)
        qs['page_index'] = [str(page)]
        return urlunparse(u._replace(query=urlencode(qs, doseq=True)))

    def _get_bucket_url(self, keyword, page):
        """生成指定关键词桶的第 page 页搜索 URL（复用配置 URL 的其余参数）。"""
        from urllib.parse import urlparse, urlencode, parse_qs, urlunparse, quote
        u = urlparse(self.url)
        qs = parse_qs(u.query)
        qs['kw'] = [keyword]
        qs['page_index'] = [str(page)]
        # 历史回填：设置 _time_window 后切到 timeType=6 显式时间窗，
        # 突破单关键词 6 天滚动窗口限制（2026-08-29）
        tw = getattr(self, '_time_window', None)
        if tw:
            qs['timeType'] = ['6']
            qs['start_time'] = [tw[0]]
            qs['end_time'] = [tw[1]]
        return urlunparse(u._replace(query=urlencode(qs, doseq=True)))

    def set_time_window(self, start, end):
        """设置历史回填时间窗（timeType=6 显式区间）。

        Args:
            start/end: 'YYYY-MM-DD' 字符串
        """
        self._time_window = (start.replace('-', ':'), end.replace('-', ':'))

    def set_slow_mode(self, enabled=True):
        """慢速模式（历史回填用）：桶间/页间/详情间隔放大，降低 ccgp 频控触发率。"""
        self._slow_mode = enabled

    def _is_yancheng(self, item):
        """盐城公告判定（2026-08-29 改为宁多勿漏）：
        1. 标题含盐城关键词 → 保留（防 ccgp 高亮空格干扰）；
        2. 正文行政区域含盐城关键词 → 保留；
        3. 行政区域明确为外地市/县/区（非开发区/高新区后缀）→ 排除；
        4. 采购人/招标人含盐城关键词 → 保留；
        5. 其余情况默认保留（省级采购人如江苏省气象局下属项目、
           行政区域字段缺失/写法特殊等，交给下游站点去重互补，不误杀）。
        """
        title = (item.get('title') or '').replace(' ', '')
        if any(kw in title for kw in self.SALT_CITY_KWS):
            return True
        content = item.get('content') or ''
        # 正文行政区域字段（ccgp 详情页格式：行政区域：盐城市亭湖区）
        m = re.search(r'行政区域\s*[：:]?\s*([^\s，。]{2,20})', content[:4000])
        if m:
            area = m.group(1)
            if any(kw in area for kw in self.SALT_CITY_KWS):
                return True
            # 行政区域是「明确的外地行政区划」才排除：
            # 市/县/区/旗结尾，且不是开发区/高新区/新区这类通用后缀。
            # 例：淮北市、樟树市、固镇县、金普新区（区）+ 非开发区后缀 → 排除；
            #     江苏省（省）→ 不排除（可能是省级采购人下属盐城项目）；
            #     盐城经济技术开发区 → 已含关键词保留。
            if re.search(r'[\u4e00-\u9fa5]{1,6}(?:市|县|区|旗)$', area) \
                    and not re.search(r'(开发区|高新区|新区|示范区)$', area):
                return False
        # 采购人/招标人字段含盐城关键词（部分公告无「行政区域」字段）
        m2 = re.search(r'(?:采购人|招标人|发包人|采购单位)\s*[：:]\s*([^\s，。]{2,30})', content[:4000])
        if m2 and any(kw in m2.group(1) for kw in self.SALT_CITY_KWS):
            return True
        # 无明确外地证据：默认保留（宁多勿漏，由下游去重互补）
        return True

    def scrape(self):
        """多关键词分桶抓取：主桶「盐城」+ 区县关键词桶，合并去重后统一抓详情。

        单关键词 120 条上限只覆盖最近约 6 天，盐城公告日均 15-20 条，
        单桶截断严重；分桶后每个桶独立翻页（每桶最多 6 页/120 条），
        按 URL 跨桶去重，最大限度覆盖保留线内公告。
        """
        print(f"[{self.name}] 开始多桶抓取 {len(self.SEARCH_KEYWORDS)} 个关键词")
        all_items = []
        seen_urls = set()
        total_pages_fetched = 0

        for kw in self.SEARCH_KEYWORDS:
            bucket_items = []
            for page in range(1, self.max_pages + 1):
                if page > 1:
                    time.sleep(REQUEST_DELAY * (3 if getattr(self, '_slow_mode', False) else 1))
                url = self._get_bucket_url(kw, page)
                html = self.fetch(url)
                if not html:
                    print(f"  [{self.name}] 桶「{kw}」第 {page} 页抓取失败，跳过")
                    continue
                page_items = self._safe_parse_list(html)
                if not page_items:
                    # 频控或空页：停止当前桶
                    break
                total_pages_fetched += 1
                added = 0
                for it in page_items:
                    u = it.get('url', '')
                    if not u or u in seen_urls:
                        continue
                    seen_urls.add(u)
                    bucket_items.append(it)
                    all_items.append(it)
                    added += 1
                # 日期早停：当前页最新日期早于保留线则后续更旧，停止翻页
                page_dates = [it.get('date', '') for it in page_items if it.get('date')]
                if page_dates and max(page_dates) < self.CUTOFF_DATE:
                    print(f"  [{self.name}] 桶「{kw}」已到 {self.CUTOFF_DATE} 保留线，停止翻页")
                    break
                if added == 0 and page >= 2:
                    # 连续翻页无新条目（ccgp 第 7 页起内容重复），停止
                    break
            print(f"  [{self.name}] 桶「{kw}」新增 {len(bucket_items)} 条，累计 {len(all_items)} 条")
            # 桶间请求间隔，降低频控风险
            time.sleep(REQUEST_DELAY * (3 if getattr(self, '_slow_mode', False) else 1))

        if not all_items:
            if total_pages_fetched == 0:
                raise RuntimeError(f"{self.name} 所有列表页请求均失败")
            print(f"  [{self.name}] 列表为空，跳过详情页抓取")
            return []

        print(f"  [{self.name}] 列表共 {len(all_items)} 条（{total_pages_fetched} 页），开始抓详情页…")
        detail_delay = DETAIL_DELAY * (3 if getattr(self, '_slow_mode', False) else 1)
        for i, item in enumerate(all_items):
            try:
                content = self.fetch_detail_content(item['url'])
                item['content'] = content
                fields = self.extract_detail_fields(content, item.get('title', ''))
                item.update(fields)
            except Exception as e:
                print(f"  [{self.name}] 详情页失败 {item.get('url', '?')}: {e}")
                item.setdefault('content', '')
            time.sleep(detail_delay)

        # 盐城过滤（宁多勿漏）
        filtered = []
        for item in all_items:
            if not self._is_yancheng(item):
                print(f"  [{self.name}] 排除非盐城公告：{item.get('title', '')}")
                continue
            filtered.append(item)
        print(f"  [{self.name}] 多桶抓取完成，共 {len(filtered)} 条（过滤前 {len(all_items)} 条）")
        return filtered

    def parse_list(self, html):
        if '频繁访问' in html or '访问过于频繁' in html:
            print(f"  [{self.name}] 官方检索触发频控，本轮跳过（由省/市采购源覆盖）")
            return []
        soup = BeautifulSoup(html, 'lxml')
        items, seen = [], set()
        for li in soup.select('ul.vT-srch-result-list-bid li, ul.c_list_bid li'):
            a = li.find('a', href=True)
            if not a:
                continue
            title = (a.get('title') or a.get_text(' ', strip=True)).strip()
            text = li.get_text(' ', strip=True)
            # 列表级预过滤：白名单对齐 SALT_CITY_KWS（2026-08-29 复查修复）。
            # 原白名单缺「亭湖/盐都/盐南/经开区」，导致分桶特意抓的
            # 「标题不含盐城」区县公告在列表层就被丢弃（如“盐都区”不是
            # “盐城”子串）。此处仅粗筛，详情抓取后仍有 _is_yancheng 二次判定。
            if not any(x in (title + text) for x in self.SALT_CITY_KWS):
                continue
            url = urljoin(self.base_url, a['href'])
            if url in seen:
                continue
            seen.add(url)
            # 列表文本里可能混入正文的开标/公告期限日期；
            # 若提取的日期晚于今天则视为误提取，置空（2026-08-06 修复）。
            # 日期格式兼容：2026-08-06 / 2026年08月06日 / 2026.08.06（2026-08-06 补）
            m = re.search(r'(\d{4})[-年.](\d{1,2})[-月.](\d{1,2})', text)
            date = ''
            if m:
                from datetime import date as _date
                try:
                    parsed = _date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
                    if parsed <= _date.today():
                        date = parsed.isoformat()
                except ValueError:
                    date = ''
            items.append({"title": title, "url": url, "date": date})
        return items


class JszbcgScraper(BaseScraper):
    """江苏招标采购服务平台 — 盐城地区公告 API。

    2026-08-06 修复：原固定抓 2 页（40 条/类），但 14 天窗口内每类公告远超 40 条
    （实测 totalCount：招标 205 / 候选 76 / 中标 117 / 更正 51），导致严重截断。
    改为按 API 返回的 totalPage 动态翻页，单类安全上限 MAX_PAGE 防失控。
    """

    max_pages = 2  # 兼容基类；实际翻页由 totalPage 驱动
    MAX_PAGE = 30  # 单类安全上限（30 页 × 20 = 600 条/类）
    TYPE_CATEGORY = {1: ('招标公告公示', 88), 2: ('中标候选人公示', 91),
                     3: ('中标结果公示', 90), 4: ('更正公告公示', 89)}

    def __init__(self, name, url, base_url, api_url, region_code='320900'):
        super().__init__(name, url, base_url)
        self.api_url = api_url
        self.region_code = region_code

    def scrape(self):
        from datetime import timedelta
        print(f"[{self.name}] 开始抓取盐城地区公告 API（动态翻页，单类上限 {self.MAX_PAGE} 页）")
        session = self._get_session()
        session.headers.update({"Origin": self.base_url, "Referer": self.base_url + "/"})
        end = datetime.now().date()
        start = end - timedelta(days=14)
        items, seen = [], set()
        for bulletin_type, (category_name, category_id) in self.TYPE_CATEGORY.items():
            total_pages = None
            page = 1
            while True:
                if total_pages and page > total_pages:
                    break
                if page > self.MAX_PAGE:
                    print(f"  ⚠️ [{self.name}] 类型 {bulletin_type} 超过安全上限 {self.MAX_PAGE} 页，停止（数据仍可能截断）")
                    break
                params = {
                    "bulletinType": bulletin_type, "industryCode": "",
                    "regionCode": self.region_code,
                    "startTime": f"{start} 00:00:00", "endTime": f"{end} 23:59:59",
                    "keyword": "", "currentPage": page, "pageSize": 20,
                    "source": "zbcg", "Source": "zbcg",
                }
                try:
                    resp = session.get(self.api_url, params=params, timeout=REQUEST_TIMEOUT)
                    resp.raise_for_status()
                    payload = resp.json()
                    data = payload.get('data') or {}
                    rows = data.get('data') or []
                    if total_pages is None:
                        total_pages = data.get('totalPage') or data.get('totalPages') or None
                except Exception as e:
                    print(f"  [{self.name}] 类型 {bulletin_type} 第 {page} 页失败: {e}")
                    break
                if not rows:
                    break
                for row in rows:
                    rid = str(row.get('bulletinID') or '')
                    if not rid or rid in seen:
                        continue
                    seen.add(rid)
                    title = (row.get('bulletinName') or '').strip()
                    if not title:
                        continue
                    url = (f"{self.base_url}/#/bulletinDetails/{category_name}/{rid}"
                           f"?bulletinType={bulletin_type}")
                    content = ' '.join(filter(None, [
                        title, row.get('regionName'), row.get('industryName'),
                        row.get('bulletinSourceName'),
                        f"开标时间：{row.get('openBidTime')}" if row.get('openBidTime') else '',
                    ]))
                    item = {
                        "title": title, "url": url,
                        "date": (row.get('noticeSendTime') or '')[:10],
                        "content": content,
                        "source_id": rid,
                    }
                    items.append(item)
                print(f"  [{self.name}] 类型 {bulletin_type} 第 {page} 页 {len(rows)} 条，累计 {len(items)}"
                      + (f"（共 {total_pages} 页）" if total_pages else ""))
                page += 1
        return items


class LegacyPublicResourceScraper(BaseScraper):
    """开发区/城南新区旧公共资源交易站 — 静态列表页。"""

    max_pages = 2

    def get_page_url(self, page):
        if page == 1:
            return self.url
        return urljoin(self.base_url, f"/jyxx/{page}.html")

    def parse_list(self, html):
        soup = BeautifulSoup(html, 'lxml')
        items, seen = [], set()
        for tr in soup.select('tbody tr'):
            a = tr.find('a', href=True)
            if not a or '/jyxx/' not in a['href'] or not a['href'].endswith('.html'):
                continue
            full_url = urljoin(self.base_url, a['href'])
            if full_url in seen:
                continue
            seen.add(full_url)
            title = a.get('title') or a.get_text(' ', strip=True)
            title = re.sub(r'^\s*(?:\[[^]]+\]\s*)+', '', title).strip()
            date = ''
            for td in reversed(tr.find_all('td')):
                m = re.search(r'\d{4}-\d{2}-\d{2}', td.get_text(' ', strip=True))
                if m:
                    date = m.group(0)
                    break
            if title:
                items.append({"title": title, "url": full_url, "date": date})
        return items


class YcPublicResourceScraper(BaseScraper):
    """盐城市公共资源交易网 — JSON 接口，正文随列表返回。
    2026-08-10：原 max_pages=2 只抓 40 条，「招标计划（采购意向）」等全部漏抓
    （如盐城城市可信数据空间一期在第 29 页）。改为动态翻页 + 日期早停。
    """

    max_pages = 100  # 上限保护：100 页 × 20 条；6 月边界约在 85 页，日期早停会提前停

    def __init__(self, name, url, base_url, api_url):
        super().__init__(name, url, base_url)
        self.api_url = api_url

    def scrape(self):
        print(f"[{self.name}] 开始抓取 JSON API (max_pages={self.max_pages})")
        items, seen = [], set()
        session = self._get_session()
        for page in range(1, self.max_pages + 1):
            try:
                resp = session.post(self.api_url, json={
                    "size": 20, "current": page, "type": "transactionInfo"
                }, timeout=REQUEST_TIMEOUT)
                resp.raise_for_status()
                rows = resp.json().get('content') or []
            except Exception as e:
                print(f"  [{self.name}] 第 {page} 页 API 失败: {e}")
                continue
            for row in rows:
                key = str(row.get('id') or row.get('code') or '')
                if not key or key in seen:
                    continue
                seen.add(key)
                title = (row.get('title') or '').strip()
                if not title:
                    continue
                content_html = row.get('content') or ''
                content = BeautifulSoup(content_html, 'lxml').get_text(' ', strip=True)[:MAX_DETAIL_CHARS]
                url = f"{self.base_url}/#/detail?id={key}&code={row.get('code','')}&title=交易信息&type=1"
                item = {
                    "title": title, "url": url,
                    "date": (row.get('createTime') or '')[:10],
                    "content": content,
                    "district_code": row.get('areaCode') or '',
                }
                item.update(self.extract_detail_fields(content, title))
                if not item.get('winner'):
                    winner_match = re.search(
                        r'中标单位名称\s*[:：]?\s*(.{3,80}?)\s+(?:项目经理|中标价格|中标价)', content)
                    if winner_match:
                        item['winner'] = winner_match.group(1).strip()
                if not item.get('winning_amount'):
                    amount_match = re.search(r'中标价格\s*[:：]?\s*([\d,.]+)\s*元', content)
                    if amount_match:
                        item['winning_amount'] = str(float(amount_match.group(1).replace(',', '')))
                items.append(item)
            print(f"  [{self.name}] 第 {page} 页新增 {len(rows)} 条，累计 {len(items)} 条")
            # 2026-08-10 日期早停：按页内最新条目判断。
            # 注意不能用最旧条目——该 API 列表混有置顶/补录的旧条目（实测 page15 混入
            # 2024-06-12），按最旧判断会误停导致漏抓（盐城可信数据空间一期因此漏掉）。
            page_dates = [(row.get('createTime') or '')[:10] for row in rows]
            if page_dates and max(page_dates) < self.CUTOFF_DATE:
                print(f"  [{self.name}] 已到 {self.CUTOFF_DATE} 保留线，停止翻页")
                break
        return items


class SufucaiScraper(BaseScraper):
    """江苏省服务类网上商城（苏服采）— 盐城市公开比选与结果公告。

    2026-08-10：原 max_pages=2 只抓 2 页（tender 接口 total=68、公告接口 total=199，
    max_pages=2 只抓 40+20 条，有缺口）。放大到 20 + 日期早停。
    """

    max_pages = 20

    def __init__(self, name, url, base_url, api_url, area_code='320900',
                 area_codes=None, cutoff_date=None, max_pages=1000):
        super().__init__(name, url, base_url)
        self.api_url = api_url.rstrip('/')
        self.area_code = area_code
        # 2026-08-20：多区域码并集（320900 全市 + 320991 经开 + 320992 盐南），
        # 只用 320900 会漏掉区级专属项目（如步凤镇无人机智慧巡查）。
        self.area_codes = area_codes or [area_code]
        self.cutoff_date = cutoff_date
        self.fetch_stats = []
        self.last_fetch_stats = []
        self.last_fetch_params = {}
        if max_pages is not None:
            self.max_pages = max_pages

    def _post(self, endpoint, payload):
        resp = self._get_session().post(
            self.api_url + endpoint, json=payload, timeout=REQUEST_TIMEOUT,
            verify=False, headers={"Origin": self.base_url, "Referer": self.base_url + "/"},
        )
        resp.raise_for_status()
        data = resp.json()
        if not data.get('success'):
            raise RuntimeError(f"苏服采接口业务失败: {data.get('message') or data.get('msg') or data}")
        res = data.get('result') or {}
        total = res.get('total')
        if total is None:
            raise RuntimeError("苏服采接口响应缺少 result.total")
        return (res.get('records') or []), int(total)

    def _row_to_item(self, row, result_row):
        """接口行 → item（比选 result_row=False / 公告 result_row=True）"""
        rid = str(row.get('id') or '')
        title = (row.get('title') or row.get('itemName') or '').strip()
        if not rid or not title:
            return None
        date = (row.get('releaseTime') or row.get('publishTime') or row.get('createTime') or '')[:10]
        need_id = str(row.get('purchasesNeedId') or row.get('pneedId') or rid)
        url = f"{self.base_url}/biddingAnnouncement/{need_id}?type={'2' if result_row else '1'}&serviceType=1"
        # 两路接口都暴露项目级 ID：结果接口是 purchasesNeedId，采购接口是 pneedId。
        # 保留记录 ID 用于公告级去重，项目 ID 单独交给 project_engine 做关联。
        project_key = str(row.get('purchasesNeedId') or row.get('pneedId') or need_id)
        content_parts = [
            title, row.get('notes') or '',
            f"采购人：{row.get('procurementUnit')}" if row.get('procurementUnit') else '',
            f"项目编号：{row.get('itemNo')}" if row.get('itemNo') else '',
        ]
        amount_raw = row.get('transactionPrice') if result_row else row.get('itemBudget')
        if amount_raw is None:
            amount_raw = row.get('itemBudget')
        try:
            amount_val = float(amount_raw) if amount_raw is not None else None
        except (ValueError, TypeError):
            amount_val = None
        if amount_val is not None:
            content_parts.append(f"{'成交金额' if result_row else '项目预算'}：{amount_val:g}元")
        deadline = row.get('tenderEndTime')
        if deadline:
            content_parts.append(f"截止时间：{deadline}")
        if project_key:
            content_parts.append(f"项目ID：{project_key}")
        if row.get('itemPerson'):
            content_parts.append(f"联系人：{row.get('itemPerson')}")
        if row.get('itemPersonTel'):
            content_parts.append(f"联系电话：{row.get('itemPersonTel')}")
        content = ' '.join(str(x) for x in content_parts if x)
        amount_str = f"{amount_val/10000:g}万元" if amount_val is not None else ''
        termination_status = row.get('terminationStatus')
        if termination_status is None:
            termination_status = row.get('pterminationStatus')
        item = {
            "title": title, "url": url, "date": date, "content": content,
            "tenderer": row.get('procurementUnit') or '',
            "bid_deadline": deadline or '',
            "amount": amount_str,
            # 苏服采接口的完整项目信息：此前只拼进 content，导致详情字段无法结构化使用。
            "project_key": project_key,
            "purchases_need_id": project_key,
            "item_no": row.get('itemNo') or '',
            "item_name": row.get('itemName') or row.get('purchasesItemName') or '',
            "contact_person": row.get('itemPerson') or '',
            "contact_phone": row.get('itemPersonTel') or '',
            "area_code": row.get('areaCode') or row.get('area_code') or '',
            "area_code_source": row.get('_area_code_source') or 'api_row',
            "tender_start_time": row.get('tenderStartTime') if row.get('tenderStartTime') is not None else '',
            "transaction_discount": row.get('transactionDiscount') if row.get('transactionDiscount') is not None else '',
            "reason": row.get('reason') if row.get('reason') is not None else '',
            "version": row.get('version') if row.get('version') is not None else '',
            "termination_status": termination_status if termination_status is not None else '',
            "bidding_status": row.get('biddingStatus') if row.get('biddingStatus') is not None else '',
            "source_id": rid,
            "source_key": f"{rid}|{project_key}",
        }
        if result_row and amount_val is not None:
            item["winning_amount"] = amount_str
        # 2026-08-21：保留 API 原始 row（包含 areaCode、itemNo 等原始字段名），
        # 让 raw_json 写存后能被 SUFUCAI_AREA_CODE_MAP 直接映射，不丢原始分类信息。
        item["raw_row"] = dict(row)
        return item

    @staticmethod
    def _bind_area_code(row, request_area_code, require_row_code=False):
        """把 API 分桶编码绑定到记录。

        /page 返回行不带 areaCode，但服务端严格按请求 areaCode 分桶，因此请求桶
        就是 API 原生区域结果；公告接口返回行必须带码且与请求桶一致。
        """
        bound = dict(row)
        row_code = str(bound.get('areaCode') or bound.get('area_code') or '').strip()
        request_area_code = str(request_area_code or '').strip()
        if require_row_code and not row_code:
            raise RuntimeError(f"苏服采公告记录缺少 areaCode（请求桶 {request_area_code}，id={bound.get('id')}）")
        if row_code and row_code != request_area_code:
            raise RuntimeError(
                f"苏服采区域码跨桶（请求 {request_area_code}，返回 {row_code}，id={bound.get('id')}）"
            )
        bound['areaCode'] = row_code or request_area_code
        bound['_area_code_source'] = 'api_row' if row_code else 'api_request_bucket'
        return bound

    def _iter_endpoint(self, endpoint, params):
        """按接口 total/pages 全量翻页，避免固定页数截断。"""
        self.last_fetch_params = dict(params)
        self.last_fetch_params['pageNumber'] = 1
        self.last_fetch_stats = []
        page_size = int(params.get('pageSize') or 20)
        page = 1
        while page <= self.max_pages:
            page_params = dict(params)
            page_params['pageNumber'] = page
            try:
                rows, total = self._post(endpoint, page_params)
            except Exception as e:
                stat = {
                    'endpoint': endpoint, 'type': params.get('type', ''),
                    'area_code': params.get('areaCode', []),
                    'page': page, 'page_size': page_size,
                    'rows': 0, 'total': None, 'error': str(e),
                }
                self.last_fetch_stats.append(stat)
                self.fetch_stats.append(stat)
                break
            stat = {
                'endpoint': endpoint, 'type': params.get('type', ''),
                'area_code': params.get('areaCode', []),
                'page': page, 'page_size': page_size,
                'rows': len(rows), 'total': total, 'error': None,
                'source_ids': [str(row.get('id') or '') for row in rows if row.get('id')],
            }
            self.last_fetch_stats.append(stat)
            self.fetch_stats.append(stat)
            if rows:
                yield rows
            if not rows:
                break
            if total is not None and page * page_size >= total:
                break
            if len(rows) < page_size and total is None:
                break
            page += 1

    def scrape(self):
        print(f"[{self.name}] 开始抓取盐城市公开 API")
        self.fetch_stats = []
        items, seen = [], set()
        source_buckets = {}

        # ── 比选接口 /page：12 个区域码逐桶、按 total 全量翻页 ──
        tender_total = 0
        for area_code in self.area_codes:
            bucket_got = 0
            tender_params = {
                "pageSize": 20, "serviceType": "1", "areaCode": [area_code],
            }
            for tenders in self._iter_endpoint('/page', tender_params):
                for original_row in tenders:
                    row = self._bind_area_code(original_row, area_code, require_row_code=False)
                    it = self._row_to_item(row, False)
                    if not it:
                        continue
                    key = 'tender:' + it['source_id']
                    previous = source_buckets.get(key)
                    if previous and previous != area_code:
                        raise RuntimeError(f"苏服采比选项目跨区域重复：{key} 同时属于 {previous}/{area_code}")
                    source_buckets[key] = area_code
                    if key in seen:
                        continue
                    seen.add(key)
                    items.append(it)
                    bucket_got += 1
                page_dates = [(row.get('publishTime') or row.get('createTime') or '')[:10] for row in tenders]
                if self.cutoff_date and page_dates and max(page_dates) < self.cutoff_date:
                    print(f"  [{self.name}] 比选 area={area_code} 已到 {self.cutoff_date} 保留线，停止翻页")
                    break
            stats = self.last_fetch_stats
            api_total = stats[0]['total'] if stats and stats[0].get('total') is not None else None
            if api_total is None or bucket_got != api_total:
                raise RuntimeError(
                    f"苏服采比选 area={area_code} 抓取不完整：total={api_total} fetched={bucket_got}"
                )
            tender_total += bucket_got
            print(f"  [{self.name}] 比选 area={area_code} 全量 {bucket_got} 条")
        print(f"  [{self.name}] 比选接口 12 区域合计 {tender_total} 条")

        # ── 公告接口：12 个区域码 × 4 个 type 逐桶抓取 ──
        notice_types = ["2", "3", "4", "5"]
        for nt in notice_types:
            type_total = 0
            for area_code in self.area_codes:
                bucket_got = 0
                notice_params = {
                    "pageSize": 20, "serviceType": "1", "areaCode": [area_code],
                    "order": "desc", "sort": "releaseTime", "type": nt,
                }
                for notices in self._iter_endpoint('/announcementResultList', notice_params):
                    for original_row in notices:
                        row = self._bind_area_code(original_row, area_code, require_row_code=True)
                        it = self._row_to_item(row, True)
                        if not it:
                            continue
                        key = 'result:' + it['source_id']
                        previous = source_buckets.get(key)
                        if previous and previous != area_code:
                            raise RuntimeError(f"苏服采公告跨区域重复：{key} 同时属于 {previous}/{area_code}")
                        source_buckets[key] = area_code
                        if key in seen:
                            continue
                        seen.add(key)
                        items.append(it)
                        bucket_got += 1
                    page_dates = [(row.get('releaseTime') or row.get('publishTime') or row.get('createTime') or '')[:10]
                                  for row in notices]
                    if self.cutoff_date and page_dates and max(page_dates) < self.cutoff_date:
                        print(f"  [{self.name}] 公告 type={nt} area={area_code} 已到保留线，停止翻页")
                        break
                stats = self.last_fetch_stats
                api_total = stats[0]['total'] if stats and stats[0].get('total') is not None else None
                if api_total is None or bucket_got != api_total:
                    raise RuntimeError(
                        f"苏服采公告 type={nt} area={area_code} 抓取不完整："
                        f"total={api_total} fetched={bucket_got}"
                    )
                type_total += bucket_got
                print(f"  [{self.name}] 公告 type={nt} area={area_code} 全量 {bucket_got} 条")
            print(f"  [{self.name}] 公告 type={nt} 12 区域合计 {type_total} 条")
        print(f"  [{self.name}] 合计 {len(items)} 条（比选+公告）")
        return items


# ==================== 新增外部平台 ====================


class YdSunshineScraper(BaseScraper):
    """悦达阳光采购平台 — a 标题 + p 兄弟节点含日期/状态
    翻页：.../zbgg/index.jhtml → .../zbgg/index_N.jhtml
    2026-08-10：原 max_pages=2 只抓 2 页（40 条），网站实际 3578 条/358 页。
    放大到 50（6 月边界约在前 10 页，日期早停会提前停）。
    """

    max_pages = 50

    def get_page_url(self, page):
        if page == 1:
            return self.url
        # https://www.ydtender.com/zbgg/index.jhtml → https://www.ydtender.com/zbgg/index_2.jhtml
        if self.url.endswith('.jhtml'):
            return self.url[:-6] + f'_{page}.jhtml'
        return self.url

    def parse_list(self, html):
        soup = BeautifulSoup(html, 'lxml')
        items = []
        seen = set()

        for a in soup.find_all('a', href=True):
            href = a['href']
            if not re.search(r'/z[a-z]+gg/\d+\.jhtml', href):
                continue
            if href in seen:
                continue
            seen.add(href)

            full_url = urljoin(self.base_url, href)
            title = a.get_text(strip=True)
            p = a.find_next_sibling('p')
            ptext = p.get_text() if p else ''

            date = ""
            dm = re.search(r'发布时间：(\d{4}-\d{2}-\d{2})', ptext)
            if dm:
                date = dm.group(1)

            status = ""
            sm = re.search(r'报名状态：\[([^\]]+)\]', ptext)
            if sm:
                status = sm.group(1)

            if len(title) > 5:
                items.append({"title": title, "url": full_url, "date": date, "status": status})

        return items


class ChinaPostScraper(BaseScraper):
    """中国邮政电子采购平台 — a 标题，URL 含日期

    翻页：首页 JS 加载，未抓到可靠分页链接。
    max_pages=1，保持只抓第 1 页。
    """

    max_pages = 1

    def parse_list(self, html):
        soup = BeautifulSoup(html, 'lxml')
        items = []
        seen = set()

        for a in soup.find_all('a', href=True):
            href = a['href']
            if 'biddingBulletin' not in href:
                continue
            if href in seen:
                continue
            seen.add(href)

            full_url = urljoin(self.base_url, href)
            title = a.get('title', '') or a.get_text(strip=True)
            date = ""
            dm = re.search(r'/(\d{4}-\d{2}-\d{2})/', href)
            if dm:
                date = dm.group(1)

            if len(title) > 5:
                items.append({"title": title, "url": full_url, "date": date})

        return items


class CtgScraper(BaseScraper):
    """三峡集团电子采购平台 — ul#list1 > li > a 标题+日期

    翻页：分页链接为 javascript:;，需 JS 渲染才能跳转，故不抓第 2 页。
    max_pages=1，保持只抓第 1 页。
    """

    max_pages = 1

    def parse_list(self, html):
        soup = BeautifulSoup(html, 'lxml')
        items = []
        seen = set()

        ul = soup.find('ul', id='list1')
        if not ul:
            return items

        for li in ul.find_all('li'):
            a = li.find('a', href=True)
            if not a:
                continue
            href = a['href']
            if href in seen:
                continue
            seen.add(href)

            full_url = urljoin(self.base_url, href)
            title = a.get_text(strip=True)
            text = li.get_text(strip=True)
            date = ""
            dm = re.search(r'(\d{4}-\d{2}-\d{2})', text)
            if dm:
                date = dm.group(1)
            title = re.sub(r'\d{4}-\d{2}-\d{2}$', '', title).strip()

            if len(title) > 5:
                items.append({"title": title, "url": full_url, "date": date})

        return items


class JsNonghuaScraper(BaseScraper):
    """江苏农华（江动）— a.infofl > div.infot1 标题 + div.infot2 日期
    翻页：.../type_id/8.html → .../type_id/8.html?type_id=8&page=N
    """

    max_pages = 2

    def get_page_url(self, page):
        if page == 1:
            return self.url
        # http://jd.dongyin.com/index/zhaobiao/index/type_id/8.html → ?type_id=8&page=N
        # 从原始 URL 提取 type_id
        import re as _re
        m = _re.search(r'type_id/(\d+)\.html', self.url)
        type_id = m.group(1) if m else '8'
        # 保留原 URL（去掉已有 query），再加 ?type_id=X&page=N
        base = self.url.split('?')[0]
        return f'{base}?type_id={type_id}&page={page}'

    def parse_list(self, html):
        soup = BeautifulSoup(html, 'lxml')
        items = []
        seen = set()

        for a in soup.find_all('a', class_='infofl', href=True):
            href = a['href']
            if href in seen:
                continue
            seen.add(href)

            full_url = urljoin(self.base_url, href)
            t1 = a.find('div', class_='infot1')
            t2 = a.find('div', class_='infot2')
            title = t1.get_text(strip=True) if t1 else a.get_text(strip=True)
            date = t2.get_text(strip=True) if t2 else ''

            if len(title) > 5:
                # 2026-08-17：发包人按标题归属硬编码（企业内采，公司恒定）
                # 东葵子公司项目 → 盐城东葵科技有限公司；其余 → 江苏农华智慧农业科技股份有限公司
                if "东葵" in title:
                    tenderer = "盐城东葵科技有限公司"
                else:
                    tenderer = "江苏农华智慧农业科技股份有限公司"
                items.append({"title": title, "url": full_url, "date": date, "tenderer": tenderer})

        return items


class SdiccScraper(BaseScraper):
    """国投集团电子采购平台 — tr onclick=urlChange(ggGuid, gcGuid), td 含标题和日期

    翻页：列表为 POST 表单（currentPage 参数），2026-08-07 已逆向，支持翻页。
    详情页需 gcGuid + ggGuid 双参数，缺一会被 WAF 拦截（403 违反安全规则）。
    """

    max_pages = 3

    def get_page_url(self, page):
        # 列表是 POST 表单，用 fragment 携带页码，fetch() 里拦截改 POST
        return f"{self.url}#page={page}"

    def fetch(self, url):
        # 拦截列表页请求：改走 POST + currentPage（2026-08-07）
        if "#page=" in url:
            page = url.split("#page=")[1]
            session = self._get_session()
            try:
                resp = session.post(self.url, data={"currentPage": page},
                                    timeout=REQUEST_TIMEOUT)
                resp.encoding = resp.apparent_encoding or resp.encoding
                if resp.status_code == 200 and resp.text:
                    return resp.text
            except Exception as e:
                print(f"  [{self.name}] POST 列表第 {page} 页失败: {e}")
            return ""
        return super().fetch(url)

    def parse_list(self, html):
        soup = BeautifulSoup(html, 'lxml')
        items = []
        seen = set()

        for tr in soup.find_all('tr', onclick=True):
            onclick = tr.get('onclick', '')
            if 'urlChange' not in onclick:
                continue

            m = re.search(r"urlChange\('([^']+)'\s*,\s*'([^']+)'", onclick)
            if not m:
                continue
            gg_guid, gc_guid = m.group(1), m.group(2)
            if not gg_guid or gg_guid in seen:
                continue
            seen.add(gg_guid)

            tds = tr.find_all('td')
            title = ''
            date = ''
            for td in tds:
                t = td.get_text(strip=True)
                if re.match(r'\d{4}-\d{2}-\d{2}', t):
                    date = t
                elif len(t) > 5:
                    title = t

            full_url = urljoin(self.base_url,
                               f'/cgxx/ggDetail?gcGuid={gc_guid}&ggGuid={gg_guid}')

            if title:
                items.append({"title": title, "url": full_url, "date": date})

        return items


# ==================== 中粮e采（JSON API） ====================


class CofcoEScraper(BaseScraper):
    """中粮e采 — 筑龙网 SPA，通过逆向 API 抓取
    API: POST /api/saas-portal/noauth/trans/trade/pageEs
    只抓名称含「盐城」且采购类型为 询比采购/谈判采购/公开招标 的项目（不分集中/分散）
    """

    # 允许的采购类型关键词（匹配前缀即可，不区分集中/分散）
    ALLOWED_METHOD_PREFIXES = ("询比采购", "谈判采购", "公开招标")
    DETAIL_API = "/api/saas-portal/noauth/trans/trade/getByTradeId"

    DETAIL_TEXT_FIELDS = (
        "projectOverview", "biddingScope", "qualificationRequirement",
        "qualificationLevel", "remark", "changeReason", "changeContent",
        "exceptionReason", "bidResult", "winBidPrinciple", "otherInfo",
    )

    def _collect_detail_text(self, value, key="", seen=None):
        """从详情 JSON 提取业务正文；排除下载链接和重复的嵌套字段。"""
        seen = seen if seen is not None else set()
        parts = []
        if isinstance(value, dict):
            for child_key, child_value in value.items():
                if child_key in self.DETAIL_TEXT_FIELDS:
                    if isinstance(child_value, str):
                        text = re.sub(r"\s+", " ", child_value).strip()
                        if text and text not in seen:
                            seen.add(text)
                            parts.append(f"{child_key}: {text}")
                    elif child_key == "changeContent":
                        parts.extend(self._collect_detail_text(child_value, child_key, seen))
                elif isinstance(child_value, (dict, list)):
                    parts.extend(self._collect_detail_text(child_value, child_key, seen))
        elif isinstance(value, list):
            for child in value:
                parts.extend(self._collect_detail_text(child, key, seen))
        return parts

    def _collect_attachments(self, value, found=None):
        found = found if found is not None else []
        if isinstance(value, dict):
            if value.get("downLoadUrl"):
                item = {"name": value.get("fileName", ""), "url": value["downLoadUrl"]}
                if item not in found:
                    found.append(item)
            for child in value.values():
                self._collect_attachments(child, found)
        elif isinstance(value, list):
            for child in value:
                self._collect_attachments(child, found)
        return found

    def _fetch_trade_detail(self, raw):
        params = {
            "id": str(raw.get("id", "")),
            "noticeType": raw.get("noticeType"),
            "publishStatus": raw.get("publishStatus"),
        }
        resp = self._get_session().get(
            urljoin(self.base_url, self.DETAIL_API), params=params,
            headers={"Accept": "application/json, text/plain, */*"},
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        payload = resp.json()
        if not payload.get("success") or not payload.get("data"):
            return "", [], {}
        data = payload["data"]
        content = "\n".join(self._collect_detail_text(data))[:MAX_DETAIL_CHARS]
        return content, self._collect_attachments(data), data

    def scrape(self):
        """重写主流程：直接调 JSON API，不走 HTML 列表+详情页"""
        print(f"[{self.name}] 开始抓取 API: {self.url}")
        items = []
        seen = set()

        session = self._get_session()
        # JSON API 需要正确的 Accept header
        session.headers['Accept'] = 'application/json, text/plain, */*'

        page = 1
        page_size = 50
        max_pages = 20  # 最多翻 20 页 = 1000 条

        while page <= max_pages:
            try:
                resp = session.post(
                    self.url,
                    # 2026-08-19：接口分页参数是 pageNum（pageNo 被忽略 → 永远只返回第一页）
                    # 此前用 pageNo 导致中粮e采只抓到前 50 条，08 月新公告全部漏抓。
                    json={"pageNum": page, "pageSize": page_size, "businessName": "盐城"},
                    timeout=REQUEST_TIMEOUT,
                )
                if resp.status_code != 200:
                    print(f"  [{self.name}] HTTP {resp.status_code}，停止翻页")
                    break

                data = resp.json()
                if not data.get("success"):
                    print(f"  [{self.name}] API 返回失败: {data.get('errMessage')}")
                    break

                page_data = data.get("data", {})
                lst = page_data.get("list", [])
                if not lst:
                    break

                for raw in lst:
                    biz_name = raw.get("businessName", "")
                    method = raw.get("purchasingMethodName", "")

                    # 过滤：名称含「盐城」且类型在允许列表（前缀匹配，不区分集中/分散）
                    if "盐城" not in biz_name:
                        continue
                    if not method or not method.startswith(self.ALLOWED_METHOD_PREFIXES):
                        continue

                    biz_id = raw.get("businessId", "")
                    item_id = raw.get("id", "")
                    guid = biz_id or item_id
                    if guid in seen:
                        continue
                    seen.add(guid)

                    # 时间戳 → 日期
                    release_ts = raw.get("releaseTime")
                    bid_end_ts = raw.get("bidEndTime")
                    date_str = ""
                    bid_deadline = ""
                    if release_ts:
                        date_str = datetime.fromtimestamp(
                            release_ts / 1000).strftime("%Y-%m-%d")
                    if bid_end_ts:
                        bid_deadline = datetime.fromtimestamp(
                            bid_end_ts / 1000).strftime("%Y年%m月%d日 %H:%M")

                    # URL 保持旧格式，避免稳定 ID 变化；actual_detail_url 记录正确详情路由。
                    detail_url = f"{self.base_url}/web-portal/index.html#/trade-info?tradeId={biz_id}"
                    actual_detail_url = (
                        f"{self.base_url}/web-portal/index.html#/trade-info-detail"
                        f"?id={item_id}&noticeType={raw.get('noticeType')}"
                        f"&publishStatus={raw.get('publishStatus')}"
                    )

                    content = ""
                    attachments = []
                    detail_data = {}
                    try:
                        content, attachments, detail_data = self._fetch_trade_detail(raw)
                    except Exception as e:
                        print(f"  [{self.name}] 详情接口失败 {item_id}: {e}")

                    fields = self.extract_detail_fields(content, biz_name) if content else {}

                    item = {
                        "title": biz_name,
                        "url": detail_url,
                        "actual_detail_url": actual_detail_url,
                        "date": date_str,
                        "tenderer": raw.get("tendererName", ""),
                        "amount": "",
                        "bid_deadline": bid_deadline,
                        "content": content,
                        "purchase_method": method,
                        "detail_attachments": attachments,
                        "detail_api_id": str(item_id),
                        "detail_notice_type": raw.get("noticeType"),
                        "detail_publish_status": raw.get("publishStatus"),
                    }
                    item.update({k: v for k, v in fields.items() if v not in (None, "")})
                    # API 列表字段比正文启发式更可靠。
                    item["tenderer"] = raw.get("tendererName", "") or item.get("tenderer", "")
                    item["bid_deadline"] = bid_deadline or item.get("bid_deadline", "")
                    items.append(item)
                    time.sleep(DETAIL_DELAY)

                total = page_data.get("totalCount", 0)
                fetched = page * page_size
                if fetched >= total:
                    break

                page += 1
                time.sleep(REQUEST_DELAY)

            except requests.exceptions.Timeout:
                print(f"  [{self.name}] 第 {page} 页超时，停止")
                break
            except Exception as e:
                print(f"  [{self.name}] 第 {page} 页异常: {e}")
                break

        print(f"  [{self.name}] 筛选到 {len(items)} 条盐城相关")
        return items


# ==================== 一汽电子采购（POST 搜索 + HTML 解析） ====================


class FawScraper(BaseScraper):
    """一汽电子招标采购交易平台 — POST 搜索"盐城"，服务端渲染 HTML
    列表页 POST searchType=5(招标人)+searchText=盐城，分页遍历
    """

    DETAIL_API = "/yq-xunjia-menhu/gonggaoxinxi/queryXiangMuByGuid.do"

    def _fetch_faw_detail(self, guid):
        resp = self._get_session().get(
            urljoin(self.base_url, self.DETAIL_API),
            params={"guid": guid},
            headers={"Accept": "application/json, text/javascript, */*; q=0.01",
                     "X-Requested-With": "XMLHttpRequest"},
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
        contents = []
        budgets = []
        for package in data.get("lsBaoJian") or []:
            text = re.sub(r"\s+", " ", package.get("gongGaoNeiRong") or "").strip()
            if text and text not in contents:
                contents.append(text)
            budget = package.get("caiGouGuSuanJia")
            if isinstance(budget, (int, float)) and budget > 0:
                budgets.append(float(budget))
        content = "\n".join(contents)[:MAX_DETAIL_CHARS]
        fields = {}
        if data.get("caiGouRenName"):
            fields["tenderer"] = data["caiGouRenName"]
        end_ts = data.get("zuiZhongGongGaoEndTime") or data.get("gongGaoEndTime")
        if end_ts:
            fields["bid_deadline"] = datetime.fromtimestamp(
                end_ts / 1000).strftime("%Y年%m月%d日 %H:%M")
        if budgets:
            total = sum(budgets)
            fields["amount"] = f"{total / 10000:.2f}万元" if total >= 10000 else f"{total:.2f}元"
        return content, fields, data

    def scrape(self):
        """重写主流程：POST 搜索 + HTML 解析，不走 BaseScraper.fetch(GET)"""
        print(f"[{self.name}] 开始抓取 {self.url}")
        items = []
        seen = set()
        session = self._get_session()

        page = 1
        max_pages = 20

        while page <= max_pages:
            try:
                resp = session.post(
                    self.url,
                    data={
                        "searchType": "5",
                        "searchText": "盐城",
                        "currentPage": str(page),
                    },
                    timeout=REQUEST_TIMEOUT,
                )
                if resp.status_code != 200 or not resp.text:
                    print(f"  [{self.name}] HTTP {resp.status_code}，停止翻页")
                    break

                html = resp.text
                soup = BeautifulSoup(html, 'lxml')
                cards = soup.find_all('div', class_='zl-list-type-2')
                if not cards:
                    break

                for card in cards:
                    try:
                        onclick = card.get('onclick', '')
                        m = re.search(r"toGgDetails\(\'([^']+)\',\'([^']+)\'", onclick)
                        if not m:
                            continue
                        gongGaoType = m.group(1)
                        guid = m.group(2)
                        if guid in seen:
                            continue
                        seen.add(guid)

                        title_el = card.find('div', class_='title')
                        title = title_el.get_text(strip=True) if title_el else ''

                        date = ''
                        company = ''
                        desc_items = card.find_all('div', class_='zl-desc-item')
                        for di in desc_items:
                            text = di.get_text(strip=True)
                            if '发布时间' in text:
                                dm = re.search(r'(\d{4}-\d{2}-\d{2})', text)
                                if dm:
                                    date = dm.group(1)
                            elif text and '项目编号' not in text and '标段' not in text:
                                company = text

                        detail_url = urljoin(
                            self.base_url,
                            f"/gg/toGongGaoDetail?guid={guid}&gongGaoType={gongGaoType}&version=&isNew=1"
                        )

                        if title:
                            content = ""
                            detail_fields = {}
                            detail_data = {}
                            try:
                                content, detail_fields, detail_data = self._fetch_faw_detail(guid)
                            except Exception as e:
                                print(f"  [{self.name}] 详情接口失败 {guid}: {e}")
                            fields = self.extract_detail_fields(content, title) if content else {}
                            item = {
                                "title": title,
                                "url": detail_url,
                                "date": date,
                                "tenderer": company,
                                "amount": "",
                                "bid_deadline": "",
                                "content": content,
                                "detail_api_guid": guid,
                            }
                            item.update({k: v for k, v in fields.items() if v not in (None, "")})
                            item.update({k: v for k, v in detail_fields.items() if v not in (None, "")})
                            items.append(item)
                            time.sleep(DETAIL_DELAY)
                    except Exception as e:
                        print(f"  [{self.name}] 单条解析异常: {e}")
                        continue

                print(f"  [{self.name}] 第 {page} 页，累计 {len(items)} 条")

                # 检查是否还有下一页
                pagination = soup.find('input', id='currentPageInput')
                if pagination:
                    max_page = pagination.get('max')
                    if max_page and int(max_page) <= page:
                        break
                else:
                    break

                page += 1
                time.sleep(REQUEST_DELAY)

            except requests.exceptions.Timeout:
                print(f"  [{self.name}] 第 {page} 页超时，停止")
                break
            except Exception as e:
                print(f"  [{self.name}] 第 {page} 页异常: {e}")
                break

        print(f"  [{self.name}] 筛选到 {len(items)} 条盐城相关")
        return items


# ==================== 奇瑞电子采购（JSON API + city 过滤） ====================


class CheryScraper(BaseScraper):
    """奇瑞集团采购服务平台 — JSON API，按 city=320900（盐城市）过滤
    API: POST /cms/api/dynamicData/queryContentPage
    """

    def scrape(self):
        """重写主流程：POST JSON API，不走 BaseScraper.fetch(GET)"""
        print(f"[{self.name}] 开始抓取 {self.url}")
        items = []
        seen = set()
        session = self._get_session()
        session.headers['Accept'] = 'application/json, text/plain, */*'

        page = 1
        page_size = 50
        max_pages = 20

        while page <= max_pages:
            try:
                resp = session.post(
                    self.url,
                    json={
                        "pageNo": page,
                        "pageSize": page_size,
                        "dto": {
                            "siteId": "747",
                            "categoryId": "5035",
                            "city": "320900",
                        },
                    },
                    timeout=REQUEST_TIMEOUT,
                )
                if resp.status_code != 200 or not resp.text:
                    print(f"  [{self.name}] HTTP {resp.status_code}，停止翻页")
                    break

                data = resp.json()
                res = data.get('res', {})
                total = res.get('total', 0)
                rows = res.get('rows', [])
                if not rows:
                    break

                for row in rows:
                    try:
                        title = row.get('title', '').strip()
                        url_path = row.get('url', '')
                        if not title or not url_path:
                            continue

                        detail_url = urljoin(self.base_url, url_path)
                        if detail_url in seen:
                            continue
                        seen.add(detail_url)

                        raw_date = row.get('publishDate', '')
                        date = ''
                        if raw_date:
                            dm = re.search(r'(\d{4}-\d{2}-\d{2})', raw_date)
                            if dm:
                                date = dm.group(1)

                        # 报名截止时间
                        bid_deadline = ''
                        end_time = row.get('quoteEndTime', '') or row.get('signUpEndTime', '')
                        if end_time:
                            dm = re.search(r'(\d{4}-\d{2}-\d{2})', end_time)
                            if dm:
                                bid_deadline = dm.group(1)

                        # 正文摘要
                        content_text = row.get('description', '') or row.get('text', '')
                        if content_text and len(content_text) > MAX_DETAIL_CHARS:
                            content_text = content_text[:MAX_DETAIL_CHARS]

                        items.append({
                            "title": title,
                            "url": detail_url,
                            "date": date,
                            "tenderer": row.get('agentCompanyName', ''),
                            "amount": "",
                            "bid_deadline": bid_deadline,
                            "content": content_text,
                        })
                    except Exception as e:
                        print(f"  [{self.name}] 单条解析异常: {e}")
                        continue

                total_pages = (total + page_size - 1) // page_size
                print(f"  [{self.name}] 第 {page}/{total_pages} 页，累计 {len(items)} 条")

                if page * page_size >= total:
                    break

                page += 1
                time.sleep(REQUEST_DELAY)

            except requests.exceptions.Timeout:
                print(f"  [{self.name}] 第 {page} 页超时，停止")
                break
            except Exception as e:
                print(f"  [{self.name}] 第 {page} 页异常: {e}")
                break

        print(f"  [{self.name}] 筛选到 {len(items)} 条盐城相关")
        return items


# ==================== 注册 ====================


def get_all_scrapers():
    """获取所有站点抓取器"""
    from config import SITES

    # 尝试导入江苏政府采购网 CDP 抓取器
    try:
        from ccgp_scraper import CcgpJiangsuScraper
        HAS_CCGP = True
    except ImportError:
        HAS_CCGP = False

    scrapers = {
        # --- 原有盐城本地平台 ---
        "盐城市大数据集团": YcDataGroupScraper(
            "盐城市大数据集团", SITES["盐城市大数据集团"]["url"], SITES["盐城市大数据集团"]["base_url"]),
        "盐城市都市建设投资集团有限公司": YcDsjtScraper(
            "盐城市都市建设投资集团有限公司", SITES["盐城市都市建设投资集团有限公司"]["url"], SITES["盐城市都市建设投资集团有限公司"]["base_url"]),
        "江苏世纪新城": CommonListScraper(
            "江苏世纪新城", SITES["江苏世纪新城"]["url"], SITES["江苏世纪新城"]["base_url"]),
        "盐城市东方集团": CommonListScraper(
            "盐城市东方集团", SITES["盐城市东方集团"]["url"], SITES["盐城市东方集团"]["base_url"]),
        "经开城发集团": CommonListScraper(
            "经开城发集团", SITES["经开城发集团"]["url"], SITES["经开城发集团"]["base_url"]),
        # --- 新增外部平台 ---
        "悦达阳光": YdSunshineScraper(
            "悦达阳光", SITES["悦达阳光"]["url"], SITES["悦达阳光"]["base_url"]),
        "中国邮政": ChinaPostScraper(
            "中国邮政", SITES["中国邮政"]["url"], SITES["中国邮政"]["base_url"]),
        "三峡集团": CtgScraper(
            "三峡集团", SITES["三峡集团"]["url"], SITES["三峡集团"]["base_url"]),
        "江苏农华": JsNonghuaScraper(
            "江苏农华", SITES["江苏农华"]["url"], SITES["江苏农华"]["base_url"]),
        "国投集团": SdiccScraper(
            "国投集团", SITES["国投集团"]["url"], SITES["国投集团"]["base_url"]),
        # --- 中粮e采（JSON API） ---
        "中粮e采": CofcoEScraper(
            "中粮e采", SITES["中粮e采"]["api_url"], SITES["中粮e采"]["base_url"]),
        # --- 一汽电子采购（POST 搜索 + HTML） ---
        "一汽电子采购": FawScraper(
            "一汽电子采购", SITES["一汽电子采购"]["url"], SITES["一汽电子采购"]["base_url"]),
        # --- 奇瑞电子采购（JSON API + city 过滤） ---
        "奇瑞电子采购": CheryScraper(
            "奇瑞电子采购", SITES["奇瑞电子采购"]["api_url"], SITES["奇瑞电子采购"]["base_url"]),
        # --- 表格补充的盐城本地来源 ---
        "盐城市政府采购网": YcGovProcurementScraper(
            "盐城市政府采购网", SITES["盐城市政府采购网"]["url"], SITES["盐城市政府采购网"]["base_url"]),
        "盐城市人民政府网": YanchengGovScraper(
            "盐城市人民政府网", SITES["盐城市人民政府网"]["url"],
            SITES["盐城市人民政府网"]["base_url"], SITES["盐城市人民政府网"]["list_url"]),
        "中国政府采购网": ChinaGovProcurementScraper(
            "中国政府采购网", SITES["中国政府采购网"]["url"], SITES["中国政府采购网"]["base_url"]),
        "江苏招标采购服务平台": JszbcgScraper(
            "江苏招标采购服务平台", SITES["江苏招标采购服务平台"]["url"],
            SITES["江苏招标采购服务平台"]["base_url"], SITES["江苏招标采购服务平台"]["api_url"],
            SITES["江苏招标采购服务平台"]["region_code"]),
        "盐城市公共资源交易网": YcPublicResourceScraper(
            "盐城市公共资源交易网", SITES["盐城市公共资源交易网"]["url"],
            SITES["盐城市公共资源交易网"]["base_url"], SITES["盐城市公共资源交易网"]["api_url"]),
        "开发区公共资源交易网": LegacyPublicResourceScraper(
            "开发区公共资源交易网", SITES["开发区公共资源交易网"]["url"], SITES["开发区公共资源交易网"]["base_url"]),
        "城南新区公共资源交易网": LegacyPublicResourceScraper(
            "城南新区公共资源交易网", SITES["城南新区公共资源交易网"]["url"], SITES["城南新区公共资源交易网"]["base_url"]),
        "苏服采": SufucaiScraper(
            "苏服采", SITES["苏服采"]["url"], SITES["苏服采"]["base_url"],
            SITES["苏服采"]["api_url"], SITES["苏服采"]["area_code"],
            area_codes=SITES["苏服采"].get("area_codes"),
            cutoff_date=SITES["苏服采"].get("cutoff_date"),
            max_pages=SITES["苏服采"].get("max_pages", 1000)),
    }

    # 注册江苏政府采购网（如果可用）
    if HAS_CCGP:
        scrapers["江苏政府采购网"] = CcgpJiangsuScraper("江苏政府采购网", use_cdp=True)
    return scrapers
