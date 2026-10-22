// 招投标板块纯常量（零依赖；值与原 tender.js 完全一致）

export const STRONG_KW = new Set([
    '视频监控', '监控系统', '弱电', '安防', '智能化',
    '网络设备', '交换机', '路由器', '防火墙',
    '综合布线', '门禁', '停车场系统', 'LED大屏',
    '数据中心', '算力', '动环监控',
    '物联网', 'IoT',
    '视频会议', '会议系统',
    '光纤', '5G',
    'UPS', '精密空调',
    '数字孪生', 'BIM',
    '数据底座', '数据空间',
    '智能化工程', '安防工程', '弱电工程',
    '智慧城市', '智慧交通', '智慧社区'
  ]);

  // —— 解析开标时间 → Date 对象 ——
  // 支持两种格式：
  //   中文："2026年7月27日 15:00"
  //   标准："2026-08-07 09:00:00" / "2026-08-07 09:00" / "2026-08-07"（富化引擎 v2 输出）
  // 兼容 "24:00:00"（视为次日 00:00）

export const FAV_KEY_BASE = 'tender_favorites_v1';

export const FAV_INTENTION_KEY_BASE = 'tender_fav_intention_v1';

  // 2026-09-23 4 汇总卡"默认隐藏"功能 —— localStorage 持久化折叠状态
  // 值：'1' = 折叠（默认，首次访问）；'0' = 展开（老用户上次展开过）

export const SUMMARY_COLLAPSE_KEY = 'tender:summaryCollapsed';
  // 启动时从 Basic Auth 凭据解码出 user；未拿到则 fallback 'anonymous'（同步后被服务端兜底）

export const FAV_LABELS = { tender: '待投标', intention: '代挂网' };

  // 项目 → 收藏桶：意向招标项目进代挂网，其余进待投标（2026-08-17）

export const USE_LAZY = true;

  // —— 查询状态（筛选条件单一事实源，替换 8 个 _activeXxx 的内存筛选）——

export const PUBLICITY_KEYWORDS = ['结果公示', '成交公示', '中标公示'];

export const PAGE_SIZE = 40;   // API 分页页大小；不把全量数据载入浏览器

export const TYPE_MAP = {
    'tender': '招标',
    'award': '中标',
    'intention': '意向',
    'other': '其他',
  };

export const PROJECT_STAGE_MAP = {
    upcoming: '招标公告', upcoming_unknown_time: '时间待确认', opening_today: '今日开标',
    pending_result: '待跟踪', candidate: '候选', awarded: '中标',
    intention: '意向招标', failed: '流标/废标', terminated: '已终止',
    suspended: '已暂停', correction: '更正/澄清', contract: '合同', unknown: '待识别',
  };

export const DISTRICTS = [
    { key: 'all',      label: '全部', match: () => true },
    { key: 'shiji',    label: '市级', match: d => d === '市级', district: '市级' },
    // 盐开区筛选只认 std_district（与卡片显示同源）——苏服采已完成 areaCode 细分，
    // 市级/其他区县项目不再混入盐开区。target_region 透传已于 2026-08-24 清理。
    { key: 'yankai',    label: '盐开', match: d => d === '盐南' || d === '经开', district: '盐南,经开' },
    { key: 'yandu',     label: '盐都', match: d => d === '盐都', district: '盐都' },
    { key: 'tinghu',    label: '亭湖', match: d => d === '亭湖', district: '亭湖' },
    { key: 'dafeng',    label: '大丰', match: d => d === '大丰', district: '大丰' },
    { key: 'xiangshui', label: '响水', match: d => d === '响水', district: '响水' },
    { key: 'binhai',    label: '滨海', match: d => d === '滨海', district: '滨海' },
    { key: 'funing',    label: '阜宁', match: d => d === '阜宁', district: '阜宁' },
    { key: 'sheyang',   label: '射阳', match: d => d === '射阳', district: '射阳' },
    { key: 'jianhu',    label: '建湖', match: d => d === '建湖', district: '建湖' },
    { key: 'dongtai',   label: '东台', match: d => d === '东台', district: '东台' },
    { key: 'unassigned', label: '未划分', match: d => !d, district: 'none' },
  ];

  // 区县原始值 → 显示名（人工修订也用同一映射；2026-08-16）

export const DISTRICT_LABELS = {
    '市级': '市级',
    '盐南': '盐开区', '经开': '盐开区', '盐都': '盐都区', '亭湖': '亭湖区',
    '大丰': '大丰区', '响水': '响水县', '滨海': '滨海县', '阜宁': '阜宁县',
    '射阳': '射阳县', '建湖': '建湖县', '东台': '东台市',
  };
  // 修订时可选的区县（2026-08-16）：盐南/经开合并为「盐开区」一项（避免重复显示）
  // vals=命中的自动值（任一匹配即点亮），save=实际保存值

export const EDITABLE_DISTRICTS = [
    { label: '市级', vals: ['市级'], save: '市级' },
    { label: '盐开区', vals: ['盐南', '经开'], save: '盐南' },
    { label: '盐都区', vals: ['盐都'], save: '盐都' },
    { label: '亭湖区', vals: ['亭湖'], save: '亭湖' },
    { label: '大丰区', vals: ['大丰'], save: '大丰' },
    { label: '响水县', vals: ['响水'], save: '响水' },
    { label: '滨海县', vals: ['滨海'], save: '滨海' },
    { label: '阜宁县', vals: ['阜宁'], save: '阜宁' },
    { label: '射阳县', vals: ['射阳'], save: '射阳' },
    { label: '建湖县', vals: ['建湖'], save: '建湖' },
    { label: '东台市', vals: ['东台'], save: '东台' },
  ];

export const SCRAPE_METHOD_LABELS = {
    html: 'HTML解析',
    api: 'API接口',
    cdp: '浏览器渲染',
    pdf: 'PDF提取',
  };

export const RETENTION_LABELS = {
    db: 'DB存正文',
    pdf: 'PDF归档',
    'pdf+ocr+md': 'PDF+OCR+MD',
  };
  // 2026-08-10：留存方式悬停说明（大白话，2026-08-10 改）

export const RETENTION_TITLES = {
    'pdf+ocr+md': '公告是 PDF 文件：PDF 原件和整理出的文字稿都存到服务器，长期保存',
    'pdf': '公告是 PDF 文件：PDF 原件存到服务器，长期保存',
    'db': '公告内容存进系统数据库，长期保存',
  };

export const ICT_CAT_TOP_N = 7;

export const ICT_CAT_PALETTE = [
    '#1e3a8a', '#f59e0b', '#0f766e', '#7c3aed', '#15803d',
    '#c2410c', '#2563eb', '#b45309', '#0e7490', '#9333ea',
    '#4d7c0f', '#be123c', '#1d4ed8', '#a16207', '#0891b2',
  ];

