# -*- coding: utf-8 -*-
"""区县归类回归测试（2026-09-07 M2，unittest 零依赖版）

来源：老板复核表 8.28/9.4 点名案例 + 修复时构造的边界用例。
运行：python3 tests/test_district.py（只读，不写库）
改区县规则（enrich_meta.py）后必须先跑本测试。
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from enrich_meta import classify_district


class TestForeignSitesBlocked(unittest.TestCase):
    """外地项目必须不参与盐城区县判定（9.4 复核案例）"""

    def test_jiangxi_water_institute(self):
        # 江西水利职业学院：tenderer_address 是南昌经开区 → 曾误归经开
        got = classify_district(
            "江西省机电设备招标有限公司关于江西水利职业学院财智一体化平台项目竞争性磋商结果公告",
            "江西水利职业学院", "中国政府采购网",
            tenderer_address="江西省南昌市经济技术开发区北山路99号",
        )
        self.assertIsNone(got, f"外地项目误归: {got}")

    def test_tianjin_tech_park(self):
        got = classify_district(
            "中共天津经济技术开发区委员会办公室 天津经开区媒体运维服务项目成交公告",
            "中共天津经济技术开发区委员会办公室（天津经济技术开发区管理委员会办公室）",
            "中国政府采购网",
            tenderer_address="天津经济技术开发区融义路1352号",
        )
        self.assertIsNone(got)

    def test_tianjin_bonded_zone_school(self):
        # 天津港保税区第一中学：曾因"第一中学"弱词误归盐南
        got = classify_district(
            "天津港保税区第一中学家具项目招标公告",
            "天津港保税区第一中学", "中国政府采购网",
            tenderer_address="天津空港经济区书康路5号",
        )
        self.assertIsNone(got)

    def test_dalian_tech_zone(self):
        got = classify_district(
            "体育公园及滨海公园微改造项目更正公告",
            "某单位", "中国政府采购网",
            tenderer_address="大连经济技术开发区金马路203号",
        )
        self.assertIsNone(got)

    def test_liaoning_binhai_campus(self):
        got = classify_district(
            "辽宁石化职业技术学院滨海校区致远馆二期改造工程项目",
            "辽宁石化职业技术学院", "中国政府采购网", "",
        )
        self.assertIsNone(got)

    def test_shandong_binhai_police(self):
        # 山东省滨海公安局（老板 9-7 点名）：不是盐城滨海县
        got = classify_district(
            "山东省 滨海 公安局（驻齐鲁石化）侦查中心专用设备购置竞争性磋商公告",
            "山东省滨海公安局", "中国政府采购网", "",
        )
        self.assertIsNone(got)


class TestLocalDistrictsCorrect(unittest.TestCase):
    """盐城本地项目正确归类（防守卫误伤）"""

    def test_gonganjing_fenju(self):
        got = classify_district(
            "农村地区智慧安防村居建设三期项目采购公告",
            "盐城市公安局盐城经济技术开发区分局", "中国政府采购网", "",
        )
        self.assertEqual(got, "经开")

    def test_binhai_port(self):
        got = classify_district(
            "盐城滨海港经济区安防救援无人艇采购项目",
            "盐城滨海港经济区管委会", "中国政府采购网",
            tenderer_address="滨海港经济区兴淮路35号",
        )
        self.assertEqual(got, "滨海")

    def test_binhai_xiangzhen(self):
        got = classify_district(
            "滨海沿海工业园供热管网项目一期监理服务评标结果公示",
            "滨海县沿海投资发展有限公司", "盐城市公共资源交易网", "",
        )
        self.assertEqual(got, "滨海")

    def test_sheyang(self):
        got = classify_district(
            "射阳县自然资源和规划局土地出让公告",
            "射阳县自然资源和规划局", "盐城市公共资源交易网", "",
        )
        self.assertEqual(got, "射阳")

    def test_yizhong_school_entity(self):
        got = classify_district(
            "盐城市第一中学AI智慧体育建设项目招标公告",
            "盐城市第一中学", "盐城市人民政府网", "",
        )
        self.assertEqual(got, "盐南")

    def test_jingmao_school(self):
        got = classify_district(
            "盐城市经贸高级职业学校2026-2027年度食堂劳务外包服务项目",
            "盐城市经贸高级职业学校", "盐城市政府采购网",
            tenderer_address="亭湖区伍佑街道",
        )
        self.assertEqual(got, "盐南")

    def test_jianhu_no1_middle(self):
        got = classify_district(
            "建湖县第一中学改造项目", "建湖县第一中学", "盐城市公共资源交易网", "",
        )
        self.assertEqual(got, "建湖")

    def test_local_site_bare_word(self):
        # 盐城本地政府站：裸词"滨海"可判（站点即盐城上下文，9-7 补充）
        got = classify_district(
            "2023全球滨海论坛展览活动及同传速记项目中标公告",
            "", "盐城市政府采购网", "",
        )
        self.assertEqual(got, "滨海")

    def test_sufucai_city_level_school_exception(self):
        # 苏服采 320900 市本级桶 + 学校实体 → 盐南（9-7 修复）
        got = classify_district(
            "盐城市第一中学AI智慧体育建设项目等项目招标代理服务成交公告",
            "盐城市第一中学", "苏服采", area_code="320900",
        )
        self.assertEqual(got, "盐南")

    def test_sufucai_city_level_non_school(self):
        got = classify_district(
            "盐城市区道路照明设施维护项目", "盐城市路灯管理处", "苏服采", area_code="320900",
        )
        self.assertEqual(got, "市级")

    def test_ecology_bureau_city_level(self):
        # 盐城市生态环境局：曾误归经开 → 应市级
        got = classify_district(
            "盐城市生态环境局2026年7月政府采购意向公告",
            "盐城市生态环境局", "盐城市政府采购网", "",
        )
        self.assertEqual(got, "市级")

    def test_binhai_chentao_town(self):
        got = classify_district(
            "滨海县陈涛镇长吉一号桥和安防提升改造工程重新招标评标结果公示",
            "滨海县陈涛镇人民政府", "盐城市公共资源交易网", "",
        )
        self.assertEqual(got, "滨海")


if __name__ == "__main__":
    unittest.main(verbosity=2)
