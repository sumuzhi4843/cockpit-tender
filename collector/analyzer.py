# -*- coding: utf-8 -*-
"""ICT 项目筛选"""
from config import ICT_STRONG_KEYWORDS, ICT_WEAK_KEYWORDS


def is_ict(title, content="", notice_type=None):
    """判断是否为 ICT 相关项目（分级匹配）

    规则：
    - 1 个强匹配 → True（标题或正文均可）
    - 2 个以上弱匹配 → True（仅标题，避免正文泛词误报）
    - 其他 → False

    2026-08-10 修复：意向/招标类公告（notice_type in intention/tender）正文也参与弱匹配。
    原因：意向公告标题是「XX单位2026年X月第X批政府采购意向公告」，具体项目名和
    采购需求概况全在正文（含监控/智能化/信息化等弱词），原逻辑只查标题导致大量漏标。
    仅对这两类放开正文弱词（泛词在正文更常见，但意向/招标正文通常较短且项目相关，
    误报可控）；正文取前 2000 字。
    """
    content4weak = content[:2000] if notice_type in ("intention", "tender") else ""
    strong_matched = [kw for kw in ICT_STRONG_KEYWORDS if kw in title or kw in content]
    weak_matched = [kw for kw in ICT_WEAK_KEYWORDS if kw in title or kw in content4weak]

    if strong_matched:
        return True, strong_matched + weak_matched
    if len(weak_matched) >= 2:
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
