# -*- coding: utf-8 -*-
"""行业分类 + 项目状态回归测试（2026-09-07 M2，unittest 零依赖版）

覆盖：13 major 归并后抽样（复核表相关站点）、未分类兜底、_stage 生命周期分支。
运行：python3 tests/test_category_stage.py
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from enrich_meta import classify_category
from project_engine import _stage


class TestCategory13Majors(unittest.TestCase):
    """13 个行业大类的代表样本（2026-09-07 A1 收敛后）"""

    CASES = [
        # (title, 期望 major)
        ("XX小区物业管理服务项目采购公告", "物业与后勤服务"),
        ("XX道路改造工程施工招标公告", "工程建设"),
        ("XX工程监理服务招标公告", "工程服务与咨询"),
        ("钢材采购项目招标公告", "货物与物资采购"),
        ("XX公司股权转让公告", "资产与土地处置"),
        ("电梯维保服务项目采购公告", "设备与机电"),
        ("公交车辆采购项目招标公告", "交通运输"),
        ("化肥采购项目招标公告", "农业与农林水利"),
        ("养老院护理服务项目采购公告", "民生与社会服务"),
        ("企业宣传视频制作服务项目", "商业与金融服务"),
        ("锅炉检验检测服务项目采购公告", "环保与检测服务"),
        ("屋顶分布式光伏发电项目招标公告", "能源与市政设施"),
        ("预算管理一体化系统运维服务采购公告", "信息化与技术服务"),
    ]

    def test_13_majors(self):
        for title, expect in self.CASES:
            minor, major = classify_category(title)
            self.assertEqual(major, expect, f"{title[:30]} → 期望 {expect} 实际 {major}")

    def test_unclassified_fallback(self):
        # 无业务词标题 → (None, None) 未分类兜底
        minor, major = classify_category("测试测试测试")
        self.assertIsNone(major)


class TestProjectStage(unittest.TestCase):
    """项目生命周期 _stage 判定（12 种）"""

    def _notice(self, subtype, date="2026-09-01", bid_deadline=None):
        # notice_type 与 subtype 对应（_stage 用 n["notice_type"] 索引访问）
        ntype = {"tender_notice": "tender", "tender_reopen": "tender",
                 "award_final": "award", "award_candidate": "award",
                 "intention": "intention"}.get(subtype, "other")
        return {"id": f"test_{subtype}", "notice_subtype": subtype, "notice_type": ntype,
                "publish_date": date, "bid_deadline": bid_deadline}

    def test_terminated(self):
        stage, _ = _stage([self._notice("terminated")])
        self.assertEqual(stage, "terminated")

    def test_awarded_final(self):
        stage, _ = _stage([self._notice("award_final")])
        self.assertEqual(stage, "awarded")

    def test_awarded_candidate(self):
        stage, _ = _stage([self._notice("award_candidate")])
        self.assertEqual(stage, "awarded")

    def test_failed(self):
        stage, _ = _stage([self._notice("failed")])
        self.assertEqual(stage, "failed")

    def test_upcoming_future_open(self):
        stage, _ = _stage([self._notice("tender_notice", bid_deadline="2099-01-01 09:00")])
        self.assertEqual(stage, "upcoming")

    def test_pending_result_past_open(self):
        stage, _ = _stage([self._notice("tender_notice", bid_deadline="2020-01-01 09:00")])
        self.assertEqual(stage, "pending_result")

    def test_upcoming_unknown_time(self):
        stage, _ = _stage([self._notice("tender_notice", bid_deadline="")])
        self.assertEqual(stage, "upcoming_unknown_time")

    def test_intention_only(self):
        stage, _ = _stage([self._notice("intention")])
        self.assertEqual(stage, "intention")

    def test_correction_only(self):
        stage, _ = _stage([self._notice("correction")])
        self.assertEqual(stage, "correction")

    def test_contract_only(self):
        stage, _ = _stage([self._notice("contract")])
        self.assertEqual(stage, "contract")

    def test_terminated_wins_over_award(self):
        # 判定顺序：terminated 优先于 awarded
        stage, _ = _stage([self._notice("award_final"), self._notice("terminated")])
        self.assertEqual(stage, "terminated")

    def test_tender_wins_over_correction(self):
        # 有招标公告时更正公告不改主状态（走 tender 时间轴）
        stage, _ = _stage([
            self._notice("tender_notice", bid_deadline="2099-01-01 09:00"),
            self._notice("correction"),
        ])
        self.assertEqual(stage, "upcoming")


if __name__ == "__main__":
    unittest.main(verbosity=2)
