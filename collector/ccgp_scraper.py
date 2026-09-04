# -*- coding: utf-8 -*-
"""江苏政府采购网 CDP 浏览器自动化抓取器

站点：http://www.ccgp-jiangsu.gov.cn/jiangsu/cggg_search.html?lmid=cggg&qh=notic_c3
抓取方式：Chrome DevTools Protocol (CDP) 浏览器自动化
筛选条件：盐城经济开发区 (320991) + 盐南高新技术产业开发区 (320992)

架构：
1. 启动/连接 Chromium 浏览器（headless）
2. 通过 CDP 打开目标页面，等待 JS 渲染完成
3. 获取验证码图片 → OCR 识别 → 填入 → 搜索
4. 提取搜索结果列表（标题、日期、区域、详情页链接）
5. 翻页直到所有数据抓取完毕
6. 抓取详情页内容，提取结构化字段

依赖：
- websocket-client (已安装)
- Chromium 浏览器（需安装，见 setup_chromium.sh）
- tesseract-ocr（用于验证码识别，备选）
"""

import re
import time
import json
import base64
import hashlib
import subprocess
import os
import sys
import struct
import tempfile
import uuid
from datetime import datetime
from io import BytesIO
from urllib.parse import urljoin, urlencode, parse_qs, urlparse

# --- CDP 协议基础 ---
import websocket

# 尽量导入可选依赖
try:
    from PIL import Image
    HAS_PIL = True
except ImportError:
    HAS_PIL = False

try:
    import ddddocr
    HAS_DDDDOCR = True
except ImportError:
    HAS_DDDDOCR = False

# 项目配置
from config import (
    REQUEST_HEADERS, REQUEST_TIMEOUT, REQUEST_DELAY,
    MAX_RETRIES, RETRY_BACKOFF, DETAIL_DELAY, MAX_DETAIL_CHARS,
)

# ==================== 常量 ====================

# 目标站点
BASE_URL = "http://www.ccgp-jiangsu.gov.cn"
SEARCH_PAGE = "/jiangsu/cggg_search.html?lmid=cggg&qh=notic_c3"
SEARCH_API = "/pss/jsp/search_cggg.jsp"
CAPTCHA_URL = "/pss/servlet/validateCodeServlet"
ZONE_API = "/pss/jsp/getNewArea.jsp"

# 盐城地区编码
YC_CODE = "320900"          # 盐城市
YC_ECONOMIC_ZONE = "320991"  # 盐城经济开发区
YC_SOUTH_HIGH_TECH = "320992"  # 盐南高新技术产业开发区

# 2026-08-20：盐城 9 个区县 + 经开/盐南高新区（11 个码全量）补全。
# 之前只配 320991+320992，导致阜宁/盐都/亭湖/大丰/东台/建湖/射阳/滨海/响水
# 这 8 个区县的政府采购公告完全漏抓（实测数据 0 条 vs 盐开区 647 条）。
YC_DISTRICT_CODES = [
    "320902",  # 亭湖区
    "320903",  # 盐都区
    "320904",  # 大丰区
    "320921",  # 响水县
    "320922",  # 滨海县
    "320923",  # 阜宁县
    "320924",  # 射阳县
    "320925",  # 建湖县
    "320981",  # 东台市
    YC_ECONOMIC_ZONE,   # 320991 经开
    YC_SOUTH_HIGH_TECH, # 320992 盐南
]

# 目标区域（盐城全部 11 个区县，2026-08-20 补全）
TARGET_ZONES = YC_DISTRICT_CODES

# 按接口 total 动态翻页；100 页只是接口异常时的安全护栏，不再固定截断 25 页。
MAX_PAGES_SAFETY = 100

# 公告类型（2026-08-07 扩展：原仅采购公告 cggg，现增加中标/成交/更正，补齐历史中标数据）
NOTICE_TYPE = "cggg"  # 兼容旧引用
NOTICE_TYPES = [
    ("cggg", "采购公告"),
    ("zbgg", "中标公告"),
    ("cjgg", "成交公告"),
    ("gzgg", "更正公告"),
]

# ==================== CDP 工具函数 ====================


class CDPConnection:
    """Chrome DevTools Protocol 连接管理器"""

    def __init__(self, host="127.0.0.1", port=9222):
        self.host = host
        self.port = port
        self.ws = None
        self._msg_id = 0
        self._pending = {}

    def _get_ws_url(self):
        """通过 HTTP 获取 WebSocket 调试 URL"""
        try:
            import requests
            resp = requests.get(f"http://{self.host}:{self.port}/json/version", timeout=5)
            if resp.status_code == 200:
                data = resp.json()
                return data.get("webSocketDebuggerUrl")
        except Exception:
            pass
        # 回退：获取页面列表
        try:
            import requests
            resp = requests.get(f"http://{self.host}:{self.port}/json", timeout=5)
            if resp.status_code == 200:
                pages = resp.json()
                if pages:
                    return pages[0].get("webSocketDebuggerUrl")
        except Exception:
            pass
        return None

    def connect(self):
        """连接到 CDP"""
        ws_url = self._get_ws_url()
        if not ws_url:
            raise ConnectionError(f"无法连接到 CDP: http://{self.host}:{self.port}")

        self.ws = websocket.create_connection(ws_url, timeout=10)
        # 启用必要的域
        self._send_cmd("Page.enable")
        self._send_cmd("Runtime.enable")
        self._send_cmd("DOM.enable")
        self._send_cmd("Network.enable")
        return True

    def _send_cmd(self, method, params=None):
        """发送 CDP 命令"""
        self._msg_id += 1
        msg = {"id": self._msg_id, "method": method}
        if params:
            msg["params"] = params
        self.ws.send(json.dumps(msg))
        # 等待响应
        while True:
            resp = json.loads(self.ws.recv())
            if resp.get("id") == self._msg_id:
                return resp.get("result", {})
            # 处理事件
            if "method" in resp:
                if resp["method"] == "Runtime.consoleAPICalled":
                    args = resp.get("params", {}).get("args", [])
                    for arg in args:
                        if arg.get("type") == "string":
                            pass  # debug logging
                elif resp["method"] == "Runtime.exceptionThrown":
                    details = resp.get("params", {}).get("exceptionDetails", {})
                    if details.get("text"):
                        pass  # debug logging

    def navigate(self, url):
        """导航到指定 URL"""
        result = self._send_cmd("Page.navigate", {"url": url})
        return result.get("frameId")

    def wait_page_loaded(self, timeout=15):
        """等待页面加载完成"""
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                result = self._send_cmd("Runtime.evaluate", {
                    "expression": "document.readyState",
                    "returnByValue": True
                })
                state = result.get("result", {}).get("value", "")
                if state == "complete":
                    return True
            except Exception:
                pass
            time.sleep(0.5)
        return False

    def evaluate(self, js):
        """执行 JavaScript 并返回结果"""
        result = self._send_cmd("Runtime.evaluate", {
            "expression": js,
            "returnByValue": True
        })
        return result.get("result", {}).get("value")

    def get_element_text(self, selector):
        """获取元素文本内容"""
        js = f"""
        (() => {{
            const el = document.querySelector('{selector}');
            return el ? el.textContent.trim() : '';
        }})()
        """
        return self.evaluate(js)

    def get_element_attribute(self, selector, attr):
        """获取元素属性"""
        js = f"""
        (() => {{
            const el = document.querySelector('{selector}');
            return el ? el.getAttribute('{attr}') : '';
        }})()
        """
        return self.evaluate(js)

    def click(self, selector):
        """点击元素"""
        js = f"""
        (() => {{
            const el = document.querySelector('{selector}');
            if (el) {{
                el.click();
                return true;
            }}
            return false;
        }})()
        """
        return self.evaluate(js)

    def set_input_value(self, selector, value):
        """设置输入框的值"""
        js = f"""
        (() => {{
            const el = document.querySelector('{selector}');
            if (el) {{
                el.value = '{value}';
                el.dispatchEvent(new Event('input', {{ bubbles: true }}));
                el.dispatchEvent(new Event('change', {{ bubbles: true }}));
                return true;
            }}
            return false;
        }})()
        """
        return self.evaluate(js)

    def get_captcha_base64(self):
        """获取验证码图片的 base64 数据"""
        js = """
        (() => {
            const img = document.getElementById('validateCodeImg');
            if (!img) return '';
            const canvas = document.createElement('canvas');
            canvas.width = img.naturalWidth || img.width;
            canvas.height = img.naturalHeight || img.height;
            const ctx = canvas.getContext('2d');
            ctx.drawImage(img, 0, 0);
            return canvas.toDataURL('image/jpeg');
        })()
        """
        data_url = self.evaluate(js)
        if data_url and ',' in data_url:
            return data_url.split(',', 1)[1]
        return ""

    def get_table_rows(self):
        """获取搜索结果表格行数据"""
        js = """
        (() => {
            const rows = document.querySelectorAll('.conn_list_items tr');
            const results = [];
            rows.forEach(tr => {
                const href = tr.getAttribute('href');
                const tds = tr.querySelectorAll('td');
                if (tds.length >= 4) {
                    results.push({
                        num: tds[0] ? tds[0].textContent.trim() : '',
                        title: tds[1] ? tds[1].textContent.trim() : '',
                        zone: tds[2] ? tds[2].textContent.trim() : '',
                        date: tds[3] ? tds[3].textContent.trim() : '',
                        href: href || ''
                    });
                }
            });
            return JSON.stringify(results);
        })()
        """
        result = self.evaluate(js)
        if result:
            return json.loads(result)
        return []

    def get_page_info(self):
        """获取当前分页信息"""
        js = """
        (() => {
            const pageEl = document.querySelector('.page .ui-paging-container');
            if (!pageEl) return JSON.stringify({total: 0, current: 0, pages: 0});
            const total = pageEl.getAttribute('total') || '0';
            const current = document.querySelector('.page .ui-paging-container .ui-paging-current');
            const curText = current ? current.textContent.trim() : '0';
            return JSON.stringify({
                total: parseInt(total),
                current: parseInt(curText),
                pages: Math.ceil(parseInt(total) / 10)
            });
        })()
        """
        result = self.evaluate(js)
        if result:
            return json.loads(result)
        return {"total": 0, "current": 0, "pages": 0}

    def go_to_page(self, page_num):
        """跳转到指定页"""
        # 尝试点击分页按钮
        js = f"""
        (() => {{
            const links = document.querySelectorAll('.page .ui-paging-container a');
            for (const a of links) {{
                if (a.textContent.trim() === '{page_num}') {{
                    a.click();
                    return true;
                }}
            }}
            // 尝试点击"下一页"
            const next = document.querySelector('.page .ui-paging-next');
            if (next) {{
                next.click();
                return true;
            }}
            return false;
        }})()
        """
        return self.evaluate(js)

    def select_zone(self, zone_code, parent_code=YC_CODE):
        """选择区域"""
        js = f"""
        (() => {{
            // 选择父级城市
            const zoneSelect = document.getElementById('zone');
            if (!zoneSelect) return false;
            zoneSelect.value = '{parent_code}';
            zoneSelect.dispatchEvent(new Event('change', {{ bubbles: true }}));
            return true;
        }})()
        """
        self.evaluate(js)
        time.sleep(0.5)

        # 选择子级区域
        js2 = f"""
        (() => {{
            const zoneLinks = document.querySelectorAll('#zone_c a');
            for (const a of zoneLinks) {{
                if (a.getAttribute('data') === '{zone_code}') {{
                    a.click();
                    return true;
                }}
            }}
            return false;
        }})()
        """
        return self.evaluate(js2)

    def click_search(self):
        """点击搜索按钮"""
        return self.click('.q_submit')

    def select_notice_type(self, type_code):
        """选择公告类型（gglxClass 链接，2026-08-07）"""
        js = f"""
        (() => {{
            const links = document.querySelectorAll('a[name="gglxClass"]');
            for (const a of links) {{
                if (a.getAttribute('data') === '{type_code}') {{
                    a.click();
                    return true;
                }}
            }}
            return false;
        }})()
        """
        return self.evaluate(js)

    def wait_for_results(self, timeout=10):
        """等待搜索结果加载"""
        deadline = time.time() + timeout
        while time.time() < deadline:
            js = """
            document.querySelector('.no_tableBox') &&
            document.querySelector('.no_tableBox').style.display !== 'none'
            """
            visible = self.evaluate(js)
            if visible:
                return True
            # 检查是否有错误提示
            err = self.get_element_text('#errmsg')
            if err:
                print(f"    [CDP] 搜索错误: {err}")
                return False
            time.sleep(0.5)
        return False

    def close(self):
        """关闭连接"""
        if self.ws:
            try:
                self.ws.close()
            except Exception:
                pass
            self.ws = None


# ==================== 验证码识别 ====================


class CaptchaSolver:
    """验证码识别器

    策略优先级：
    1. ddddocr（如果可用）
    2. tesseract（如果可用）
    3. 保存图片到文件（人工识别）
    """

    def __init__(self):
        self._ocr = None
        if HAS_DDDDOCR:
            try:
                self._ocr = ddddocr.DdddOcr()
                print("    [验证码] 使用 ddddocr")
            except Exception:
                self._ocr = None

    def solve(self, image_data):
        """识别验证码图片

        Args:
            image_data: bytes 或 base64 字符串

        Returns:
            (code: str | None, image_bytes: bytes | None)
        """
        if isinstance(image_data, str):
            # base64 → bytes
            try:
                img_bytes = base64.b64decode(image_data)
            except Exception:
                return None, None
        else:
            img_bytes = image_data

        # 方法 1: ddddocr
        if self._ocr:
            try:
                result = self._ocr.classification(img_bytes)
                if result and len(result) >= 3:
                    print(f"    [验证码] ddddocr 识别: '{result}'")
                    return result, img_bytes
            except Exception:
                pass

        # 方法 2: tesseract
        if HAS_PIL:
            try:
                result = self._tesseract_solve(img_bytes)
                if result:
                    print(f"    [验证码] tesseract 识别: '{result}'")
                    return result, img_bytes
            except Exception:
                pass

        # 方法 3: 保存图片
        save_path = f"/tmp/ccgp_captcha_{int(time.time())}.jpg"
        with open(save_path, 'wb') as f:
            f.write(img_bytes)
        print(f"    [验证码] 识别失败，图片已保存到 {save_path}")
        return None, img_bytes

    def _tesseract_solve(self, img_bytes):
        """使用 tesseract 识别验证码"""
        try:
            import subprocess
            img = Image.open(BytesIO(img_bytes))
            gray = img.convert('L')
            # 尝试不同的阈值
            for threshold in [120, 140, 160, 180, 200]:
                bw = gray.point(lambda x: 0 if x < threshold else 255)
                bw = bw.resize((bw.width * 5, bw.height * 5))
                with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as tmp:
                    tmp_path = tmp.name
                    bw.save(tmp_path)
                result = subprocess.run(
                    ['tesseract', tmp_path, 'stdout', '--psm', '8'],
                    capture_output=True, text=True, timeout=5
                )
                os.unlink(tmp_path)
                text = result.stdout.strip()
                if text and len(text) >= 3:
                    return text
        except Exception:
            pass
        return None


# ==================== 浏览器管理 ====================


class ChromiumManager:
    """Chromium 浏览器进程管理"""

    CHROMIUM_PATHS = [
        "/usr/bin/chromium-browser",
        "/usr/bin/chromium",
        "/snap/bin/chromium",
        "/usr/bin/google-chrome",
        "/usr/bin/google-chrome-stable",
        "/usr/bin/google-chrome-beta",
        "/usr/bin/google-chrome-unstable",
        # Puppeteer 内置
        os.path.expanduser("~/.cache/puppeteer/chrome/linux-*/chrome-linux64/chrome"),
        os.path.expanduser("~/.cache/ms-playwright/chromium-*/chrome-linux/chrome"),
    ]

    def __init__(self, port=9222):
        self.port = port
        self.process = None
        self.binary_path = None

    def find_binary(self):
        """查找 Chromium 二进制文件"""
        # 先检查环境变量
        env_path = os.environ.get("CHROMIUM_PATH") or os.environ.get("CHROME_PATH")
        if env_path and os.path.isfile(env_path):
            self.binary_path = env_path
            return self.binary_path

        # 检查常见路径
        for path in self.CHROMIUM_PATHS:
            if '*' in path:
                import glob
                matches = glob.glob(path)
                if matches:
                    self.binary_path = matches[0]
                    return self.binary_path
            elif os.path.isfile(path):
                self.binary_path = path
                return self.binary_path

        # 检查 PATH
        for cmd in ["chromium-browser", "chromium", "google-chrome", "google-chrome-stable"]:
            try:
                result = subprocess.run(["which", cmd], capture_output=True, text=True)
                if result.returncode == 0:
                    self.binary_path = result.stdout.strip()
                    return self.binary_path
            except Exception:
                pass

        return None

    def is_running(self):
        """检查是否已有浏览器在调试端口运行"""
        try:
            import requests
            resp = requests.get(f"http://127.0.0.1:{self.port}/json/version", timeout=3)
            return resp.status_code == 200
        except Exception:
            return False

    def start(self, headless=True, user_data_dir=None):
        """启动 Chromium 浏览器"""
        if self.is_running():
            print(f"  [Chromium] 已有浏览器在端口 {self.port} 运行，跳过启动")
            return True

        binary = self.find_binary()
        if not binary:
            print(f"  [Chromium] 未找到 Chromium 二进制文件")
            print(f"  [Chromium] 请运行 setup_chromium.sh 安装，或设置 CHROMIUM_PATH 环境变量")
            return False

        cmd = [
            binary,
            f"--remote-debugging-port={self.port}",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-gpu",
            "--disable-dev-shm-usage",
            "--no-sandbox",  # 2026-08-20：snap chromium 在容器内必须 --no-sandbox（参考 9333 端口实例启动参数）
        ]

        if headless:
            cmd.append("--headless=new")

        if user_data_dir:
            cmd.append(f"--user-data-dir={user_data_dir}")
        else:
            import tempfile
            tmpdir = tempfile.mkdtemp(prefix="ccgp_chromium_")
            cmd.append(f"--user-data-dir={tmpdir}")

        cmd.append("about:blank")

        try:
            self.process = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            # 等待浏览器启动
            for _ in range(30):
                time.sleep(1)
                if self.is_running():
                    print(f"  [Chromium] 已启动，端口 {self.port}")
                    return True
            print(f"  [Chromium] 启动超时")
            return False
        except Exception as e:
            print(f"  [Chromium] 启动失败: {e}")
            return False

    def stop(self):
        """停止浏览器"""
        if self.process:
            try:
                self.process.terminate()
                self.process.wait(timeout=5)
            except Exception:
                try:
                    self.process.kill()
                except Exception:
                    pass
            self.process = None
            print(f"  [Chromium] 已停止")


# ==================== 主抓取器 ====================


class CcgpJiangsuScraper:
    """江苏政府采购网 CDP 抓取器

    使用 CDP 协议连接 Chromium 浏览器，自动化抓取采购公告。
    筛选条件：盐城经济开发区 (320991) + 盐南高新技术产业开发区 (320992)

    同时提供不依赖浏览器的直接 API 抓取回退方案（需要验证码识别）。
    """

    def __init__(self, name="江苏政府采购网", use_cdp=True):
        self.name = name
        self.use_cdp = use_cdp
        self.base_url = BASE_URL
        self.cdp = None
        self.chromium = None
        self.captcha_solver = CaptchaSolver()
        self.session = None
        self.scope_filter = None
        self.run_id = os.environ.get("COCKPIT_CRAWL_RUN_ID") or uuid.uuid4().hex
        self.fetch_stats = []
        self.failed_scopes = []
        self.status = "running"
        self.deadline = None

    def set_scope_filter(self, scopes):
        """仅补抓指定的 公告类型:区域码 范围。"""
        self.scope_filter = set(scopes or []) or None

    @staticmethod
    def _scope_key(nt_code, zone_code):
        return f"{nt_code}:{zone_code}"

    def _iter_scopes(self):
        for nt_code, nt_name in NOTICE_TYPES:
            for zone_code in TARGET_ZONES:
                key = self._scope_key(nt_code, zone_code)
                if self.scope_filter and key not in self.scope_filter:
                    continue
                yield nt_code, nt_name, zone_code, key

    def _deadline_reached(self):
        return self.deadline is not None and time.monotonic() >= self.deadline

    def _record_scope(self, *, scope, status, expected_count=None,
                      fetched_count=0, successful_pages=0, expected_pages=None,
                      failed_pages=None, error="", started_at=""):
        stat = {
            "scope": scope,
            "status": status,
            "expected_count": expected_count,
            "fetched_count": fetched_count,
            "successful_pages": successful_pages,
            "expected_pages": expected_pages,
            "failed_pages": failed_pages or [],
            "error": error or "",
        }
        self.fetch_stats.append(stat)
        if status != "success" and scope not in self.failed_scopes:
            self.failed_scopes.append(scope)
        try:
            from crawl_ledger import record
            record(
                self.run_id, self.name, status, scope=scope,
                expected_count=expected_count, fetched_count=fetched_count,
                successful_pages=successful_pages, expected_pages=expected_pages,
                failed_pages=failed_pages, error=error,
                started_at=started_at,
                finished_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            )
        except Exception as e:
            print(f"    [{self.name}] 分页账本写入失败: {e}")

    def health(self):
        if self.failed_scopes:
            status = "partial" if any(s.get("fetched_count", 0) for s in self.fetch_stats) else "failed"
        else:
            status = "success"
        self.status = status
        return {
            "status": status,
            "failed_scopes": list(self.failed_scopes),
            "scopes": list(self.fetch_stats),
        }

    def _get_session(self):
        """获取 requests session"""
        if self.session is None:
            import requests
            self.session = requests.Session()
            self.session.headers.update(REQUEST_HEADERS)
        return self.session

    # ==================== CDP 浏览器自动化方式 ====================

    def scrape_with_cdp(self):
        """使用 CDP 浏览器自动化抓取"""
        print(f"  [{self.name}] 准备 CDP 浏览器自动化…")

        # 启动浏览器
        self.chromium = ChromiumManager()
        if not self.chromium.start():
            print(f"  [{self.name}] 浏览器启动失败，回退到 API 方式")
            return self.scrape_with_api()

        # 连接 CDP
        self.cdp = CDPConnection(port=self.chromium.port)
        try:
            self.cdp.connect()
        except Exception as e:
            print(f"  [{self.name}] CDP 连接失败: {e}")
            self.chromium.stop()
            return self.scrape_with_api()

        try:
            return self._cdp_scrape_flow()
        finally:
            self.cdp.close()
            self.chromium.stop()

    def _cdp_scrape_flow(self):
        """CDP 抓取主流程"""
        cdp = self.cdp

        # 导航到搜索页
        full_url = urljoin(self.base_url, SEARCH_PAGE)
        print(f"  [{self.name}] 导航到 {full_url}")
        cdp.navigate(full_url)
        cdp.wait_page_loaded(timeout=15)

        # 等待页面 JS 初始化完成
        time.sleep(3)

        # 收集所有结果
        all_items = []
        visited_urls = set()

        # 对每个公告类型×目标区域分别搜索（2026-08-07 扩展中标/成交/更正）
        scopes = list(self._iter_scopes())
        for scope_index, (nt_code, nt_name, zone_code, scope) in enumerate(scopes):
            if self._deadline_reached():
                for _, _, _, pending_scope in scopes[scope_index:]:
                    self._record_scope(
                        scope=pending_scope, status="timeout", failed_pages=[1],
                        error="站点总时限已到，范围尚未开始",
                    )
                break
            scope_started = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            zone_name = ZONE_CODE_TO_NAME.get(zone_code, zone_code)
            print(f"  [{self.name}] 搜索 {nt_name}({nt_code})·{zone_name}({zone_code})…")

            # 选择公告类型 + 区域
            cdp.select_notice_type(nt_code)
            time.sleep(0.5)
            cdp.select_zone(zone_code)
            time.sleep(1)

            # 处理验证码 + 搜索
            page = 1
            max_pages = MAX_PAGES_SAFETY
            consecutive_empty = 0
            scope_start_count = len(all_items)
            expected_pages = None
            failed_pages = []

            while page <= max_pages:
                if self._deadline_reached():
                    failed_pages.append(page)
                    break
                # 获取并识别验证码
                captcha_code = self._solve_captcha_via_cdp(cdp)
                if not captcha_code:
                    print(f"    [{self.name}] 验证码识别失败，跳过当前区域")
                    failed_pages.append(page)
                    break

                # 填入验证码
                cdp.set_input_value('#validateCode', captcha_code)
                time.sleep(0.5)

                # 如果非第一页，手动设置分页参数
                if page > 1:
                    # 尝试点击分页
                    cdp.go_to_page(page)
                    time.sleep(2)
                    # 可能不需要重新搜验证码，尝试直接获取结果
                    cdp.wait_for_results(timeout=5)
                else:
                    # 点击搜索
                    cdp.click_search()
                    time.sleep(1)
                    cdp.wait_for_results(timeout=10)

                # 提取结果
                rows = cdp.get_table_rows()
                if not rows:
                    consecutive_empty += 1
                    if consecutive_empty >= 3:
                        print(f"    [{self.name}] 连续 {consecutive_empty} 页无数据，停止翻页")
                        failed_pages.append(page)
                        break
                    # 如果没有数据，可能是验证码错误，重新尝试
                    print(f"    [{self.name}] 第 {page} 页无数据，重试验证码…")
                    continue

                consecutive_empty = 0

                # 处理当前页数据
                for row in rows:
                    item_url = row.get("href", "")
                    if item_url:
                        # 补全 URL
                        if item_url.startswith("/"):
                            item_url = urljoin(self.base_url, item_url)
                        if item_url in visited_urls:
                            continue
                        visited_urls.add(item_url)

                    title = row.get("title", "")
                    zone = row.get("zone", "")
                    date = row.get("date", "")

                    # 确定区域名称
                    zone_display = ZONE_CODE_TO_NAME.get(zone_code, zone_name)

                    item = {
                        "title": title,
                        "url": item_url,
                        "date": date,
                        "zone_name": zone_display,
                        "zone_code": zone_code,
                        "source": "江苏政府采购网",
                        "content": "",
                        "tenderer": "",
                        "amount": "",
                        "bid_deadline": "",
                    }
                    all_items.append(item)

                print(f"    [{self.name}] 区域 {zone_name} 第 {page} 页，累计 {len(all_items)} 条")

                # 检查是否有下一页
                page_info = cdp.get_page_info()
                total_pages = page_info.get("pages", 0)
                expected_pages = total_pages or expected_pages
                if page >= total_pages:
                    print(f"    [{self.name}] 区域 {zone_name} 已到最后一页 (共 {total_pages} 页)")
                    break

                page += 1
                time.sleep(1)

            fetched = len(all_items) - scope_start_count
            capped = bool(expected_pages and expected_pages > MAX_PAGES_SAFETY)
            if capped:
                failed_pages.extend(range(MAX_PAGES_SAFETY + 1, expected_pages + 1))
            status = "success" if not failed_pages and not capped else ("partial" if fetched else "failed")
            self._record_scope(
                scope=scope, status=status, fetched_count=fetched,
                successful_pages=max(0, page - len(set(failed_pages))),
                expected_pages=expected_pages, failed_pages=sorted(set(failed_pages)),
                error="达到安全页数上限" if capped else ("分页/验证码失败" if failed_pages else ""),
                started_at=scope_started,
            )

        print(f"  [{self.name}] CDP 抓取完成，共 {len(all_items)} 条")
        return all_items

    def _solve_captcha_via_cdp(self, cdp):
        """通过 CDP 获取并识别验证码"""
        # 获取验证码图片 base64
        captcha_b64 = cdp.get_captcha_base64()
        if not captcha_b64:
            # 尝试刷新验证码
            cdp.click('#validateCodeRefresh')
            time.sleep(1)
            captcha_b64 = cdp.get_captcha_base64()
            if not captcha_b64:
                return None

        # 识别
        code, _ = self.captcha_solver.solve(captcha_b64)
        return code

    # ==================== 直接 API 方式（回退方案） ====================

    def scrape_with_api(self):
        """使用直接 API 调用抓取（需要验证码识别）"""
        print(f"  [{self.name}] 使用 API 方式抓取…")
        all_items = []
        visited_urls = set()
        session = self._get_session()

        # 先获取区域列表
        try:
            resp = session.get(
                urljoin(self.base_url, ZONE_API),
                params={"pid": "32"},
                timeout=REQUEST_TIMEOUT,
            )
            zone_data = resp.json()
        except Exception as e:
            print(f"  [{self.name}] 获取区域列表失败: {e}")
            return []

        scopes = list(self._iter_scopes())
        for scope_index, (nt_code, nt_name, zone_code, scope) in enumerate(scopes):
            if self._deadline_reached():
                for _, _, _, pending_scope in scopes[scope_index:]:
                    self._record_scope(
                        scope=pending_scope, status="timeout", failed_pages=[1],
                        error="站点总时限已到，范围尚未开始",
                    )
                break
            scope_started = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            zone_name = ZONE_CODE_TO_NAME.get(zone_code, zone_code)
            print(f"  [{self.name}] 搜索 {nt_name}({nt_code})·{zone_name}({zone_code})…")

            page = 1
            max_pages = MAX_PAGES_SAFETY
            consecutive_fail = 0
            page_total = 0  # 记录第一页返回的总条数，用于翻页判断
            expected_pages = None
            successful_pages = 0
            failed_pages = []
            scope_start_count = len(all_items)
            last_error = ""

            while page <= max_pages:
                if self._deadline_reached():
                    failed_pages.append(page)
                    last_error = "站点总时限已到"
                    break
                # 获取验证码
                captcha_resp = session.get(
                    urljoin(self.base_url, CAPTCHA_URL),
                    timeout=REQUEST_TIMEOUT,
                )
                captcha_bytes = captcha_resp.content

                # 识别验证码
                code, _ = self.captcha_solver.solve(captcha_bytes)
                if not code:
                    print(f"    [{self.name}] 验证码识别失败，重试…")
                    consecutive_fail += 1
                    if consecutive_fail >= 5:
                        print(f"    [{self.name}] 连续 {consecutive_fail} 次验证码失败，跳过")
                        failed_pages.append(page)
                        last_error = "验证码连续识别失败"
                        break
                    time.sleep(2)
                    continue

                # 调搜索 API
                try:
                    resp = session.get(
                        urljoin(self.base_url, SEARCH_API),
                        params={
                            "page": page,
                            "sd": 0,
                            "ed": 0,
                            "qy": zone_code,
                            "pqy": YC_CODE,
                            "cglx": nt_code,
                            "bt": "",
                            "code": code,
                            "cgfs": "",
                        },
                        timeout=REQUEST_TIMEOUT,
                    )
                    # API 返回 Content-Type: text/html 但内容为 JSON
                    # 验证码错误时返回 HTML 错误页，需要先检查
                    if '验证码' in resp.text[:200] and '"code"' not in resp.text[:200]:
                        raise ValueError("验证码错误")
                    data = resp.json()
                    if not isinstance(data, dict):
                        raise ValueError("接口返回非 JSON 对象")
                except Exception as e:
                    print(f"    [{self.name}] API 请求失败: {e}")
                    last_error = str(e)
                    consecutive_fail += 1
                    if consecutive_fail >= 5:
                        print(f"    [{self.name}] 连续 {consecutive_fail} 次失败，跳过")
                        failed_pages.append(page)
                        break
                    time.sleep(1.5)
                    continue

                if data.get("code") != 200:
                    msg = data.get("message", "")
                    print(f"    [{self.name}] API 错误: {msg}")
                    if "验证码" in msg:
                        consecutive_fail += 1
                        if consecutive_fail >= 5:
                            print(f"    [{self.name}] 验证码连续失败，跳过")
                            failed_pages.append(page)
                            last_error = msg or "验证码连续失败"
                            break
                        time.sleep(1)
                        continue
                    else:
                        failed_pages.append(page)
                        last_error = msg or "API 业务错误"
                        break

                consecutive_fail = 0
                result = data.get("result", {})
                items = result.get("list", [])
                total = result.get("count", 0)

                if not items:
                    print(f"    [{self.name}] 第 {page} 页无数据，停止")
                    if page_total and page <= (page_total + 9) // 10:
                        failed_pages.append(page)
                        last_error = "接口提前返回空页"
                    break

                # 记录第一页的 total 作为翻页基准
                if page == 1:
                    page_total = total
                    expected_pages = (page_total + 9) // 10 if page_total > 0 else 0

                successful_pages += 1

                for item in items:
                    title = item.get("title", "")
                    detail_url = item.get("url", "")
                    if not detail_url and item.get("id"):
                        gg_code = item.get("ggCode", "cggg")
                        gg_id = item.get("id", "")
                        detail_url = f"/jiangsu/js_cggg/details.html?gglb={gg_code}&ggid={gg_id}"
                    if detail_url:
                        if detail_url.startswith("/"):
                            detail_url = urljoin(self.base_url, detail_url)
                        if detail_url in visited_urls:
                            continue
                        visited_urls.add(detail_url)

                    pub_date = item.get("publishDate", "")
                    if pub_date:
                        pub_date = pub_date.split(" ")[0] if " " in pub_date else pub_date

                    all_items.append({
                        "title": title,
                        "url": detail_url,
                        "date": pub_date,
                        "zone_name": zone_name,
                        "zone_code": zone_code,
                        "source": "江苏政府采购网",
                        "content": "",
                        "tenderer": "",
                        "amount": "",
                        "bid_deadline": "",
                    })

                print(f"    [{self.name}] {zone_name} 第 {page} 页，累计 {len(all_items)} 条 (共 {total} 条)")

                # 翻页判断：基于 page_total 计算，防止 API 返回不一致的 total
                if page_total > 0:
                    page_size = 10
                    total_pages = (page_total + page_size - 1) // page_size
                    expected_pages = total_pages
                    if page >= total_pages:
                        break
                else:
                    if page >= max_pages:
                        break
                page += 1
                time.sleep(REQUEST_DELAY)

            fetched = len(all_items) - scope_start_count
            capped = bool(expected_pages and expected_pages > max_pages and page >= max_pages)
            if capped:
                # 仅记录缺失区间，不构造超大请求；凌晨补偿仍按 scope 重试。
                failed_pages.extend(range(max_pages + 1, expected_pages + 1))
                last_error = f"接口总页数 {expected_pages} 超过安全上限 {max_pages}"
            status = "success" if not failed_pages and not capped else ("partial" if fetched else "failed")
            self._record_scope(
                scope=scope, status=status, expected_count=page_total or None,
                fetched_count=fetched, successful_pages=successful_pages,
                expected_pages=expected_pages, failed_pages=sorted(set(failed_pages)),
                error=last_error, started_at=scope_started,
            )

        print(f"  [{self.name}] API 抓取完成，共 {len(all_items)} 条")
        return all_items

    # ==================== 主入口 ====================

    def scrape(self, fetch_details=True, max_items=0, ict_only=True):
        """主抓取入口

        优先使用 CDP 浏览器自动化，回退到直接 API 调用。

        Args:
            fetch_details: 是否抓取详情页（默认 True，耗时较长）
            max_items: 最大抓取条数（0 = 不限）
            ict_only: 是否只抓取 ICT 候选项目的详情页（默认 True）
        """
        print(f"[{self.name}] 开始抓取 {urljoin(self.base_url, SEARCH_PAGE)}")
        # 同一个实例可能被补偿任务重试；每轮必须重新计算健康状态。
        self.fetch_stats = []
        self.failed_scopes = []
        self.status = "running"

        if self.use_cdp:
            items = self.scrape_with_cdp()
        else:
            items = self.scrape_with_api()

        if max_items > 0 and len(items) > max_items:
            items = items[:max_items]

        # ICT 关键词预筛（标题级别）
        from config import ICT_STRONG_KEYWORDS, ICT_WEAK_KEYWORDS
        for item in items:
            title = item.get("title", "")
            strong_hits = [kw for kw in ICT_STRONG_KEYWORDS if kw in title]
            weak_hits = [kw for kw in ICT_WEAK_KEYWORDS if kw in title]
            if strong_hits:
                item["_ict_candidate"] = True
                item["_ict_strong"] = strong_hits
                item["_ict_weak"] = weak_hits
            elif len(weak_hits) >= 2:
                item["_ict_candidate"] = True
                item["_ict_strong"] = strong_hits
                item["_ict_weak"] = weak_hits
            else:
                item["_ict_candidate"] = False

        # 详情页抓取：抓取全部（江苏政府采购网正文走接口，需全量才能富化）
        detail_targets = [i for i in items if i.get("url")] if not ict_only else [i for i in items if i.get("_ict_candidate")]
        if items and fetch_details and detail_targets:
            print(f"  [{self.name}] 开始抓取详情页 ({len(detail_targets)}/{len(items)} 条)…")
            for i, item in enumerate(detail_targets):
                if item.get("url") and not item.get("content"):
                    try:
                        content = self._fetch_detail(item["url"])
                        item["content"] = content
                        # 传 title 让 notice_type 正确推断（中标/成交标题→award，才会提取 winner）（2026-08-07）
                        fields = self._extract_detail_fields(content, item.get("title", ""))
                        item.update(fields)
                    except Exception as e:
                        print(f"    [{self.name}] 详情页 {i+1} 失败: {e}")
                    time.sleep(DETAIL_DELAY)

        # 关闭 session
        if self.session:
            self.session.close()
            self.session = None

        print(f"  [{self.name}] 抓取完成，共 {len(items)} 条")
        health = self.health()
        print(f"  [{self.name}] 状态 {health['status']}，失败/缺口范围 {len(health['failed_scopes'])} 个")
        return items

    def _fetch_detail(self, url):
        """抓取详情页内容

        优先调用江苏政府采购网正文接口（relevantCgggGetById.jsp）取真实正文，
        失败再回退 requests 抓 HTML。详情页正文是 JS 异步加载的，
        直接 requests 只能拿到模板骨架，必须走接口。
        """
        # 1) 优先：从 URL 提取 ggid，调正文接口
        content = self._fetch_detail_via_api(url)
        if content:
            return content

        # 2) 回退：requests 抓 HTML 解析
        session = self._get_session()
        try:
            resp = session.get(url, timeout=REQUEST_TIMEOUT)
            resp.encoding = resp.apparent_encoding or resp.encoding
            if resp.status_code == 200:
                from bs4 import BeautifulSoup
                soup = BeautifulSoup(resp.text, 'lxml')
                body_selectors = [
                    "div.content", "div.article", "div.detail",
                    "div.news_content", "div.text", "div.info_content",
                    "div.article-content", ".detail-content", ".main-content",
                    ".articleCon", "div.xxgk_content", "#content",
                ]
                best_text = ""
                for sel in body_selectors:
                    els = soup.select(sel)
                    for el in els:
                        text = el.get_text(separator=" ", strip=True)
                        if len(text) > len(best_text) and len(text) >= 50:
                            best_text = text
                if best_text:
                    return best_text[:MAX_DETAIL_CHARS]
                body = soup.find('body')
                if body:
                    return body.get_text(separator=" ", strip=True)[:MAX_DETAIL_CHARS]
        except Exception:
            pass
        return ""

    def _fetch_detail_via_api(self, url):
        """调用江苏政府采购网正文接口取真实正文（JS 异步加载，接口才拿得到）"""
        import re as _re
        from urllib.parse import parse_qs, urlparse
        try:
            qs = parse_qs(urlparse(url).query)
            ggid = (qs.get("ggid") or [None])[0]
            if not ggid:
                return ""
            session = self._get_session()
            session.headers.update({
                "Referer": url,
                "X-Requested-With": "XMLHttpRequest",
            })
            resp = session.post(
                urljoin(self.base_url, "/pss/jsp/relevantCgggGetById.jsp"),
                data={"ggid": ggid},
                timeout=REQUEST_TIMEOUT,
            )
            if resp.status_code != 200:
                return ""
            data = resp.json()
            if data.get("msg") != "OK":
                return ""
            d = data.get("data") or {}
            # 采购公告正文在 content，中标/成交公告在 summary（2026-08-07 兼容）
            content = d.get("content") or d.get("summary") or ""
            if not content:
                return ""
            # 去 HTML 标签转纯文本
            text = _re.sub(r"<[^>]+>", " ", content)
            text = _re.sub(r"\s+", " ", text).strip()
            return text[:MAX_DETAIL_CHARS]
        except Exception:
            return ""

    def _extract_detail_fields(self, content, title=""):
        """从详情页正文提取字段（复用 BaseScraper 逻辑）；传 title 使 notice_type 正确推断（2026-08-07）"""
        from scrapers import BaseScraper
        # 临时创建 BaseScraper 实例来调用 extract_detail_fields
        dummy = BaseScraper("dummy", "", "")
        return dummy.extract_detail_fields(content, title)

    def close(self):
        """清理资源"""
        if self.session:
            self.session.close()
            self.session = None


# ==================== 区域名称映射 ====================

ZONE_CODE_TO_NAME = {
    "320902": "亭湖区",
    "320903": "盐都区",
    "320904": "大丰区",
    "320921": "响水县",
    "320922": "滨海县",
    "320923": "阜宁县",
    "320924": "射阳县",
    "320925": "建湖县",
    "320981": "东台市",
    "320991": "盐城经济开发区",
    "320992": "盐南高新技术产业开发区",
}

ZONE_NAME_TO_CODE = {
    "盐南高新区": "320992",
    "盐南高新技术产业开发区": "320992",
    "经济开发区": "320991",
    "盐城经济开发区": "320991",
}


# ==================== 注册函数 ====================


def get_ccgp_scraper():
    """获取江苏政府采购网抓取器实例"""
    return CcgpJiangsuScraper("江苏政府采购网", use_cdp=True)
