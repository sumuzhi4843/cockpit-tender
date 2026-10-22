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
from datetime import datetime, timedelta
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

# 2026-09-07 N1 增量水位：公告按日期倒序返回，整页公告都早于 N 天前即停止翻页。
# 根治“每 scope 翻全量历史”（50 scopes × 45-85 页 × 4s/页 ≈ 75 分钟/轮）；
# 主抓每天 3 次 + 凌晨补偿，30 天水位覆盖充分（更早的已在库，notices 只增不减）。
# 设 0 禁用（回退全量翻页）。
CCGP_RECENT_DAYS = int(os.environ.get("CCGP_RECENT_DAYS", "30"))

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
        self.session_id = None
        self._browser_endpoint = False

    def _get_ws_url(self):
        """通过 HTTP 获取 WebSocket 调试 URL

        2026-09-21 根因修复：/json/version 返回的是 **browser 级**端点，不支持
        Page/Runtime/DOM 域（实测报 -32601 "'Page.enable' wasn't found"），
        而 _send_cmd 对 error 响应静默返回 {} → 连接“成功”但所有页面操作全空转
        → 验证码抠图永远为空 → 9-20 18:00 起江苏政采 0 条。
        必须连 **page 级** target；若只能拿到 browser 端点，则 attach 到新建 page
        后走 sessionId 通道（Flatten session）。
        """
        import requests
        # 首选：page 级 target（支持 Page/Runtime 域）
        try:
            resp = requests.get(f"http://{self.host}:{self.port}/json", timeout=5)
            if resp.status_code == 200:
                pages = resp.json()
                for t in pages:
                    if t.get("type") == "page" and t.get("webSocketDebuggerUrl"):
                        self.session_id = None
                        self._browser_endpoint = False
                        return t["webSocketDebuggerUrl"]
        except Exception:
            pass
        # 回退：browser 级端点 + attach 到新 page（_attach_browser_target 里建会话）
        try:
            resp = requests.get(f"http://{self.host}:{self.port}/json/version", timeout=5)
            if resp.status_code == 200:
                ws_url = resp.json().get("webSocketDebuggerUrl")
                if ws_url:
                    self._browser_endpoint = True
                    return ws_url
        except Exception:
            pass
        return None

    def _attach_browser_target(self):
        """browser 级端点时，新建 target 并以 sessionId 通道驱动页面。

        返回 True=已建立会话；False=失败（调用方应走 API 兜底）。
        """
        try:
            resp = self._send_cmd("Target.createTarget", {"url": "about:blank"})
            target_id = resp.get("targetId")
            if not target_id:
                return False
            att = self._send_cmd("Target.attachToTarget",
                                 {"targetId": target_id, "flatten": True})
            self.session_id = att.get("sessionId")
            return bool(self.session_id)
        except Exception:
            return False

    def connect(self):
        """连接到 CDP"""
        ws_url = self._get_ws_url()
        if not ws_url:
            raise ConnectionError(f"无法连接到 CDP: http://{self.host}:{self.port}")

        self.ws = websocket.create_connection(ws_url, timeout=10)
        # browser 级端点 → 先 attach 出 page 会话，否则 Page/Runtime 域不可用
        if self._browser_endpoint:
            if not self._attach_browser_target():
                raise ConnectionError("browser 级端点 attach page target 失败")
        self._send_cmd("Page.enable")
        self._send_cmd("Runtime.enable")
        self._send_cmd("DOM.enable")
        self._send_cmd("Network.enable")
        # 健康自检：确认当前通道真的能执行页面 JS（连错 target 时立即暴露，
        # 触发上层回退 API，而不是 44 区县全部“验证码识别失败”空转 75 分钟）
        probe = self.evaluate("1+1")
        if probe != 2:
            raise ConnectionError(f"CDP evaluate 自检失败（返回 {probe!r}），页面通道不可用")
        return True

    def _send_cmd(self, method, params=None):
        """发送 CDP 命令（page 级直接发；browser 级经 sessionId 通道）"""
        self._msg_id += 1
        msg = {"id": self._msg_id, "method": method}
        if params:
            msg["params"] = params
        if self.session_id:
            msg["sessionId"] = self.session_id
        self.ws.send(json.dumps(msg))
        # 等待响应
        while True:
            resp = json.loads(self.ws.recv())
            if resp.get("id") == self._msg_id:
                if "error" in resp:
                    # 不再静默吞错：让调用方看到异常（-32601=连错 target）
                    raise RuntimeError(f"CDP {method} 报错: {resp['error'].get('message')}")
                result = resp.get("result", {})
                # Runtime.evaluate 的 JS 异常显式化（旧版静默 None）
                exc = result.get("exceptionDetails")
                if exc:
                    print(f"    [CDP] JS 执行异常: {exc.get('text', '')[:200]}")
                return result
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
        """获取当前分页信息

        2026-09-23 适配站点分页控件改版：新控件无 total 属性、页码是
        li[data-page] 而非 <a>，旧逻辑算出 pages=0 → 每范围只抓第 1 页就停
        （9-22 起 203 条/轮 vs API 时代 ~970）。现按 li[data-page] 解析页码，
        并新增 has_next（下一页按钮存在且未禁用）作为翻页驱动信号。
        """
        js = """
        (() => {
            const cont = document.querySelector('.page .ui-paging-container');
            if (!cont) return JSON.stringify({total: 0, current: 0, pages: 0, has_next: false});
            let maxPage = 0, cur = 0;
            cont.querySelectorAll('li[data-page]').forEach(li => {
                const p = parseInt(li.getAttribute('data-page'), 10) || 0;
                if (p > maxPage) maxPage = p;
                if (li.className.indexOf('focus') >= 0) cur = p;
            });
            const next = cont.querySelector('li.js-page-next');
            const hasNext = !!(next && next.className.indexOf('ui-pager-disabled') < 0);
            let total = parseInt(cont.getAttribute('total') || '0', 10) || 0;
            return JSON.stringify({
                total: total,
                current: cur || 1,
                pages: Math.max(total ? Math.ceil(total / 10) : maxPage, cur),
                has_next: hasNext
            });
        })()
        """
        result = self.evaluate(js)
        if result:
            return json.loads(result)
        return {"total": 0, "current": 0, "pages": 0, "has_next": False}

    def go_to_page(self, page_num):
        """跳转到指定页（2026-09-23 适配新分页控件：li[data-page] 页码优先，
        页码窗口不含目标页时若恰为下一页则点 js-page-next 前进）"""
        js = f"""
        (() => {{
            const cont = document.querySelector('.page .ui-paging-container');
            if (cont) {{
                const li = cont.querySelector('li[data-page="{page_num}"]');
                if (li) {{ li.click(); return true; }}
                const cur = cont.querySelector('li.focus[data-page]');
                const next = cont.querySelector('li.js-page-next');
                if (next && next.className.indexOf('ui-pager-disabled') < 0 &&
                    cur && parseInt(cur.getAttribute('data-page'), 10) === {page_num} - 1) {{
                    next.click();
                    return true;
                }}
            }}
            // 旧版控件回退
            const links = document.querySelectorAll('.page .ui-paging-container a');
            for (const a of links) {{
                if (a.textContent.trim() === '{page_num}') {{
                    a.click();
                    return true;
                }}
            }}
            const next2 = document.querySelector('.page .ui-paging-next, .page li.js-page-next');
            if (next2) {{
                next2.click();
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

    def wait_for_table(self, page_num, timeout=10):
        """点分页后等表格"就位且停变"再返回 True。

        2026-09-23 时序修复：站点翻页是异步 AJAX，固定 sleep 会拿到半刷新表
        （盐都实测第 2 页 13/20 条，7 行还是上一页旧行）。判据：分页 focus 已落到
        目标页 且 行指纹（首尾 href+行数）连续两次采样不变。"""
        js = """
        (() => {
            const focus = document.querySelector('.page .ui-paging-container li.focus[data-page]');
            const rows = document.querySelectorAll('.conn_list_items tr');
            if (!rows.length) return '';
            const f = focus ? focus.getAttribute('data-page') : '0';
            const first = rows[0].getAttribute('href') || '';
            const last = rows[rows.length - 1].getAttribute('href') || '';
            return f + '::' + first + '::' + last + '::' + rows.length;
        })()
        """
        deadline = time.time() + timeout
        last_sig = None
        stable = 0
        while time.time() < deadline:
            sig = self.evaluate(js)
            if sig and sig.startswith(f"{page_num}::"):
                if sig == last_sig:
                    stable += 1
                    if stable >= 2:
                        return True
                else:
                    stable = 0
                last_sig = sig
            time.sleep(0.7)
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
            "--remote-allow-origins=*",  # 2026-09-20：Chromium 新版拒绝带 Origin 的 WS 握手→CDP 403，江苏政采一直退回验证码 API 模式；加此参数恢复 S3 无头渲染主通道
            "--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",  # 2026-09-23：隐藏 HeadlessChrome 指纹（实测站点 UA 级 WAF 存在；与 API 通道 REQUEST_HEADERS 同 UA）
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
        self._api_tried = False
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

    def scrape_with_cdp(self, allow_api_fallback=True):
        """使用 CDP 浏览器自动化抓取（allow_api_fallback=False 用于 API 已是主通道时防止双跑）"""
        print(f"  [{self.name}] 准备 CDP 浏览器自动化…")

        def _fallback_empty():
            if allow_api_fallback:
                return self.scrape_with_api()
            return []

        # 启动浏览器
        self.chromium = ChromiumManager()
        if not self.chromium.start():
            print(f"  [{self.name}] 浏览器启动失败，回退到 API 方式")
            return _fallback_empty()

        # 连接 CDP
        self.cdp = CDPConnection(port=self.chromium.port)
        try:
            self.cdp.connect()
        except Exception as e:
            print(f"  [{self.name}] CDP 连接失败: {e}")
            self.chromium.stop()
            return _fallback_empty()

        items = None
        try:
            items = self._cdp_scrape_flow()
        except Exception as e:
            print(f"  [{self.name}] CDP 流程异常: {e}")
        finally:
            self.cdp.close()
            self.chromium.stop()
        if not items:
            print(f"  [{self.name}] CDP 无数据/异常，回退 API 方式抓取")
            return self.scrape_with_api()  # CDP 作为主通道时，0 条必须换 API 保数据流
        return items

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
        # N1 增量水位日期（CDP 路径）
        _cdp_watermark = ((datetime.now() - timedelta(days=CCGP_RECENT_DAYS)).strftime("%Y-%m-%d")
                          if CCGP_RECENT_DAYS > 0 else "")

        # 对每个公告类型×目标区域分别搜索（2026-08-07 扩展中标/成交/更正）
        scopes = list(self._iter_scopes())
        # N1 增量水位日期（YYYY-MM-DD，字符串可比较）
        _watermark = ((datetime.now() - timedelta(days=CCGP_RECENT_DAYS)).strftime("%Y-%m-%d")
                      if CCGP_RECENT_DAYS > 0 else "")
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
                # 2026-09-23 修复：验证码只在本范围首次搜索时填。
                # 实测（盐都 cggg）：旧流程"每页重识别+重填"会让第 2 页与第 1 页 7/10 重合
                # （重抠图会刷新站点验证码图，把分页查询重置）；改为只在第 1 页填后：
                # P2 与 P1 0 重合、累计 20 条、末页判定正确。翻页点击是页面内会话 AJAX，无需新码。
                if page == 1:
                    captcha_code = self._solve_captcha_via_cdp(cdp)
                    if not captcha_code:
                        print(f"    [{self.name}] 验证码识别失败，跳过当前区域")
                        failed_pages.append(page)
                        break

                    # 填入验证码并搜索
                    cdp.set_input_value('#validateCode', captcha_code)
                    time.sleep(0.5)
                    cdp.click_search()
                    time.sleep(1)
                    cdp.wait_for_results(timeout=10)
                else:
                    # 2026-09-23 时序修复：点分页后等"页码就位+表格停变"再采，
                    # 旧固定 sleep 常拿半刷新表（盐都实测 13/20）；一次不中就重点页。
                    if not cdp.wait_for_table(page, timeout=10):
                        cdp.go_to_page(page)
                        if not cdp.wait_for_table(page, timeout=10):
                            print(f"    [{self.name}] 区域 {zone_name} 第 {page} 页刷新未到位，按已取数据停止翻页")
                            break

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

                # N1 增量水位（CDP 路径同款）：本页全部早于水位 → 停止
                if _cdp_watermark and page >= 2:
                    _page_dates = [(r.get("date") or "").split(" ")[0] for r in rows]
                    if _page_dates and all(d and d < _cdp_watermark for d in _page_dates):
                        print(f"    [{self.name}] 区域 {zone_name} 第 {page} 页全部早于 {CCGP_RECENT_DAYS} 天水位，增量停止")
                        break

                # 检查是否有下一页（2026-09-23：改按"下一页"按钮可用性驱动，
                # 不再依赖 total 页数——新分页控件拿不到总页数，旧逻辑必现 pages=0 假末页）
                page_info = cdp.get_page_info()
                total_pages = page_info.get("pages", 0)
                expected_pages = total_pages or expected_pages
                if not page_info.get("has_next"):
                    print(f"    [{self.name}] 区域 {zone_name} 第 {page} 页即末页（无下一页按钮）")
                    break
                if page >= MAX_PAGES_SAFETY:
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
        self._api_tried = True
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
        # N1 增量水位日期（YYYY-MM-DD，字符串可比较）
        _watermark = ((datetime.now() - timedelta(days=CCGP_RECENT_DAYS)).strftime("%Y-%m-%d")
                      if CCGP_RECENT_DAYS > 0 else "")
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
                # 获取验证码（2026-09-21 加固：空图=会话被标记，旧代码不换会话导致
                # 全区县连坐——9-07~9-20 的 934 张 0 字节存图即此症状；实测换新会话立即恢复）
                captcha_bytes = b""
                for _c_try in range(3):
                    captcha_resp = session.get(
                        urljoin(self.base_url, CAPTCHA_URL),
                        timeout=REQUEST_TIMEOUT,
                    )
                    captcha_bytes = captcha_resp.content
                    if len(captcha_bytes) > 200:  # 真图 ~1.4-1.6KB，探针实测
                        break
                    print(f"    [{self.name}] 验证码响应异常(HTTP {captcha_resp.status_code}/{len(captcha_bytes)}B)，换新会话…")
                    time.sleep(2 + _c_try)  # 退避
                    self.session = None
                    session = self._get_session()
                    try:  # 预热新会话（zone API 拿 JSESSIONID）
                        session.get(urljoin(self.base_url, ZONE_API),
                                    params={"pid": "32"}, timeout=REQUEST_TIMEOUT)
                    except Exception:
                        pass

                # 识别验证码（始终空图则不计 OCR，避免污染 /tmp 存图统计）
                code = None
                if len(captcha_bytes) > 200:
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

                # N1 增量水位：本页公告全部早于水位日期 → 后面更老，停止翻页
                if _watermark and page >= 2:
                    _page_dates = [(it.get("publishDate") or "").split(" ")[0] for it in items]
                    if _page_dates and all(d and d < _watermark for d in _page_dates):
                        print(f"    [{self.name}] {zone_name} 第 {page} 页全部早于 {CCGP_RECENT_DAYS} 天水位({_watermark})，增量停止")
                        break

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

        API 直调为主通道，CDP 浏览器自动化为兜底（2026-09-23 倒置，见 scrape() 内铁证注释）。

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

        # 2026-09-23 通道倒置：API 升主、CDP 降兜底（备份 .bak-20260923-api-primary）。
        # 铁证：①CDP 主通道 9-22/9-23 只出 203→258 条/轮（基线 ~970）；②验证码层已健康
        # （0 失败/0 换会话）但 CDP 浏览器面自身采集面窄：同范围盐都实测 CDP 13 vs 探针 20，
        # 而 API 盐南 cggg 20 条/亭湖 zbgg 30 条（盐南正是 CDP 多日缺口范围）；③API 翻页/水位/OCR 均实测健康。
        items = self.scrape_with_api()
        if not items and self.use_cdp:
            print(f"  [{self.name}] API 0 条（验证码或接口异常），回退 CDP 浏览器抓取")
            items = self.scrape_with_cdp(allow_api_fallback=False)

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
