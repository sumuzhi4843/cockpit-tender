# -*- coding: utf-8 -*-
"""招标监控配置"""

# 站点配置
SITES = {
    # --- 原有盐城本地平台 ---
    "盐城市大数据集团": {
        "url": "http://www.ycdatagroup.cn/news/19.html",
        "display_url": "https://www.ycdatagroup.cn/news/19.html",
        "base_url": "http://www.ycdatagroup.cn",
        "category": "政府",
        "scrape_method": "html",
        "retention": "db",
    },
    "盐城市都市建设投资集团有限公司": {
        "url": "http://www.ycdsjt.cn/?zhaobiao/",
        "base_url": "http://www.ycdsjt.cn",
        "category": "政府",
        "scrape_method": "html",
        "retention": "db",
    },
    "江苏世纪新城": {
        "url": "http://www.jscncg.com/tenderLease/tender/",
        "display_url": "https://www.jscncg.com/tenderLease/tender/",
        "base_url": "http://www.jscncg.com",
        "category": "政府",
        "scrape_method": "html",
        "retention": "db",
    },
    "盐城市东方集团": {
        "url": "http://www.ycdfjt.com/zbzl/qzzgs/",
        "display_url": "http://www.ydfjt.cn/zbzl/qzzgs/",
        "base_url": "http://www.ycdfjt.com",
        "category": "政府",
        "scrape_method": "html",
        "retention": "db",
    },
    "经开城发集团": {
        "url": "http://ycjkct.com/zbcg/zbxx/",
        "display_url": "http://ycjkct.com/business/town/",
        "base_url": "http://ycjkct.com",
        "category": "政府",
        "scrape_method": "html",
        "retention": "db",
    },
    # --- 新增外部平台 ---
    "悦达阳光": {
        "url": "https://www.ydtender.com/zbgg/index.jhtml",
        "base_url": "https://www.ydtender.com",
        "category": "企业",
        "scrape_method": "html",
        "retention": "db",
    },
    "中国邮政": {
        "url": "https://cg.11185.cn/",
        "base_url": "https://cg.11185.cn",
        "category": "企业",
        "scrape_method": "pdf",
        "retention": "pdf",
    },
    "三峡集团": {
        "url": "https://eps.ctg.com.cn/cms/channel/1ywgg1/index.htm",
        "base_url": "https://eps.ctg.com.cn",
        "category": "企业",
        "scrape_method": "html",
        "retention": "db",
    },
    "江苏农华": {
        "url": "http://jd.dongyin.com/index/zhaobiao/index/type_id/8.html",
        "base_url": "http://jd.dongyin.com",
        "category": "企业",
        "scrape_method": "html",
        "retention": "db",
    },
    "国投集团": {
        "url": "https://www.sdicc.com.cn/cgxx/ggList",
        "base_url": "https://www.sdicc.com.cn",
        "category": "企业",
        "scrape_method": "html",
        "retention": "db",
    },
    # --- 中粮e采（JSON API，非 HTML） ---
    "中粮e采": {
        "api_url": "https://ecai.cofco.com/api/saas-portal/noauth/trans/trade/pageEs",
        "base_url": "https://ecai.cofco.com",
        "url": "https://ecai.cofco.com/web-portal/index.html#/trade-info",
        "category": "企业",
        "scrape_method": "api",
        "retention": "db",
    },
    # --- 一汽电子采购（POST 搜索 + HTML） ---
    "一汽电子采购": {
        "url": "https://etp.faw.cn/gg/allJYTypeGGList?hangYeType=&xmLeiXing=&ggStartTimeEnd=&gongGaoType=5&isNew=1",
        "base_url": "https://etp.faw.cn",
        "category": "企业",
        "scrape_method": "html",
        "retention": "db",
        # 2026-08-20 老板拍板：一汽只抓盐城分公司项目，整站划入盐开区
        "scope": "盐城市",
        "target_regions": ["盐南", "经开"],
    },
    # --- 江苏政府采购网（CDP 浏览器自动化） ---
    # 2026-08-20：盐城 11 个区县码全量抓取。
    # 之前只配 320991+320992（仅盐南+经开），导致阜宁/盐都/亭湖/大丰/东台/建湖/
    # 射阳/滨海/响水这 8 个区县的政府采购公告完全漏抓（实测数据 0 条）。
    "江苏政府采购网": {
        "url": "http://www.ccgp-jiangsu.gov.cn/jiangsu/cggg_search.html?lmid=cggg&qh=notic_c4",
        "base_url": "http://www.ccgp-jiangsu.gov.cn",
        "scraper_type": "cdp",
        "target_zones": [
            "320902",  # 亭湖区
            "320903",  # 盐都区
            "320904",  # 大丰区
            "320921",  # 响水县
            "320922",  # 滨海县
            "320923",  # 阜宁县
            "320924",  # 射阳县
            "320925",  # 建湖县
            "320981",  # 东台市
            "320991",  # 经开
            "320992",  # 盐南
        ],
        "zone_names": {
            "320902": "亭湖区", "320903": "盐都区", "320904": "大丰区",
            "320921": "响水县", "320922": "滨海县", "320923": "阜宁县",
            "320924": "射阳县", "320925": "建湖县", "320981": "东台市",
            "320991": "盐城经济开发区", "320992": "盐南高新技术产业开发区",
        },
        "category": "政府",
        "scrape_method": "cdp",
        "retention": "db",
    },
    # --- 表格补充的盐城本地来源（2026-08-05） ---
    "盐城市政府采购网": {
        "url": "https://czj.yancheng.gov.cn/col/col2383/index.html",
        "base_url": "https://czj.yancheng.gov.cn",
        "category": "政府",
        "scrape_method": "html",
        "retention": "db",
    },
    "盐城市人民政府网": {
        "url": "https://www.yancheng.gov.cn/col/col16735/index.html",
        "list_url": "https://www.yancheng.gov.cn/module/xxgk/search_custom.jsp?fieldConfigId=38195&area=",
        "base_url": "https://www.yancheng.gov.cn",
        "category": "政府",
        "scrape_method": "html",
        "retention": "db",
    },
    "中国政府采购网": {
        "url": "https://search.ccgp.gov.cn/bxsearch?searchtype=2&page_index=1&timeType=2&searchchannel=0&dbselect=bidx&kw=%E7%9B%90%E5%9F%8E",
        "base_url": "https://www.ccgp.gov.cn",
        "category": "政府",
        "scrape_method": "html",
        "retention": "db",
    },
    "江苏招标采购服务平台": {
        "url": "https://www.jszbcg.com/#/bulletin?page=1&category_id=88",
        "api_url": "https://api.jszbtb.com/DataGatewayApi/PublishBulletins",
        "base_url": "https://www.jszbcg.com",
        "region_code": "320900",
        "category": "政府",
        "scrape_method": "api",
        "retention": "pdf+ocr+md",
    },
    "盐城市公共资源交易网": {
        "url": "https://ycggzy.jszwfw.gov.cn/tradeInfor?secondId=19&secondCode=transactionInfo",
        "api_url": "https://ycggzy.jszwfw.gov.cn/cums/home/notice/noticePage",
        "base_url": "https://ycggzy.jszwfw.gov.cn",
        "category": "政府",
        "scrape_method": "api",
        "retention": "db",
    },
    "开发区公共资源交易网": {
        "url": "http://218.92.181.186:8081/jyxx/about.html",
        "base_url": "http://218.92.181.186:8081",
        "category": "政府",
        "scrape_method": "html",
        "retention": "db",
    },
    "城南新区公共资源交易网": {
        "url": "http://221.231.11.22:8099/jyxx/tradeInfo.html",
        "base_url": "http://221.231.11.22:8099",
        "category": "政府",
        "scrape_method": "html",
        "retention": "db",
    },
    "苏服采": {
        "url": "https://js.fwgov.cn/bidding?serviceType=1",
        "api_url": "https://js.fwgov.cn:868/purchases/tenders/notice",
        "base_url": "https://js.fwgov.cn",
    "area_code": "320900",
    # 2026-08-20：苏服采 API 实测——父级 320900 不包含子级（[320900] 只返 74 条 vs 12 码并集 250 条）；
    # 必须用盐城全部 11 个区县码并集才能抓全。320991/320992 是经济/高新区专属，
    # 320902-320904/320921-320925/320981 是 9 个行政区专属项目。
    "area_codes": [
        "320900",  # 父级（市本级）
        "320902",  # 亭湖区
        "320903",  # 盐都区
        "320904",  # 大丰区
        "320921",  # 响水县
        "320922",  # 滨海县
        "320923",  # 阜宁县
        "320924",  # 射阳县
        "320925",  # 建湖县
        "320981",  # 东台市
        "320991",  # 经开
        "320992",  # 盐南
    ],
    "cutoff_date": None,       # 苏服采按 API 总量全量抓取，不在 2026-06-01 早停
    "max_pages": 1000,         # 安全上限；实际以接口 total 为停止条件
    # 2026-08-22：所有接口按 12 个区域码逐桶查询；/page 返回行不带 areaCode，
    # 由请求桶写入；公告接口校验返回 areaCode 与请求桶一致。禁止地址猜测。
    "scope": "盐城市",
    "target_regions": ["盐南", "经开"],
    "category": "政府",
        "scrape_method": "api",
        "retention": "db",
    },
    # --- 奇瑞电子采购（JSON API + city 过滤） ---
    "奇瑞电子采购": {
        "api_url": "https://ebd.mychery.com/cms/api/dynamicData/queryContentPage",
        "base_url": "https://ebd.mychery.com",
        "url": "https://ebd.mychery.com/cms/zd20=jycggg/index.html",
        "category": "企业",
        "scrape_method": "api",
        "retention": "db",
    },
}

# ICT 关键词分级
# 强匹配：1 个即判定为 ICT 项目
ICT_STRONG_KEYWORDS = [
    "视频监控", "监控系统", "弱电", "安防", "智能化",
    "网络设备", "交换机", "路由器", "防火墙",
    "综合布线", "门禁", "停车场系统", "LED大屏",
    "数据中心", "算力", "动环监控",
    "物联网", "IoT",
    "视频会议", "会议系统",
    "光纤", "5G",
    "UPS", "精密空调",
    "数字孪生", "BIM",
    "数据底座", "数据空间",
    "智能化工程", "安防工程", "弱电工程",
    "智慧城市", "智慧交通", "智慧社区",
]

# 弱匹配：需 2 个以上弱匹配才判定为 ICT
ICT_WEAK_KEYWORDS = [
    "大数据", "平台", "AI", "人工智能",
    "机房", "智慧", "软件", "信息系统",
    "服务器", "存储", "通信", "传感器",
    "电子政务", "云计算", "广播",
    "数字化", "信息化",
]

# 公告类型关键词（用于标题提取）
NOTICE_TYPES = [
    "招标公告", "中标结果公示", "中标公示", "询价公告", "询比公告",
    "失败公示", "流标公示", "终止公告", "征集公告", "报名公告",
    "补充通知", "补充公告", "询价结果公示", "招标失败公示",
    "询比采购终止公告",
    # 2026-08-17 补充：非标准招标类公告词（大数据集团招租等）
    "公开招租的公告", "招租公告", "竞价公告", "竞价结果公示", "成交公告", "中标结果公告",
]

# 请求配置
REQUEST_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}

REQUEST_TIMEOUT = 15          # 单次请求超时（秒）
REQUEST_DELAY = 1.5           # 站间间隔（秒）
MAX_RETRIES = 3               # 单个 URL 最大重试次数
RETRY_BACKOFF = 2             # 重试退避基数（秒）：第 n 次等 RETRY_BACKOFF * n
DETAIL_DELAY = 0.5           # 详情页请求间隔（秒）
MAX_DETAIL_CHARS = 8000      # 详情页正文最大截断长度

# 去重存储
STORE_FILE = "data/store.json"
STORE_MAX_ENTRIES = 5000     # 去重列表上限，超过则裁剪旧条目

# 报告输出目录
REPORT_DIR = "reports"
