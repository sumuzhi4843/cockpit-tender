# -*- coding: utf-8 -*-
"""富化引擎 — 移植自 yancheng-bidding-pro/enrich_details.py + html_common.py

纯文本解析，无 SQLite / 网络依赖，专为本地 tender_monitor 设计。
对外提供:
    enrich_detail(text, notice_type='tender') -> dict
        # 返回 {tenderer, amount, amount_unit, open_date, deadline,
        #        winner, winning_amount, notice_type, ...}
    infer_notice_type(title) -> str
    parse_datetime / parse_date_only

字段命名对齐本地 cockpit:
    tenderer      发包/采购单位 (仓库 purchaser)
    amount        金额字符串 (仓库 budget 格式化, 如 "21.44万元")
    bid_deadline  开标时间 (本地沿用, 仓库 open_date)
    deadline      报名截止时间 (仓库 deadline, 新增)
"""
import html as html_lib
import re
from dataclasses import dataclass, asdict
from typing import Dict, Optional, Tuple


# ─────────────────────────────────────────────
# 公告分类器 v2（2026-08-07 并入：原 notice_classifier.py）
# 一级类型 notice_type 保持兼容：tender / award / intention / other。
# 二级类型 notice_subtype 提供业务语义，项目生命周期基于二级类型计算。
# ─────────────────────────────────────────────

CLASSIFICATION_VERSION = "2.0.0"


@dataclass(frozen=True)
class Classification:
    notice_type: str
    notice_subtype: str
    confidence: float
    reason: str
    version: str = CLASSIFICATION_VERSION

    def to_dict(self):
        return asdict(self)


def _hit(text: str, words) -> list:
    return [w for w in words if w in text]


def _content_category(content: str):
    """2026-09-11：正文类型线索（面包屑优先，其次正文开头）。
    背景：部分站点标题只有项目名，「类型」只出现在正文开头的栏目串里（如
    盐城市政府采购网「当前位置： 首页 > 合同公告 …」、公共资源交易网正文首词「中标合同」）。
    只看「当前位置」后 240 字或正文前 60 字，避免正文引用/导航误伤。
    关键词表由库内「已知正确公告」反向标定（每类抽样>90% 一致才收录）。"""
    i = content.find("当前位置")
    seg = content[i:i + 240] if 0 <= i <= 200 else content[:60]
    for subtype, words in (
        ("contract", ("合同公告", "合同公示", "合同签订公告", "采购合同公告", "中标合同", "合同履行及变更", "合同变更")),
        ("award_candidate", ("中标候选人公示", "评标结果公示")),
        ("award_final", ("中标（成交）公告", "中标(成交)公告", "中标公告", "成交公告", "中标结果公告", "成交结果公告", "结果公告", "结果公示", "定标结果公示", "入围公告")),
        ("correction", ("更正公告", "澄清公告", "变更公告", "补充公告")),
        ("terminated", ("终止公告", "项目终止")),
        ("failed", ("废标公告", "流标公告", "废标(终止)公告")),
        ("intention", ("采购意向", "意向公告", "招标计划")),
        ("tender_notice", ("公开招标公告", "竞争性磋商公告", "竞争性磋商", "竞争性谈判公告", "询价公告",
                           "征集公告", "单一来源公示", "单一来源采购", "采购公告", "招标公告")),
    ):
        for w in words:
            if w in seg:
                return subtype
    return None


def _url_category(url: str):
    """2026-09-11：URL 里的类型信号（站点栏目路径 / 列表类型码），同样反向标定过。
    仅收录标定纯度 >90% 的信号，不把混合栏目拿来当依据。"""
    if not url:
        return None
    if "jszbcg.com" in url:
        m = re.search(r"bulletinDetails/([^/]+)/", url)
        if m:
            cat = m.group(1)
            if "中标候选人" in cat:
                return "award_candidate"
            if "中标结果" in cat:
                return "award_final"
            if "招标公告" in cat:
                return "tender_notice"
    if "ydtender" in url and re.search(r"/zhwgg/\d+\.jhtml", url):
        return "tender_notice"
    if "fwgov.cn" in url and re.search(r"[?&]type=1(&|$)", url):
        return "tender_notice"
    return None


def classify_notice(title: str, content: str = "", url: str = "") -> Classification:
    """以标题为主分类，正文仅作弱兜底，避免正文泛词造成误判。"""
    title = re.sub(r"\s+", "", title or "")
    content = re.sub(r"\s+", "", content or "")[:3000]

    rules = [
        ("other", "terminated", ("终止公告", "项目终止", "终止采购", "采购终止"), 0.99),
        ("other", "suspended", ("暂停公告", "项目暂停", "暂停采购", "暂停招标"), 0.99),
        ("other", "failed", ("流标公告", "流标公示", "废标公告", "废标公示", "失败公告", "失败公示", "采购失败", "招标失败", "异常结果"), 0.99),
        ("other", "correction", ("更正公告", "更正公示", "澄清公告", "答疑公告", "补充公告", "变更公告"), 0.98),
        ("award", "award_final", ("中标结果公告", "中标结果公示", "中标公告", "成交结果公告", "成交结果公示", "成交公告", "中选公告", "中选公示", "中选结果"), 0.99),
        ("award", "award_candidate", ("中标候选人公示", "中标候选人公告", "成交候选人公示", "评标结果公示", "评审结果公示"), 0.98),
        ("award", "award_final", ("询价结果公示", "询价结果公告", "比选结果公示", "比选结果公告"), 0.96),
        ("other", "contract", ("合同公告", "合同公示", "合同签订公告", "采购合同公告", "合同变更公告",
                              "中标合同", "合同履行及变更", "合同履行"), 0.96),
        ("intention", "intention", ("采购意向", "意向公告", "采购计划", "招标计划", "预算公告"), 0.98),
        ("tender", "tender_reopen", ("二次招标", "三次招标", "重新招标", "二次采购", "三次采购", "重新采购"), 0.94),
    ]

    for notice_type, subtype, words, confidence in rules:
        hits = _hit(title, words)
        if hits:
            return Classification(notice_type, subtype, confidence, f"标题命中：{'、'.join(hits)}")

    tender_words = (
        "招标公告", "采购公告", "询价公告", "磋商公告", "谈判公告", "比选公告",
        "征集公告", "竞价公告", "招募公告", "采购文件", "招标文件",
        # 2026-09-11：补常见变体（反向标定：多为招标类）
        "竞争性磋商", "竞争性谈判", "单一来源", "征求意见公告", "公开招标", "邀请招标",
    )
    hits = _hit(title, tender_words)
    if hits:
        return Classification("tender", "tender_notice", 0.96, f"标题命中：{'、'.join(hits)}")

    # 严格限制正文兜底：只识别明确且组合完整的结果/异常词。
    content_rules = [
        # 2026-08-10：招标计划提前发布一览表（标题常只有项目名，无公告类型词，需正文识别）
        ("intention", "intention", ("招标计划提前发布",), 0.85),
        ("other", "terminated", ("本项目终止采购", "本项目终止招标"), 0.80),
        ("other", "failed", ("本项目流标", "本项目废标"), 0.80),
        # 2026-08-11：正文前 500 字内出现「中标结果公告」= 中标结果（城南新区 8-10 腾兴案例，老板报障）。
        # 真公告标题在正文开头（pos ~105），招标公告作为合同条款引用时在 pos 1658+（中位数）。
        # pos_limit=500 排除条款引用误判。置信 0.95。
        # 招标公告正文里作为合同条款引用时（如"处于中标结果公告（直接发包...）到合同约定..."）
        # pos 中位数 1658+，远在 500 后。用 pos_limit=500 排除条款引用误判。置信 0.95。
        ("award", "award_final", ("中标结果公告",), 0.95, 500),
        ("award", "award_final", ("中标单位：", "成交供应商：", "中选单位："), 0.78),
    ]
    for rule in content_rules:
        # 规则项支持 4 元（words/conf）或 5 元（多一个 pos_limit，仅匹配正文前 N 字）
        if len(rule) == 5:
            notice_type, subtype, words, confidence, pos_limit = rule
        else:
            notice_type, subtype, words, confidence = rule
            pos_limit = None
        check_content = content[:pos_limit] if pos_limit else content
        hits = _hit(check_content, words)
        if hits:
            reason_suffix = f"（正文前 {pos_limit} 字）" if pos_limit else ""
            return Classification(notice_type, subtype, confidence, f"正文兜底命中：{'、'.join(hits)}{reason_suffix}")

    # 2026-09-11：正文/URL 类型线索（在“默认招标”兑底之前）——部分站点标题无类型词，
    # 栏目信息在正文开头或 URL 里（盐城市政府采购网合同公告误判为招标的根因）。
    _bc = _content_category(content)
    if _bc:
        _bc_type = {"award_final": "award", "award_candidate": "award"}.get(_bc, "other")
        if _bc == "tender_notice":
            return Classification("tender", "tender_notice", 0.9, "正文栏目命中：招标类")
        return Classification(_bc_type, _bc, 0.9, f"正文栏目命中：{_bc}")
    _uc = _url_category(url)
    if _uc:
        _uc_type = {"award_final": "award", "award_candidate": "award"}.get(_uc, "tender" if _uc == "tender_notice" else "other")
        return Classification(_uc_type, _uc, 0.88, f"URL 栏目命中：{_uc}")

    # 默认仍兼容旧系统：未知公告先视作招标公告，但显式标记低置信度。
    return Classification("tender", "tender_notice", 0.55, "未命中明确规则，按招标公告兜底")


def classify_item(item: dict) -> Classification:
    return classify_notice(item.get("title", ""), item.get("content", ""), item.get("url", ""))


# ─────────────────────────────────────────────
# 文本清理
# ─────────────────────────────────────────────

def _clean(text: str) -> str:
    return re.sub(r'\s+', ' ', text or "").strip()


def _strip_html(html: str) -> str:
    """HTML → 纯文本，并提取 meta description 追加到末尾（部分站点正文藏在此处）。"""
    text = html_lib.unescape(html)
    text = text.replace('\xa0', ' ').replace('　', ' ')
    meta_desc = re.search(
        r'<meta[^>]+name=["\']description["\'][^>]+content=["\'](.*?)["\']',
        text, re.S | re.I)
    if not meta_desc:
        meta_desc = re.search(
            r'<meta[^>]+content=["\'](.*?)["\'][^>]+name=["\']description["\']',
            text, re.S | re.I)
    meta_text = (" " + meta_desc.group(1)) if meta_desc else ""
    text = re.sub(r'<script[^>]*>.*?</script>', ' ', text, flags=re.S | re.I)
    text = re.sub(r'<style[^>]*>.*?</style>', ' ', text, flags=re.S | re.I)
    text = re.sub(r'<[^>]+>', ' ', text)
    text = text + meta_text
    text = re.sub(r'&[a-zA-Z#0-9]+;', ' ', text)
    return text


# ─────────────────────────────────────────────
# 关键词表（源自仓库，含 P1-4/P1-5/P1-6/P1-7/P2-1 等修复）
# ─────────────────────────────────────────────

PURCHASER_KEYWORDS = [
    "采购人信息", "采购人", "采购单位", "发包单位", "发包方", "发包人",
    "业主单位", "建设单位", "项目单位", "招标人", "招标单位", "委托单位",
    "询价人", "委托方", "单位名称",
]

BUDGET_KEYWORDS = [
    "预算金额", "项目预算", "采购预算", "控制价", "总预算",
    "限价", "最高限价", "最高金额",
    "总投资", "投资额",
    "项目规模", "服务费", "监理费", "工程造价", "项目造价",
    "合同估算价", "合同预估金额", "合同预计金额", "合同预计总金额",
    "估算价", "估算总投资",
    "标的额", "采购金额", "总服务费", "服务总费用", "总费用",
    "采购规模", "招标规模", "项目金额", "本次采购金额",
    "规模", "建设规模", "工程规模",
    "自筹资金", "财政资金", "财政性资金",
    "项目资金来源为其他资金", "项目资金来源为财政资金", "项目资金来源为自筹资金",
    "项目资金来源为国有资金", "项目资金来源为私有资金",
    "项目资金米源", "资金来源为其他资金", "资金来源为国有资金", "资金来源为私有资金",
    "预算：人民币", "预算人民币", "起始价",
    "建设资金来自", "资金来自",
    "采购预算(万元)", "项目预算(万元)",
    "合同预估金额（万元）", "合同预计金额（万元）",
    "预算金额（万元）", "最高限价(万元)", "招标控制价(万元)",
]

# 金额类型（2026-08-17）：合同预估类不进入主列表金额列，限价类保留
CONTRACT_ESTIMATE_KEYWORDS = (
    "合同估算价", "合同预估金额", "合同预计金额", "合同预计总金额", "估算价", "估算总投资",
)
LIMIT_KEYWORDS = ("控制价", "限价", "最高限价", "招标控制价")

PRECISE_BUDGET_KEYWORDS = [
    "项目总投资", "预估项目总投资", "总投资额", "项目总投资额",
    "预估总投资", "工程总投资额", "项目概算总投资",
]

_BUDGET_INLINE_RE = [
    re.compile(r'不(?:超过|高于)人民币\s*([\d.]+)\s*(万元|亿元|元)'),
    re.compile(r'约人民币\s*([\d.]+)\s*(万元|亿元|元)'),
    re.compile(r'(?:本工程|本项目|本次|全费用)约\s*([\d.]+)\s*(万元|亿元|元)'),
    re.compile(r'标的额约?\s*([\d.]+)\s*(万元|亿元|元)'),
    re.compile(r'总服务费用不超过\s*([\d.]+)\s*(万元|亿元|元)'),
    re.compile(r'采购规模[：:\s约]{0,4}([\d.]+)\s*(万元|亿元|元)'),
]

BUDGET_EXCLUDE = [
    "保证金", "履约金", "押金", "违约金",
    "代理费", "服务费", "中介费", "咨询费", "评审费", "专家费",
    "手续费", "公证费", "审计费", "律师费", "鉴证费",
    "招标服务费", "招标代理服务费", "采购代理服务费",
    "交易服务费", "平台服务费",
]

OPEN_DATE_KEYWORDS = [
    "开标时间", "开标日期", "开启时间",
    "截止时间、开标时间和地点",
    "递交截止时间、开标时间",
    "投标截止时间、开标时间",
    "文件开启时间", "开启日期",
]

DEADLINE_KEYWORDS = [
    "报名截止", "投标截止", "截标时间", "递交截止", "报名截止时间", "截止时间",
]

EXPECTED_KEYWORDS = ["预计挂网时间", "预计发布时间", "预计挂网日期", "预计公告时间"]

WINNER_KEYWORDS = [
    "中标单位", "中标供应商", "成交供应商", "中标人",
    "中标候选人第一名", "中标候选人", "中标侯选人",
    "中选人", "中选供应商", "成交人",
    "供应商名称", "投标供应商名称", "中标供应商名称",
    "中标单位名称", "成交供应商名称", "中标单位名称：", "中标单位名称:",
]

WIN_AMOUNT_KEYWORDS = [
    "中标金额", "成交金额", "中标价格", "成交价格", "中标价",
    "投标报价金额", "中标报价金额", "成交报价金额", "报价金额",
    # 含括号的联合写法（盐城财政局、江苏政府采购网等常用格式）
    "中标（成交）金额", "成交（中标）金额", "中标(成交)金额", "成交(中标)金额",
]

# ─────────────────────────────────────────────
# 发包单位后缀 / 校验
# ─────────────────────────────────────────────

_ORG_SUFFIX = (
    r'公司|集团|局|委员会|管委会|政府|中心|学校|医院|协会|基金|银行|事务所|研究院|研究所|大学|学院'
    r'|办事处|街道办|街道|管理处|管理委员会|部门|办公室'
    r'|宣传部|财政局|教育局|卫生局|民政局|住建局|自然资源局'
    r'|队|所|站|院|厂|社|馆'
    r'|联合会|残联|红十字会|商会|校友会|促进会|联盟|理事会|联席会|基金会'
    r'|中学|小学|幼儿园|中专|技校|党校'
    r'|党委|党组|党工委|党支部|纪委|监委|人大|政协'
    r'|村委|居委|工作站|服务处'
    r'|管理局|管理处|建设处|工程处|工程局'
    r'|集团有限|有限责任|股份有限'
)
_ORG_PATTERN = re.compile(_ORG_SUFFIX)

_BAD_PURCHASER_RE = re.compile(
    r'满足《|中华人民共和国|政府采购法|申请人|不得参加|资格要求|期间通过|依据《|根据《'
    r'|报名期间|同一合同|参加政府|参与政府|属于政府|适用政府|具备以下|本次采购依据'
    r'|重大税收|当事人名单|失信被执行'
    r'|报名时间[：:]|报名地点|领取地点|现场报名'
    r'|项目名称[：:]|名称[：:][^一-龥]'
)

_CIRCLE_NUM_RE = re.compile(r'^[①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮]')


def _clean_purchaser_val(val: str) -> str:
    val = html_lib.unescape(val)
    val = re.sub(r'^【[^】]{2,12}】\s*', '', val)
    val = re.sub(r'^\[上一篇\]\(\)\[', '', val)
    val = re.sub(r'^\[(?!一-鿿)', '', val)
    val = re.sub(r'^(?:招标人|采购人|发包人|单位名称)[：:]\s*', '', val)
    val = re.sub(r'(?:单位)?(?:地址|联系人|联系电话)[：:].*', '', val)
    val = re.sub(r'(?:HIS|系统|驻场)[、，。].*', '', val)
    return val.strip()


def _is_valid_purchaser(name: str) -> bool:
    if not name or len(name) < 3:
        return False
    if 3 <= len(name) <= 8 and re.search(r'(局|院|办|会|中心|公司|集团|政府)$', name):
        return True
    # 2026-09-08：中文数字只在后接顿点（序号“一、”）时拒，避免误杀“三仓镇/五烈镇”等地名单
    if re.match(r'^[(（\d]', name):
        return False
    if re.match(r'^[一二三四五六七八九十]+[、.．]', name):
        return False
    if _CIRCLE_NUM_RE.match(name):
        return False
    if _BAD_PURCHASER_RE.search(name):
        return False
    if re.search(r'[一-鿿]{2,}、[一-鿿]{2,}', name):
        return False
    if re.match(r'^中标(?:候选人|结果|公示|公告)', name):
        return False
    if re.search(r'\d{4}年\d{1,2}月', name):
        return False
    return True


# 2026-09-08 发包人清洗器：存量脏数据三形态（正文混入/括号截断/公文误提）+
# “街道办事”被后缀正则截成“街道办”的新发现 bug。入库前终点过滤，回灌脚本复用。
_PURCHASER_LABEL_CUT = re.compile(
    r'招标(?:代理|机构)|采购代理|（如有）|代理机构|执行部门|项目名称|工程名称|施工内容'
    r'|中标(?:候选人|结果|单位|公告|公示)|联系方式|联系电话|联系人'
)
_PURCHASER_DOCS_RE = re.compile(r'参照|暂行办法|计价格|已加盖|提供的|适用于|以下简称|按照《')


def sanitize_purchaser(val: str) -> str:
    """清洗发包人名称；无法救回时返回空串（宁缺勿脏）。"""
    if not val:
        return ""
    val = val.strip()
    # 0) 先剥采购人类标签前缀（“采购单位：XX支队” → “XX支队”）
    val = re.sub(r'^(?:招标人|采购人|采购单位|发包人|发包单位|业主单位|建设单位|委托单位|单位名称)\s*[：:]\s*', '', val)
    val = re.sub(r'^[\d一二三四五六七八九十①②③④⑤⑥⑦⑧⑨⑩][、.．]\s*', '', val)
    val = re.sub(r'\d+\s*[、.．]\s*', '', val)  # "公司2、执行部门" → "公司执行部门"
    # 1) 标签截断：取标签前缀（如“XX局招标代理机构：XX公司”→“XX局”）
    m = _PURCHASER_LABEL_CUT.search(val)
    if m:
        head = val[:m.start()].strip(' ：:，,、')
        head = re.sub(r'\s*\d+\s*[、.．]\s*', '', head).strip()
        # 截断后的前缀必须是完整单位名（以机构后缀结尾），否则整条置空
        val = head if len(head) >= 3 and re.search(rf'(?:{_ORG_SUFFIX})$', head) else ""
        if not val:
            return ""
    # 2) 残留冒号：还有就取第一段前缀
    if '：' in val or ':' in val:
        head = re.split(r'[：:]', val)[0].strip()
        val = head if len(head) >= 3 else ""
        if not val:
            return ""
    # 3) 括号截断修复：尾部未闭合的（…）补全
    op, cl = val.count('（') + val.count('('), val.count('）') + val.count(')')
    if op > cl and op - cl <= 2:
        val = val + ('）' if val.count('（') >= val.count('(') else ')')
    elif cl > op:
        val = val.lstrip('）)').strip()
    # 4) 后缀修复：被截断的机构名补全
    if val.endswith('街道办'):
        val += '事处'
    elif val.endswith(('有限责任', '股份有限', '集团有限', '有限')):
        val += '公司'
    # 5) 公文词黑名单：整段是正文的没救回价值，置空
    if _PURCHASER_DOCS_RE.search(val):
        return ""
    val = val.strip()
    if not (3 <= len(val) < 45) or not _is_valid_purchaser(val):
        return ""
    return val


# ─────────────────────────────────────────────
# 通用提取函数
# ─────────────────────────────────────────────

def _extract_after_keyword(text: str, keywords: list, window: int = 100) -> Optional[str]:
    """找 keyword，要求后 5 字内有冒号，返回冒号后 window 字符（去空白）。"""
    t = re.sub(r'[*_~`]', '', re.sub(r'\s+', '', text))
    for kw in keywords:
        kw_stripped = re.sub(r'\s+', '', kw)
        kw_pat = re.escape(kw_stripped) + r'[^：:]{0,5}[：:]'
        m = re.search(kw_pat, t)
        if not m:
            continue
        idx = m.end()
        return t[idx:idx + window]
    return None


def _parse_amount(raw: str) -> Tuple[Optional[float], str]:
    """从字符串提取金额（元）和单位。返回 (amount_in_元, unit)。"""
    if not raw:
        return None, "UNKNOWN"
    raw = raw.replace(",", "").replace("，", "")

    def safe_float(s):
        try:
            return float(s)
        except ValueError:
            return None

    m_total = re.search(r'总计\s*([\d.]+)\s*(亿|万元|万|元)', raw)
    if m_total:
        num = safe_float(m_total.group(1))
        if num is not None:
            unit = m_total.group(2)
            if unit == "亿":
                return num * 1e8, "亿"
            if unit in ("万元", "万"):
                return num * 1e4, "元"
            return num, "元"
    m = re.search(r'([\d.]+)\s*(亿|万元|万|元|RMB)', raw)
    if m:
        num = safe_float(m.group(1))
        if num is not None:
            unit = m.group(2)
            if unit == "亿":
                return num * 1e8, "亿"
            if unit in ("万元", "万"):
                return num * 1e4, "元"
            return num, "元"
    m2 = re.search(r'([\d.]+)', raw)
    if m2:
        num = safe_float(m2.group(1))
        if num is not None:
            return num, "元"
    return None, "UNKNOWN"


# ─────────────────────────────────────────────
# 日期解析（源自 html_common.py）
# ─────────────────────────────────────────────

def parse_datetime(raw: str) -> Optional[str]:
    """归一化日期时间为 'YYYY-MM-DD HH:MM:SS'。"""
    if not raw:
        return None
    raw = re.sub(r'[*_~`]', '', raw)
    raw = re.sub(r'\s+', '', raw)
    raw = raw.replace('：', ':')
    patterns = [
        r'(\d{4})[年\-/.](\d{1,2})[月\-/.](\d{1,2})日?(\d{1,2})[时:点](\d{1,2})分?(\d{1,2})?秒?',
        r'(\d{4})[年\-/.](\d{1,2})[月\-/.](\d{1,2})日?(\d{1,2})[时:点](\d{1,2})',
        r'(\d{4})[年\-/.](\d{1,2})[月\-/.](\d{1,2})日?(\d{1,2})时',
        r'(\d{4})[年\-/.](\d{1,2})[月\-/.](\d{1,2})',
    ]
    for pat in patterns:
        m = re.search(pat, raw)
        if m:
            g = m.groups()
            y, mo, d = g[0], g[1], g[2]
            hh = g[3] if len(g) > 3 and g[3] else "00"
            mm = g[4] if len(g) > 4 and g[4] else "00"
            ss = g[5] if len(g) > 5 and g[5] else "00"
            return f"{int(y):04d}-{int(mo):02d}-{int(d):02d} {int(hh):02d}:{int(mm):02d}:{int(ss):02d}"
    return None


def parse_date_only(raw: str) -> Optional[str]:
    if not raw:
        return None
    raw = re.sub(r'\s+', '', raw)
    m = re.search(r'(\d{4})[年\-/.](\d{1,2})[月\-/.](\d{1,2})', raw)
    if m:
        return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    return None


# ─────────────────────────────────────────────
# 公告类型推断（源自 html_common.infer_notice_type）
# ─────────────────────────────────────────────

def infer_notice_type(text: str) -> str:
    """兼容旧调用；一级类型由公告分类器 v2 统一产出。"""
    return classify_notice(text).notice_type


# ─────────────────────────────────────────────
# 发包人提取
# ─────────────────────────────────────────────

def _extract_purchaser(text: str, result: Dict) -> None:
    # 2026-10-03 v3.2: 新增项目业主/业主单位/建设单位为 XXX 公司 模式（ycggzy / czj 经常用）
    # 修复 _legacy_extract 漏模式导致 "项目业主为 XXX" 被采成规则描述
    for owner_pattern in [
        r'项目业主为\s*([^，。、（【\[【\s]+?(?:' + _ORG_SUFFIX + '))',
        r'项目业主[：:]\s*([^，。、（【\[【\s]+?(?:' + _ORG_SUFFIX + '))',
        r'业主单位为\s*([^，。、（【\[【\s]+?(?:' + _ORG_SUFFIX + '))',
        r'业主单位[：:]\s*([^，。、（【\[【\s]+?(?:' + _ORG_SUFFIX + '))',
        r'业主为\s*([^，。、（【\[【\s]+?(?:' + _ORG_SUFFIX + '))',
        r'业主[：:]\s*([^，。、（【\[【\s]+?(?:' + _ORG_SUFFIX + '))',
    ]:
        m_owner = re.search(owner_pattern, text)
        if m_owner:
            v = _clean_purchaser_val(m_owner.group(1).strip())
            if 3 <= len(v) < 45 and _is_valid_purchaser(v):
                result["purchaser"] = v
                return

    # 2026-08-17 精确兑底：在主流程前先尝试对去空白原文本精确匹配"招标人/采购人：XXX单位名"。
    # 修复列表页正文含导航垃圾时 _extract_after_keyword 的 40 字符窗口截断问题
    # （例：国投集团 chunk=导航后面 40 字包含完整单位名+后面"代理机构：..."被拼接入库）
    t_strip_early = re.sub(r'\s+', '', text)
    # 用 finditer + max(end) 取最右后缀（避免 alternation 从最左匹配导致截在"有限"）
    _matches = list(re.finditer(
        rf'(?:招标人|采购人|采购单位|发包人)[：:]+(.{{4,40}}?(?:{_ORG_SUFFIX}))',
        t_strip_early))
    if _matches:
        _v = _matches[max(range(len(_matches)), key=lambda i: _matches[i].end())].group(1)
        _v = _clean_purchaser_val(_v.strip())
        # 2026-08-17 补齐截断：val 尾部是"有限责任/股份有限/集团有限/有限"时拼上"公司"
        if _v.endswith(('有限责任', '股份有限', '集团有限', '有限')):
            _v += '公司'
        if 3 <= len(_v) < 45 and _is_valid_purchaser(_v):
            result["purchaser"] = _v
            return

    chunk = _extract_after_keyword(text, PURCHASER_KEYWORDS, 40)
    if chunk:
        for _inner_kw in PURCHASER_KEYWORDS:
            _kw = re.sub(r'\s+', '', _inner_kw)
            _pos = chunk.find(_kw)
            if 1 < _pos <= 40:
                _next = chunk[_pos + len(_kw):]
                if _next and _next[0] in '：:':
                    _after = re.sub(r'^[：:\s\xa0]+', '', _next)
                    if len(_after) >= 4:
                        chunk = _after
                        break
        chunk = re.sub(r'^[^一-龥a-zA-Z0-9]+', '', chunk)
        chunk = re.split(r'[一二三四五六七八九十]\s*[、．.]', chunk)[0]
        m_name = re.search(r'^[^，。]{0,15}名称[：:]', chunk)
        if m_name:
            chunk = chunk[m_name.end():]
        chunk = re.sub(r'^(?:信息)?(?:单位|机构|联系|地址)?(?:名称)?\s*[：:]\s*', '', chunk)
        chunk = re.sub(r'^[甲乙丙丁][方部]?[）)）]*\s*[：:]\s*', '', chunk)
        chunk = re.sub(r'^(?:为|是|由|自|经|向|系|即|指|该|此|因|被|对|关|有|其|名|称|本)\s*', '', chunk)
        # 2026-08-17 修复：原 re.match 从最左非贪婪匹配，遇到"有限责任公司"会停在"有限责任"（被截断为不完整单位名）
        # 改为取所有后缀匹配中最右的位置作为终止点，确保抽出"…有限责任公司"完整名
        all_matches = list(re.finditer(rf'(?:{_ORG_SUFFIX})', chunk))
        if all_matches:
            last_match = all_matches[-1]
            val = chunk[:last_match.end()].strip()
            val = _clean_purchaser_val(val)
            if 3 <= len(val) < 45 and _is_valid_purchaser(val):
                result["purchaser"] = val

    if "purchaser" not in result:
        m = re.search(
            rf'因([^，。\s]{{4,40}}(?:{_ORG_SUFFIX}))(?:[经业]营|工作|发展)需要',
            text)
        if not m:
            m = re.search(
                rf'(?:因[经业]营需要[，,]|因工作需要[，,]|为[满完]足[^，。]{{0,10}}[，,])'
                rf'([^，。\s]{{4,35}}(?:{_ORG_SUFFIX}))[^，。]{{0,8}}(?:需|拟|将|决|计划)',
                text)
        if not m:
            m = re.search(
                rf'([^，。\s]{{4,40}}(?:{_ORG_SUFFIX}))\s*(?:负责实施|决定对|现对|现需|计划对)',
                text)
        if not m:
            m = re.search(
                rf'(?:^|[ 。\n，])([^，。\s]{{4,40}}(?:{_ORG_SUFFIX}))(?:[^，。\s]{{0,15}}(?:项目|工程|服务|采购|询价))',
                text)
        if not m:
            m = re.search(
                rf'([^，。\n\s]{{4,40}}(?:{_ORG_SUFFIX}))\s*在[^，。]{{0,20}}(?:项目|工程|服务|采购|询价|通过)',
                text)
        if not m:
            _t5 = re.sub(r'\s+', ' ', text)
            m = re.search(rf'招标人为([^，。]{{4,40}}(?:{_ORG_SUFFIX}))', _t5)
        if not m:
            m = re.search(
                rf'([^，。\s]{{4,40}}(?:{_ORG_SUFFIX}))关于[^，。]{{2,30}}(?:公告|招标|询价|竞争性)',
                text)
        if not m:
            m = re.search(
                rf'(?:Copyright[^\n]*?|版权所有[：:]\s*)([^，。\s]{{4,40}}(?:{_ORG_SUFFIX}))',
                text, re.IGNORECASE)
        if m:
            val = m.group(1).strip()
            val = re.sub(r'^[一二三四五六七八九十①②③④⑤⑥⑦⑧⑨⑩][、.．]\s*', '', val)
            val = _clean_purchaser_val(val)
            if (_is_valid_purchaser(val) and
                    not any(x in val for x in ("采购网", "政府采购", "交易中心", "招标平台", "该单位", "本单位",
                                               "招投标", "公共资源", "技术支持"))):
                result["purchaser"] = val

    if "purchaser" not in result:
        m9 = re.search(r'([^，。\s]{4,30})\s+\d{4}年\d{1,2}月\d{1,2}日\s*打印此页', text)
        if m9:
            val9 = _clean_purchaser_val(m9.group(1).strip())
            if _is_valid_purchaser(val9):
                result["purchaser"] = val9

    # 2026-08-17 兜底：针对 "招标人/采购人/采购单位/发包人：XXX单位名" 精确格式。
    # 解决列表页正文含导航垃圾时主流程匹配窗口错位问题（仅能拿到 chunk=40 字符导致截断）。
    if "purchaser" not in result or not _is_valid_purchaser(result.get("purchaser", "")):
        # 去空白后找 "招标人/采购人/采购单位/发包人：" 后接一个单位名
        t_strip = re.sub(r'\s+', '', text)
        m_tail = re.search(
            rf'(?:招标人|采购人|采购单位|发包人)[：:]+(.{{4,40}}?(?:{_ORG_SUFFIX}))',
            t_strip)
        if m_tail:
            val_tail = _clean_purchaser_val(m_tail.group(1).strip())
            if 3 <= len(val_tail) < 45 and _is_valid_purchaser(val_tail):
                if "purchaser" not in result or not _is_valid_purchaser(result.get("purchaser", "")):
                    result["purchaser"] = val_tail

    if result.get("purchaser", "").startswith("关于"):
        result["purchaser"] = result["purchaser"][2:].lstrip()
    # 2026-09-08 终点清洗（防新脏入库；回灌脚本同样走这里）
    if "purchaser" in result:
        result["purchaser"] = sanitize_purchaser(result["purchaser"])
        if not result["purchaser"]:
            result.pop("purchaser", None)
    if not _is_valid_purchaser(result.get("purchaser", "")):
        result.pop("purchaser", None)


# ─────────────────────────────────────────────
# 金额提取
# ─────────────────────────────────────────────

def _extract_budget(text: str, result: Dict) -> None:
    # 2026-10-13 修复：盐城公共资源交易网等站点用 CSS letter-spacing 把数字渲染为
    # "1 5 00"（数字间夹空格），导致 "1500万元" 被拆成 "1 5 00"、正则抓不到连续数字，
    # scraper 退而抓到前缀的 "1"（如表格行号）当预算。归一化"数字+空白+数字"→连续数字。
    # 不影响 "数字+空白+汉字"（如"2026年"、"1 万元"），因为 lookahead 是数字。
    text = re.sub(r'(\d)\s+(?=\d)', r'\1', text)
    t_nospace = re.sub(r'\s+', '', text)

    _PRECISE_RE = re.compile(
        r'(?:项目总投资|预估项目总投资|总投资额|项目总投资额|预估总投资|'
        r'工程总投资额|项目概算总投资)'
        r'[\s\S]{0,10}?(\d[\d.]*)\s*(万元|万|亿|元)'
    )
    m_precise = _PRECISE_RE.search(t_nospace)
    if m_precise:
        amount_raw = m_precise.group(1) + m_precise.group(2)
        amount, unit = _parse_amount(amount_raw)
        if amount and amount > 0 and 100 <= amount <= 5e10:
            result["budget"] = amount
            result["budget_unit"] = unit
            result["budget_type"] = "budget"  # 2026-08-17 金额类型
            result["budget_text"] = m_precise.group(0)[:40]
            return

    for kw in BUDGET_KEYWORDS:
        chunk = _extract_after_keyword(text, [kw], 60)
        if not chunk:
            m_direct = re.search(re.escape(kw) + r'[^\d，。]{0,3}([\d,.]+(?:\.\d+)?)\s*(万元|亿|元)', t_nospace)
            if m_direct:
                # 2026-10-12 修复：采购意向公告常分多个包件（如「包1：采购预算约380万元；包2：采购预算约90万元；总预算 470万元」），
                # re.search 只返回第一个子预算，金额被低估。改用 findall 求和拿到总预算。
                # 仅作用于「采购预算」关键词，避免影响其它站/其它关键词的现有行为。
                if kw == "采购预算":
                    all_matches = re.findall(
                        re.escape(kw) + r'[^\d，。]{0,3}([\d,.]+(?:\.\d+)?)\s*(万元|亿|元)',
                        t_nospace
                    )
                    if len(all_matches) >= 2:
                        total_yuan = 0.0
                        ok = True
                        for num_str, unit_str in all_matches:
                            try:
                                v = float(num_str.replace(',', ''))
                            except ValueError:
                                ok = False
                                break
                            if unit_str == '万元':
                                total_yuan += v * 1e4
                            elif unit_str in ('亿', '亿元'):
                                total_yuan += v * 1e8
                            else:  # 元
                                total_yuan += v
                        if ok and 100 <= total_yuan <= 5e10:
                            chunk = f"{total_yuan / 1e4:.2f}万元"
                        else:
                            chunk = m_direct.group(1) + m_direct.group(2)
                    else:
                        chunk = m_direct.group(1) + m_direct.group(2)
                else:
                    chunk = m_direct.group(1) + m_direct.group(2)
            else:
                if any(c in kw for c in '()（）'):
                    m_unit = re.search(r'[（(](\S+?)[）)]', kw)
                    if m_unit:
                        bracket_unit = m_unit.group(1)
                        # 2026-10-13 修复：原 30 字符 window 在表格型公告里只抓到行号（首个数字），
                        # 例如「合同预估金额（万元） 1 新 风路工程 ... 1500 备注」中应取 1500。
                        # 2026-10-13 防御（C）：仅当首个候选是 1-10 的小数字（疑似行号）时才改取 window 内
                        # 最后一个数字；第一个本就是真金额的场景保持原行为，避免误伤其它站/其它关键词。
                        # 2026-10-13 补强：bracket-paren 正则放宽，允许单位与右括号之间有内容
                        # （如「（万元 /年 ）」）；window 内优先看「X 万元」成对模式，避免被末尾「（3 年）」
                        # 这种服务期数字误中。
                        m_table_start = re.search(
                            re.escape(re.sub(r'[（(].*?[）)]', '', kw).strip())
                            + r'\s*[（(][^）)]*?' + re.escape(bracket_unit) + r'[^）)]*?[）)]',
                            text)
                        if m_table_start:
                            window_text = text[m_table_start.end():m_table_start.end() + 200]
                            # 优先 1：找「X 万元/亿元/元」成对模式，取最后一个（表格行末的金额）
                            m_unit_pairs = re.findall(
                                r'(\d+(?:\.\d+)?)\s*(?:万元|亿元|元)',
                                window_text)
                            if m_unit_pairs:
                                chunk = m_unit_pairs[-1] + bracket_unit
                            else:
                                # 优先 2：退回"first≤10 走 last"（处理「1 5 00」这种 row number + 裸数字）
                                candidates = re.findall(r'\d[\d,.]*', window_text)
                                if candidates:
                                    chosen = candidates[0]
                                    if len(candidates) > 1:
                                        try:
                                            first_val = int(float(candidates[0]))
                                            if first_val <= 10:
                                                chosen = candidates[-1]
                                        except ValueError:
                                            pass
                                    chunk = chosen + bracket_unit
                                else:
                                    continue
                        else:
                            continue
                    else:
                        continue
                else:
                    continue
        ctx = text[max(0, text.find(kw) - 20):text.find(kw) + 80] if kw in text else ""
        if any(ex in ctx for ex in BUDGET_EXCLUDE):
            continue
        amount, unit = _parse_amount(chunk)
        has_unit = bool(re.search(r'[万元亿]', chunk))
        if amount and amount > 0 and has_unit and 100 <= amount <= 5e10:
            # 单价过滤（对齐 Pro 版 P0-1）：仅当 _parse_amount 抓到的第一个金额
            # 本身就是 "X元/Y" 单价时才跳过；若窗口内是先有真预算、后出现单价，则保留真预算
            m_first_amount = re.search(r'[\d,.]+\s*(?:万元|亿|元)', chunk)
            if m_first_amount:
                tail = chunk[m_first_amount.start():]
                m_denom = re.match(r'[\d,.]+\s*(?:万元|亿|元)\s*[/／]\s*(\S{0,6})', tail)
                if m_denom:
                    denom = m_denom.group(1) or ""
                    # 2026-08-17：分母为期限（年/月/季）是总额不是单价，必须保留；
                    # 实物单位（个/件/套/台/公斤/m²等）才是单价，跳过
                    if denom and not any(d in denom for d in ("年", "月", "季")):
                        continue
            result["budget"] = amount
            result["budget_unit"] = unit
            # 金额类型（2026-08-17）：按命中的关键词分组
            if kw in CONTRACT_ESTIMATE_KEYWORDS:
                result["budget_type"] = "contract_estimate"
            elif kw in LIMIT_KEYWORDS:
                result["budget_type"] = "limit"
            else:
                result["budget_type"] = "budget"
            result["budget_text"] = chunk[:40]
            return


# ─────────────────────────────────────────────
# 时间字段提取
# ─────────────────────────────────────────────

def _extract_times(text: str, result: Dict) -> None:
    # 开标时间
    chunk = _extract_after_keyword(text, OPEN_DATE_KEYWORDS, 40)
    if chunk:
        dt = parse_datetime(chunk)
        if dt:
            result["open_date"] = dt
    if not result.get("open_date"):
        m = re.search(r'开标时间[^：:\d\n]{0,15}\n+(\d{4}[-/年]\d{1,2}[-/月]\d{1,2}[^\n]{0,20})', text)
        if m:
            dt = parse_datetime(m.group(1))
            if dt:
                result["open_date"] = dt
    if not result.get("open_date"):
        t_ns = re.sub(r'\s+', '', text)
        m = re.search(r'开标时间[^：:]{0,20}(\d{4}[-]\d{2}[-]\d{2}.{0,8}\d{2}:\d{2})', t_ns)
        if m:
            dt = parse_datetime(m.group(1))
            if dt:
                result["open_date"] = dt

    # 截止时间
    chunk = _extract_after_keyword(text, DEADLINE_KEYWORDS, 40)
    if chunk:
        dt = parse_datetime(chunk)
        if dt:
            result["deadline"] = dt
    if not result.get("deadline"):
        m = re.search(r'于(\d{4}[年\-]\d{1,2}[月\-]\d{1,2}日?\s*\d{1,2}[时:：]\d{2})[^前]{0,5}前', text)
        if m:
            dt = parse_datetime(m.group(1))
            if dt:
                result["deadline"] = dt
    if not result.get("deadline"):
        m = re.search(
            r'[（(]?(?:投标截止时间|截标时间|递交截止时间)[^）)]{0,10}[）)]?\s*为\s*'
            r'(\d{4}年\d{1,2}月\d{1,2}日?\s*\d{1,2}时\d{0,2})',
            text)
        if m:
            dt = parse_datetime(m.group(1))
            if dt:
                result["deadline"] = dt
    if not result.get("deadline"):
        _ct = re.sub(r'[*_~`]', '', re.sub(r'\s+', '', text)).replace('：', ':')
        m = re.search(r'(?:递交截止|投标截止|截标时间)[^:\d]{0,25}(\d{4}年\d{1,2}月\d{1,2}日\d{1,2}:\d{2})', _ct)
        if m:
            dt = parse_datetime(m.group(1))
            if dt:
                result["deadline"] = dt

    # 开标时间空 → 用截止时间兜底（"截止即开标"）
    if not result.get("open_date") and result.get("deadline"):
        result["open_date"] = result["deadline"]


# ─────────────────────────────────────────────
# 中标单位提取（award 类）
# ─────────────────────────────────────────────

def _extract_winner(text: str, html: str, result: Dict) -> None:
    winner_val = None
    chunk = _extract_after_keyword(text, WINNER_KEYWORDS, 80)
    if chunk:
        chunk = re.sub(r'^[^一-龥a-zA-Z0-9]+', '', chunk)
        for _inner_kw in WINNER_KEYWORDS:
            _kw = re.sub(r'\s+', '', _inner_kw)
            _pos = chunk.find(_kw)
            if 1 < _pos <= 40:
                _next = chunk[_pos + len(_kw):]
                if _next and _next[0] in '：:':
                    _after = re.sub(r'^[：:\s\xa0]+', '', _next)
                    if len(_after) >= 4:
                        chunk = _after
                        break
        chunk = re.sub(r'^(?:为|是|由|系|该|此|因|被)\s*', '', chunk)
        chunk = re.sub(r'^第[一二三1-3]名[：:]', '', chunk)
        if chunk.startswith('推荐如下'):
            before_semi = re.split(r'[;；]', chunk)[0]
            parts = re.split(r'[:：]', before_semi)
            chunk = parts[-1].strip()
        val = re.split(r'[,，。；;]', chunk)[0].strip()
        m_org = re.match(rf'.{{2,40}}?(?:{_ORG_SUFFIX})', val)
        if m_org:
            val = m_org.group(0).strip()
        if 3 <= len(val) < 50 and _ORG_PATTERN.search(val):
            if not re.search(r'^详见|见公示|见公告|类型投标报价', val):
                winner_val = val

    if not winner_val:
        m_cand = re.search(
            r'(?:中标候选人名单|候选人名单)[\s\S]{0,20}?第[一1]名\s*[：:]\s*([^\n,，;；]{4,50})',
            text)
        if m_cand:
            v = m_cand.group(1).strip()
            m_c2 = re.match(rf'.{{2,40}}?(?:{_ORG_SUFFIX})', v)
            if m_c2:
                v = m_c2.group(0).strip()
            if _ORG_PATTERN.search(v) and 3 <= len(v) < 50:
                winner_val = v

    if not winner_val:
        t_stripped = re.sub(r'\s+', '', text)
        m = re.search(rf'(?:中标|成交)[^一-龥]{{0,6}}金额\d+(.{{2,35}}?(?:{_ORG_SUFFIX}))', t_stripped)
        if m:
            val = m.group(1).strip()
            if 3 <= len(val) < 50:
                winner_val = val

    if not winner_val:
        try:
            import bs4
            soup = bs4.BeautifulSoup(html, 'html.parser')
            for table in soup.find_all('table'):
                headers = [th.get_text(strip=True) for th in table.find_all(['th', 'td'])[:20]]
                w_idx = next((i for i, h in enumerate(headers) if '中标单位' in h or '成交供应商' in h), None)
                a_idx = next((i for i, h in enumerate(headers) if '中标价格' in h or '成交金额' in h or '中标金额' in h or '中标价' in h), None)
                if w_idx is None:
                    continue
                for tr in table.find_all('tr')[1:]:
                    cells = [td.get_text(strip=True) for td in tr.find_all(['th', 'td'])]
                    if w_idx < len(cells):
                        v = cells[w_idx]
                        if v and _ORG_PATTERN.search(v) and 3 <= len(v) < 50:
                            winner_val = v
                            if a_idx and a_idx < len(cells) and 'winning_amount' not in result:
                                amt, _ = _parse_amount(cells[a_idx])
                                if amt and 100 <= amt <= 5e10:
                                    result['winning_amount'] = amt
                            break
                if winner_val:
                    break
        except Exception:
            pass

    if not winner_val:
        m_md = re.search(r'---\s+(\d+)\s*\|\s*([^|]{4,50})\s*\|', text)
        if m_md:
            v = m_md.group(2).strip()
            if _ORG_PATTERN.search(v) and 3 <= len(v) < 50 and _is_valid_purchaser(v):
                winner_val = v
                tail = text[m_md.end():]
                m_amt = re.search(r'([\d,.]+)\s*(万元|亿元|元)', tail[:200])
                if m_amt:
                    amt, _ = _parse_amount(m_amt.group(0))
                    if amt and 100 <= amt <= 5e10:
                        result['winning_amount'] = amt

    if winner_val:
        result["winner"] = winner_val
    if "winning_amount" not in result:
        chunk = _extract_after_keyword(text, WIN_AMOUNT_KEYWORDS, 200)
        if chunk:
            amount, unit = _parse_amount(chunk)
            has_unit = bool(re.search(r'[\d,.]+\s*(?:万元|亿元|元|万)', chunk[:150]))
            if amount and amount > 0 and has_unit and 100 <= amount <= 5e10:
                result["winning_amount"] = amount


# ─────────────────────────────────────────────
# 对外统一入口
# ─────────────────────────────────────────────

def enrich_detail(html_or_text: str, notice_type: str = "tender") -> Dict:
    """从详情页 HTML 或纯文本解析结构化字段。

    本地兼容: 返回字段同时给仓库命名 + 本地命名。
    """
    result: Dict = {}
    text = _clean(_strip_html(html_or_text))

    _extract_purchaser(text, result)
    _extract_budget(text, result)

    # 意向公告表格兜底：预算列纯数字（单位在列头"万元"）
    if "budget" not in result and notice_type == "intention":
        _tn = re.sub(r'\s+', '', text)
        _budget_cells = re.findall(r'\|(\d+(?:\.\d+)?)\|20\d{2}[-年]\d{2}', _tn)
        _budget_vals = [float(n) for n in _budget_cells if float(n) >= 5]
        if _budget_vals:
            _total = sum(_budget_vals) * 1e4
            if 100 <= _total <= 5e10:
                result["budget"] = _total
                result["budget_unit"] = "元"
                result["budget_type"] = "budget"  # 2026-08-17 意向表格兜底视为预算
                result["budget_text"] = "+".join(_budget_cells[:3]) + "万元"

    # budget inline fallback
    if "budget" not in result:
        for pat in _BUDGET_INLINE_RE:
            m = pat.search(text)
            if m:
                raw_num = m.group(1).replace(",", "")
                try:
                    v = float(raw_num)
                    unit_str = m.group(2)
                    if unit_str in ("万元", "亿元"):
                        v *= 1e4 if unit_str == "万元" else 1e8
                    if 100 <= v <= 5e10:
                        result["budget"] = v
                        result["budget_unit"] = "元"
                        result["budget_type"] = "budget"  # 2026-08-17 inline fallback 视为预算
                        result["budget_text"] = m.group(0)[:40]
                        break
                except ValueError:
                    pass

    if notice_type in ("tender", "other"):
        _extract_times(text, result)

    if notice_type == "award":
        _extract_winner(text, html_or_text, result)

    # 本地命名映射
    out = {
        "tenderer": result.get("purchaser", ""),
        "bid_deadline": result.get("open_date", ""),
        "deadline": result.get("deadline", ""),
        "winner": result.get("winner", ""),
        "winning_amount": result.get("winning_amount", ""),
        "notice_type": notice_type,
    }
    if "budget" in result:
        out["amount"] = _fmt_amount(result["budget"], result.get("budget_unit", "元"))
        out["amount_value"] = result["budget"]
        out["amount_unit"] = result.get("budget_unit", "元")
        out["budget_type"] = result.get("budget_type", "budget")  # 2026-08-17
    else:
        out["amount"] = ""
        out["amount_value"] = None
        out["amount_unit"] = ""
        out["budget_type"] = None
    return out


def _fmt_amount(amount: float, unit: str) -> str:
    """金额格式化为人读字符串。unit 为 '元'/'亿'，金额单位为元。"""
    if not amount:
        return ""
    if amount >= 1e8:
        return f"{amount / 1e8:.2f}亿元"
    if amount >= 1e4:
        return f"{amount / 1e4:.2f}万元"
    return f"{amount:.0f}元"
