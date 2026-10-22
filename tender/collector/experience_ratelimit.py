# -*- coding: utf-8 -*-
"""2026-10-01 风控防护（Layer 1: 限速 + 重试 + CAPTCHA 检测）。

从 rate_limits.yaml 读 per-host 配置（delay / batch / captcha marker），
每次 fetch 后 sleep、按 429/403/CAPTCHA 自动 retry 或 abort。
"""
import re
import sys
import time
import logging
import yaml
import requests
from pathlib import Path
from dataclasses import dataclass, field
from collections import defaultdict
from urllib.parse import urlparse

HERE = Path(__file__).parent


class AbortError(Exception):
    """风控触发 abort（CAPTCHA / 持续 403 / 持续 429），调用方应立即停止。"""


@dataclass
class HostRateLimit:
    """per-host 限速配置（对应 rate_limits.yaml 一行）。"""
    delay_s: float = 1.5           # 每次 fetch 后 sleep
    batch_size: int = 20           # 一批多少条
    batch_pause_s: float = 10.0    # 批后 sleep
    max_per_session: int = 100     # 单次会话上限（防 WAF 累计计数）
    captcha_markers: list = field(default_factory=list)  # CAPTCHA/WAF 关键字（小写匹配）


class RateLimitedFetcher:
    """带限速 + 重试 + CAPTCHA 检测的 fetcher。

    用法：
            fetcher = RateLimitedFetcher('rate_limits.yaml')
            text, err = fetcher.fetch(url)
            if err: ...
            elif 'CAPTCHA' in err: raise AbortError(...)
    """

    DEFAULT_LIMIT = HostRateLimit()

    def __init__(self, config_path='rate_limits.yaml'):
        self.config_path = Path(config_path)
        if not self.config_path.is_absolute():
            self.config_path = HERE / config_path
        self.config = self._load_config(self.config_path)
        self.session = self._make_session()
        self.stats = defaultdict(lambda: defaultdict(int))  # host -> {success/fail/...}

    def _load_config(self, path):
        if not Path(path).exists():
            return {}
        with open(path, encoding='utf-8') as f:
            data = yaml.safe_load(f) or {}
        cfg = {}
        for host, params in data.items():
            cfg[host] = HostRateLimit(**{k: v for k, v in params.items() if k in HostRateLimit().__dataclass_fields__})
        return cfg

    def _make_session(self):
        """Real Chrome 120 on Mac + 拒 gzip 解码，跟真实样式一致。"""
        s = requests.Session()
        s.headers.update({
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
            'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
            'Cache-Control': 'no-cache',
            'Pragma': 'no-cache',
        })
        return s

    def get_limit(self, host):
        return self.config.get(host, self.DEFAULT_LIMIT)

    def fetch(self, url):
        """Fetch URL with retry + rate limit.

        Returns:
            (text, None) 成功
            (None, 'http_403' / 'http_503' / 'http_xxx' / 'max_retry' / '<Exception msg>') 失败
        Raises:
            AbortError: CAPTCHA 检测 / 持续 429（已重试 3 次）
        """
        host = urlparse(url).netloc
        rate = self.get_limit(host)

        time.sleep(rate.delay_s)

        last_err = None
        for attempt in range(1, 4):  # 3 attempts max
            t0 = time.time()
            try:
                resp = self.session.get(url, timeout=15, allow_redirects=True)
                dur_ms = int((time.time() - t0) * 1000)

                if resp.status_code == 200:
                    # 中文站可能是 GBK/GB2312，requests 默认 utf-8 解码会出乱码（东方集团就是）
                    # 用 apparent_encoding 从 HTML meta 自动探测
                    text = resp.content.decode(resp.apparent_encoding or 'utf-8', errors='ignore')
                    # CAPTCHA / 风控标记检测（小写）
                    text_lower = text.lower()
                    for marker in rate.captcha_markers:
                        if marker.lower() in text_lower:
                            self.stats[host]['captcha'] += 1
                            raise AbortError(f"CAPTCHA '{marker}' detected on {host} (url={url[:80]})")
                    self.stats[host]['success'] += 1
                    self.stats[host]['ms_total'] += dur_ms
                    return text, None

                elif resp.status_code == 429:
                    last_err = '429'
                    self.stats[host]['429'] += 1
                    # 429 退避：60s、120s、180s
                    backoff = 60 * attempt
                    time.sleep(backoff)
                    continue

                elif resp.status_code in (403, 503):
                    self.stats[host][f'http_{resp.status_code}'] += 1
                    return None, f'http_{resp.status_code}'

                else:
                    self.stats[host][f'http_{resp.status_code}'] += 1
                    return None, f'http_{resp.status_code}'

            except requests.RequestException as e:
                last_err = str(e)[:80]
                self.stats[host]['exc'] += 1
                time.sleep(2 * attempt)
                continue

        # 3 次重试全失败
        if last_err == '429':
            # 持续 429 → abort（人在回路处理）
            raise AbortError(f"持续 429 on {host} (url={url[:80]})")
        return None, last_err or 'max_retry'

    def get_stats(self):
        """导出当前会话统计（老板 review 用）。"""
        out = {}
        for host, s in self.stats.items():
            success = s.get('success', 0)
            fail = sum(v for k, v in s.items() if k not in ('success', 'ms_total'))
            ms_total = s.get('ms_total', 0)
            avg_ms = (ms_total // success) if success else 0
            out[host] = {
                'success': success,
                'fail': fail,
                'fail_rate': (fail / (success + fail)) if (success + fail) else 0,
                'avg_ms': avg_ms,
                'by_error': {k: v for k, v in s.items() if k not in ('success', 'ms_total')},
            }
        return out


def load_config(config_path='rate_limits.yaml'):
    """Public: load YAML 配置（独立于 fetcher 状态）。"""
    return RateLimitedFetcher(config_path).config