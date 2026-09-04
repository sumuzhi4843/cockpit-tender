# -*- coding: utf-8 -*-
"""苏服采 areaCode 专属判定测试（2026-08-24：resolve_target_region 已删，
改为只测 classify_district——前端盐开筛选只认 std_district）"""
import unittest

from enrich_meta import classify_district
from scrapers import SufucaiScraper


class SufucaiAreaCodeOnlyTests(unittest.TestCase):
    def test_authoritative_yannan_code(self):
        raw = {"area_code": "320992", "detail_address": "盐都区新都路"}
        self.assertEqual("盐南", classify_district("盐都项目", "盐都单位", "苏服采", raw=raw))

    def test_authoritative_jingkai_code(self):
        raw = {"raw_row": {"areaCode": "320991"}, "detail_address": "亭湖区"}
        self.assertEqual("经开", classify_district("亭湖项目", "亭湖单位", "苏服采", raw=raw))

    def test_other_district_cannot_be_guessed(self):
        raw = {"area_code": "320903", "detail_address": "盐南高新区新都路"}
        self.assertEqual("盐都", classify_district("盐南项目", "盐南单位", "苏服采", raw=raw))

    def test_missing_code_stays_unassigned(self):
        raw = {"detail_address": "盐城经济技术开发区"}
        self.assertIsNone(classify_district("步凤项目", "经开单位", "苏服采", raw=raw))

    def test_other_sites_keep_text_rules(self):
        raw = {"detail_address": "盐南高新区"}
        # 非苏服采站：详情地址不再参与（已删），标题/发包人无区县词 → None
        self.assertIsNone(classify_district("普通项目", "普通单位", "其他网站", raw=raw))

    def test_page_row_inherits_request_bucket(self):
        row = SufucaiScraper._bind_area_code({"id": "1", "title": "项目"}, "320991")
        self.assertEqual("320991", row["areaCode"])
        self.assertEqual("api_request_bucket", row["_area_code_source"])

    def test_notice_row_must_match_request_bucket(self):
        with self.assertRaises(RuntimeError):
            SufucaiScraper._bind_area_code(
                {"id": "1", "areaCode": "320903"}, "320992", require_row_code=True
            )


if __name__ == "__main__":
    unittest.main()
