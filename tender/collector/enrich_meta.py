# -*- coding: utf-8 -*-
"""元数据富化 — 区县 / 行业分类 / 中小微标签（Phase 5）

适配本地单库 tender.db（原仓库为每站独立 DB + unified.db）。
为 notices 表追加列并批量打标：
  std_district    — 区县（盐南/经开/盐都/亭湖/大丰/响水/滨海/阜宁/射阳/建湖/东台/市级）
  proj_major_cat  — 行业大类（如 物业外包服务/市政工程/信息化商机池）
  proj_minor_cat  — 行业小类
  sme_target      — 中小微标签（专门面向/非专门但优惠/不涉及）

行业分类规则来自 rules/category.yaml（must_any 命中 + must_not 排除）。
未命中任何分类 = 信息化商机池（反向分类思路，见 analyzer 的 ICT 正向匹配互补）。

用法:
  python3 enrich_meta.py            # 全量打标
  python3 enrich_meta.py --stats    # 仅打印统计
"""
import argparse
import json
import re
import sqlite3
import urllib.parse
import urllib.request
from pathlib import Path

try:
    import yaml as _yaml
    _YAML_PATH = Path(__file__).parent / "rules" / "category.yaml"
    _raw = _yaml.safe_load(_YAML_PATH.read_text(encoding="utf-8"))
    RULES = [
        (r["minor"], r["major"], r["must_any"], r.get("must_not") or [])
        for r in _raw["categories"]
    ]
except Exception as _e:
    print(f"[warn] 无法加载 rules/category.yaml: {_e}")
    RULES = []

DB_PATH = Path(__file__).parent / "data" / "tender.db"

# 2026-09-13 方案3（增量富化）：规则版本号；改富化规则时 +1 会自动触发一次全库重算。
ENRICH_VERSION = 2

# ── 区县关键词（顺序敏感：越具体越优先）──
# 学校精准覆盖（2026-08-30 老板拍板）——学校本体归属优先于：
#  a) 区县名子串命中（防误吸：如「盐都区新都路618号」类文本；注意「新都路」本身
#     仍是跨区混杂词，8-19 已验证禁止单独收录，只能用学校全称级关键词）
#  b) 站点整站硬编码（SITE_DISTRICT，见 classify_district L0 例外分支）
# 证据：
#  - 京师实验学校：盐城经济技术开发区新都东路118号（凤依湖畔），
#    经开区管委会官网 2024-12「学在盐开」专题稿
#  - 新都路实验学校（原新都路小学）：盐南高新区新都中路16号，
#    盐南管委会 2025-07-17 施教区公告，纳入聚亨路小学教育集团
# 优先级方向说明（与下方「第一中学」块相反，勿合并）：
#  - 本块在区县名【之前】：学校名必须赢过区名子串与站点硬编码
#  - 「第一中学」块在区县名【之后】：县中（建湖县第一中学/东台市第一中学）
#    要被区名先截住，只有无区名的市直属校才落到学校词
SCHOOL_DISTRICT_KEYWORDS = [
    ("京师实验学校", "经开"),
    ("新都路实验学校", "盐南"),
    ("新都路小学", "盐南"),
    # 盐城市第一中学（蓝海路/润海路/西环路校区，盐南高新区）→ 盐南（2026-08-17 老板规则）
    # 2026-08-30 升级为 L1 实体映射：招标人地址按行政区划标注为「盐都区西环路6号」，
    # 地址证据先命中「盐都」导致同批次 6 项目中 3 个错归盐都（老板点名）。
    # 只用带市前缀全称/固定简称，防建湖县第一中学/东台市第一中学误伤。
    ("盐城市第一中学", "盐南"),
    ("盐城一中", "盐南"),
    # 盐城市经贸高级职业学校：盐南高新区伍佑街道通榆南路3号（财政局/市政府官网采购公告一致）。
    # 地址常被标注为「亭湖区伍佑街道」（行政区划），错归亭湖（2026-08-30 老板点名）。
    ("盐城市经贸高级职业学校", "盐南"),
]

DISTRICT_KEYWORDS = [
    *SCHOOL_DISTRICT_KEYWORDS,
    # 行政区划名（具体，无跨市复用问题）——必须先于功能区泛词：
    # 「阜宁县城南新区上海路168号」要先命中阜宁而非城南新区（2026-08-30 中采网案例）
    ("亭湖", "亭湖"), ("盐都", "盐都"), ("盐南", "盐南"),
    ("响水", "响水"), ("滨海县", "滨海"), ("阜宁", "阜宁"),
    ("射阳", "射阳"), ("建湖", "建湖"), ("大丰", "大丰"), ("东台", "东台"),
    # 功能区泛词（跨市复用：武汉/潍坊/济南等几十个经开区；外地上下文由外地项目过滤拦截）
    # 2026-09-06：泛词拆分处理——(经济技术开发区/经开区/城南新区/第一中学) 不再直接放这里，
    # 改由 _match_district_keyword 带盐城上下文判断，防天津/南昌/大连等外地经开区误伤、
    # 外地“第一中学”（如天津港保税区第一中学）误归盐南。
    # 保留盐城独有/无冲突词：步凤镇/新城街道属经开区（老板拍板一汽整站划盐开后同类补齐）
    ("盐城一中", "盐南"),
    ("步凤", "经开"), ("新城街道", "经开"),
]

# 跨市复用泛词 → 区县（2026-09-06 拆分）：命中时必须先有盐城上下文才判，
# 防外地经开区/滨海新区/城南新区项目误归盐城区县（9.4 复核：江西水利、天津经开区…）
AMBIGUOUS_KEYWORDS = [
    ("经济技术开发区", "经开"),
    ("经开区", "经开"),
    ("城南新区", "盐南"),
    ("滨海", "滨海"),   # 盐城滨海县 vs 天津滨海新区；需盐城上下文
    ("第一中学", "盐南"),  # 盐城一中/各县一中 vs 外地一中（天津港保税区第一中学案例）
]

# 盐城上下文特征：命中任一项即视为“盐城本地”（决定泛词可否判定区县）
# 2026-09-06 新增。用于 L1b 地址 / L2 文本的泛词守卫。
_YANCHENG_CTX_WORDS = (
    "盐城", "亭湖", "盐都", "盐南", "射阳", "建湖", "大丰", "东台", "响水",
    "阜宁", "滨海县", "滨海港",
)

# 盐城本地政府站（2026-09-06 补充）：站点本身即盐城上下文。
# 这些站只发盐城本地公告（czj.yancheng.gov.cn 盐城政采 / www.yancheng.gov.cn
# 政府网 / ycggzy.jszwfw.gov.cn 公共资源交易网），标题仅有"滨海/经开/城南"裸词
# 时（如“全球滨海论坛”）也应正常判区县，无需再出现"盐城"字面。
# 注意：不含全国站（中国政采/江苏政采）与功能区本地站（走 SITE_DISTRICT 不会到 L2）。
_YANCHENG_LOCAL_SITES = frozenset({
    "盐城市政府采购网",
    "盐城市人民政府网",
    "盐城市公共资源交易网",
})


def _has_yancheng_ctx(*texts: str) -> bool:
    """拼接文本中是否存在盐城本地上下文（防外地泛词误伤）。"""
    joined = " ".join(t or "" for t in texts)
    return any(w in joined for w in _YANCHENG_CTX_WORDS)

# ── 苏服采 areaCode → 区县映射（2026-08-21）──
# 苏服采 API 后端已按区县编码分发项目（每个 row 自带 areaCode 字段），
# 平台权威分类比自家猜的关键词可靠，不需要二次分配。
# 11 个区县级码 + 320900 父级（市本级）。
# 注：与现有 std_district 取值风格一致（短名，无"县/区"后缀），避免改名。
SUFUCAI_AREA_CODE_MAP = {
    "320900": "市级",       # 父级（市本级）
    "320902": "亭湖", "320903": "盐都", "320904": "大丰",
    "320921": "响水", "320922": "滨海", "320923": "阜宁",
    "320924": "射阳", "320925": "建湖", "320981": "东台",
    "320991": "经开", "320992": "盐南",
}

# 站点级硬编码（仅盐城本地站，行政区划明确）
# 2026-08-19：补齐「城南新区公共资源交易网」「开发区公共资源交易网」—
# 老板列出的区域归属清单里明记「全部归属盐开」，原本缺这两条，导致
# 标题不含具体区县词的项目被误归市级。
SITE_DISTRICT = {
    # 盐南站（2026-08-19 图片确认）
    "盐城市大数据集团": "盐南",
    "盐城市都市建设投资集团有限公司": "盐南",
    "城南新区公共资源交易网": "盐南",   # 2026-08-19 补
    # 江苏世纪新城（2026-08-24 老板工商信息纠正）：
    # 江苏世纪新城投资控股集团有限公司，国有企业，注册地址盐城经济技术开发区黄山路9号 → 经开。
    # 原 8-19 图片确认误归盐南，本次纠正。
    "江苏世纪新城": "经开",
    # 经开站（2026-08-19 图片确认）
    "盐城市东方集团": "经开",
    "经开城发集团": "经开",
    "开发区公共资源交易网": "经开",   # 2026-08-19 补
    # 一汽电子采购（2026-08-20 老板拍板）：全部为盐城分公司项目，
    # 注册地址盐城经济技术开发区珠江路22号 → 整站划入盐开区
    "一汽电子采购": "经开",
}

# 公司精准映射（注册地址明确、名称无区县词）
COMPANY_MAP = [
    ("悦达", "经开"), ("盐城华数", "盐南"), ("盐城大数据", "盐南"),
    ("伍佑", "盐南"),  # 伍佑属盐南高新区（2026-08-04）
    ("盐城福汇", "经开"),  # 2026-10-09：中粮e采_盐城福汇项目被分到"市级"，实际在中粮盐城经开园区（老板复核）
]

# 苏服采 source_scope=320900 只代表“盐城市全市”，不能据此伪造项目所在地。
# 目标区域需要单独的地址/单位/标题证据；无法证明时保留为市级。
# 2026-08-19：按企业库注册地址分布验证后扩充高置信特征（≥85% 盐南）：
#   康居路 127/128、串场河 176/176、聚亨路 72/81、人民南路 98%、日月路 94%、
#   蓝海路 93%、润海路 99%、戴庄路 99%、解放南路 86%、伍佑 97%。
# 跨区混杂词（盐渎 4%、西环路 13%、文港、新都路、世纪大道）不收录，防误伤。
# ── 2026-10-20 摘除企业库联动（老板拍板：后续不再使用该功能）──
# 背景：ykcompany 企业库服务已下线——8001 端口不监听，ykqcc.db 及 enterprise/db/ 目录均不存在。
#   原 _company_lookup_api() 异常时返回 []，故 _company_target_region() 早已恒返回 (None, None)，
#   本段属「对着死服务空转」的死代码，摘除后行为零变化。
# 一并删除：ENTERPRISE_API / _COMPANY_REGION_CACHE / _COMPANY_LOOKUP_TIMEOUT /
#           _company_lookup_api() / BRANCH_SUFFIX_WORDS / _ENTERPRISE_HINT_WORDS /
#           _NON_ENTERPRISE_WORDS / _looks_like_local_enterprise() / _company_target_region()
# 备份：enrich_meta.py.bak-20261020-drop-ent
# 注意：LOCAL_ADMIN_PREFIXES / _CITY_PREFIX_RE 保留（下方 L0.5 外地过滤仍在用，勿删）。

# 外地项目过滤（2026-08-30 中采网案例）：全国性站点招标人常以外市行政区开头
# （武汉/潍坊/台州/天津/纳雍…），标题/地址里的「经济技术开发区/滨海」等泛词
# 会误归盐城区县。招标人以盐城辖区行政区开头 → 正常判定；以外市行政区开头 → 不参与区县判定。
LOCAL_ADMIN_PREFIXES = {
    "盐城市", "盐南高新区", "城南新区", "盐城经济技术开发区",
    "亭湖区", "盐都区", "大丰区", "东台市",
    "响水县", "滨海县", "阜宁县", "射阳县", "建湖县",
}
_CITY_PREFIX_RE = re.compile(r'^([\u4e00-\u9fa5]{1,4}?(?:市|县|区|州|盟|旗))')



# ── 详情地址 → 区县解析（2026-08-20 细分增强）──
# 2026-08-24 已删除：详情地址只回填在苏服采（backfill_sufucai_detail.py），
# 而苏服采已改为只认 API areaCode（0 级）直接返回 → 本套逻辑为死代码。
# 2026-08-24 再删：resolve_target_region 也已移除——前端盐开区筛选自 8-20 起
# 只认 std_district（与卡片显示同源），target_region 字段无人消费（冗余）。


# 2026-09-19 江苏招标平台（jszbcg，SPA站）列表 content 头部带平台官方地域面包屑，
# 形如「大数据…招标公告 江苏省/盐城市/大丰区 房屋建筑 工具发布 开标时间：…」。
# 该标注是平台按项目实际归属打的，权威度等同 areaCode；但 2026-09-19 老板复核发现
# 「大丰区」这类只出现在 content、title/tenderer 无区县词的项目被整批漏判（752 条）。
_JSZBCG_BREADCRUMB_RE = re.compile(r"江苏省/盐城市/([\u4e00-\u9fa5]{1,4}[区县市])")


def _match_district_keyword(*texts: str) -> tuple | None:
    """在文本中匹配区县关键词，返回 (区县, 命中的词)。

    2026-09-06 重构：
    - 盐城独有具体词（亭湖/盐都/射阳…/步凤/第一中学 等）→ 直接命中即判；
    - 跨市复用泛词（经济技术开发区/经开区/城南新区/滨海）→ 要求同文含盐城上下文，
      防天津经开区/南昌经开区/大连经开区/天津滨海新区等外地项目误归盐城区县。
      （9.4 复核案例：江西水利职业学院→南昌经开区地址误判经开；
       天津经开区/大连经开区项目正文含“经济技术开发区”误判经开）
    """
    text = " ".join(t or "" for t in texts)
    # 第一遍：具体词（无歧义，直接命中）
    for kw, district in DISTRICT_KEYWORDS:
        if kw in text:
            return district, kw
    # 第二遍：跨市泛词（需盐城上下文）
    for kw, district in AMBIGUOUS_KEYWORDS:
        if kw in text and _has_yancheng_ctx(text):
            return district, kw
    return None


def classify_district(title, tenderer, site, area_code=None, raw=None, tenderer_address=None):
    """返回区县或 None。等级（2026-08-25 整理合并，老板拍板）：

    L0 站点平台权威（最高，禁止二次猜测）
      苏服采 areaCode / 江苏政采 zone_code / 本地站整站硬编码 SITE_DISTRICT
    L1 发包人信息（普通站点第一等级，2026-08-25 老板拍板）
      1a 学校实体映射（招标人/标题命中学校全称 → 判定，2026-08-30 新增）
         ——实体归属优先于地址：盐南/经开功能区学校的注册地址常按行政区划
         标注为盐都区/亭湖区（一中「盐都区西环路6号」、经贸学校「亭湖区伍佑街道」），
         地址证据会误判，学校本体（老板拍板实体）更权威
      1b 招标人地址（tenderer_address）含区县词 → 判定
      1c 招标人名称命中公司映射（悦达→经开 等）
      1d 招标人名称命中企业库注册地址（机构后缀过滤，防分支机构）
    L2 文本关键词（标题+发包人全文区县词）
    L3 兜底（含盐城 → 市级）

    合并说明（相对 2026-08-24 版）：
    - 原 2 级公司映射 + 原 3 级企业库 + 新增招标人地址 合并为「L1 发包人信息」，
      并提到文本关键词之前——招标人是谁/在哪，比标题泛词更能说明项目归属。
    - 企业库不再要求「发包人出现在标题中」：tenderer 字段本身取自"招标人：XXX"，
      已是项目主体而非代理机构；仍保留机构后缀过滤（中国邮政案例）。
    - 站点权威（苏服采/江苏政采/本地站硬编码）保持最高位不变。
    """
    text = f"{title or ''} {tenderer or ''}"

    # L0. 站点平台权威（最高，老板多次拍板"禁止二次猜测"）
    if site == "苏服采":
        raw = raw or {}
        raw_row = raw.get("raw_row") or {}
        api_code = str(
            raw.get("area_code") or raw.get("areaCode") or
            raw_row.get("areaCode") or area_code or ""
        ).strip()
        dist = SUFUCAI_AREA_CODE_MAP.get(api_code)
        # 2026-09-06：苏服采市本级桶(320900)的学校项目例外——平台把学校项目
        # （如一中/新都路实验学校）的招标代理/安保等公告归入 320900 市本级，
        # 但项目主体（学校）在盐南/经开。按老板 8-30"学校本体归属优先"，
        # 市本级桶先查学校实体，命中则按学校归属（8.28 复核：一中 AI 智慧
        # 体育招标代理成交、新都路实验学校安保归市级为空）。
        if dist == "市级":
            text_entity = f"{tenderer or ''} {title or ''}"
            for kw, school_district in SCHOOL_DISTRICT_KEYWORDS:
                if kw in text_entity:
                    return school_district
        return dist
    if site == "江苏政府采购网":
        raw = raw or {}
        zone_code = str(
            raw.get("zone_code") or raw.get("area_code") or raw.get("areaCode") or ""
        ).strip()
        return SUFUCAI_AREA_CODE_MAP.get(zone_code)
    if site in SITE_DISTRICT:
        # 学校本体归属 > 整站硬编码（2026-08-30 老板拍板）：
        # 如「新都路小学改造项目」发布在硬编码经开的江苏世纪新城站，
        # 仍应按学校所在地归盐南；非学校项目维持整站硬编码不变。
        for kw, district in SCHOOL_DISTRICT_KEYWORDS:
            if kw in (title or ""):
                return district
        return SITE_DISTRICT[site]

    # L0.4 站点结构化面包屑（2026-09-19 大丰漏判案例）：江苏招标平台列表 content 头部
    # 带「江苏省/盐城市/X区县市」官方地域标注，此前判定链只看 title/tenderer、
    # 永远看不到 content，导致该站 752 条空区县。放在 L0.5 前：平台标注比
    # 招标人名称前缀启发式更权威。命中且能映射到本市区县才返回，否则继续走链。
    if site == "江苏招标采购服务平台":
        _bc_content = (raw or {}).get("content") or ""
        _bc = _JSZBCG_BREADCRUMB_RE.search(_bc_content[:500])
        if _bc:
            _bd = _match_district_keyword(_bc.group(1))
            if _bd:
                return _bd[0]

    # L0.5 外地项目过滤（2026-08-30 中采网案例，不涉及 L0 权威层）：
    # 招标人以外市行政区开头（武汉市/台州市/纳雍县…）→ 全国站外地项目，
    # 不参与盐城区县判定；招标人以盐城辖区开头或无行政区前缀 → 正常走 L1。
    if tenderer:
        m = _CITY_PREFIX_RE.match(tenderer.strip())
        if m and m.group(1) not in LOCAL_ADMIN_PREFIXES:
            return None

    # L1. 发包人信息（普通站点第一等级）
    # 1a. 学校实体映射（boss-certified，优先于地址；2026-08-30 一中/经贸学校案例）
    text_entity = f"{tenderer or ''} {title or ''}"
    for kw, district in SCHOOL_DISTRICT_KEYWORDS:
        if kw in text_entity:
            return district
    # 1b. 招标人地址含区县词（2026-09-06：泛词需盐城上下文，防外地经开区地址误伤）
    if tenderer_address:
        _m = _match_district_keyword(tenderer_address, tenderer, title)
        if _m:
            return _m[0]
    # 1c. 招标人名称命中公司精准映射（注册地址明确、名称无区县词）
    if tenderer:
        for kw, district in COMPANY_MAP:
            if kw in tenderer:
                return district
    # 1d. 企业库注册地址辅助判定：2026-10-20 已随企业库联动整体摘除（服务下线，代码长期空转）。

    # L2. 文本关键词（标题+发包人全文，区县名在前防误伤县中学）
    # 2026-09-06：泛词需盐城上下文（text 含盐城/区县名），防外地项目文本泛词误伤；
    #   盐城本地政府站（czj.yancheng / yancheng.gov.cn 等）站点本身即盐城上下文，
    #   标题仅有"滨海""经开"裸词（如"全球滨海论坛"）也正常判定（9-6 复核补充）
    if site in _YANCHENG_LOCAL_SITES:
        _m = _match_district_keyword(text, "盐城")
    else:
        _m = _match_district_keyword(text)
    if _m:
        return _m[0]

    # L3. 兜底：无法归到具体区县的盐城项目 → 市级
    if "盐城" in text:
        return "市级"
    return None


def classify_category(title):
    """返回 (小类, 大类)。未命中返回 (None, None)。"""
    if not title:
        return None, None
    for minor, major, must_any, must_not in RULES:
        if any(kw in title for kw in must_any):
            if not any(kw in title for kw in must_not):
                return minor, major
    return None, None


# ── 中小微标签（基于 raw_json.content 正文）──
TARGETED_PATTERNS = [
    r'本项目\s*(?:仅|只|专门)\s*.*?中小[微小]?\s*企业',
    r'面向\s*中小[微小]?\s*企业\s*预留',
    r'预留\s*份额.*?中小[微小]?\s*企业',
    r'本项目\s*属于\s*.*?专门\s*面向\s*中小[微小]?',
    r'非\s*中小[微小]?\s*企业\s*不得\s*参加',
    r'限\s*中小[微小]?\s*企业\s*.*?投标',
    r'本采购包\s*专门\s*面向\s*中小[微小]?',
    r'(?:接受|仅接受)\s*中小[微小]?\s*企业',
    r'本项目\s*专门\s*面向\s*中小[微小]?\s*企业',
]

PREFERENCE_PATTERNS = [
    r'价格\s*扣除\s*[优惠折扣]?',
    r'(?:给予|按).{0,10}\d+\s*%\s*扣[除价]',
    r'小微[型企业].*?\d+\s*%\s*扣[除价]',
    r'(?:小微型企业|小微企业).*?价格.{0,5}扣[除价]',
    r'小微[型企业].*?优惠',
    r'执行价格扣除优惠',
]

TEMPLATE_SECTION = re.compile(r'十[、\.]\s*附件|附件[：:]')


def classify_sme(content):
    """对正文内容分类中小微标签。"""
    if not content:
        return "不涉及"
    for pat in TARGETED_PATTERNS:
        m = re.search(pat, content)
        if m:
            ctx = content[max(0, m.start() - 200):m.end() + 200]
            if TEMPLATE_SECTION.search(ctx):
                continue
            return "专门面向"
    for pat in PREFERENCE_PATTERNS:
        m = re.search(pat, content)
        if m:
            ctx = content[max(0, m.start() - 200):m.end() + 200]
            if TEMPLATE_SECTION.search(ctx):
                continue
            return "非专门但优惠"
    return "不涉及"


def build_tags(district, major, minor, sme, is_ict=False, amount_value=None, notice_type=None, budget_type=None):
    """统一打标：把各判别结果汇总成 tags 数组（2026-08-04）

    tags 前缀约定：
      区县→直接区县名；行业→大类+小类；ICT→'ICT'；
      中小微→'中小微-xxx'；金额档位→'金额>xxx'；类型→'类型-xxx'

    2026-08-17：合同预估类金额不进档位（不参与金额筛选，主列表也不展示）
    """
    tags = []
    # 区县
    if district:
        tags.append(district)
    # 行业大类 / 小类
    if major:
        tags.append(f"行业-{major}")
    if minor and minor != major:
        tags.append(f"行业-{minor}")
    # ICT
    if is_ict:
        tags.append("ICT")
    # 中小微
    if sme and sme != "不涉及":
        tags.append(f"中小微-{sme}")
    # 金额档位（合同预估类不计入，2026-08-17）
    if amount_value and budget_type != "contract_estimate":
        if amount_value < 100_000:
            tags.append("金额<10万")
        elif amount_value < 1_000_000:
            tags.append("金额10-100万")
        elif amount_value < 5_000_000:
            tags.append("金额100-500万")
        else:
            tags.append("金额>500万")
    # 公告类型
    if notice_type:
        tmap = {"tender": "类型-招标", "award": "类型-中标", "intention": "类型-意向", "other": "类型-其他"}
        if notice_type in tmap:
            tags.append(tmap[notice_type])
    return tags


# 2026-08-17 补齐发包人：content 含"招标人/采购人：XXX单位名"但 notices.tenderer 空时
# 从 content 重提取（避免从 scraper 源头重抓）。仅补空，不覆盖现有
_PUR_KW_RE = re.compile(r'(?:招标人|采购人|采购单位|发包人)[：:]')
_ORG_TAIL_RE = re.compile(
    r'(?:有限责任|股份有限|集团有限|公司|集团|局|委员会|管委会|政府|中心|'
    r'学校|医院|协会|研究院|研究所|事务所|厂|院|所|站)'
)


def _try_extract_purchaser(content: str) -> str:
    """从 content 提取发包人。失败返回空串。处理"有限责任公司"截断问题。"""
    if not content:
        return ""
    t = re.sub(r'\s+', '', content)
    m = _PUR_KW_RE.search(t)
    if not m:
        return ""
    rest = t[m.end():]
    # 取最右 ORG_TAIL 匹配位置（避免截在"有限责任"），优先 _ORG_TAIL_RE 自身（覆盖"公司"等短后缀）
    tail_matches = list(_ORG_TAIL_RE.finditer(rest[:25]))
    if not tail_matches:
        return ""
    # 取最右（end 最大）的后缀位置作为终止点
    end_pos = max(tm.end() for tm in tail_matches)
    val = rest[:end_pos]
    if val.endswith(('有限责任', '股份有限', '集团有限', '有限')) and not val.endswith('公司'):
        val += '公司'
    if 4 <= len(val) <= 45:
        return val
    return ""


def enrich(stats_only=False, full=False):
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row

    # 加列（幂等）
    cols = [r[1] for r in conn.execute("PRAGMA table_info(notices)")]
    for col in [
        "std_district", "proj_major_cat", "proj_minor_cat", "sme_target", "tags",
        # 2026-08-25：发包人信息块（正文结构化提取）
        "tenderer_address", "agency", "agency_address",
        "contact_person", "contact_phone", "agency_contact", "agency_phone",
        "supervision_phone", "email", "postal_code",
        # 2026-09-13 方案3：增量富化水位列
        "needs_enrich", "enrich_ver", "enrich_src_len",
    ]:
        if col not in cols:
            conn.execute(f"ALTER TABLE notices ADD COLUMN {col} TEXT")
    conn.commit()

    if full:
        conn.execute("UPDATE notices SET needs_enrich=1")
        conn.commit()

    # 2026-09-13 方案3（增量富化）：默认只处理「待富化 / 规则版本过期 / 正文长度变化」的行。
    # 全量重算：enrich(full=True) 或 CLI --full（改规则时把 ENRICH_VERSION +1 也会全量一次）。
    rows = conn.execute("""
        SELECT id, title, tenderer, site, raw_json, budget_type, url, winner, tenderer_address
        FROM notices
        WHERE COALESCE(needs_enrich, 1) = 1
           OR COALESCE(enrich_ver, 0) < ?
           OR COALESCE(CAST(enrich_src_len AS INTEGER), -1) <> length(COALESCE(raw_json, ''))
    """, (ENRICH_VERSION,)).fetchall()
    _processed_ids = [r["id"] for r in rows]
    _total_all = conn.execute("SELECT COUNT(*) FROM notices").fetchone()[0]
    print(f"  [富化增量] 本轮处理 {len(rows)}/{_total_all} 条（其余沿用已有结果）")

    dist_cnt = {"未知": 0}
    cat_cnt = {"未分类": 0}
    sme_cnt = {"不涉及": 0, "未找到正文": 0}
    updates = []
    raw_updates = []
    tenderer_updates = []  # 2026-08-17 补齐发包人
    winner_updates = []    # 2026-08-19 苏服采详情供应商回填
    contact_updates = []   # 2026-08-25 发包人信息块（正文提取）

    for r in rows:
        # 区县
        try:
            raw_for_district = json.loads(r["raw_json"] or "{}")
        except Exception:
            raw_for_district = {}
        area_code = raw_for_district.get("area_code") or raw_for_district.get("areaCode")
        # 2026-08-21：删苏服采强制覆盖 area_code=320900 的逻辑——
        # API row.areaCode 已是平台按区县分发的权威分类，不应再硬猜。
        # 苏服采专属快捷路径在 classify_district 内（SUFUCAI_AREA_CODE_MAP）处理。
        dist = classify_district(
            r["title"], r["tenderer"], r["site"],
            area_code=area_code, raw=raw_for_district,
            tenderer_address=r["tenderer_address"],
        )
        dist_cnt[dist or "未知"] = dist_cnt.get(dist or "未知", 0) + 1

        if r["site"] == "苏服采":
            # 历史批次没有保存接口返回的 purchase ID；URL 的 /biddingAnnouncement/ 段
            # 仍是稳定的项目级 ID，回填后项目关联可以优先走 project_key。
            m = re.search(r"/biddingAnnouncement/([^?]+)", r["url"] or "")
            project_key = raw_for_district.get("project_key") or raw_for_district.get("purchases_need_id") or (m.group(1) if m else "")
            raw_row = raw_for_district.get("raw_row") or {}
            source_area_code = (
                raw_for_district.get("area_code") or raw_for_district.get("areaCode") or
                raw_row.get("areaCode") or ""
            )
            enriched_raw = dict(raw_for_district)
            enriched_raw.update({
                "project_key": project_key,
                "purchases_need_id": project_key,
                "area_code": source_area_code,
            })
            if enriched_raw != raw_for_district:
                raw_updates.append((json.dumps(enriched_raw, ensure_ascii=False), r["id"]))

        # 2026-08-24：不再写 target_region 四字段（前端盐开筛选只认 std_district，冗余已清）

        # 行业分类
        minor, major = classify_category(r["title"])
        cat_cnt[major or "未分类"] = cat_cnt.get(major or "未分类", 0) + 1

        # 中小微（从 raw_json.content）
        sme = "不涉及"
        amount_value = None
        notice_type = "tender"
        is_ict = False
        try:
            raw = json.loads(r["raw_json"] or "{}")
            content = raw.get("content", "")
            amount_value = raw.get("amount_value")
            notice_type = raw.get("notice_type", "tender")
            is_ict = bool(raw.get("ict_keywords"))
        except Exception:
            content = ""
        if content:
            sme = classify_sme(content)
            sme_cnt[sme] = sme_cnt.get(sme, 0) + 1
        else:
            sme_cnt["未找到正文"] = sme_cnt.get("未找到正文", 0) + 1

        # 统一打标（2026-08-04；2026-08-17 合同预估类金额不进档位）
        tags = build_tags(dist, major, minor, sme, is_ict=is_ict,
                          amount_value=amount_value, notice_type=notice_type,
                          budget_type=r["budget_type"])
        tags_json = json.dumps(tags, ensure_ascii=False)

        updates.append((
            dist, major, minor, sme, tags_json, r["id"]
        ))

        # 2026-08-17：tenderer 为空且 content 能提取时回填（不发爬请求）
        if not (r["tenderer"] and r["tenderer"].strip()) and content:
            _new_tenderer = _try_extract_purchaser(content)
            if _new_tenderer:
                tenderer_updates.append((_new_tenderer, r["id"]))

        # 2026-08-19：苏服采详情接口回填的供应商（detail_supplier）→ winner
        # 此前公开列表接口不返回成交供应商，winner 一直为空；
        # backfill_sufucai_detail.py 调详情接口后 raw 里带 detail_supplier。
        if r["site"] == "苏服采" and not (r["winner"] or "").strip():
            _ds = raw.get("detail_supplier") or ""
            if _ds.strip():
                winner_updates.append((_ds.strip(), r["id"]))

        # 2026-08-25：发包人信息块——从正文提取招标人地址/代理机构/联系人/电话等
        # tenderer 只在为空时补（已有 tenderer 来自更精细的富化链路，不覆盖）；
        # 其余字段为新增列，直接写入。
        if content:
            try:
                from contact_info import extract_contact_info
                ci = extract_contact_info(content)
            except Exception:
                ci = {}
            # 现有 tenderer 优先，正文提取仅补空
            ci_tenderer = (ci or {}).get("tenderer", "")
            final_tenderer = (r["tenderer"] or "").strip() or ci_tenderer
            if final_tenderer and final_tenderer != (r["tenderer"] or "").strip():
                tenderer_updates.append((final_tenderer, r["id"]))
            contact_updates.append((
                (ci or {}).get("tenderer_address", ""),
                (ci or {}).get("agency", ""),
                (ci or {}).get("agency_address", ""),
                (ci or {}).get("contact_person", ""),
                (ci or {}).get("contact_phone", ""),
                (ci or {}).get("agency_contact", ""),
                (ci or {}).get("agency_phone", ""),
                (ci or {}).get("supervision_phone", ""),
                (ci or {}).get("email", ""),
                (ci or {}).get("postal_code", ""),
                r["id"],
            ))

    if stats_only:
        return dist_cnt, cat_cnt, sme_cnt

    conn.executemany(
        """UPDATE notices SET
               std_district=?, proj_major_cat=?, proj_minor_cat=?, sme_target=?, tags=?
           WHERE id=?""",
        updates,
    )
    if raw_updates:
        conn.executemany("UPDATE notices SET raw_json=? WHERE id=?", raw_updates)
    # 2026-08-17：补齐发包人写入（不覆盖现有 tenderer）
    if tenderer_updates:
        conn.executemany("UPDATE notices SET tenderer=? WHERE id=?", tenderer_updates)
    # 2026-08-19：苏服采成交供应商回填（不覆盖现有 winner）
    if winner_updates:
        conn.executemany("UPDATE notices SET winner=? WHERE id=?", winner_updates)
    # 2026-08-25：发包人信息块写入（新增列）
    if contact_updates:
        conn.executemany("""
            UPDATE notices SET
                tenderer_address=?, agency=?, agency_address=?,
                contact_person=?, contact_phone=?, agency_contact=?, agency_phone=?,
                supervision_phone=?, email=?, postal_code=?
            WHERE id=?
        """, contact_updates)

    # 2026-08-20：苏服采同项目（project_key）district 统一——
    # 同项目多条公告（招标+中标+合同）的 loc 常是不同开标地点（代理机构会议室），
    # 导致同项目被判不同区县。规则：盐南/经开优先 → 出现最多的区县 → 市级。
    try:
        groups = conn.execute("""
            SELECT json_extract(raw_json, '$.project_key') pk, std_district, COUNT(*) n
            FROM notices WHERE site='苏服采' AND json_extract(raw_json, '$.project_key') IS NOT NULL
            GROUP BY 1, 2
        """).fetchall()
        by_pk = {}
        for g in groups:
            pk, dist, n = g["pk"], g["std_district"], g["n"]
            by_pk.setdefault(pk, {})[dist or "无"] = n
        unified = []
        for pk, dists in by_pk.items():
            # 盐开优先
            chosen = None
            for cand in ("盐南", "经开"):
                if cand in dists:
                    chosen = cand
                    break
            if chosen is None:
                # 多数区县（排除市级兜底优先取具体区县）
                non_shiji = {k: v for k, v in dists.items() if k not in ("市级", "无", None)}
                if non_shiji:
                    chosen = max(non_shiji, key=non_shiji.get)
                else:
                    chosen = "市级"
            unified.append((chosen, pk))
        if unified:
            conn.executemany("""
                UPDATE notices SET std_district=? WHERE site='苏服采'
                  AND json_extract(raw_json, '$.project_key') = ?
            """, unified)
            print(f"  苏服采同项目统一: {len(unified)} 个 project_key")
    except Exception as _e:
        print(f"  [苏服采同项目统一] 失败: {_e}")

    # 2026-09-13 方案3：写回增量水位（处理过的行标记版本 + 正文长度留档）
    if _processed_ids:
        conn.executemany(
            "UPDATE notices SET needs_enrich=0, enrich_ver=?, "
            "enrich_src_len=length(COALESCE(raw_json,'')) WHERE id=?",
            [(ENRICH_VERSION, i) for i in _processed_ids],
        )
        print(f"  [富化增量] 水位回写 {len(_processed_ids)} 条")

    conn.commit()
    conn.close()
    return dist_cnt, cat_cnt, sme_cnt


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--stats", action="store_true")
    parser.add_argument("--full", action="store_true", help="忽略水位，全库重算一次")
    args = parser.parse_args()

    dist, cat, sme = enrich(stats_only=args.stats, full=args.full)

    total = sum(dist.values())
    print(f"\n=== 区县分布（共 {total} 条）===")
    for k, v in sorted(dist.items(), key=lambda x: -x[1]):
        print(f"  {k}: {v} ({v * 100 // total}%)")

    print(f"\n=== 行业分类分布 ===")
    for k, v in sorted(cat.items(), key=lambda x: -x[1]):
        print(f"  {k}: {v}")

    print(f"\n=== 中小微标签分布 ===")
    for k, v in sorted(sme.items(), key=lambda x: -x[1]):
        print(f"  {k}: {v}")

    if not args.stats:
        print("\n✅ 已写入 tender.db")


if __name__ == "__main__":
    main()
