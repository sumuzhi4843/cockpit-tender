# -*- coding: utf-8 -*-
"""2026-10-02 experience_playwright.py — Tier 3 实做。

apply_unified v3 的 Tier 3 fallback。每个 JS 站有专属的 fetch 策略：
- cofco: URL 切到 actual_detail_url，点击「结果公告」tab，等 list 行
- jszbcg: 等「中标人信息」section
- ycggzy: 等「中标候选人」section
- 江苏政采: 等「三、中标（成交）信息」section

单 fetcher 实例（懒加载）。每次 fetch 新 page，关闭（避免 leak）。
"""
import time
import logging
from typing import Optional, Tuple

_LOG = logging.getLogger(__name__)


# JS 站 selector 配置（按 host 匹配）
_SITE_CONFIG = {
    'ecai.cofco.com': {
        'wait_text': '详情',
        'extra_steps': ['click_result_tab'],
        'wait_ms_after_nav': 3000,
        'wait_ms_after_click': 3000,
    },
    'www.jszbcg.com': {
        'wait_text': '中标人信息',
        'extra_steps': [],
        'wait_ms_after_nav': 3000,
        'wait_ms_after_click': 0,
    },
    'ycggzy.jszwfw.gov.cn': {
        'wait_text': '中标候选人',
        'extra_steps': [],
        'wait_ms_after_nav': 5000,
        'wait_ms_after_click': 0,
    },
    'www.ccgp-jiangsu.gov.cn': {
        'wait_text': '中标',
        'extra_steps': [],
        'wait_ms_after_nav': 4000,
        'wait_ms_after_click': 0,
    },
}


def _site_config(url: str) -> dict:
    for host, cfg in _SITE_CONFIG.items():
        if host in url:
            return cfg
    return {}


class PlaywrightFetcher:
    """懒加载的 Playwright fetcher（首次 fetch 时启动 chromium）。"""

    def __init__(self, headless: bool = True):
        self.headless = headless
        self._playwright = None
        self._browser = None
        self._stats = {'fetches': 0, 'success': 0, 'fail': 0, 'ms_total': 0}

    def _ensure_browser(self):
        if self._browser is None:
            from playwright.sync_api import sync_playwright
            self._playwright = sync_playwright().start()
            self._browser = self._playwright.chromium.launch(headless=self.headless)
            _LOG.info("playwright chromium 启动")
        return self._browser

    def fetch(self, url: str, timeout: int = 20) -> Tuple[str, Optional[str]]:
        """Fetch URL via headless Chromium + 站特定 waits/clicks。

        Returns:
            (rendered_html, error_msg) - html="" 表示失败
        """
        cfg = _site_config(url)
        browser = self._ensure_browser()

        t0 = time.time()
        ctx = browser.new_context(
            user_agent='Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            viewport={'width': 1280, 'height': 720},
        )
        page = ctx.new_page()
        try:
            self._stats['fetches'] += 1
            page.goto(url, wait_until='networkidle', timeout=timeout * 1000)
            page.wait_for_timeout(cfg.get('wait_ms_after_nav', 3000))

            # 站特定步骤
            for step in cfg.get('extra_steps', []):
                if step == 'click_result_tab':
                    self._click_result_tab(page)
                    if cfg.get('wait_ms_after_click', 0):
                        page.wait_for_timeout(cfg['wait_ms_after_click'])

            # 等关键文本出现
            wait_text = cfg.get('wait_text')
            if wait_text:
                try:
                    page.wait_for_selector(f'text={wait_text}', timeout=5000)
                except Exception:
                    pass  # 超时也不抛——可能页结构变了，content 取先

            html = page.content()
            self._stats['success'] += 1
            self._stats['ms_total'] += int((time.time() - t0) * 1000)
            return html, None

        except Exception as e:
            self._stats['fail'] += 1
            return '', f'pw-error: {type(e).__name__}: {str(e)[:80]}'
        finally:
            try:
                page.close()
                ctx.close()
            except Exception:
                pass

    def _click_result_tab(self, page):
        """cofco 详情页：点「结果公告」tab。"""
        tabs = page.locator('text=结果公告').all()
        if tabs:
            tabs[-1].click()  # 点击最后一个（通常是 result tab）
        else:
            # 退而求其次：找所有 text 含「结果」的按钮
            tabs = page.locator('text=/结果/').all()
            if tabs:
                tabs[-1].click()

    def close(self):
        if self._browser:
            try:
                self._browser.close()
            except Exception:
                pass
        if self._playwright:
            try:
                self._playwright.stop()
            except Exception:
                pass

    def stats(self):
        return dict(self._stats)


# 模块级单例（apply_unified 调用）
_instance: Optional[PlaywrightFetcher] = None


def fetch(url: str, timeout: int = 20) -> Tuple[str, Optional[str]]:
    """Module-level fetch 函数（被 apply_unified.Tier 3 调用）。

    Returns (html, error_msg). html 为空表示失败。
    """
    global _instance
    if _instance is None:
        _instance = PlaywrightFetcher()
    return _instance.fetch(url, timeout=timeout)


def get_stats() -> dict:
    """获取 Playwright session 统计。"""
    if _instance is None:
        return {}
    return _instance.stats()


def close():
    """关闭 fetcher（cron 退出时调）。"""
    global _instance
    if _instance:
        _instance.close()
        _instance = None