# -*- coding: utf-8 -*-
"""ICT 判定回归测试（2026-09-07 M2，unittest 零依赖版）

来源：老板复核表 ICT 列案例 + analyzer 方案 B（反向词/动作词）边界用例。
运行：python3 tests/test_ict.py
改 analyzer.py / config.py ICT 关键词后必须先跑本测试。
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from analyzer import is_ict


class TestReviewTableCases(unittest.TestCase):
    """老板复核表点名的误判案例（必须保持 False）"""

    def test_fengyifu_consulting(self):
        # 凤依府项目立项咨询：曾命中"平台/软件"弱词误判
        flag, _ = is_ict(
            "凤依府项目用电接入系统工程项目立项咨询服务公告",
            "本项目立项咨询服务，含平台、软件方案论证", "tender")
        self.assertFalse(flag)

    def test_kansen_land_rent(self):
        flag, _ = is_ict("康森生态园土地招租公告", "生态园土地招租", "tender")
        self.assertFalse(flag)

    def test_tennis_court_rent(self):
        # 大数据产业园网球场招租：园区名含"大数据"误判
        flag, _ = is_ict(
            "关于大数据产业园Fit空间网球场公开招租的公告",
            "大数据产业园网球场招租", "tender")
        self.assertFalse(flag)

    def test_nongmao_quick_test(self):
        # 农贸市场食品快检：曾误判
        flag, _ = is_ict(
            "盐城经济技术开发区2026年度农贸市场食品安全快速检测服务项目采购公告",
            "农贸市场食品快检服务，检测设备", "tender")
        self.assertFalse(flag)

    def test_ca_certificate(self):
        flag, _ = is_ict(
            "XX单位CA证书办理及电子签章服务项目",
            "CA证书平台服务", "tender")
        self.assertFalse(flag)


class TestRealICTKept(unittest.TestCase):
    """真 ICT 项目必须保持 True（防反向词误伤）"""

    def test_yizhong_ai_sports(self):
        flag, _ = is_ict(
            "盐城市第一中学AI智慧体育建设项目招标公告",
            "建设AI智慧体育系统含智慧平台", "tender")
        self.assertTrue(flag)

    def test_smart_traffic(self):
        flag, _ = is_ict("城市智慧交通信号灯改造项目", "智慧交通信号控制系统建设", "tender")
        self.assertTrue(flag)

    def test_video_monitoring(self):
        flag, _ = is_ict("视频监控系统采购项目", "采购高清视频监控设备", "tender")
        self.assertTrue(flag)

    def test_consulting_with_platform_building(self):
        # 咨询服务（标段含平台建设）：强词"数据空间"命中应仍 True（弱否决不拦强词）
        flag, _ = is_ict(
            "盐城市大数据集团数智协同一体化平台项目（标段二）咨询服务",
            "数智协同一体化平台建设，含数据空间", "tender")
        self.assertTrue(flag)

    def test_old_building_smart_renovation(self):
        # 老旧小区智能化采购：曾被正文"物业"政策模板词误杀（9-7 v2 修复）
        flag, _ = is_ict(
            "建湖县2026年老旧小区智能化采购及安装项目",
            "智能化系统采购安装（正文含贫困县物业公司政策条款）", "tender")
        self.assertTrue(flag)


class TestHardVetoWords(unittest.TestCase):
    """硬否决词单测（标题命中即否决）"""

    def test_hard_veto_words_list(self):
        from analyzer import ICT_NON_ICT_WORDS
        for w in ("招租", "出租", "食堂", "CA证书", "保洁", "物业"):
            self.assertIn(w, ICT_NON_ICT_WORDS, f"硬否决词缺失: {w}")

    def test_weak_veto_words_list(self):
        from analyzer import ICT_WEAK_VETO_WORDS
        for w in ("立项咨询", "咨询服务", "监理", "审计", "快检"):
            self.assertIn(w, ICT_WEAK_VETO_WORDS, f"弱否决词缺失: {w}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
