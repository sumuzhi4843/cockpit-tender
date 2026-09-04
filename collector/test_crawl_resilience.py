# -*- coding: utf-8 -*-
"""不联网的抓取韧性回归测试。"""
import json
import os
import tempfile
import unittest
import sqlite3
from unittest.mock import patch

import ccgp_scraper
import crawl_ledger
import run


class FakeResponse:
    def __init__(self, *, payload=None, content=b"captcha"):
        self._payload = payload
        self.content = content
        self.text = json.dumps(payload) if payload is not None else ""

    def json(self):
        if self._payload is None:
            raise ValueError("invalid json")
        return self._payload


class FakeSession:
    def __init__(self, total=630, fail_page=None):
        self.total = total
        self.fail_page = fail_page
        self.headers = {}

    def get(self, url, params=None, timeout=None):
        if url.endswith(ccgp_scraper.ZONE_API):
            return FakeResponse(payload={})
        if url.endswith(ccgp_scraper.CAPTCHA_URL):
            return FakeResponse(content=b"captcha")
        page = int(params["page"])
        if page == self.fail_page:
            return FakeResponse(payload=None)
        start = (page - 1) * 10
        rows = [
            {
                "id": f"{page}-{i}", "ggCode": "cggg", "title": f"项目 {page}-{i}",
                "publishDate": "2026-08-22",
            }
            for i in range(10)
            if start + i < self.total
        ]
        return FakeResponse(payload={"code": 200, "result": {"count": self.total, "list": rows}})

    def close(self):
        pass


class CrawlResilienceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.ledger = os.path.join(self.tmp.name, "crawl.db")

    def make_scraper(self, session):
        scraper = ccgp_scraper.CcgpJiangsuScraper(use_cdp=False)
        scraper.set_scope_filter(["cggg:320902"])
        scraper.session = session
        scraper.captcha_solver.solve = lambda _: ("ABCD", 1.0)
        return scraper

    def test_dynamic_pagination_exceeds_old_25_page_limit(self):
        scraper = self.make_scraper(FakeSession(total=630))
        with patch.object(ccgp_scraper, "REQUEST_DELAY", 0), \
             patch.object(crawl_ledger, "DB_PATH", self.ledger):
            items = scraper.scrape(fetch_details=False, ict_only=False)
        self.assertEqual(630, len(items))
        self.assertEqual("success", scraper.health()["status"])
        self.assertEqual(63, scraper.fetch_stats[0]["expected_pages"])

    def test_partial_page_failure_enters_gap_state(self):
        scraper = self.make_scraper(FakeSession(total=630, fail_page=26))
        with patch.object(ccgp_scraper, "REQUEST_DELAY", 0), \
             patch.object(ccgp_scraper.time, "sleep", return_value=None), \
             patch.object(crawl_ledger, "DB_PATH", self.ledger):
            items = scraper.scrape(fetch_details=False, ict_only=False)
        self.assertEqual(250, len(items))
        health = scraper.health()
        self.assertEqual("partial", health["status"])
        self.assertEqual(["cggg:320902"], health["failed_scopes"])
        self.assertIn(26, scraper.fetch_stats[0]["failed_pages"])
        conn = sqlite3.connect(self.ledger)
        row = conn.execute(
            "SELECT status, expected_count, fetched_count, failed_pages FROM crawl_runs"
        ).fetchone()
        conn.close()
        self.assertEqual(("partial", 630, 250), row[:3])
        self.assertIn(26, json.loads(row[3]))

    def test_failed_status_file_preserves_scope_details(self):
        path = os.path.join(self.tmp.name, "failed.json")
        states = {
            "江苏政府采购网": {
                "status": "partial", "failed_scopes": ["cggg:320902"],
                "error": "page 26",
            }
        }
        with patch.object(run, "FAILED_FILE", path):
            run._write_failed("run-1", "2026-08-22T09:00:00", ["江苏政府采购网"], states)
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(["江苏政府采购网"], data["failed"])
        self.assertEqual(["cggg:320902"], data["details"]["江苏政府采购网"]["failed_scopes"])


if __name__ == "__main__":
    unittest.main()
