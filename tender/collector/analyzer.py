# -*- coding: utf-8 -*-
"""ICT 项目筛选"""
from config import ICT_STRONG_KEYWORDS, ICT_WEAK_KEYWORDS

# ── 非 ICT 业务反向词（2026-09-06 新增，方案 B）──
# 命中任一即否决 ICT 判定——这些是明确的非信息化业务，正文/标题出现说明
# 项目本体不是 ICT（即便出现监控/平台/系统等词，也多为旁支描述）。
# 来自老板复核表 + 抽样误判案例：
#   - 凤依府立项咨询（命中"平台/软件"误判）
#   - 康森生态园土地招租 / 大数据产业园网球场招租（场地/资产出租非 ICT）
#   - CA证书办理（弱词"平台"）
#   - 农贸市场食品快检（弱词"检测"类关联）
# 反向词比弱词更"实义"：如"招租"出现则整单是资产处置，ICT 词只是园区名/背景。
ICT_NON_ICT_WORDS = [
    # 资产/场地出租处置（复核表：土地招租、网球场招租误判）——硬否决
    "招租", "出租", "土地出让", "资产处置", "拍卖", "转让",
    # 后勤/非信息化服务（硬否决）
    "食堂", "餐饮", "配餐", "保洁", "绿化养护", "物业", "保安", "门卫",
    "食材", "粮油", "服装", "印刷", "车辆租赁", "用车服务",
    "家具", "家电", "办公用品", "装修",
    # 认证/证书/签章类（硬否决：业务本体是办证/签章，非 ICT 建设）
    "CA证书", "电子签章", "数字证书", "资质认证", "体系认证",
]

# ── 弱否决词（2026-09-06）：服务/中介/检测类，单独出现不强，但若有强词仍可判 ICT ──
# 与硬否决区别：硬否决词一票否决（招租/食堂…业务性质明确非 ICT）；
# 弱否决词只是"业务偏服务"（如"XX 咨询服务（标段：平台项目）"实际含平台建设），
# 有强词命中时仍判 ICT，仅拦"纯弱词命中"的咨询/监理/检测单。
ICT_WEAK_VETO_WORDS = [
    "立项咨询", "咨询服务", "监理", "审计", "评估服务", "检测服务", "快检",
]

# ── 弱词业务动作词（2026-09-06 新增）──
# 弱词（平台/软件/系统/智慧…）命中时，须与"建设/采购/开发/升级/部署"等业务动作
# 同现才算 ICT 项目——弱词本身太泛（"大数据产业园"是地名、"平台"可能是 CA 平台）。
# 反向：弱词后无业务动作（如招租、咨询、场地）→ 不判。
ICT_WEAK_ACTION_WORDS = [
    "建设", "采购", "开发", "升级", "改造", "部署", "实施",
    "集成", "运维", "维护", "服务", "项目", "工程", "系统",
    "搭建", "新建", "扩建", "购置", "设备",
]


# 强词中也可能被非 ICT 场景误用的词：这些词独立出现不够实义，需业务动作同现
# （如"光纤"在"光纤租赁场地"里不是 ICT；"会议系统"在"会议室装修"里是配套）
# 暂不拆分，交给反向词 + 动作词双层过滤。
ICT_STRONG_NEED_ACTION = frozenset()  # 预留：后续若发现强词误伤再移入


def _has_non_ict(title: str, content: str) -> bool:
    """命中硬否决业务词 → 整单否决。

    2026-09-06 v2：只查标题，不查正文——正文含大量模板/政策文字
    （如"中标供应商为贫困县物业公司的…"扶贫条款），扫正文会误杀
    真 ICT 项目（老旧小区智能化采购、警务站智能化工程等 80 条）。
    """
    for w in ICT_NON_ICT_WORDS:
        if w in title:
            return True
    return False


def _hit_weak_veto(title: str, content4weak: str) -> bool:
    """命中弱否决词（咨询/监理/检测…）：仅拦纯弱词命中，不拦强词。
    同样只查标题。"""
    for w in ICT_WEAK_VETO_WORDS:
        if w in title:
            return True
    return False


def _weak_has_action(title: str, content4weak: str) -> bool:
    """弱词命中的文本段是否含业务动作词。"""
    for w in ICT_WEAK_ACTION_WORDS:
        if w in title or w in content4weak:
            return True
    return False


def is_ict(title, content="", notice_type=None):
    """判断是否为 ICT 相关项目（分级匹配 + 非 ICT 否决）

    规则（2026-09-06 方案 B 重构）：
    0. 硬否决：标题/正文命中非 ICT 业务词（招租/食堂/CA证书/保洁…）→ 直接 False
       （防止"大数据产业园网球场招租""土地招租"等资产类项目被弱词误判）
    1. 强词（标题或正文前 2000 字）：1 个命中即 True——强词实义（监控/弱电/物联网），
       不受弱否决词影响（"XX 咨询服务（标段：数智平台项目）"仍判 ICT）
    2. 弱词（标题 + 意向/招标正文前 2000 字）：≥2 个命中 且 含业务动作词 → True；
       但命中弱否决词（咨询/监理/检测服务…）→ 仅此一档被拦，防"立项咨询"误判
    3. 其他 → False

    复核案例回归（2026-09-06）：
      - 凤依府立项咨询（命中平台/软件）→ 弱否决"立项咨询"拦弱词档 ✅
      - 康森生态园土地招租 / 大数据产业园网球场招租 → 硬否决"招租" ✅
      - CA证书办理 → 硬否决"CA证书" ✅
      - 大数据集团咨询服务（标段：数智协同一体化平台）→ 强词"数据空间/平台"命中，
        不受弱否决影响，仍 True ✅
      - 一中 AI 智慧体育 / 城市智慧交通改造 → 不受影响，保持 True ✅
    """
    title = title or ""
    content = content or ""
    if _has_non_ict(title, content):
        return False, []

    content_strong = content[:2000]
    content4weak = content[:2000] if notice_type in ("intention", "tender") else ""
    strong_matched = [kw for kw in ICT_STRONG_KEYWORDS if kw in title or kw in content_strong]
    weak_matched = [kw for kw in ICT_WEAK_KEYWORDS if kw in title or kw in content4weak]

    if strong_matched:
        return True, strong_matched + weak_matched
    if len(weak_matched) >= 2 and _weak_has_action(title, content4weak):
        if _hit_weak_veto(title, content4weak):
            return False, weak_matched
        return True, weak_matched
    return False, weak_matched


def analyze_items(items):
    """对列表项进行 ICT 筛选
    返回 (ict_items, non_ict_items)
    """
    ict_items = []
    non_ict_items = []
    for item in items:
        flag, keywords = is_ict(item["title"], item.get("content", ""), item.get("notice_type"))
        if flag:
            item["ict_keywords"] = keywords
            ict_items.append(item)
        else:
            non_ict_items.append(item)
    return ict_items, non_ict_items
