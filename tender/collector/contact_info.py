#!/usr/bin/env python3
"""发包人信息块提取器（2026-08-25）

从公告正文提取结构化"发包人/联系方式"信息：
  tenderer          招标人/采购人/发包人名称（复用 enrich_engine 已有逻辑）
  tenderer_address  招标人地址
  agency            招标代理机构名称
  agency_address    代理机构地址
  contact_person    招标人联系人
  contact_phone     招标人联系电话
  agency_contact    招标代理联系人
  agency_phone      招标代理联系电话
  supervision_phone 监督电话
  email             电子邮箱
  postal_code       邮编

识别目标格式（实际公告常见变体）：
  招标人：XXX有限公司 地址：XX路X号 代理机构：XXX有限公司 地址：XX
  招标人：XXX  地 址：XX  联系人：X先生 电 话：1XXXXXXXXXX
  项目联系人：孙晨 联系电话：15389897651 招标代理联系人：白雅辉 联系电话：18547739521
  本机构监督电话：0912-7876360

用法：
  python3 contact_info.py "正文..."     # 命令行测试
  或
  from contact_info import extract_contact_info
  info = extract_contact_info(content)
"""
from __future__ import annotations

import re
from typing import Dict

# 单位名称后缀（与 enrich_engine._ORG_SUFFIX 对齐的常用子集）
_ORG_SUFFIX = (
    r"(?:公司|集团|股份有限公司|有限责任公司|有限公司|事务所|中心|委员会|"
    r"医院|学校|学院|大学|中学|小学|幼儿园|局|站|所|院|部|办|队|社|馆|"
    r"银行|支行|分行|营业部|营业所|支局|监管局|管理处|管理所)"
)

_ORG_SUFFIX_RE = re.compile(_ORG_SUFFIX)

# 电话：区号-号码 / 手机 / 400 / 分机
_PHONE_RE = re.compile(
    r"(?:0\d{2,3}-?\d{7,8}|1[3-9]\d{9}|400-?\d{3}-?\d{4}|"
    r"\d{7,8}(?:-\d{1,5})?)"
)


def _clean(s: str) -> str:
    return re.sub(r"\s+", "", s).strip(" ：:，。；;、\n\t")


def _find_unit(text: str) -> str | None:
    """从一段文本开头提取单位名（到机构后缀为止）。"""
    m = re.search(rf"^[^，。；;：:\n]{{2,60}}?{_ORG_SUFFIX}", text)
    if m:
        val = m.group(0).strip(" ：:，。；;、")
        return val if 2 <= len(val) <= 60 else None
    return None


def _org_name_after(t: str, kw_pattern: re.Pattern) -> str | None:
    """在去空白文本里找关键词后的单位名，取最右机构后缀（避免截断在'集团'）。"""
    m = kw_pattern.search(t)
    if not m:
        return None
    rest = t[m.end():]
    # 单位名应在第一个信息标签（地址/联系人/电话/代理机构等）前结束
    cut_m = re.search(
        r"(?:地址|联系人|联系电话|电话|邮编|邮箱|电子邮箱|代理机构|"
        r"开标时间|项目名称|招标编号|采购编号)[：:]", rest)
    name_zone = rest[:cut_m.start()] if cut_m else rest
    matches = list(re.finditer(rf"(?:{_ORG_SUFFIX})", name_zone))
    if not matches:
        return None
    val = _clean(name_zone[:matches[-1].end()])
    return val if 2 <= len(val) <= 60 else None


def extract_contact_info(content: str | None) -> Dict[str, str]:
    """从正文提取发包人信息块。返回字段字典（空字符串表示未识别）。"""
    out = {
        "tenderer": "",
        "tenderer_address": "",
        "agency": "",
        "agency_address": "",
        "contact_person": "",
        "contact_phone": "",
        "agency_contact": "",
        "agency_phone": "",
        "supervision_phone": "",
        "email": "",
        "postal_code": "",
    }
    if not content:
        return out

    # 统一空白：中文正文常 "地 址"/"电 话" 中间带空格
    t = re.sub(r"\s+", " ", content)
    t_strip = re.sub(r"\s+", "", content)

    # ── 0. 邮箱 / 邮编（全局，最可靠）──
    m = re.search(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", t)
    if m:
        out["email"] = m.group(0).strip()
    m = re.search(r"邮编[：:]\s*(\d{6})", t_strip)
    if m:
        out["postal_code"] = m.group(1)

    # ── 1. 招标人名称（复用 enrich_engine 主逻辑的简化版）──
    _TENDERER_KW = re.compile(
        r"(?:招标人|采购人|采购单位|发包人)[：:]+")
    out["tenderer"] = _org_name_after(t_strip, _TENDERER_KW) or ""

    # ── 2. 代理机构 ──
    _AGENCY_KW = re.compile(r"(?:招标代理机构|招标代理|代理机构|代理单位)[：:]+")
    out["agency"] = _org_name_after(t_strip, _AGENCY_KW) or ""

    # ── 3. 地址：招标人地址（招标人/采购人关键词后）──
    # 先找"招标人/采购人"块起点，取其后的第一个地址
    for kw in ("招标人", "采购人", "发包人"):
        idx = t.find(kw)
        if idx >= 0:
            seg = t[idx: idx + 300]
            m = re.search(r"(?:地\s*址|地址)[：:]\s*([^，。；;：:\n]{3,90})", seg)
            if m:
                addr = m.group(1).strip()
                # 地址可能尾随"联系人/电话"，截断
                addr = re.split(r"(?:联系人|联\s*系\s*人|电话|电\s*话|邮编|邮箱|代理机构)", addr)[0]
                out["tenderer_address"] = addr.strip(" 　，。；;")
                break
    # 代理机构地址（代理机构关键词后）
    if out["agency"]:
        idx = t_strip.find(out["agency"])
        if idx >= 0:
            seg = t_strip[idx: idx + 250]
            m = re.search(r"(?:地\s*址|地址)[：:]\s*([^，。；;：:\n]{3,90})", seg)
            if m:
                addr = re.split(
                    r"(?:联系人|电话|邮编|邮箱|电子邮箱|代理机构|招标人)", m.group(1))[0]
                out["agency_address"] = addr.strip(" 　，。；;")

    # ── 4. 联系人 / 电话（区分招标人与代理）──
    # 代理联系人优先匹配（带"代理"前缀）
    m = re.search(
        r"(?:招标代理联系人|代理机构联系人|代理联系人|招标代理[^：:]{0,6}?联系人)"
        r"[：:]\s*([^，。；;\s]{1,30})", t)
    if m:
        out["agency_contact"] = m.group(1).strip(" ：:，。；;、")
    m = re.search(
        r"(?:招标代理联系电话|代理机构电话|代理电话|招标代理[^：:]{0,6}?电话|"
        r"招标代理[^：:]{0,6}?联系电话)[：:]\s*([0-9\-—]{6,25})", t)
    if m:
        out["agency_phone"] = m.group(1).strip("：:，。；;-—")

    # 监督电话（可多个，取第一个）
    m = re.search(
        r"(?:监督电话|投诉电话|举报电话|本机构监督电话|上级机构监督电话)"
        r"[：:]\s*([0-9\-—]{6,25})", t)
    if m:
        out["supervision_phone"] = m.group(1).strip("：:，。；;-—")

    # 招标人联系人 / 电话（排除已匹配的代理/监督）
    m = re.search(r"(?:项目联系人|联系\s*人|联\s*系\s*人|联系人)[：:]\s*([^，。；;\s]{1,30})", t)
    if m:
        cand = m.group(1).strip(" ：:，。；;、")
        if cand and "代理" not in cand and "监督" not in cand:
            out["contact_person"] = cand

    # 代理电话：代理联系人或代理机构名之后 100 字内的电话
    if out["agency_contact"] or out["agency"]:
        anchor = out["agency_contact"] or out["agency"]
        idx = t.find(anchor)
        if idx >= 0:
            seg = t[idx: idx + 120]
            m = re.search(_PHONE_RE, seg)
            if m:
                out["agency_phone"] = m.group(0).strip("：:，。；;-—")

    # 招标人电话：招标人联系人或招标人名之后 100 字内、且非代理/监督的电话
    m = re.search(
        r"(?:联系电话|联系\s*电\s*话|电\s*话|电话)[：:]\s*([0-9\-—]{6,25})", t)
    if m:
        cand = m.group(1).strip("：:，。；;-—")
        if cand and cand != out["agency_phone"] and cand != out["supervision_phone"]:
            # 优先取招标人（非代理）上下文里的电话
            pos = t.find(cand)
            ctx = t[max(0, pos - 60):pos]
            if "代理" not in ctx:
                out["contact_phone"] = cand

    return out


if __name__ == "__main__":
    import sys
    sample = sys.argv[1] if len(sys.argv) > 1 else ""
    if not sample:
        print("用法: python3 contact_info.py '正文'")
        sys.exit(1)
    import json
    print(json.dumps(extract_contact_info(sample), ensure_ascii=False, indent=2))
