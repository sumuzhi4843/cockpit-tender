/* ============================================
 * tender.js · 盐开区招投标信息板块
 * — 项目进度：招标公告 / 今日开标 / 历史中标；辅助入口：待跟踪结果 / 采购意向 / 其他公告
 * ============================================ */

const Tender = (() => {

  // 强匹配关键词（与 config.py ICT_STRONG_KEYWORDS 对应）
  const STRONG_KW = new Set([
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
  function parseBidDeadline(str) {
    if (!str || !str.trim()) return null;
    const s = String(str).trim();

    // 中文格式：2026年7月27日 15:00
    const m = s.match(/(\d{4})年(\d{1,2})月(\d{1,2})日\s*(\d{1,2}):(\d{2})/);
    if (m) return new Date(+m[1], +m[2] - 1, +m[3], +m[4], +m[5]);

    // 标准格式：2026-08-07 09:00:00 | 2026-08-07 09:00 | 2026-08-07
    const parts = s.match(/^(\d{4})[-/](\d{1,2})[-/](\d{1,2})(?:\s+(\d{1,2}):(\d{2})(?::(\d{2}))?)?$/);
    if (parts) {
      let hour = +(parts[4] || 0);
      let day = +parts[3];
      if (hour === 24) { hour = 0; day += 1; }  // 24:00 → 次日 00:00
      return new Date(+parts[1], +parts[2] - 1, day, hour, +(parts[5] || 0), +(parts[6] || 0));
    }

    return null;
  }

  // ===== 收藏夹（2026-08-06）=====
  // localStorage 持久化，键为归一化项目名；数组顺序 = 收藏时间倒序（新在前）
  // ===== 收藏夹（2026-08-06）=====
  // 2026-08-17 双桶化：待投标桶（tender，招投标项目）+ 代挂网桶（intention，意向招标项目）
  // 2026-08-19 多账号隔离：localStorage key 加 user 前缀，避免同一浏览器不同账号串数据
  const FAV_KEY_BASE = 'tender_favorites_v1';
  const FAV_INTENTION_KEY_BASE = 'tender_fav_intention_v1';
  // 启动时从 Basic Auth 凭据解码出 user；未拿到则 fallback 'anonymous'（同步后被服务端兜底）
  let _currentUser = 'anonymous';
  try {
    const cred = (window.Auth && window.Auth.getCred) ? window.Auth.getCred() : '';
    if (cred) {
      const decoded = atob(cred);
      const u = (decoded.split(':')[0] || '').trim();
      if (u) _currentUser = u;
    }
  } catch (e) { /* ignore */ }
  // 基于当前用户动态计算 key；保留读老 KEY（未迁移的用户）作为 fallback
  function favKey(bucket) {
    const base = bucket === 'intention' ? FAV_INTENTION_KEY_BASE : FAV_KEY_BASE;
    return `fav_${_currentUser}_${base}`;
  }
  function legacyFavKey(bucket) {
    return bucket === 'intention' ? FAV_INTENTION_KEY_BASE : FAV_KEY_BASE;
  }
  const FAV_LABELS = { tender: '待投标', intention: '代挂网' };

  // 项目 → 收藏桶：意向招标项目进代挂网，其余进待投标（2026-08-17）
  function favBucketOf(item) {
    return item && item.project_stage === 'intention' ? 'intention' : 'tender';
  }

  function getFavorites(bucket) {
    const b = bucket || 'tender';
    // 优先读新 KEY（按用户隔离）；不存在则 fallback 老 KEY（多账号上线前已用账号的兼容）
    try {
      let raw = localStorage.getItem(favKey(b));
      if (raw == null) raw = localStorage.getItem(legacyFavKey(b));
      const arr = JSON.parse(raw || '[]');
      return Array.isArray(arr) ? arr : [];
    } catch (e) {
      return [];
    }
  }

  function isFavorite(key, bucket) {
    return getFavorites(bucket).includes(key);
  }

  function toggleFavorite(key, bucket) {
    const b = bucket || 'tender';
    let favs = getFavorites(b);
    if (favs.includes(key)) {
      favs = favs.filter(k => k !== key);
    } else {
      favs.unshift(key); // 新收藏放最前（收藏时间倒序）
    }
    try {
      localStorage.setItem(favKey(b), JSON.stringify(favs));
      // 清理可能遗留的老 KEY（跨账号串数据修复）
      try { localStorage.removeItem(legacyFavKey(b)); } catch (e2) {}
    } catch (e) { /* 隐私模式等场景忽略 */ }
    return favs.includes(key);
  }

  // 收藏按钮 HTML：star=☆ 空心 / ★ 实心；文案随桶（待投标/代挂网）
  function renderFavBtn(key, bucket) {
    const b = bucket || 'tender';
    const label = FAV_LABELS[b] || '待投标';
    const on = isFavorite(key, b);
    return `<button class="tender-fav-btn ${on ? 'tender-fav-btn--on' : ''}" data-fav-key="${escapeHtml(key)}" data-fav-bucket="${b}" type="button" title="${on ? '取消' + label : '加入' + label}" aria-label="${on ? '取消' + label : '加入' + label}">${on ? '★' : '☆'}</button>`;
  }

  // 收藏夹视图：从 _allItems 中取已收藏项目（按收藏时间倒序）
  // 代挂网桶只取意向招标项目（同名正式招标项目不串入，2026-08-17）
  function favoriteItems(bucket) {
    const b = bucket || 'tender';
    const favs = getFavorites(b);
    const pool = b === 'intention' ? _allItems.filter(i => i.project_stage === 'intention') : _allItems;
    const byKey = new Map(pool.map(i => [normalizeProjectName(i.title), i]));
    const out = [];
    for (const key of favs) {
      const it = byKey.get(key);
      if (it) out.push(it);
    }
    return out;
  }

  // 老收藏迁移（2026-08-17 双桶化）：旧待投标桶里已收藏的意向项目 → 自动搬进代挂网桶，幂等
  // 2026-08-19：迁移作用在新 KEY（按当前用户）；老 KEY 跨账号为隔离写，只读不写。
  let _favMigrated = false;
  function migrateLegacyFavorites() {
    if (_favMigrated) return;
    _favMigrated = true;
    try {
      const legacy = getFavorites('tender');
      if (!legacy.length) return;
      // 迁移前备份旧桶完整快照（2026-08-17 防丢）：与历史备份取并集，迁移异常可从备份恢复
      const bakKey = legacyFavKey('tender') + '_backup';
      try {
        const prevBak = JSON.parse(localStorage.getItem(bakKey) || '[]');
        const bak = Array.isArray(prevBak) ? prevBak : [];
        let changed = false;
        for (const k of legacy) if (!bak.includes(k)) { bak.push(k); changed = true; }
        if (changed) localStorage.setItem(bakKey, JSON.stringify(bak));
      } catch (e) { /* 忽略 */ }
      const intentionKeys = new Set(
        _allItems.filter(i => i.project_stage === 'intention').map(i => normalizeProjectName(i.title))
      );
      const moved = legacy.filter(k => intentionKeys.has(k));
      if (!moved.length) return;
      const intentionFavs = getFavorites('intention');
      let changed2 = false;
      for (const k of moved) {
        if (!intentionFavs.includes(k)) { intentionFavs.unshift(k); changed2 = true; }
      }
      if (changed2) localStorage.setItem(favKey('intention'), JSON.stringify(intentionFavs));
      localStorage.setItem(favKey('tender'), JSON.stringify(legacy.filter(k => !intentionKeys.has(k))));
    } catch (e) { /* 忽略 */ }
  }

  // ===== 收藏服务端同步（2026-08-17 防丢根治）=====
  // 服务端为主（SQLite favorites 表），localStorage 当本地缓存；启动拉取并集合并，改动防抖推送
  let _favSyncStarted = false;

  // 并集合并：服务端在前（新收藏在前），本地独有的补后；去重
  function mergeFavLists(serverArr, localArr) {
    const out = [];
    const seen = new Set();
    for (const k of [...(serverArr || []), ...(localArr || [])]) {
      if (!k || seen.has(k)) continue;
      seen.add(k);
      out.push(k);
    }
    return out;
  }

  // 启动拉取：GET 服务端 → 与本地并集 → 写回本地；与本地或服务端有差异即触发推送（老数据自动上云）
  async function loadFavoritesFromServer() {
    try {
      const res = await fetch('/api/tender/favorites', { cache: 'no-cache', headers: apiHeaders() });
      if (!res.ok) return false;
      const data = await res.json();
      const sameArr = (a1, a2) => a1.length === a2.length && a1.every((k, i) => k === a2[i]);
      let localDirty = false;   // 合并结果 ≠ 本地 → 写回本地
      let serverDirty = false;  // 合并结果 ≠ 服务端 → 推送上云（含本地独有/老收藏）
      for (const b of ['tender', 'intention']) {
        const serverArr = Array.isArray(data[b]) ? data[b] : [];
        const localArr = getFavorites(b);
        const merged = mergeFavLists(serverArr, localArr);
        if (!sameArr(merged, localArr)) {
          localStorage.setItem(favKey(b), JSON.stringify(merged));
          localDirty = true;
        }
        if (!sameArr(merged, serverArr)) serverDirty = true;
      }
      if (localDirty || serverDirty) scheduleFavPush();
      return true;
    } catch (e) {
      return false;  // 离线/服务异常：本地照常，下次再同步
    }
  }

  let _favPushTimer = null;
  async function pushFavoritesToServer() {
    _favPushTimer = null;
    try {
      const payload = { tender: getFavorites('tender'), intention: getFavorites('intention') };
      const res = await fetch('/api/tender/favorites', {
        method: 'PUT',
        headers: apiHeaders({ 'Content-Type': 'application/json' }),
        body: JSON.stringify(payload),
      });
      return res.ok;
    } catch (e) {
      return false;  // 推送失败静默，下次改动/加载再推
    }
  }

  function scheduleFavPush() {
    if (_favPushTimer) clearTimeout(_favPushTimer);
    _favPushTimer = setTimeout(pushFavoritesToServer, 1000);
  }

  function startFavSync() {
    if (_favSyncStarted) return;
    _favSyncStarted = true;
    loadFavoritesFromServer();
  }

  // 绑定收藏按钮点击（事件委托，容器级）
  function bindFavButtons(container) {
    if (!container) return;
    container.querySelectorAll('.tender-fav-btn').forEach(btn => {
      btn.addEventListener('click', (e) => {
        e.stopPropagation();
        e.preventDefault();
        const key = btn.dataset.favKey;
        const bucket = btn.dataset.favBucket || 'tender';
        const label = FAV_LABELS[bucket] || '待投标';
        const on = toggleFavorite(key, bucket);
        btn.classList.toggle('tender-fav-btn--on', on);
        btn.title = on ? '取消' + label : '加入' + label;
        btn.setAttribute('aria-label', on ? '取消' + label : '加入' + label);
        btn.textContent = on ? '★' : '☆';
        // 收藏夹视图内取消收藏 → 移出列表
        if ((_activeStage === 'favorites' || _activeStage === 'fav-intention') && !on) {
          const card = btn.closest('.tender-card');
          const row = btn.closest('tr');
          if (card) { card.remove(); }
          if (row) { row.remove(); }
          refreshContent();
        }
        // 服务端同步（防抖 1s，2026-08-17）
        scheduleFavPush();
        // 刷新导航计数
        const stageNav = document.querySelector('.tender-stage');
        if (stageNav) renderStageNavCount();
      });
    });
  }

  function renderStageNavCount() {
    // 只更新收藏夹计数，避免整页重排（2026-08-17：待投标 + 代挂网双计数）
    const favCnt = getFavorites('tender').length;
    const favIntCnt = getFavorites('intention').length;
    document.querySelectorAll('.tender-stage__btn[data-stage="favorites"] .tender-stage__count')
      .forEach(el => { el.textContent = favCnt; });
    document.querySelectorAll('.tender-stage__btn[data-stage="fav-intention"] .tender-stage__count')
      .forEach(el => { el.textContent = favIntCnt; });
  }

  // —— 开标时间显示：日期+小时，中文格式（2026-08-06）——
  function formatBidTime(str) {
    if (!str) return '';
    const hasTime = /\d{1,2}:\d{2}/.test(str);
    const d = parseBidDeadline(str);
    if (!d) return str;
    const pad = n => String(n).padStart(2, '0');
    const datePart = `${d.getFullYear()}年${pad(d.getMonth() + 1)}月${pad(d.getDate())}日`;
    if (!hasTime) return datePart;  // 纯日期不带小时，避免显示 00时
    return `${datePart}${pad(d.getHours())}时`;
  }

  // —— 分类单条 item ——
  // 返回 'upcoming' | 'countdown' | 'history'
  // countdown = 现在起到 7 天内（含今天）未开标的项目，2026-08-07
  function classifyItem(item) {
    const d = parseBidDeadline(item.bid_deadline);
    const now = new Date();
    const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
    const tomorrow = new Date(today); tomorrow.setDate(tomorrow.getDate() + 1);
    const horizon = new Date(now.getTime() + 7 * 24 * 60 * 60 * 1000);

    if (d) {
      if (d < today) return 'history';
      if (d >= today && d < tomorrow) return 'countdown';
      if (d >= tomorrow && d <= horizon) return 'countdown';
      return 'upcoming';
    }

    // 无开标时间不能推断已经开标，归待开标并在列表标记“时间待确认”。
    return 'upcoming';
  }

  // —— 收集所有 items（从 sites 展平），标记来源和分类 ——
  function collectAllItems(data) {
    if (data.schema_version >= 2 && Array.isArray(data.projects)) {
      return data.projects.map(p => {
        // 人工修订优先：overrides[project_id] 覆盖自动打标（2026-08-16）
        const rawDistrict = _districtOverrides[p.id] != null ? _districtOverrides[p.id] : (p.district || '');
        return {
          id: p.id,
          title: p.name,
          url: p.award_url || p.tender_url || ((p.notices || []).at(-1) || {}).url || '',
          date: p.latest_notice_date,
          tenderer: p.purchaser,
          amount: p.budget ? formatMoney(p.budget) : '',
          amount_value: p.budget,
          budget_type: p.budget_type || '',
          bid_deadline: p.open_date,
          notice_type: p.stage === 'awarded' || p.stage === 'candidate' ? 'award'
            : p.stage === 'intention' ? 'intention'
            : ['failed', 'terminated', 'suspended', 'correction', 'contract', 'unknown'].includes(p.stage) ? 'other'
            : 'tender',
          notice_subtype: ((p.notices || []).at(-1) || {}).notice_subtype || '',
          winner: p.winner,
          winning_amount: p.winning_amount,
          std_district: rawDistrict,
          district_raw: rawDistrict,
          district_label: districtLabel(rawDistrict),
          district_overridden: Object.prototype.hasOwnProperty.call(_districtOverrides, p.id),
          proj_major_cat: p.category,
          proj_minor_cat: p.minor_category,
          tags: p.tags || [],
          site: p.site,
          siteCategory: p.site_category || '',
          isIct: Boolean(p.is_ict),
          keywords: p.keywords || [],
          is_new: Boolean(p.is_new),
          isProject: true,
          project_stage: p.stage,
          stage_reason: p.stage_reason,
          link_confidence: p.link_confidence,
          cycle_days: p.cycle_days,
          notices: p.notices || [],
          tender_url: p.tender_url || '',
          award_url: p.award_url || '',
          last_seen: p.last_seen || '',
        };
      });
    }
    const sites = data.sites || {};
    const all = [];
    for (const [siteName, info] of Object.entries(sites)) {
      const ictTitles = new Set((info.ict_items || []).map(i => i.title));
      for (const item of (info.items || [])) {
        all.push({
          ...item,
          site: siteName,
          isIct: ictTitles.has(item.title),
          keywords: ictTitles.has(item.title)
            ? (info.ict_items.find(i => i.title === item.title) || {}).keywords || []
            : []
        });
      }
    }
    return all;
  }

  function normalizeProjectName(name) {
    return String(name || '')
      .replace(/[【】\[\]()（）]/g, '')
      .replace(/(?:采购|招标|询价|竞争性磋商|比选)?(?:公告|结果公示|中标结果公示|中标候选人公示|成交公告|成交结果公示)$/g, '')
      .replace(/\s+/g, '')
      .trim();
  }

  // 主状态与公告类型分开判定，确保一个公告只归属一个入口。
  // 2026-08-07：中标公示（publicity）从历史中标拆出——「中标/成交/中标候选人公示」类公示
  const PUBLICITY_KEYWORDS = ['结果公示', '成交公示', '中标公示'];
  function isPublicity(item) {
    if (!item.isProject) return false;
    if (item.project_stage !== 'awarded') return false;
    const name = item.title || '';
    return PUBLICITY_KEYWORDS.some(kw => name.indexOf(kw) >= 0);
  }
  function getProjectStage(item) {
    if (item.isProject) {
      // 中标公示优先于历史中标（同一项目不应同时出现在两边）
      if (isPublicity(item)) return 'publicity';
      const stageMap = {
        upcoming: 'upcoming', upcoming_unknown_time: 'upcoming',
        opening_today: 'upcoming', awarded: 'awarded', candidate: 'awarded',
        pending_result: 'pending_result', intention: 'intention',
        failed: 'exceptions', terminated: 'exceptions', suspended: 'exceptions',
        correction: 'exceptions', contract: 'exceptions', unknown: 'exceptions',
      };
      // 2026-08-17：招标公告=全量（upcoming/时间待确认/今日开标都归 upcoming），
      // 开标倒计时由 isCountdown() 独立判定（与 upcoming 重叠不互斥），不再分流
      return stageMap[item.project_stage] || 'exceptions';
    }
    const type = item.notice_type || 'tender';
    if (type === 'award') return 'awarded';
    if (type === 'intention') return 'intention';
    if (type !== 'tender') return 'other';

    const timeStage = classifyItem(item);
    if (timeStage === 'upcoming') return 'upcoming';
    return _linkedTenderNames.has(normalizeProjectName(item.title)) ? 'awarded-linked' : 'pending_result';
  }

  // 开标倒计时独立判定（2026-08-17）：截止当日 + 有开标时间 + 未开标 + 未来7天内
  function isCountdown(item) {
    if (!item || !item.bid_deadline) return false;
    const d = parseBidDeadline(item.bid_deadline);
    if (!d) return false;
    const now = new Date();
    const horizon = new Date(now.getTime() + 7 * 24 * 60 * 60 * 1000);
    return d > now && d <= horizon;
  }

  function formatGenTime(genAt) {
    // 抓取时间拆两行显示（日期/时间），卡片与其他汇总卡等大（2026-08-06）
    const t = genAt || '';
    const parts = String(t).trim().split(/[\s]+/);
    if (parts.length >= 2) {
      return `${escapeHtml(parts[0])}<span class="tender-summary__time">${escapeHtml(parts.slice(1).join(' '))}</span>`;
    }
    return escapeHtml(t || '—');
  }

  function renderStageNav(counts) {
    const primary = [
      { key: 'all', label: '全部' },
      { key: 'intention', label: '意向招标' },
      { key: 'upcoming', label: '招标公告' },
      { key: 'countdown', label: '开标倒计时' },
      { key: 'favorites', label: '待投标' },
      { key: 'fav-intention', label: '代挂网' },  // 2026-08-17 意向收藏夹（老板命名）
    ];
    const secondary = [
      { key: 'publicity', label: '中标公示' },
      { key: 'awarded', label: '历史中标' },
      { key: 'exceptions', label: '异常公告' },
      { key: 'pending_result', label: '待跟踪结果' },
    ];
    const button = item => `<button class="tender-stage__btn ${_activeStage === item.key ? 'tender-stage__btn--active' : ''}" data-stage="${item.key}" type="button">${item.label}<span class="tender-stage__count">${counts[item.key] || 0}</span></button>`;
    return `
      <div class="tender-stage" aria-label="项目进度">
        <div class="tender-stage__primary">${primary.map(button).join('')}</div>
        <div class="tender-stage__secondary">${secondary.map(button).join('')}</div>
      </div>
    `;
  }

  let _allItems = [];   // 缓存全部条目
  let _data = null;     // 缓存最近一次 render 的数据（下拉切换区县用）
  let _districtOverrides = {};  // 人工区县修订（2026-08-16）：project_id → 区县原始值；loadDistrictOverrides 加载
  // 2026-08-04 多选筛选：空数组 = 全部
  let _activeStage = 'all'; // 当前项目进度（主状态或辅助入口，单选；2026-08-06 默认全部）
  let _sites = [];      // 监控网页多选（平台名）
  let _activeCat = [];      // 行业大类多选（2026-08-17 改多选；[] = 全部）
  let _activeAmount = [];   // 金额区间多选（2026-08-17 改多选）
  let _activeSme = [];      // 中小微多选（2026-08-17 改多选）
  let _activeIct = [];      // ICT 多选（2026-08-17 改多选）
  let _activeType = [];     // 政府/企业分类多选（2026-08-17 改多选）
  let _activeDate = null;      // 发布日期筛选（2026-08-08：{from:'YYYY-MM-DD', to:'YYYY-MM-DD'} 或 null）
  let _activeOpenDate = null;  // 开标时间筛选（2026-08-08：{from:'YYYY-MM-DD', to:'YYYY-MM-DD'} 或 null）
  let _activeDateKind = 'publish';  // 当前弹层在编辑哪个：'publish' | 'open'（2026-08-08）
  let _activeSearch = '';      // 标题关键词搜索（2026-08-04 P2）      // 标题关键词搜索（2026-08-04 P2）
  let _overridesLoaded = false; // 区县修订是否已从 API 加载（2026-08-16）

  // —— 分页状态（Phase 4 分页）——
  const PAGE_SIZE = 20;   // 每页条数
  let _currentPage = 1;   // 当前页
  let _currentList = [];  // 当前筛选后的列表（分页基于它切片）
  let _linkedTenderNames = new Set(); // 已关联中标公告的招标项目名
  let _awardLinkMap = new Map();      // 中标公告名 → 关联信息

  // 公告类型映射
  const TYPE_MAP = {
    'tender': '招标',
    'award': '中标',
    'intention': '意向',
    'other': '其他',
  };
  const PROJECT_STAGE_MAP = {
    upcoming: '招标公告', upcoming_unknown_time: '时间待确认', opening_today: '今日开标',
    pending_result: '待跟踪', candidate: '候选', awarded: '中标',
    intention: '意向招标', failed: '流标/废标', terminated: '已终止',
    suspended: '已暂停', correction: '更正/澄清', contract: '合同', unknown: '待识别',
  };

  function renderItemBadge(item) {
    if (item.isProject) {
      const cssType = item.project_stage === 'awarded' ? 'award'
        : item.project_stage === 'candidate' || item.project_stage === 'intention' ? 'intention'
        : ['failed', 'terminated', 'suspended', 'correction', 'contract', 'unknown'].includes(item.project_stage) ? 'other'
        : 'tender';
      // 已下架类状态（数据源明确发过终止/流标/暂停/废标公告 = 硬信号，2026-08-16）
      const gone = ['failed', 'terminated', 'suspended'].includes(item.project_stage);
      const goneCls = gone ? ' tender-type-badge--gone' : '';
      const clickable = gone ? ' data-stage-tip="1" style="cursor:pointer"' : '';
      return `<span class="tender-type-badge tender-type-badge--${cssType}${goneCls}"${clickable}>${PROJECT_STAGE_MAP[item.project_stage] || '项目'}</span>`;
    }
    return `<span class="tender-type-badge tender-type-badge--${item.notice_type || 'tender'}">${TYPE_MAP[item.notice_type] || '招标'}</span>`;
  }

  // —— 区县子菜单（2026-08-04）——
  // 每个区县对应 std_district 字段（Phase 5 元数据富化打标）
  const DISTRICTS = [
    { key: 'all',      label: '全部', match: () => true },
    { key: 'shiji',    label: '市级', match: d => d === '市级' },
    // 盐开区筛选只认 std_district（与卡片显示同源）——苏服采已完成 areaCode 细分，
    // 市级/其他区县项目不再混入盐开区。target_region 透传已于 2026-08-24 清理。
    { key: 'yankai',    label: '盐开区', match: d => d === '盐南' || d === '经开' },
    { key: 'yandu',     label: '盐都区', match: d => d === '盐都' },
    { key: 'tinghu',    label: '亭湖区', match: d => d === '亭湖' },
    { key: 'dafeng',    label: '大丰区', match: d => d === '大丰' },
    { key: 'xiangshui', label: '响水县', match: d => d === '响水' },
    { key: 'binhai',    label: '滨海县', match: d => d === '滨海' },
    { key: 'funing',    label: '阜宁县', match: d => d === '阜宁' },
    { key: 'sheyang',   label: '射阳县', match: d => d === '射阳' },
    { key: 'jianhu',    label: '建湖县', match: d => d === '建湖' },
    { key: 'dongtai',   label: '东台市', match: d => d === '东台' },
    { key: 'unassigned', label: '未划分至区县', match: d => !d },
  ];

  // 区县原始值 → 显示名（人工修订也用同一映射；2026-08-16）
  const DISTRICT_LABELS = {
    '市级': '市级',
    '盐南': '盐开区', '经开': '盐开区', '盐都': '盐都区', '亭湖': '亭湖区',
    '大丰': '大丰区', '响水': '响水县', '滨海': '滨海县', '阜宁': '阜宁县',
    '射阳': '射阳县', '建湖': '建湖县', '东台': '东台市',
  };
  // 修订时可选的区县（2026-08-16）：盐南/经开合并为「盐开区」一项（避免重复显示）
  // vals=命中的自动值（任一匹配即点亮），save=实际保存值
  const EDITABLE_DISTRICTS = [
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
  function districtLabel(raw) {
    return DISTRICT_LABELS[raw] || raw || '—';
  }

  // —— 加载人工区县修订（2026-08-16）——
  function apiHeaders(extra) {
    const headers = Object.assign({}, extra || {});
    const cred = (window.Auth && window.Auth.getCred) ? window.Auth.getCred() : '';
    if (cred) headers['Authorization'] = 'Basic ' + cred;
    return headers;
  }
  async function loadDistrictOverrides() {
    try {
      const res = await fetch('/api/tender/district_overrides', { cache: 'no-cache', headers: apiHeaders() });
      if (res.ok) {
        const data = await res.json();
        _districtOverrides = data || {};
        return true;
      }
      console.warn('区县修订加载失败:', res.status);
      _districtOverrides = {};
      return false;
    } catch (e) {
      console.warn('区县修订加载失败:', e);
      _districtOverrides = {};
      return false;
    }
  }

  let _activeDistrict = 'all';   // 当前激活区县（2026-08-06：默认全部）
  let _districtItems = [];          // 当前区县筛选后的条目

  function render(data, root) {
    root = root || document.getElementById('tender-body');
    if (!root) return;

    // 2026-08-23：render 重建筛选栏前，清理 body 下残留的 fixed 菜单（僵尸菜单）
    // 场景：菜单打开中（menu 已在 body）时触发 render（点区县/搜索等）→ 旧 root 被替换，
    // body 下的 menu 不会自动消失，必须手动移除。
    document.querySelectorAll('.tender-multi__menu[data-fixed-pos]').forEach(m => m.remove());

    if (!data || !data.summary) {
      root.innerHTML = emptyState('暂无数据');
      return;
    }

    _data = data;

    // 区县下拉触发只绑一次（trigger 在 index.html 静态头部，不随 render 重建）
    bindDropdown();

    // 人工区县修订：首次渲染前先加载（2026-08-16）
    if (!_overridesLoaded) {
      _overridesLoaded = true;
      loadDistrictOverrides().then((ok) => {
        if (ok === false) {
          // 加载失败：允许下次 render 重试（2026-08-16 复查修复）
          _overridesLoaded = false;
          return;
        }
        // 修订加载完重新收集条目（合并修订），再完整重渲染一次
        if (_data) render(_data, root);
      });
    }

    const s = data.summary;
    const sites = data.sites || {};
    _allItems = collectAllItems(data);
    // 老收藏双桶迁移（2026-08-17）：意向项目自动搬进代挂网桶
    migrateLegacyFavorites();
    // 收藏服务端同步（2026-08-17）：首次拉取并集合并 + 上云
    startFavSync();
    _sitesData = sites;
    _links = data.links || [];
    _linkedTenderNames = new Set(_links.map(l => normalizeProjectName(l.tender_name)));
    _awardLinkMap = new Map(_links.map(l => [normalizeProjectName(l.award_name), l]));

    // 按区县筛选（2026-08-04 区县子菜单）
    _districtItems = filterByDistrict(_allItems);

    // 项目进度统计（基于当前区县；主状态互斥）
    const stageCounts = { all: _districtItems.filter(i => getProjectStage(i) !== 'awarded' && getProjectStage(i) !== 'publicity').length, upcoming: 0, countdown: 0, awarded: 0, publicity: 0, pending_result: 0, candidate: 0, intention: 0, exceptions: 0, favorites: getFavorites('tender').length, 'fav-intention': getFavorites('intention').length };
    // 2026-08-17：countdown 独立统计（与 upcoming 重叠，不漏算）
    stageCounts.countdown = _districtItems.filter(i => isCountdown(i)).length;
    for (const item of _districtItems) {
      const stage = getProjectStage(item);
      if (stageCounts[stage] != null) stageCounts[stage]++;
    }

    // 行业大类统计（基于当前区县，2026-08-04）
    const catCounts = { all: _districtItems.length };
    for (const item of _districtItems) {
      const c = item.proj_major_cat || '未分类';
      catCounts[c] = (catCounts[c] || 0) + 1;
    }

    // 当前区县汇总（从 _districtItems 计算，非全局）
    const dTotal = _districtItems.length;
    const dIct = _districtItems.filter(i => i.isIct).length;
    const activeLabel = (DISTRICTS.find(d => d.key === _activeDistrict) || {}).label || '';
    // 平台总数 = 监控的全部平台（全局），不随区县筛选变化
    const totalSites = Object.keys(sites).length;

    // 汇总卡片（反映当前区县）
    const summaryHtml = `
      <div class="tender-summary">
        <div class="tender-summary__card tender-summary__card--info tender-summary__card--clickable" onclick="Tender.showSites()">
          <span class="tender-summary__label">平台数</span>
          <span class="tender-summary__value">${totalSites}<span class="tender-summary__hint">→</span></span>
          <span class="tender-summary__sub">已监控 · 点击查看</span>
        </div>
        <div class="tender-summary__card tender-summary__card--clickable" onclick="window.open(location.protocol+'//'+location.hostname+':5313/', '_blank')" title="打开监控中心大屏">
          <span class="tender-summary__label">${data.schema_version >= 2 ? activeLabel + '项目数' : activeLabel + '公告数'}</span>
          <span class="tender-summary__value">${Utils.formatInt(dTotal)}<span class="tender-summary__hint">→</span></span>
          <span class="tender-summary__sub">大屏展示 · 点击查看</span>
        </div>
        <div class="tender-summary__card tender-summary__card--accent tender-summary__card--clickable" onclick="Tender.showIctPie()">
          <span class="tender-summary__label">ICT 相关</span>
          <span class="tender-summary__value tender-summary__value--accent">${dIct}</span>
          <span class="tender-summary__sub">占比 ${dTotal ? (dIct / dTotal * 100).toFixed(1) : 0}% · 点击看类型分布</span>
        </div>
        <div class="tender-summary__card tender-summary__card--success">
          <span class="tender-summary__label">抓取时间</span>
          <span class="tender-summary__value">${formatGenTime(data.generated_at)}</span>
        </div>
      </div>
    `;

    // 三个主状态 + 三个辅助入口。状态互斥，避免中标公告混入待开标。
    const stageNavHtml = renderStageNav(stageCounts);
    // 监控网页多选（按政府/企业分组，2026-08-07）
    const siteOptions = Object.keys(sites)
      .map(sn => ({
        key: sn, label: sn, cnt: _districtItems.filter(i => i.site === sn).length,
        category: (sites[sn] || {}).category || '',
      }))
      .sort((a, b) => a.label.localeCompare(b.label, 'zh-Hans-CN'));

    // ICT 选项（2026-08-05：替代原"标签"下拉，ICT 是核心关注点单独入口）
    const ictCnt = _districtItems.filter(i => i.isIct).length;
    const nonIctCnt = _districtItems.length - ictCnt;
    const ictOptions = [
      { key: 'ict',     label: '仅 ICT', cnt: ictCnt },
      { key: 'non_ict', label: '非 ICT', cnt: nonIctCnt },
    ];

    // 政府/企业分类选项（2026-08-07：按站点归属过滤）
    const govCnt = _districtItems.filter(i => (i.siteCategory || '') === '政府').length;
    const entCnt = _districtItems.filter(i => (i.siteCategory || '') === '企业').length;
    const typeOptions = [
      { key: '政府', label: '政府分类', cnt: govCnt },
      { key: '企业', label: '企业分类', cnt: entCnt },
    ];

    // 金额区间选项（2026-08-05）：从 tags 统计，无金额的归"其他"
    const amtBands = ['金额<10万', '金额10-100万', '金额100-500万', '金额>500万'];
    const amtCounts = {};
    let noAmount = 0;
    for (const it of _districtItems) {
      const hasAmt = (it.tags || []).some(t => t.startsWith('金额'));
      if (!hasAmt) { noAmount++; continue; }
      const band = (it.tags || []).find(t => amtBands.includes(t));
      if (band) amtCounts[band] = (amtCounts[band] || 0) + 1;
    }
    const amountOptions = amtBands.map(b => ({ key: b, label: b.replace('金额', ''), cnt: amtCounts[b] || 0 }))
      .concat([{ key: '其他', label: '金额不详', cnt: noAmount }]);  // 2026-08-17 改名

    // 中小微选项（2026-08-05）：从 tags 统计
    const smeTags = ['中小微-专门面向', '中小微-非专门但优惠'];
    const smeCounts = {};
    let smeNone = 0;
    for (const it of _districtItems) {
      const smeTag = (it.tags || []).find(t => t.startsWith('中小微'));
      if (smeTag) smeCounts[smeTag] = (smeCounts[smeTag] || 0) + 1;
      else smeNone++;
    }
    const smeOptions = smeTags.map(t => ({ key: t, label: t.replace('中小微-', ''), cnt: smeCounts[t] || 0 }))
      .concat([{ key: '不涉及', label: '不涉及', cnt: smeNone }]);

    const filterBarHtml = `
      <div class="tender-filterbar">
        <div class="tender-filterbar__row">
          ${renderSingleDropdown('ict', 'ICT', ictOptions, _activeIct)}
          ${renderSingleDropdown('type', '分类', typeOptions, _activeType)}
          ${renderCatDropdownTrigger()}
          ${renderMultiDropdown('sites', '监控网页', siteOptions, _sites)}
          ${renderSingleDropdown('amount', '金额', amountOptions, _activeAmount)}
          ${renderSingleDropdown('sme', '中小微', smeOptions, _activeSme)}
        </div>
        <div class="tender-filterbar__search">
          <div class="tender-search">
            <input type="text" class="tender-search__input" id="tender-search-input" placeholder="搜索标题/发包人…" value="${escapeHtml(_activeSearch)}" />
            ${_activeSearch ? `<button class="tender-search__clear" id="tender-search-clear" title="清除搜索">✕</button>` : ''}
            <button class="tender-search__btn" id="tender-search-btn" type="button">搜索</button>
          </div>
        </div>
      </div>
    `;

    // 内容（多选筛选链 + 分页）。收藏夹：跨区县、不叠加其他筛选器（2026-08-06）
    let baseList;
    if (_activeStage === 'favorites') {
      baseList = favoriteItems('tender');
    } else if (_activeStage === 'fav-intention') {
      baseList = favoriteItems('intention');
    } else {
      baseList = filterByStage(filterBySites(filterByType(filterByIct(filterByDate(filterByOpenDate(filterByAmount(filterBySme(_districtItems))))))));
    }
    _currentList = filterByCat(filterBySearch(baseList));
    _currentPage = 1;
    const contentHtml = renderGroup(_currentList, _sites.length === 1 && _sites[0] === '__none__' ? 'sites-none' : '');

    // 区县下拉：移到汇总卡之后、项目进度(stage)之前（2026-08-06 方案B）
    const dropdownHtml = renderDropdownHtml();
    root.innerHTML = summaryHtml + dropdownHtml + stageNavHtml + filterBarHtml + `<div id="tender-subcontent">${contentHtml}</div>`;

    // 绑定多选下拉（2026-08-04）
    bindStageNav();
    bindMultiDropdown('sites');
    bindSingleDropdown('ict');
    bindSingleDropdown('type');
    bindSingleDropdown('amount');
    bindSingleDropdown('sme');
    bindMultiDocClose();
    bindCatDropdown();
    renderCatDropdown(_districtItems);
    bindDateFilter();  // 2026-08-08 发布日期列筛选
    // 2026-08-23：每次 render 后重新注册 fixed-pos 观察（MO 只观察绑定时的元素，
    // 重建后的新 .tender-multi 必须重新 observe + 重设 _menuOwner，否则关闭不触发还原）
    bindMenuFixedObservers();

    // 绑定搜索输入（2026-08-04：回车才搜索 + 搜索按钮）
    const searchInput = document.getElementById('tender-search-input');
    const searchBtn = document.getElementById('tender-search-btn');
    const doSearch = () => {
      if (!searchInput) return;
      _activeSearch = searchInput.value.trim();
      _currentPage = 1;
      render(_data);
    };
    if (searchInput) {
      searchInput.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') doSearch();
      });
      const clearBtn = document.getElementById('tender-search-clear');
      if (clearBtn) {
        clearBtn.addEventListener('click', () => {
          _activeSearch = '';
          _currentPage = 1;
          render(_data);
        });
      }
    }
    if (searchBtn) {
      searchBtn.addEventListener('click', doSearch);
    }

    // 绑定分页按钮
    bindPagination(document.getElementById('tender-subcontent'));
    // 绑定移动端卡片点击
    bindCardList(document.getElementById('tender-subcontent'));
    // 绑定收藏按钮（2026-08-06）
    bindFavButtons(document.getElementById('tender-subcontent'));
  }

  // —— 刷新内容区（重置到第 1 页）——
  function refreshContent() {
    // 收藏夹：跨区县、不叠加其他筛选器（2026-08-06）
    let baseList;
    if (_activeStage === 'favorites') {
      baseList = favoriteItems('tender');
    } else if (_activeStage === 'fav-intention') {
      baseList = favoriteItems('intention');
    } else {
      baseList = filterByStage(filterBySites(filterByType(filterByIct(filterByDate(filterByOpenDate(filterByAmount(filterBySme(_districtItems))))))));
    }
    _currentList = filterByCat(filterBySearch(baseList));
    _currentPage = 1;
    const sub = document.getElementById('tender-subcontent');
    if (sub) {
      sub.innerHTML = renderGroup(_currentList, _sites.length === 1 && _sites[0] === '__none__' ? 'sites-none' : '');
      bindPagination(sub);
      bindCardList(sub);
      bindFavButtons(sub);
    }
  }

  // —— 绑定分页按钮点击 ——
  function bindPagination(container) {
    if (!container) return;
    container.querySelectorAll('.tender-pagebtn').forEach(btn => {
      btn.addEventListener('click', () => {
        if (btn.disabled) return;
        const p = parseInt(btn.dataset.page, 10);
        if (!p || p === _currentPage) return;
        _currentPage = p;
        const sub = document.getElementById('tender-subcontent');
        if (sub) {
          sub.innerHTML = renderGroup(_currentList);
          bindPagination(sub);
          bindCardList(sub);
          bindFavButtons(sub);
        }
      });
    });
  }

  // —— 绑定移动端卡片整行点击展开/收起（2026-08-04 手机排版优化）——
  function bindCardList(container) {
    if (!container) return;
    container.querySelectorAll('.tender-card').forEach(card => {
      card.addEventListener('click', (e) => {
        // 点击区县编辑不触发展开（2026-08-16 修复：避免弹窗+展开同时发生）
        if (e.target.closest('[data-card-district-edit]')) return;
        const open = card.dataset.open === '1';
        card.dataset.open = open ? '0' : '1';
      });
    });
  }

  // —— 按互斥项目进度筛选 ——
  function filterByStage(items) {
    // 全部：进行中项目（排除已中标，已中标仅在历史中标入口看，2026-08-07）
    if (_activeStage === 'all') return (items || []).filter(i => getProjectStage(i) !== 'awarded');
    // 收藏夹：只显示已收藏项目（跨区县、不叠加其他筛选器，2026-08-06）
    if (_activeStage === 'favorites') return favoriteItems('tender');
    // 代挂网：只显示已收藏的意向招标项目（2026-08-17）
    if (_activeStage === 'fav-intention') return favoriteItems('intention');
    // 开标倒计时：独立判定（2026-08-17），与「招标公告」重叠不互斥
    if (_activeStage === 'countdown') return (items || []).filter(i => isCountdown(i));
    return (items || []).filter(i => getProjectStage(i) === _activeStage);
  }

  function bindStageNav() {
    document.querySelectorAll('.tender-stage__btn').forEach(btn => {
      btn.addEventListener('click', () => {
        _activeStage = btn.dataset.stage || 'all';
        _currentPage = 1;
        render(_data);
      });
    });
  }

  // —— 按监控网页多选筛选（2026-08-04）——
  function filterBySites(items) {
    if (!_sites || _sites.length === 0) return items || [];
    if (_sites.length === 1 && _sites[0] === '__none__') return [];
    return (items || []).filter(i => _sites.includes(i.site));
  }

  // —— 按发布日期范围筛选（2026-08-08）——
  function filterByDate(items) {
    if (!_activeDate) return items || [];
    const { from, to } = _activeDate;
    return (items || []).filter(i => {
      if (!i.date) return false;
      // date 为 YYYY-MM-DD 字符串，字典序可直接比较
      return i.date >= from && i.date <= to;
    });
  }

  // —— 按开标时间范围筛选（2026-08-08）——
  function filterByOpenDate(items) {
    if (!_activeOpenDate) return items || [];
    const { from, to } = _activeOpenDate;
    return (items || []).filter(i => {
      if (!i.bid_deadline) return false;
      const d = parseBidDeadline(i.bid_deadline);
      if (!d) return false;
      const ds = `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`;
      return ds >= from && ds <= to;
    });
  }

  // —— 按 ICT 筛选（2026-08-05：替代原"标签"下拉；2026-08-17 改多选）——
  function filterByIct(items) {
    if (!_activeIct || _activeIct.length === 0) return items || [];
    // 多选：匹配任一选中值（仅 ICT / 非 ICT 互斥，但保留数组语义）
    return (items || []).filter(i => {
      if (_activeIct.includes('ict') && i.isIct) return true;
      if (_activeIct.includes('non_ict') && !i.isIct) return true;
      return false;
    });
  }

  // —— 按政府/企业分类筛选（2026-08-07：按站点归属；2026-08-17 改多选）——
  function filterByType(items) {
    if (!_activeType || _activeType.length === 0) return items || [];
    return (items || []).filter(i => _activeType.includes(i.siteCategory || ''));
  }

  // —— 按金额区间筛选（2026-08-05；2026-08-17 改多选）——
  function filterByAmount(items) {
    if (!_activeAmount || _activeAmount.length === 0) return items || [];
    return (items || []).filter(i => {
      const tags = i.tags || [];
      for (const band of _activeAmount) {
        if (band === '其他') {
          if (!tags.some(t => t.startsWith('金额'))) return true;
        } else {
          if (tags.includes(band)) return true;
        }
      }
      return false;
    });
  }

  // —— 按中小微筛选（2026-08-05；2026-08-17 改多选）——
  function filterBySme(items) {
    if (!_activeSme || _activeSme.length === 0) return items || [];
    return (items || []).filter(i => {
      const tags = i.tags || [];
      for (const smeTag of _activeSme) {
        if (smeTag === '不涉及') {
          if (!tags.some(t => t.startsWith('中小微'))) return true;
        } else {
          if (tags.includes(smeTag)) return true;
        }
      }
      return false;
    });
  }

  // —— 按行业大类筛选（2026-08-04；2026-08-17 改多选）——
  function filterByCat(items) {
    if (!_activeCat || _activeCat.length === 0) return items || [];
    return (items || []).filter(i => _activeCat.includes(i.proj_major_cat || '未分类'));
  }

  // —— 按标题关键词搜索（2026-08-04 P2）——
  function filterBySearch(items) {
    if (!_activeSearch) return items || [];
    const kw = _activeSearch.toLowerCase();
    return (items || []).filter(i => {
      const title = (i.title || '').toLowerCase();
      const tenderer = (i.tenderer || '').toLowerCase();
      return title.includes(kw) || tenderer.includes(kw);
    });
  }

  // —— 按区县筛选（2026-08-04）——
  function filterByDistrict(items) {
    const d = DISTRICTS.find(x => x.key === _activeDistrict);
    if (!d) return items || [];
    return (items || []).filter(i => d.match(i.std_district || '', i));
  }

  // ===== 多选下拉菜单（2026-08-04 重构）=====
  // 子标签 / 公告类型 / 监控网页 三个多选筛选共用一套逻辑

  // 渲染多选下拉（返回 HTML 字符串）
  // type: 'status'|'sites'；options: [{key,label,cnt}]；selected: 当前选中 key 数组
  function renderMultiDropdown(type, title, options, selected) {
    const sel = selected || [];
    // 2026-08-23：清空哨兵 __none__ → 全部不勾 + label“未选任何网页”
    const isNone = sel.length === 1 && sel[0] === '__none__';
    const isAll = sel.length === 0;
    const labelText = isNone ? '未选任何网页'
      : isAll ? '全部' + title
      : sel.length === 1 ? (options.find(o => o.key === sel[0]) || {}).label || sel[0]
      : `已选 ${sel.length} 项`;
    const allChecked = isAll;
    // 2026-08-23：跟其他按钮统一 ——「全部监控网页」改为列表顶部全宽选项（与站点项同样式），
    // 不再用头部独立「全选」按钮（样式不一致、不好找）
    const allItemHtml = `
      <button class="tender-multi__item ${isAll ? 'tender-multi__item--on' : ''}" data-key="__all__" data-type="${type}" type="button" role="option" aria-selected="${isAll}">
        <span class="tender-multi__check">${isAll ? '✓' : ''}</span>
        <span class="tender-multi__label">全部${escapeHtml(title)}</span>
      </button>
    `;
    // 2026-08-07：监控网页按政府/企业分组
    let itemsHtml;
    if (type === 'sites' && options.some(o => o.category)) {
      const govOpts = options.filter(o => o.category === '政府');
      const entOpts = options.filter(o => o.category === '企业');
      const renderGroup = (label, opts, color) => {
        if (!opts.length) return '';
        const optsHtml = opts.map(o => {
          // 2026-08-23：全选（空数组）时菜单显示全勾；清空时不勾
          const on = isAll ? true : (isNone ? false : sel.includes(o.key));
          return `
            <button class="tender-multi__item ${on ? 'tender-multi__item--on' : ''}" data-key="${o.key}" data-type="${type}" type="button" role="option" aria-selected="${on}">
              <span class="tender-multi__check">${on ? '✓' : ''}</span>
              <span class="tender-multi__tag tender-multi__tag--${color}">${color === 'gov' ? '政' : '企'}</span>
              <span class="tender-multi__label">${escapeHtml(o.label)}</span>
            </button>`;
        }).join('');
        return `<div class="tender-multi__group">
          <div class="tender-multi__group-title">${label} <span class="tender-multi__group-count">${opts.length}</span></div>
          ${optsHtml}
        </div>`;
      };
      itemsHtml = allItemHtml + renderGroup('政府', govOpts, 'gov') + renderGroup('企业', entOpts, 'ent');
    } else {
      itemsHtml = allItemHtml + options.map(o => {
        const on = isAll ? true : (isNone ? false : sel.includes(o.key));
        return `
          <button class="tender-multi__item ${on ? 'tender-multi__item--on' : ''}" data-key="${o.key}" data-type="${type}" type="button" role="option" aria-selected="${on}">
            <span class="tender-multi__check">${on ? '✓' : ''}</span>
            <span class="tender-multi__label">${escapeHtml(o.label)}</span>
          </button>`;
      }).join('');
    }
    return `
      <div class="tender-multi tender-multi--${type}" data-multi="${type}">
        <button class="tender-multi__trigger${(!isNone && sel.length) ? ' tender-multi__trigger--active' : ''}${isNone ? ' tender-multi__trigger--empty' : ''}" data-multi-trigger="${type}" type="button" aria-haspopup="listbox" aria-expanded="false">
          <span class="tender-multi__label">${escapeHtml(labelText)}</span>
          <span class="tender-multi__caret">▾</span>
        </button>
        <div class="tender-multi__menu" role="listbox" aria-label="${escapeHtml(title)}" aria-multiselectable="true">
          <div class="tender-multi__head">
            <span class="tender-multi__head-label">${escapeHtml(title)}</span>
            <button class="tender-multi__clear" data-multi-clear="${type}" type="button">清空选择</button>
          </div>
          <div class="tender-multi__list">${itemsHtml}</div>
        </div>
      </div>
    `;
  }

  // 单选下拉（2026-08-07 重构：「全部」改为列表顶部全宽选项，与行业下拉风格一致、点击区更大）
  function renderSingleDropdown(type, title, options, active) {
    const sel = Array.isArray(active) ? active : (active === 'all' || !active ? [] : [active]);
    // 2026-08-24：与「全部监控网页」一致 —— 全部状态下所有选项也显示勾选
    const isAll = sel.length === 0;
    const labelText = sel.length === 0 ? '全部' + title
      : sel.length === 1 ? (options.find(o => o.key === sel[0]) || {}).label || sel[0]
      : `已选 ${sel.length} 项`;
    const allItemHtml = `
        <button class="tender-multi__item ${sel.length === 0 ? 'tender-multi__item--on' : ''}" data-key="__all__" data-single="${type}">
          <span class="tender-multi__check">${sel.length === 0 ? '✓' : ''}</span>
          <span class="tender-multi__label">全部${escapeHtml(title)}</span>
        </button>
    `;
    const itemsHtml = options.map(o => {
      const on = isAll ? true : sel.includes(o.key);
      return `
        <button class="tender-multi__item ${on ? 'tender-multi__item--on' : ''}" data-key="${o.key}" data-single="${type}">
          <span class="tender-multi__check">${on ? '✓' : ''}</span>
          <span class="tender-multi__label">${escapeHtml(o.label)}</span>
        </button>
      `;
    }).join('');
    return `
      <div class="tender-multi tender-multi--${type}" data-multi="${type}">
        <button class="tender-multi__trigger${sel.length ? ' tender-multi__trigger--active' : ''}" data-multi-trigger="${type}" type="button" aria-haspopup="listbox" aria-expanded="false">
          <span class="tender-multi__label">${escapeHtml(labelText)}</span>
          <span class="tender-multi__caret">▾</span>
        </button>
        <div class="tender-multi__menu">
          <div class="tender-multi__list">${allItemHtml}${itemsHtml}</div>
        </div>
      </div>
    `;
  }

  // 绑定单选下拉（2026-08-05；2026-08-17 改多选：菜单不缩回，可连续选择）
  function bindSingleDropdown(type) {
    const rootEl = document.querySelector(`[data-multi="${type}"]`);
    if (!rootEl) return;
    const trigger = rootEl.querySelector('.tender-multi__trigger');
    if (!trigger) return;
    trigger.addEventListener('click', (e) => {
      e.stopPropagation();
      document.querySelectorAll('.tender-multi--open').forEach(el => {
        if (el !== rootEl) {
          el.classList.remove('tender-multi--open');
          const otherTrigger = el.querySelector('.tender-multi__trigger');
          if (otherTrigger) otherTrigger.setAttribute('aria-expanded', 'false');
        }
      });
      rootEl.classList.toggle('tender-multi--open');
      // 2026-08-20：同步 aria-expanded
      trigger.setAttribute('aria-expanded', rootEl.classList.contains('tender-multi--open'));
    });

    trigger.addEventListener('keydown', (e) => {
      if (!['ArrowDown', 'Enter', ' '].includes(e.key) || rootEl.classList.contains('tender-multi--open')) return;
      e.preventDefault();
      trigger.click();
      requestAnimationFrame(() => {
        const firstItem = document.querySelector('.tender-multi__item[data-single="' + type + '"]');
        if (firstItem) firstItem.focus({ preventScroll: true });
      });
    });
    // 多选状态存取：type → 对应数组
    const getArr = () => {
      if (type === 'amount') return _activeAmount;
      if (type === 'sme') return _activeSme;
      if (type === 'ict') return _activeIct;
      if (type === 'type') return _activeType;
      return [];
    };
    const setArr = (arr) => {
      if (type === 'amount') _activeAmount = arr;
      else if (type === 'sme') _activeSme = arr;
      else if (type === 'ict') _activeIct = arr;
      else if (type === 'type') _activeType = arr;
    };
    rootEl.querySelectorAll('.tender-multi__item').forEach(item => {
      item.addEventListener('click', (e) => {
        e.stopPropagation();
        const raw = item.dataset.key;
        const key = raw === '__all__' ? 'all' : raw;
        let arr = getArr();
        if (key === 'all') {
          arr = [];  // 全部 = 清空
        } else {
          arr = arr.slice();
          const idx = arr.indexOf(key);
          if (idx >= 0) arr.splice(idx, 1); else arr.push(key);
        }
        setArr(arr);
        _currentPage = 1;
        // 手动更新 ✓ 标记 + 触发器文字 + refreshContent（菜单保持打开，2026-08-17）
        renderSingleChecks(rootEl, arr);
        const labelEl = trigger.querySelector('.tender-multi__label');
        if (labelEl) {
          const titles = { ict: 'ICT', type: '分类', amount: '金额', sme: '中小微' };
          if (arr.length === 0) labelEl.textContent = '全部' + (titles[type] || '');
          else if (arr.length === 1) {
            // 2026-08-23：菜单打开时会被移到 body 下（8-21 fixed-pos 机制），
            // 容器内 querySelectorAll 查不到 item → 全局查 [data-single] 取中文 label
            const onItem = Array.from(document.querySelectorAll('[data-single="' + type + '"]'))
              .find(it => (it.dataset.key === '__all__' ? 'all' : it.dataset.key) === arr[0]);
            labelEl.textContent = onItem ? (onItem.querySelector('.tender-multi__label') || {}).textContent || arr[0] : arr[0];
          } else labelEl.textContent = `已选 ${arr.length} 项`;
        }
        refreshContent();
      });
    });
  }

  // 2026-08-17：更新单选下拉（现多选）的 ✓ 标记
  function renderSingleChecks(rootEl, arr) {
    // 2026-08-23：菜单打开时在 body 下（8-21 fixed-pos），容器内查不到 item → 全局查
    const type = rootEl.dataset.multi;
    document.querySelectorAll('[data-single="' + type + '"]').forEach(it => {
      const itKey = it.dataset.key === '__all__' ? 'all' : it.dataset.key;
      // 2026-08-24：全部状态下所有选项都勾选（与「全部监控网页」一致）
      const on = arr.length === 0 ? true : arr.includes(itKey);
      it.classList.toggle('tender-multi__item--on', on);
      const check = it.querySelector('.tender-multi__check');
      if (check) check.textContent = on ? '✓' : '';
    });
    // 2026-08-23 灵动：同步触发器“已筛选”状态（非全部时高亮 + 圆点）
    const t = rootEl.querySelector('.tender-multi__trigger');
    if (t) t.classList.toggle('tender-multi__trigger--active', arr.length > 0);
  }

  // 行业下拉触发（嵌在筛选栏里，2026-08-04）
  function renderCatDropdownTrigger() {
    return `
      <div class="tender-multi tender-multi--cat" data-multi="cat">
        <button class="tender-multi__trigger${(_activeCat && _activeCat.length) ? ' tender-multi__trigger--active' : ''}" data-multi-trigger="cat" type="button" aria-haspopup="listbox" aria-expanded="false">
          <span class="tender-multi__label" id="tender-cat-label">全部行业</span>
          <span class="tender-multi__caret">▾</span>
        </button>
        <div class="tender-multi__menu">
          <div class="tender-multi__list" id="tender-cat-menu"></div>
        </div>
      </div>
    `;
  }

  // 绑定多选下拉：trigger 开关 + 菜单项点击 + 全选/清空 + 点击外部关闭
  function bindMultiDropdown(type) {
    const rootEl = document.querySelector(`[data-multi="${type}"]`);
    if (!rootEl) return;
    const trigger = rootEl.querySelector('.tender-multi__trigger');
    if (!trigger) return;

    trigger.addEventListener('click', (e) => {
      e.stopPropagation();
      // 关闭其他下拉
      document.querySelectorAll('.tender-multi--open').forEach(el => {
        if (el !== rootEl) {
          el.classList.remove('tender-multi--open');
          const otherTrigger = el.querySelector('.tender-multi__trigger');
          if (otherTrigger) otherTrigger.setAttribute('aria-expanded', 'false');
        }
      });
      rootEl.classList.toggle('tender-multi--open');
      // 2026-08-20：同步 aria-expanded 与视觉一致（屏幕阅读器/键盘可达）
      trigger.setAttribute('aria-expanded', rootEl.classList.contains('tender-multi--open'));
    });

    trigger.addEventListener('keydown', (e) => {
      if (!['ArrowDown', 'Enter', ' '].includes(e.key) || rootEl.classList.contains('tender-multi--open')) return;
      e.preventDefault();
      trigger.click();
      requestAnimationFrame(() => {
        const firstItem = document.querySelector('.tender-multi__item[data-type="' + type + '"]');
        if (firstItem) firstItem.focus({ preventScroll: true });
      });
    });

    // 2026-08-23：头部「全选」按钮已移除（改为列表顶部「全部监控网页」项，与单选统一）
    // 清空 = 取消全部平台（2026-08-04 注释；2026-08-23 修复实现）
    // 原实现 _sites = Object.keys(_sitesData) 恰好等于全选全部站点（语义反了）。
    // 改为哨兵值 ['__none__']：filterBySites 匹配不到任何站点 → 列表空。
    const clearBtn = rootEl.querySelector('[data-multi-clear]');
    if (clearBtn) {
      clearBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        if (type === 'sites') _sites = ['__none__'];
        _currentPage = 1;
        renderMultiChecks(rootEl, type);
        refreshContent();
      });
    }
    // 平台项点击（多选 toggle；2026-08-23：__all__ = 全部监控网页，点它=全选）
    rootEl.querySelectorAll('.tender-multi__item').forEach(item => {
      item.addEventListener('click', (e) => {
        e.stopPropagation();
        const key = item.dataset.key;
        if (key === '__all__') {
          // 「全部监控网页」= 主开关：当前已是全部 → 取消全选（未选任何网页）；
          // 否则（未选/部分）→ 恢复全部。2026-08-24 按老板直觉修正。
          _sites = (_sites.length === 0) ? ['__none__'] : [];
        } else {
          // 从“未选（__none__）”点单项 → 以该项开始选择
          if (_sites.length === 1 && _sites[0] === '__none__') _sites = [];
          // 从“全部”点单项 → 切到只选该项（选一个就是一个），
          // 之后每点一项累加（选两个就是两个）。2026-08-24 修正反选。
          const idx = _sites.indexOf(key);
          if (idx >= 0) _sites.splice(idx, 1); else _sites.push(key);
          // 取消最后一个具体选项后保持“未选”，不能意外恢复为“全部”。
          if (_sites.length === 0) _sites = ['__none__'];
          // 手动选齐所有网页时自动归一为“全部”，避免冗余状态。
          const totalSites = document.querySelectorAll('.tender-multi__item[data-type="' + type + '"]:not([data-key="__all__"])').length;
          if (totalSites > 0 && _sites.length === totalSites) _sites = [];
        }
        // 2026-08-17 修复：不 render(_data)（会重建筛选栏导致菜单缩回），
        // 改为手动更新 ✓ 标记 + 触发器文字 + refreshContent（只刷列表区）
        _currentPage = 1;
        renderMultiChecks(rootEl, type);
        refreshContent();
      });
    });
    const menu = rootEl.querySelector('.tender-multi__menu');
    if (menu) {
      menu.addEventListener('keydown', (e) => {
        if (!['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(e.key)) return;
        const items = Array.from(document.querySelectorAll('.tender-multi__item[data-type="' + type + '"]'));
        if (!items.length) return;
        e.preventDefault();
        const current = items.indexOf(document.activeElement);
        let next = current;
        if (e.key === 'Home') next = 0;
        else if (e.key === 'End') next = items.length - 1;
        else if (e.key === 'ArrowDown') next = current < 0 ? 0 : (current + 1) % items.length;
        else next = current < 0 ? items.length - 1 : (current - 1 + items.length) % items.length;
        items[next].focus({ preventScroll: true });
      });
    }
  }

  // 2026-08-23：统一刷新多选下拉的三处视觉状态（勾选标记 / 全选高亮 / 触发器 label）
  // 全选/清空/单项点击都走这里，保证反馈一致：
  //   - 勾选标记：菜单里每个 item 的 ✓ 与 --on
  //   - 全选高亮：_sites 为空（=全部）时全选按钮加粗
  //   - 触发器 label：全部 / 单站点名 / 已选 N 项 / 未选任何网页
  function renderMultiChecks(rootEl, type) {
    const sel = type === 'sites' ? _sites : [];
    // 全选（空数组）时勾选所有 item；清空（哨兵 __none__）时全不勾。
    // 2026-08-23：视觉反馈与数据语义解耦——数据层 _sites=[] 表示“全部”，
    // 但菜单里应显示 23 项全勾，避免“点了全选没反馈”。
    const isAll = sel.length === 0;
    const isNone = sel.length === 1 && sel[0] === '__none__';
    // 2026-08-23：菜单打开时在 body 下（8-21 fixed-pos），容器内查不到 item → 全局查
    document.querySelectorAll('.tender-multi__item[data-type="' + type + '"]').forEach(item => {
      const on = isAll ? true : (isNone ? false : sel.includes(item.dataset.key));
      item.classList.toggle('tender-multi__item--on', on);
      item.setAttribute('aria-selected', String(on));
      const check = item.querySelector('.tender-multi__check');
      if (check) check.textContent = on ? '✓' : '';
    });
    // 2. 全选按钮高亮（空数组 = 全部选中）—— 全选按钮在 menu 头部，同样可能在 body 下
    const allEl = document.querySelector('[data-multi-all="' + type + '"]');
    if (allEl) allEl.classList.toggle('tender-multi__all--on', isAll);
    // 3. 触发器 label
    const trigger = rootEl.querySelector('.tender-multi__trigger');
    updateMultiTrigger(rootEl, type, trigger);
  }

  // 2026-08-17：手动更新多选下拉触发器文字（不重建 DOM）
  function updateMultiTrigger(rootEl, type, trigger) {
    const sel = type === 'sites' ? _sites : [];
    const labelEl = trigger.querySelector('.tender-multi__label');
    if (!labelEl) return;
    // 2026-08-23：哨兵值 __none__ = 清空（未选任何网页）
    if (sel.length === 1 && sel[0] === '__none__') {
      labelEl.textContent = '未选任何网页';
    } else if (sel.length === 0) {
      labelEl.textContent = '全部' + (type === 'sites' ? '监控网页' : '');
    } else if (sel.length === 1) {
      // 2026-08-23：菜单打开时在 body 下 → 全局查 item 取中文 label
      const opts = document.querySelectorAll('.tender-multi__item[data-type="' + type + '"]');
      const found = Array.from(opts).find(o => o.dataset.key === sel[0]);
      labelEl.textContent = found ? (found.querySelector('.tender-multi__label') || {}).textContent || sel[0] : sel[0];
    } else {
      labelEl.textContent = `已选 ${sel.length} 项`;
    }
    // 2026-08-23 灵动：同步触发器“已筛选”状态（清空哨兵不算筛选）
    const hasSel = sel.length > 0 && !(sel.length === 1 && sel[0] === '__none__');
    trigger.classList.toggle('tender-multi__trigger--active', hasSel);
    trigger.classList.toggle('tender-multi__trigger--empty', sel.length === 1 && sel[0] === '__none__');
  }

  // 点击外部关闭多选下拉（只绑一次，2026-08-04）
  let _multiDocBound = false;
  function bindMultiDocClose() {
    if (_multiDocBound) return;
    _multiDocBound = true;
    document.addEventListener('click', (e) => {
      if (!e.target.closest('.tender-multi') && !e.target.closest('.tender-multi__menu[data-fixed-pos]')) {
        document.querySelectorAll('.tender-multi--open').forEach(el => {
          el.classList.remove('tender-multi--open');
          const trigger = el.querySelector('.tender-multi__trigger');
          if (trigger) trigger.setAttribute('aria-expanded', 'false');
        });
      }
    });
    document.addEventListener('keydown', (e) => {
      if (e.key !== 'Escape') return;
      document.querySelectorAll('.tender-multi--open').forEach(el => {
        el.classList.remove('tender-multi--open');
        const trigger = el.querySelector('.tender-multi__trigger');
        if (trigger) {
          trigger.setAttribute('aria-expanded', 'false');
          trigger.focus({ preventScroll: true });
        }
      });
    });
  }

  // —— 区县平铺栏（2026-08-06：原下拉菜单改为平铺一排展示）——
  function renderDropdownHtml() {
    const items = DISTRICTS.map(d => {
      // 区县计数基于全量数据（_allItems），不随当前选中区县变化（2026-08-07 修复）
      const cnt = (_allItems || []).filter(i => d.match(i.std_district || '', i)).length;
      return `<button class="tender-districts__btn ${d.key === _activeDistrict ? 'tender-districts__btn--active' : ''}" data-district="${d.key}" type="button">${d.label}<span class="tender-districts__count">${cnt}</span></button>`;
    }).join('');
    return `<div class="tender-districts" id="tender-districts">${items}</div>`;
  }

  // —— 行业下拉菜单（2026-08-04）——
  // 排序：全部在最前，其余按数量降序，未分类强制最后
  function renderCatDropdown(items) {
    const label = document.getElementById('tender-cat-label');
    const menu = document.getElementById('tender-cat-menu');
    if (!label || !menu) return;

    // 统计行业分布
    const counts = {};
    for (const it of (items || [])) {
      const c = it.proj_major_cat || '未分类';
      counts[c] = (counts[c] || 0) + 1;
    }

    // 排序：全部 → 数量降序 → 未分类最后
    const cats = Object.keys(counts).sort((a, b) => {
      if (a === '未分类') return 1;
      if (b === '未分类') return -1;
      return (counts[b] || 0) - (counts[a] || 0);
    });
    const total = (items || []).length;

    // 标题：当前选中行业（多选 2026-08-17）
    label.textContent = _activeCat.length === 0 ? '全部行业'
      : _activeCat.length === 1 ? _activeCat[0]
      : `已选 ${_activeCat.length} 项`;

    // 菜单项：全部 + 各行业（排序后），带 ✓ 标记（行业为多选，2026-08-17）
    const allItem = `<button class="tender-multi__item ${_activeCat.length === 0 ? 'tender-multi__item--on' : ''}" data-cat="all">
      <span class="tender-multi__check">${_activeCat.length === 0 ? '✓' : ''}</span>
      <span class="tender-multi__label">全部行业</span>
    </button>`;
    const catItems = cats.map(c => {
      // 2026-08-24：全部行业状态下所有行业也显示勾选（与「全部监控网页」一致）
      const on = _activeCat.length === 0 || _activeCat.includes(c);
      return `
      <button class="tender-multi__item ${on ? 'tender-multi__item--on' : ''}" data-cat="${c}">
        <span class="tender-multi__check">${on ? '✓' : ''}</span>
        <span class="tender-multi__label">${c}</span>
      </button>`;
    }).join('');
    menu.innerHTML = allItem + catItems;

    // 绑定菜单项点击（多选 toggle，2026-08-17）
    menu.querySelectorAll('.tender-multi__item').forEach(item => {
      item.addEventListener('click', (e) => {
        e.stopPropagation();
        const key = item.dataset.cat;
        if (key === 'all') {
          _activeCat = [];  // 全部 = 清空
        } else {
          const idx = _activeCat.indexOf(key);
          if (idx >= 0) _activeCat.splice(idx, 1); else _activeCat.push(key);
        }
        _currentPage = 1;
        // 手动更新 ✓ 标记 + 触发器文字 + refreshContent（菜单保持打开，2026-08-17）
        menu.querySelectorAll('.tender-multi__item').forEach(it => {
          const on = it.dataset.cat === 'all'
            ? _activeCat.length === 0
            : (_activeCat.length === 0 || _activeCat.includes(it.dataset.cat));
          it.classList.toggle('tender-multi__item--on', on);
          const check = it.querySelector('.tender-multi__check');
          if (check) check.textContent = on ? '✓' : '';
        });
        label.textContent = _activeCat.length === 0 ? '全部行业'
          : _activeCat.length === 1 ? _activeCat[0]
          : `已选 ${_activeCat.length} 项`;
        // 2026-08-23 灵动：同步行业触发器“已筛选”状态
        const ddEl = document.querySelector('[data-multi="cat"]');
        const catTrigger = ddEl ? ddEl.querySelector('.tender-multi__trigger') : null;
        if (catTrigger) catTrigger.classList.toggle('tender-multi__trigger--active', _activeCat.length > 0);
        refreshContent();
      });
    });
  }

  // —— 行业下拉展开/收起（2026-08-04 新 trigger 结构）——
  function toggleCatDropdown() {
    const dd = document.querySelector('[data-multi="cat"]');
    if (!dd) return;
    dd.classList.toggle('tender-multi--open');
    // 2026-08-20：同步 aria-expanded
    const t = dd.querySelector('[data-multi-trigger="cat"]');
    if (t) t.setAttribute('aria-expanded', dd.classList.contains('tender-multi--open'));
  }

  function closeCatDropdown() {
    const dd = document.querySelector('[data-multi="cat"]');
    if (dd) {
      dd.classList.remove('tender-multi--open');
      const t = dd.querySelector('[data-multi-trigger="cat"]');
      if (t) t.setAttribute('aria-expanded', 'false');
    }
  }

  // —— 绑定行业下拉触发（2026-08-04 适配多选结构）——
  // trigger 在 tender-body 内，每次 render 重建，须每次重新绑定；
  // document 级关闭监听只绑一次（事件委托，不受重建影响）
  let _catDocBound = false;
  let _catTriggerBound = new WeakSet();  // 防重复绑定（2026-08-04）
  function bindCatDropdown() {
    if (!_catDocBound) {
      _catDocBound = true;
      document.addEventListener('click', (e) => {
        if (!e.target.closest('.tender-multi--cat')) closeCatDropdown();
      });
    }
    const dd = document.querySelector('[data-multi="cat"]');
    if (!dd) return;
    const trigger = dd.querySelector('[data-multi-trigger="cat"]');
    if (!trigger || _catTriggerBound.has(trigger)) return;
    _catTriggerBound.add(trigger);
    trigger.addEventListener('click', (e) => {
      e.stopPropagation();
      toggleCatDropdown();
    });
  }

  // —— 区县人工修订弹窗（2026-08-16）——
  function renderDistrictModal(projectId) {
    const item = (_allItems || []).find(i => i.id === projectId);
    if (!item) return null;
    const current = item.district_raw || '';
    const options = EDITABLE_DISTRICTS.map(d => {
      const on = d.vals.includes(current);
      return `
        <button class="tender-district-opt ${on ? 'tender-district-opt--on' : ''}" data-district-val="${d.save}" type="button">
          <span class="tender-district-opt__check">${on ? '✓' : ''}</span>
          ${d.label}
        </button>`;
    }).join('');
    const clearBtn = current
      ? `<button class="tender-district-opt tender-district-opt--clear" data-district-val="" type="button"><span class="tender-district-opt__check"></span>不标注（恢复自动）</button>`
      : '';
    const modal = document.createElement('div');
    modal.className = 'tender-district-modal';
    modal.innerHTML = `
      <div class="tender-district-modal__card" role="dialog" aria-modal="true" aria-label="修改区县">
        <button type="button" class="tender-district-modal__close" aria-label="关闭">✕</button>
        <div class="tender-district-modal__title">修改区县归属</div>
        <div class="tender-district-modal__sub">${escapeHtml(item.title)}</div>
        <div class="tender-district-modal__opts">${options}${clearBtn}</div>
        <div class="tender-district-modal__hint">修改后立即生效：筛选对应区县即可看到该项目。</div>
      </div>`;
    return modal;
  }

  async function saveDistrictOverride(projectId, district) {
    try {
      const res = await fetch('/api/tender/district', {
        method: 'PUT',
        headers: apiHeaders({ 'Content-Type': 'application/json' }),
        body: JSON.stringify({ project_id: projectId, district: district }),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        alert('保存失败：' + (err.error || res.status));
        return false;
      }
      const data = await res.json();
      _districtOverrides = Object.assign({}, _districtOverrides);
      if (data.district === '') {
        delete _districtOverrides[projectId];
      } else {
        _districtOverrides[projectId] = data.district;
      }
      return true;
    } catch (e) {
      console.error('区县修订保存失败:', e);
      alert('保存失败：网络异常');
      return false;
    }
  }

  // —— 轻量提示（2026-08-16：保存区县修订后的反馈）——
  let _toastTimer = null;
  function showToast(msg, isErr) {
    let el = document.getElementById('tender-toast');
    if (!el) {
      el = document.createElement('div');
      el.id = 'tender-toast';
      el.className = 'tender-toast';
      document.body.appendChild(el);
    }
    el.textContent = msg;
    el.classList.toggle('tender-toast--error', !!isErr);
    el.classList.add('tender-toast--show');
    clearTimeout(_toastTimer);
    _toastTimer = setTimeout(() => el.classList.remove('tender-toast--show'), 2600);
  }

  function closeDistrictModal() {
    const m = document.getElementById('tender-district-modal');
    if (m) m.remove();
  }

  // 关闭项目状态弹窗（2026-08-16 复查：与区县弹窗互斥）
  function closeStageModal() {
    const m = document.getElementById('tender-stage-modal');
    if (m) m.remove();
  }

  // —— 绑定区县 ✎ 编辑（2026-08-16，事件委托已并入 bindDropdown；本函数保留空壳以防遗漏）——
  function openDistrictModal(pid) {
    closeDistrictModal();
    closeStageModal();
    const modal = renderDistrictModal(pid);
    if (!modal) return;
    modal.id = 'tender-district-modal';
    modal._pid = pid;
    document.body.appendChild(modal);
    modal.querySelector('.tender-district-modal__close')?.focus();
    // 关闭：✕ / 点遮罩 / Esc（2026-08-16 复查修复）
    const closeHandler = () => closeDistrictModal();
    modal.querySelector('.tender-district-modal__close').addEventListener('click', closeHandler);
    modal.addEventListener('click', (e) => {
      if (e.target === modal) closeHandler();
    });
    const onKey = (e) => {
      if (e.key === 'Escape') {
        closeHandler();
        document.removeEventListener('keydown', onKey);
      }
    };
    document.addEventListener('keydown', onKey);
    // 选项点击 → 保存
    modal.querySelectorAll('[data-district-val]').forEach(btn => {
      btn.addEventListener('click', async () => {
        const val = btn.dataset.districtVal;
        const ok = await saveDistrictOverride(pid, val);
        if (ok) {
          closeDistrictModal();
          const item = (_allItems || []).find(i => i.id === pid);
          const newLabel = val === '' ? '不标注' : districtLabel(val);
          // 当前区县筛选下项目移出视图时，提示用户（2026-08-16）
          const d = DISTRICTS.find(x => x.key === _activeDistrict);
          const movedOut = d && d.key !== 'all' && item && !d.match(val);
          if (movedOut) {
            showToast(`已改为${newLabel}，当前「${d.label}」筛选下不再显示，可在${d.label}以外查看`);
          } else {
            showToast(`已改为${newLabel}`);
          }
          render(_data);  // 重新渲染（筛选、计数、区县列全部刷新）
        }
      });
    });
  }

  // —— 项目状态提示弹窗（2026-08-16：已下架/终止/流标类硬信号提示）——
  function showStageTip(pid) {
    const item = (_allItems || []).find(i => i.id === pid);
    if (!item) return;
    const stageText = PROJECT_STAGE_MAP[item.project_stage] || item.project_stage || '未知状态';
    const stageReason = item.stage_reason || '';
    const map = {
      'failed': '该项目数据源已发布流标/废标公告，采购未成交。数据保留供追溯。',
      'terminated': '该项目数据源已发布终止公告，项目已终止。数据保留供追溯。',
      'suspended': '该项目数据源已发布暂停公告，项目暂停中。数据保留供追溯。',
      'correction': '该项目数据源发布过更正/澄清公告，项目仍在进行。',
      'contract': '该项目数据源已发布合同公告，项目已进入合同阶段。',
    };
    const tipText = map[item.project_stage] || '该项目当前状态：' + stageText + '。';
    const lastSeenLine = item.last_seen
      ? `<br>数据源最后见到：${escapeHtml(item.last_seen)}` : '';
    closeDistrictModal();
    closeStageModal();
    const modal = document.createElement('div');
    modal.className = 'tender-district-modal';
    modal.innerHTML = `
      <div class="tender-district-modal__card" role="dialog" aria-modal="true" aria-label="项目状态">
        <button type="button" class="tender-district-modal__close" aria-label="关闭">✕</button>
        <div class="tender-district-modal__title">项目状态：${escapeHtml(stageText)}</div>
        <div class="tender-district-modal__sub">${escapeHtml(item.title)}</div>
        <div class="tender-district-modal__hint">${escapeHtml(tipText)}${stageReason ? '<br>原因：' + escapeHtml(stageReason) : ''}${lastSeenLine}</div>
      </div>`;
    modal.id = 'tender-stage-modal';
    document.body.appendChild(modal);
    const close = () => modal.remove();
    modal.querySelector('.tender-district-modal__close').addEventListener('click', close);
    modal.addEventListener('click', (e) => { if (e.target === modal) close(); });
  }

  // —— 绑定区县按钮点击（2026-08-06：原下拉委托改为平铺按钮直接委托）——
  let _dropdownBound = false;

  // 2026-08-23 重构：fixed-pos 机制抽成顶层函数（原为 bindDropdown 内闭包）。
  // 原实现两个问题导致菜单关不掉（见下方注释），本次一并修复。
  let _menuMO = null;

  // 同步 dropdown menu 的 fixed 定位（2026-08-21 引入）：
  // 任意 .tender-multi 打开时，把 menu 临时 append 到 body 下 + fixed 定位
  // （脱离 row 的 overflow-x:auto + mask-image 限制，避免被裁切）；关闭时还原。
  // 2026-08-23 修复：关闭分支不再用后代选择器（menu 移到 body 后不再是
  // .tender-multi 的后代，旧选择器匹配不到 → 菜单永远残留），改为直接查所有
  // [data-fixed-pos] 的 menu，按 owner 是否仍 open 决定保留/移回/移除。
  function syncMenuFixedPos() {
    // 打开中的菜单：移到 body + fixed 定位
    document.querySelectorAll('.tender-multi--open').forEach(root => {
      const trigger = root.querySelector('.tender-multi__trigger');
      const menu = root.querySelector('.tender-multi__menu');
      if (!trigger || !menu) return;
      // 2026-08-23：每次打开都重新绑定 owner（close 分支不再 delete _menuOwner，
      // 否则“关闭后再打开”时 _menuOwner 丢失 → close 分支把 menu 当无主直接 remove）
      menu._menuOwner = root;
      if (menu.parentElement !== document.body) document.body.appendChild(menu);
      const tRect = trigger.getBoundingClientRect();
      const vh = window.innerHeight, vw = window.innerWidth;
      // 先设 display 让 menu 有真实尺寸
      if (menu.style.display !== 'flex') menu.style.display = 'flex';
      const menuW = menu.offsetWidth || 180;
      const menuH = menu.offsetHeight || 100;
      let top = tRect.bottom + 6;
      if (top + menuH > vh - 8) top = Math.max(8, tRect.top - menuH - 6);
      let left = tRect.left;
      if (left + menuW > vw - 8) left = vw - menuW - 8;
      if (left < 8) left = 8;
      menu.style.position = 'fixed';
      menu.style.top = top + 'px';
      menu.style.left = left + 'px';
      menu.setAttribute('data-fixed-pos', '1');
    });
    // 已关闭的菜单：还原（直接查 [data-fixed-pos]，不限位置）
    document.querySelectorAll('.tender-multi__menu[data-fixed-pos]').forEach(menu => {
      const owner = menu._menuOwner;
      const ownerOpen = owner && document.contains(owner) && owner.classList.contains('tender-multi--open');
      if (ownerOpen) return;  // 仍打开：保留（open 分支负责定位）
      // 关闭：移回 owner；owner 已脱离 DOM（render 重建）→ 直接移除僵尸菜单
      if (owner && document.contains(owner)) {
        if (menu.parentElement !== owner) owner.appendChild(menu);
      } else {
        menu.remove();
      }
      menu.style.position = '';
      menu.style.top = '';
      menu.style.left = '';
      menu.style.display = '';
      menu.removeAttribute('data-fixed-pos');
      // 2026-08-23：不再 delete _menuOwner —— 删掉后“再打开”时 close 分支会
      // 把刚移到 body 的 menu 误判为无主而 remove（菜单从 DOM 消失）。
      // 绑定关系由 open 分支每次重设；render 重建后旧 menu 会被整体移除。
    });
  }

  // 注册/刷新 fixed-pos 观察（幂等；每次 render 重建筛选栏后必须重调）。
  // 2026-08-23：MutationObserver 只观察绑定时的元素，render 重建后的新
  // .tender-multi 不在观察列表 → 关闭时不会触发 syncMenuFixedPos → 菜单残留。
  // 故每次 render 后重新 observe 当前全部元素 + 重设 _menuOwner。
  function bindMenuFixedObservers() {
    if (typeof MutationObserver === 'undefined') return;
    if (!_menuMO) {
      _menuMO = new MutationObserver(syncMenuFixedPos);
      let raf = 0;
      const onScroll = () => {
        if (raf) return;
        raf = requestAnimationFrame(() => { raf = 0; syncMenuFixedPos(); });
      };
      window.addEventListener('scroll', onScroll, { passive: true, capture: true });
      window.addEventListener('resize', onScroll);
    }
    document.querySelectorAll('.tender-multi').forEach(root => {
      const menu = root.querySelector('.tender-multi__menu');
      if (menu) menu._menuOwner = root;
      _menuMO.observe(root, { attributes: true, attributeFilter: ['class'] });
    });
  }

  function bindDropdown() {
    if (_dropdownBound) return;
    _dropdownBound = true;
    // 2026-08-21：修复平板/手机 dropdown menu 被父级 overflow-x:auto + mask 裁切
    // 任意 .tender-multi 打开时，同步 menu 用 fixed 定位（脱离 row overflow/mask 限制）；
    // 关闭时复原。详见 CSS .tender-multi--open > .tender-multi__menu { position: fixed !important; }
    bindMenuFixedObservers();
    document.addEventListener('click', (e) => {
      // 已下架/终止状态徽章点击 → 状态提示（2026-08-16）
      const tipBtn = e.target.closest('[data-stage-tip]');
      if (tipBtn) {
        e.stopPropagation();
        const row = tipBtn.closest('tr') || tipBtn.closest('.tender-card');
        const pid = row ? (row.querySelector('[data-district-edit]')?.dataset.districtEdit ||
                           row.querySelector('[data-card-district-edit]')?.dataset.cardDistrictEdit) : '';
        if (pid) showStageTip(pid);
        return;
      }
      const editBtn = e.target.closest('[data-district-edit]');
      if (editBtn) {
        e.stopPropagation();
        openDistrictModal(editBtn.dataset.districtEdit);
        return;
      }
      // 移动端卡片区县点击（2026-08-16）
      const cardEdit = e.target.closest('[data-card-district-edit]');
      if (cardEdit) {
        e.stopPropagation();
        openDistrictModal(cardEdit.dataset.cardDistrictEdit);
        return;
      }
      const btn = e.target.closest('.tender-districts__btn');
      if (!btn) return;
      const key = btn.dataset.district;
      if (key === _activeDistrict) return;
      _activeDistrict = key;
      _activeStage = 'all';
      _sites = [];
      _activeCat = [];
      _activeAmount = [];
      _activeSme = [];
      _activeIct = [];
      _activeType = [];
      _activeDate = null;
      _activeOpenDate = null;
      _activeSearch = '';
      _currentPage = 1;
      render(_data);
    });
  }

  // —— 关联项目展示区（Phase 4）——

  // —— 日期筛选弹层（2026-08-08）——
  function renderDatePopover() {
    if (!_activeDate) _activeDate = null;
    const today = new Date();
    const todayStr = formatYMD(today);
    const target = _activeDateKind === 'open' ? _activeOpenDate : _activeDate;
    const fromDefault = target?.from || todayStr;
    const toDefault = target?.to || todayStr;

    // 预设区间（5 个） + 命中数（基于 _districtItems）
    const presets = [
      { key: 'today', label: '今天', days: 0 },
      { key: '7d', label: '最近 7 天', days: 7 },
      { key: '30d', label: '最近 30 天', days: 30 },
      { key: 'month', label: '本月', days: -1 },
      { key: '3m', label: '最近 3 个月', days: 90 },
    ];
    const presetHtml = presets.map(p => {
      let cnt;
      if (p.days === -1) {
        // 本月
        const y = today.getFullYear(), m = today.getMonth();
        cnt = (_districtItems || []).filter(i => {
          if (!i.date) return false;
          const d = new Date(i.date);
          return d.getFullYear() === y && d.getMonth() === m;
        }).length;
      } else if (p.days === 0) {
        cnt = (_districtItems || []).filter(i => i.date === todayStr).length;
      } else {
        const from = new Date(today); from.setDate(from.getDate() - p.days + 1);
        const fromStr = formatYMD(from);
        cnt = (_districtItems || []).filter(i => i.date >= fromStr && i.date <= todayStr).length;
      }
      return `<button class="tender-date-pop__preset" data-date-preset="${p.key}" type="button">
        <span>${p.label}</span><span class="tender-date-pop__preset-count">${cnt}</span>
      </button>`;
    }).join('');

    return `
      <div class="tender-date-pop" id="tender-date-pop">
        <div class="tender-date-pop__section">
          <div class="tender-date-pop__title">⚡ 快速区间</div>
          <div class="tender-date-pop__presets">${presetHtml}</div>
        </div>
        <div class="tender-date-pop__divider"></div>
        <div class="tender-date-pop__section">
          <div class="tender-date-pop__title">🗓 自定义区间</div>
          <div class="tender-date-pop__custom">
            <label>从</label><input type="date" id="tender-date-from" value="${fromDefault}" max="${todayStr}">
            <label>到</label><input type="date" id="tender-date-to" value="${toDefault}" max="${todayStr}">
          </div>
        </div>
        <div class="tender-date-pop__actions">
          <button class="tender-date-pop__btn" data-date-clear type="button">清空</button>
          <button class="tender-date-pop__btn tender-date-pop__btn--primary" data-date-apply type="button">应用筛选</button>
        </div>
      </div>
    `;
  }

  function formatYMD(d) {
    const y = d.getFullYear();
    const m = String(d.getMonth() + 1).padStart(2, '0');
    const day = String(d.getDate()).padStart(2, '0');
    return `${y}-${m}-${day}`;
  }

  function bindDateTrigger(triggerEl, containerEl, openClass, kind) {
    if (!triggerEl || !containerEl) return;
    const closePop = () => {
      containerEl.classList.remove(openClass);
      const pop = document.getElementById('tender-date-pop');
      if (pop) pop.remove();
    };

    triggerEl.addEventListener('click', (e) => {
      e.stopPropagation();
      const isOpen = containerEl.classList.contains(openClass);
      // 关闭其他弹层
      document.querySelectorAll('.tender-multi--open').forEach(el => el.classList.remove('tender-multi--open'));
      closePop();
      if (isOpen) return;
      _activeDateKind = kind;  // 记住当前弹层在编辑哪种日期
      containerEl.classList.add(openClass);
      containerEl.insertAdjacentHTML('beforeend', renderDatePopover());
      wireDatePopEvents(closePop);
    });
  }

  function bindDateFilter() {
    // 发布日期：桌面表头
    const pubDesktopTrigger = document.getElementById('tender-date-trigger');
    const pubDesktopContainer = pubDesktopTrigger ? pubDesktopTrigger.closest('th') : null;
    if (pubDesktopContainer) bindDateTrigger(pubDesktopTrigger, pubDesktopContainer, 'tender-th--open', 'publish');

    // 开标时间：桌面表头
    const openDesktopTrigger = document.getElementById('tender-open-trigger');
    const openDesktopContainer = openDesktopTrigger ? openDesktopTrigger.closest('th') : null;
    if (openDesktopContainer) bindDateTrigger(openDesktopTrigger, openDesktopContainer, 'tender-th--open', 'open');

    // 首次绑定：点击外部关闭（幂等）
    if (!bindDateFilter._docBound) {
      bindDateFilter._docBound = true;
      document.addEventListener('click', (e) => {
        if (!e.target.closest('.tender-date-pop') &&
            !e.target.closest('#tender-date-trigger') &&
            !e.target.closest('#tender-open-trigger')) {
          const pop = document.getElementById('tender-date-pop');
          if (pop) pop.remove();
          document.querySelectorAll('.tender-th--open').forEach(el => el.classList.remove('tender-th--open'));
        }
      });
    }
  }

  function wireDatePopEvents(closePop) {
    const today = new Date();
    const todayStr = formatYMD(today);

    // 根据 _activeDateKind 选择操作的 state / 默认 from
    const target = _activeDateKind === 'open' ? _activeOpenDate : _activeDate;
    const setTarget = (v) => {
      if (_activeDateKind === 'open') _activeOpenDate = v;
      else _activeDate = v;
    };
    const defaultFrom = target ? target.from : todayStr;
    const defaultTo = target ? target.to : todayStr;

    const presets = document.querySelectorAll('[data-date-preset]');
    presets.forEach(btn => {
      btn.addEventListener('click', (e) => {
        e.stopPropagation();
        const key = btn.dataset.datePreset;
        let from, to;
        if (key === 'today') {
          from = to = todayStr;
        } else if (key === 'month') {
          const y = today.getFullYear(), m = today.getMonth();
          const first = new Date(y, m, 1);
          from = formatYMD(first);
          to = todayStr;
        } else {
          const days = key === '7d' ? 7 : key === '30d' ? 30 : 90;
          const f = new Date(today); f.setDate(f.getDate() - days + 1);
          from = formatYMD(f);
          to = todayStr;
        }
        setTarget({ from, to });
        _currentPage = 1;
        render(_data);
      });
    });

    const applyBtn = document.querySelector('[data-date-apply]');
    if (applyBtn) {
      applyBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        let from = document.getElementById('tender-date-from').value;
        let to = document.getElementById('tender-date-to').value;
        if (!from && !to) return;
        // 开标时间筛选时，「未设 from」默认用今天（开标是未来事件，不像发布那样需要取最早）
        if (!from) from = todayStr;
        if (!to) to = todayStr;
        if (from > to) [from, to] = [to, from];
        setTarget({ from, to });
        _currentPage = 1;
        render(_data);
      });
    }

    const clearBtn = document.querySelector('[data-date-clear]');
    if (clearBtn) {
      clearBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        setTarget(null);
        _currentPage = 1;
        render(_data);
      });
    }
  }

  // —— 渲染某分类的项目列表 ——
  function renderGroup(items, emptyReason) {
    if (!items || items.length === 0) {
      if (emptyReason === 'sites-none') {
        return `<div class="tender-empty tender-empty--filtered">
          <span class="tender-empty__icon" aria-hidden="true">⌁</span>
          <strong>未选任何监控网页</strong>
          <span>请至少选择一个网页，或在上方恢复“全部监控网页”</span>
        </div>`;
      }
      return emptyState(_activeStage === 'favorites' ? '暂无待投标项目，点击项目旁的 ☆ 加入待投标' : _activeStage === 'fav-intention' ? '暂无代挂网项目，在意向招标列表中点击 ☆ 收藏意向项目' : '暂无符合条件的项目');
    }

    if (_activeStage === 'awarded') return renderAwardGroup(items);

    // 排序：全部/意向招标按发布日期倒序；开标倒计时按开标时间升序；
    // 其他状态保持 ICT 优先 + 开标时间降序（2026-08-06；2026-08-12 意向招标改按日期）
    items = [...items].sort((a, b) => {
      if (_activeStage === 'favorites' || _activeStage === 'fav-intention') {
        // 收藏夹：保持收藏顺序（favoriteItems 已按收藏时间倒序，2026-08-06）
        return 0;
      }
      if (_activeStage === 'countdown') {
        // 开标倒计时：按开标时间升序（即将开标的在前，2026-08-07）
        const da = a.bid_deadline ? parseBidDeadline(a.bid_deadline) : new Date(8.64e15);
        const db = b.bid_deadline ? parseBidDeadline(b.bid_deadline) : new Date(8.64e15);
        return da - db;
      }
      if (_activeStage === 'all' || _activeStage === 'intention' || _activeStage === 'upcoming') {
        // 全部/意向招标/招标公告：发布日期（latest_notice_date）倒序，最新在前
        // 意向公告无开标时间，ICT 优先无意义，应按时间展示（2026-08-12）
        // 招标公告按发布时间排序（2026-08-13 老板要求）
        return String(b.date || '').localeCompare(String(a.date || ''));
      }
      if (_activeStage !== 'all') {
        if (a.isIct && !b.isIct) return -1;
        if (!a.isIct && b.isIct) return 1;
        const da = a.bid_deadline ? parseBidDeadline(a.bid_deadline) : null;
        const db = b.bid_deadline ? parseBidDeadline(b.bid_deadline) : null;
        // 都有开标时间：按开标时间降序（2026-08-06）
        if (da && db) return db - da;
        // 一个有开标时间、一个没有：有开标时间的在前（避免无时间被 Date(0) 挤到乱序区）
        if (da && !db) return -1;
        if (!da && db) return 1;
        // 都无开标时间：按发布日期倒序（2026-08-13 修复：与意向招标同款逻辑，
        // 避免无开标时间公告互相乱序）
        return String(b.date || '').localeCompare(String(a.date || ''));
      }
      // 兜底：发布日期倒序
      return String(b.date || '').localeCompare(String(a.date || ''));
    });

    // —— 分页计算 ——
    const total = items.length;
    const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));
    if (_currentPage > totalPages) _currentPage = totalPages;
    if (_currentPage < 1) _currentPage = 1;
    const start = (_currentPage - 1) * PAGE_SIZE;
    const pageItems = items.slice(start, start + PAGE_SIZE);

    // 2026-08-13：开标倒计时视图把「开标时间」前移、「发布日期」后置；
    // 招标公告视图保持发布时间在前、开标时间最后一列（2026-08-13 老板确认）
    const swapDateCols = _activeStage === 'countdown';
    const thPublish = `<th style="position: relative;"><button class="tender-th__filter ${_activeDate ? 'tender-th__filter--active' : ''}" id="tender-date-trigger" type="button">📅 发布日期 <span class="tender-th__caret">▾</span>${_activeDate ? '<span class="tender-th__dot" title="筛选生效中"></span>' : ''}</button></th>`;
    const thOpen = `<th style="position: relative;"><button class="tender-th__filter ${_activeOpenDate ? 'tender-th__filter--active' : ''}" id="tender-open-trigger" type="button">⏰ 开标时间 <span class="tender-th__caret">▾</span>${_activeOpenDate ? '<span class="tender-th__dot" title="筛选生效中"></span>' : ''}</button></th>`;
    // 2026-08-16：来源列前加「区县」列（人工可修订）
    const thDistrict = `<th class="tender-table__th-district">区县</th>`;
    const headCols = swapDateCols
      ? `<th style="min-width: 260px;">项目名称</th>${thDistrict}<th>来源</th>${thOpen}<th>发包人</th><th>金额</th>${thPublish}`
      : `<th style="min-width: 260px;">项目名称</th>${thDistrict}<th>来源</th>${thPublish}<th>发包人</th><th>金额</th>${thOpen}`;

    return `
      <div class="tender-table-wrap tender-table-wrap--desktop">
        <table class="tender-table">
          <thead>
            <tr>
              ${headCols}
            </tr>
          </thead>
          <tbody>
            ${pageItems.map(p => {
              const ictKeywords = p.keywords || [];
              const tdName = `<td class="tender-table__title">
                    ${renderFavBtn(normalizeProjectName(p.title), favBucketOf(p))}
                    ${p.is_new ? '<span class="tender-new-tag" title="本次抓取新增" aria-label="本次新增">NEW</span>' : ''}
                    ${p.isIct ? `<span class="tender-ict-tag" title="ICT 命中：${escapeHtml(ictKeywords.join('、'))}">● ICT</span>` : ''}
                    ${renderItemBadge(p)}
                    <a href="${p.url}" target="_blank" rel="noopener" title="${escapeHtml(p.title)}">${escapeHtml(p.title)}</a>
                  </td>`;
              const tdSite = `<td class="tender-table__site">${escapeHtml(p.site)}</td>`;
              // 2026-08-16：区县列（人工修订蓝色高亮 + ✎ 可改）
              const tdDistrict = `<td class="tender-table__district">
                <span class="tender-district ${p.district_overridden ? 'tender-district--edited' : ''}" title="${p.district_overridden ? '人工修订（点击修改）' : '自动识别（点击修改）'}">${escapeHtml(p.district_label)}</span>
                <button class="tender-district__edit" data-district-edit="${p.id}" type="button" title="修改区县">✎</button>
              </td>`;
              const tdPublish = `<td class="tender-table__date">${p.date || '—'}</td>`;
              const tdTenderer = `<td>${escapeHtml(p.tenderer || '—')}</td>`;
              // 2026-08-17：合同预估类金额不进入主列表金额列（老板确认剔除）
              const tdAmount = p.budget_type === 'contract_estimate'
                ? '<td class="tender-table__date"><span class="tender-time-pending" title="合同预估金额，未计入金额列">估算</span></td>'
                : `<td>${escapeHtml(p.amount || '—')}</td>`;
              const tdOpen = `<td class="tender-table__date">${p.bid_deadline ? escapeHtml(formatBidTime(p.bid_deadline)) : (_activeStage === 'upcoming' ? '<span class="tender-time-pending">时间待确认</span>' : '—')}</td>`;
              const bodyCols = swapDateCols
                ? `${tdName}${tdDistrict}${tdSite}${tdOpen}${tdTenderer}${tdAmount}${tdPublish}`
                : `${tdName}${tdDistrict}${tdSite}${tdPublish}${tdTenderer}${tdAmount}${tdOpen}`;
              return `
                <tr ${p.isIct ? 'style="border-left: 3px solid var(--color-accent);"' : ''}>
                  ${bodyCols}
                </tr>
              `;
            }).join('')}
          </tbody>
        </table>
      </div>
      ${renderCardList(pageItems)}
      ${renderPagination(total, totalPages)}
    `;
  }

  function formatMoney(value) {
    const n = Number(value);
    if (!Number.isFinite(n) || n <= 0) return '—';
    if (n >= 100000000) return `${(n / 100000000).toFixed(2)}亿元`;
    if (n >= 10000) return `${(n / 10000).toFixed(2)}万元`;
    return `${n.toLocaleString('zh-CN', { maximumFractionDigits: 2 })}元`;
  }

  function getAwardInfo(item) {
    if (item.isProject) {
      const confidence = item.link_confidence || 'none';
      // 2026-08-17：合同预估类金额不算折扣率/原预算（金额不可靠）
      const budget = item.budget_type === 'contract_estimate' ? 0 : Number(item.amount_value || 0);
      const winning = Number(item.winning_amount || 0);
      const canCalculate = ['manual', 'high', 'medium'].includes(confidence) && budget > 0 && winning > 0;
      return {
        link: item.cycle_days != null ? { cycle_days: item.cycle_days } : null,
        original: item.tender_url ? { url: item.tender_url } : null,
        winner: item.winner || '—', winning, budget, confidence,
        discount: canCalculate ? `${confidence === 'medium' ? '约 ' : ''}${(winning / budget * 100).toFixed(1)}%` : '—',
      };
    }
    const link = _awardLinkMap.get(normalizeProjectName(item.title));
    const original = link
      ? _allItems.find(i => (i.notice_type || 'tender') === 'tender' && normalizeProjectName(i.title) === normalizeProjectName(link.tender_name))
      : null;
    const budget = link ? Number(link.budget) : 0;
    const winning = Number(item.winning_amount || (link && link.winning_amount) || 0);
    return {
      link,
      original,
      winner: item.winner || (link && link.winner) || '—',
      winning,
      budget,
      confidence: link ? (link.match_confidence || 'none') : 'none',
      discount: link && ['manual', 'high', 'medium'].includes(link.match_confidence) && budget > 0 && winning > 0
        ? `${link.match_confidence === 'medium' ? '约 ' : ''}${(winning / budget * 100).toFixed(1)}%` : '—',
    };
  }

  function isCandidate(item) {
    // 候选公示：stage_reason 含"候选人"（2026-08-07 与历史中标合并后区分徽章）
    return (item.stage_reason || '').indexOf('候选人') >= 0;
  }

  function awardBadge(item) {
    return isCandidate(item)
      ? '<span class="tender-type-badge tender-type-badge--candidate">中标候选人</span>'
      : '<span class="tender-type-badge tender-type-badge--award">中标</span>';
  }

  function renderAwardGroup(items) {
    items = [...items].sort((a, b) => String(b.date || '').localeCompare(String(a.date || '')));
    const total = items.length;
    const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));
    if (_currentPage > totalPages) _currentPage = totalPages;
    const start = (_currentPage - 1) * PAGE_SIZE;
    const pageItems = items.slice(start, start + PAGE_SIZE);
    const rows = pageItems.map(p => {
      const a = getAwardInfo(p);
      return `<tr>
        <td class="tender-table__title">${renderFavBtn(normalizeProjectName(p.title), favBucketOf(p))}${awardBadge(p)}<a href="${p.url}" target="_blank" rel="noopener" title="${escapeHtml(p.title)}">${escapeHtml(p.title)}</a></td>
        <td>${escapeHtml(a.winner)}</td>
        <td>${formatMoney(a.winning)}</td>
        <td>${formatMoney(a.budget)}</td>
        <td>${a.discount}</td>
        <td>${renderConfidence(a.confidence)}</td>
        <td class="tender-table__date">${a.link && a.link.cycle_days != null ? `${a.link.cycle_days} 天` : '—'}</td>
        <td class="tender-table__date">${escapeHtml(p.date || '—')}</td>
        <td class="tender-table__site">${escapeHtml(p.site)}</td>
        <td>${a.original ? `<a href="${a.original.url}" target="_blank" rel="noopener">查看招标公告</a>` : '<span class="tender-table__muted">暂未匹配</span>'}</td>
      </tr>`;
    }).join('');
    const cards = pageItems.map(p => {
      const a = getAwardInfo(p);
      return `<div class="tender-card" data-open="0">
        <div class="tender-card__main">
          <div class="tender-card__title">${renderFavBtn(normalizeProjectName(p.title), favBucketOf(p))}${awardBadge(p)}<a href="${p.url}" target="_blank" rel="noopener" title="${escapeHtml(p.title)}">${escapeHtml(p.title)}</a></div>
          <div class="tender-card__sub">${escapeHtml(p.site)} · ${escapeHtml(p.date || '—')}</div>
          <div class="tender-card__meta"><span class="tender-card__meta-item"><span class="tender-card__meta-label">中标单位</span>${escapeHtml(a.winner)}</span><span class="tender-card__meta-item"><span class="tender-card__meta-label">中标金额</span>${formatMoney(a.winning)}</span></div>
        </div>
        <div class="tender-card__detail">
          <div class="tender-card__detail-row"><span class="tender-card__meta-label">原预算</span>${formatMoney(a.budget)}</div>
          <div class="tender-card__detail-row"><span class="tender-card__meta-label">折扣率</span>${a.discount}</div>
          <div class="tender-card__detail-row"><span class="tender-card__meta-label">关联可信度</span>${renderConfidence(a.confidence)}</div>
          <div class="tender-card__detail-row"><span class="tender-card__meta-label">采购周期</span>${a.link && a.link.cycle_days != null ? `${a.link.cycle_days} 天` : '—'}</div>
          ${a.original ? `<div class="tender-card__detail-row"><span class="tender-card__meta-label">原招标</span><a href="${a.original.url}" target="_blank" rel="noopener">查看招标公告 →</a></div>` : '<div class="tender-card__detail-row"><span class="tender-card__meta-label">原招标</span>暂未匹配</div>'}
        </div>
      </div>`;
    }).join('');
    return `<div class="tender-table-wrap tender-table-wrap--desktop"><table class="tender-table tender-award-table"><thead><tr><th style="min-width:260px">中标项目</th><th>中标单位</th><th>中标金额</th><th>原预算</th><th>折扣率</th><th>关联可信度</th><th>周期</th><th>公告日期</th><th>来源</th><th>原招标</th></tr></thead><tbody>${rows}</tbody></table></div><div class="tender-cardlist">${cards}</div>${renderPagination(total, totalPages)}`;
  }

  function renderConfidence(value) {
    const map = { manual: '人工确认', high: '高可信', medium: '可能关联', low: '待复核', standalone: '仅中标公告', none: '暂未匹配' };
    return `<span class="tender-confidence tender-confidence--${escapeHtml(value || 'none')}">${map[value] || '暂未匹配'}</span>`;
  }

  // —— 移动端卡片列表（≤768px 显示，2026-08-04 手机排版优化）——
  // 整行可点击展开详情；风格沿用现有 CSS 变量，不新增色系
  function renderCardList(items) {
    if (!items || items.length === 0) return '';
    return `
      <div class="tender-cardlist">
        ${items.map(p => {
          const ictKeywords = p.keywords || [];
          const badges = `
            ${p.is_new ? '<span class="tender-new-tag" title="本次抓取新增" aria-label="本次新增">NEW</span>' : ''}
            ${p.isIct ? `<span class="tender-ict-tag" title="ICT 命中：${escapeHtml(ictKeywords.join('、'))}">● ICT</span>` : ''}
            ${renderItemBadge(p)}
          `;
          const meta = [];
          // 2026-08-16：移动端卡片加区县（可编辑）
          meta.push(`<span class="tender-card__meta-item"><span class="tender-card__meta-label">区县</span><span class="tender-district ${p.district_overridden ? 'tender-district--edited' : ''}" data-card-district-edit="${p.id}" title="点击修改区县">${escapeHtml(p.district_label)} ✎</span></span>`);
          if (p.amount && p.budget_type !== 'contract_estimate') meta.push(`<span class="tender-card__meta-item"><span class="tender-card__meta-label">金额</span>${escapeHtml(p.amount)}</span>`);
          if (p.budget_type === 'contract_estimate') meta.push('<span class="tender-card__meta-item"><span class="tender-card__meta-label">金额</span><span class="tender-time-pending">估算</span></span>');
          if (p.bid_deadline) meta.push(`<span class="tender-card__meta-item"><span class="tender-card__meta-label">开标</span>${escapeHtml(formatBidTime(p.bid_deadline))}</span>`);
          if (!p.bid_deadline && _activeStage === 'upcoming') meta.push('<span class="tender-card__meta-item"><span class="tender-card__meta-label">开标</span><span class="tender-time-pending">时间待确认</span></span>');
          if (!p.amount && !p.bid_deadline) meta.push(`<span class="tender-card__meta-item"><span class="tender-card__meta-label">来源</span>${escapeHtml(p.site)}</span>`);
          const detail = [
            p.tenderer ? `<div class="tender-card__detail-row"><span class="tender-card__meta-label">发包人</span>${escapeHtml(p.tenderer)}</div>` : '',
            p.proj_major_cat ? `<div class="tender-card__detail-row"><span class="tender-card__meta-label">行业</span>${escapeHtml(p.proj_major_cat)}</div>` : '',
            ictKeywords.length ? `<div class="tender-card__detail-row"><span class="tender-card__meta-label">关键词</span><span class="tender-keywords">${ictKeywords.map(kw => {
              const isStrong = STRONG_KW.has(kw);
              return `<span class="tender-keyword ${isStrong ? 'tender-keyword--strong' : ''}">${escapeHtml(kw)}</span>`;
            }).join('')}</span></div>` : '',
            `<div class="tender-card__detail-row"><span class="tender-card__meta-label">原文</span><a href="${p.url}" target="_blank" rel="noopener">查看公告 →</a></div>`,
          ].filter(Boolean).join('');
          return `
            <div class="tender-card ${p.isIct ? 'tender-card--ict' : ''}" data-open="0">
              <div class="tender-card__main">
                <div class="tender-card__title">${renderFavBtn(normalizeProjectName(p.title), favBucketOf(p))}${badges}<a href="${p.url}" target="_blank" rel="noopener" title="${escapeHtml(p.title)}">${escapeHtml(p.title)}</a></div>
                <div class="tender-card__sub">${escapeHtml(p.site)} · ${escapeHtml(p.date || '—')}</div>
                <div class="tender-card__meta">${meta.join('')}</div>
              </div>
              <div class="tender-card__detail">${detail}</div>
            </div>
          `;
        }).join('')}
      </div>
    `;
  }

  // —— 分页条 ——
  function renderPagination(total, totalPages) {
    if (total <= PAGE_SIZE) return '';  // 不足一页不需分页
    const pages = buildPageList(_currentPage, totalPages);
    const pageBtns = pages.map(p => {
      if (p === '...') return '<span class="tender-page-ellipsis">…</span>';
      return `<button class="tender-pagebtn ${p === _currentPage ? 'tender-pagebtn--active' : ''}" data-page="${p}">${p}</button>`;
    }).join('');
    return `
      <div class="tender-pagination">
        <span class="tender-pagination__info">共 ${total} 条 · 第 ${_currentPage}/${totalPages} 页</span>
        <div class="tender-pagination__controls">
          <button class="tender-pagebtn" data-page="${_currentPage - 1}" ${_currentPage <= 1 ? 'disabled' : ''}>上一页</button>
          ${pageBtns}
          <button class="tender-pagebtn" data-page="${_currentPage + 1}" ${_currentPage >= totalPages ? 'disabled' : ''}>下一页</button>
        </div>
      </div>
    `;
  }

  // —— 页码序列（多页时用省略号）——
  function buildPageList(cur, total) {
    if (total <= 7) return Array.from({ length: total }, (_, i) => i + 1);
    const pages = [1];
    const left = Math.max(2, cur - 1);
    const right = Math.min(total - 1, cur + 1);
    if (left > 2) pages.push('...');
    for (let i = left; i <= right; i++) pages.push(i);
    if (right < total - 1) pages.push('...');
    pages.push(total);
    return pages;
  }

  // —— 空状态 ——
  function emptyState(msg) {
    return `
      <div class="tender-empty">
        <span class="tender-empty__icon">📋</span>
        <span>${escapeHtml(msg)}</span>
      </div>
    `;
  }

  // —— HTML 转义 ——
  function escapeHtml(text) {
    if (text == null) return '';
    const map = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
    return String(text).replace(/[&<>"']/g, m => map[m]);
  }

  // —— 平台列表模态弹窗 ——

  // 采集方式 / 留存方式 标签映射（2026-08-07：平台列表弹窗删网址、增两列）
  const SCRAPE_METHOD_LABELS = {
    html: 'HTML解析',
    api: 'API接口',
    cdp: '浏览器渲染',
    pdf: 'PDF提取',
  };
  const RETENTION_LABELS = {
    db: 'DB存正文',
    pdf: 'PDF归档',
    'pdf+ocr+md': 'PDF+OCR+MD',
  };
  // 2026-08-10：留存方式悬停说明（大白话，2026-08-10 改）
  const RETENTION_TITLES = {
    'pdf+ocr+md': '公告是 PDF 文件：PDF 原件和整理出的文字稿都存到服务器，长期保存',
    'pdf': '公告是 PDF 文件：PDF 原件存到服务器，长期保存',
    'db': '公告内容存进系统数据库，长期保存',
  };

  function closeSites() {
    const m = document.getElementById('tender-sites-modal');
    if (m) m.remove();
  }

  // —— ICT 项目行业分布饼图弹窗（2026-08-31：点击顶部「ICT 相关」卡片）——
  // 数据：当前区县筛选下的 ICT 项目（_districtItems），按行业 proj_major_cat 分组
  // 展示：Top 7 行业 + 其余合并「其他行业」+ 「未分类」
  const ICT_CAT_TOP_N = 7;
  const ICT_CAT_PALETTE = [
    '#1e3a8a', '#f59e0b', '#0f766e', '#7c3aed', '#15803d',
    '#c2410c', '#2563eb', '#b45309', '#0e7490', '#9333ea',
    '#4d7c0f', '#be123c', '#1d4ed8', '#a16207', '#0891b2',
  ];

  function closeIctPie() {
    const m = document.getElementById('tender-ict-modal');
    if (m) m.remove();
  }

  // SVG 饼图：根据分组数量生成扇区 path（起点 12 点方向顺时针）
  function buildPieSvg(slices, total, size) {
    const R = size / 2 - 4;
    const cx = size / 2, cy = size / 2;
    let acc = 0;
    const paths = slices.map((s) => {
      const frac = total > 0 ? s.count / total : 0;
      const start = acc;
      const end = acc + frac;
      acc = end;
      if (frac <= 0) return '';
      const a0 = start * 2 * Math.PI - Math.PI / 2;
      const a1 = end * 2 * Math.PI - Math.PI / 2;
      const x0 = cx + R * Math.cos(a0);
      const y0 = cy + R * Math.sin(a0);
      const x1 = cx + R * Math.cos(a1);
      const y1 = cy + R * Math.sin(a1);
      const large = (end - start) > 0.5 ? 1 : 0;
      return `<path d="M${cx},${cy} L${x0.toFixed(2)},${y0.toFixed(2)} A${R},${R} 0 ${large} 1 ${x1.toFixed(2)},${y1.toFixed(2)} Z" fill="${s.color}" stroke="#ffffff" stroke-width="2"/>`;
    }).join('');
    return `<svg class="tender-ict-pie__svg" viewBox="0 0 ${size} ${size}" role="img" aria-label="ICT 项目行业分布饼图">${paths}</svg>`;
  }

  // 行业分组 → 切片（Top N + 其他行业 + 未分类）
  function buildIctSlices(ictItems) {
    const counts = {};
    ictItems.forEach(i => {
      const cat = i.proj_major_cat || '未分类';
      counts[cat] = (counts[cat] || 0) + 1;
    });
    // 未分类单独拆出
    const uncat = counts['未分类'] || 0;
    delete counts['未分类'];
    // 其余按数量降序
    const entries = Object.entries(counts).sort((a, b) => b[1] - a[1]);
    const top = entries.slice(0, ICT_CAT_TOP_N);
    const restCount = entries.slice(ICT_CAT_TOP_N).reduce((s, e) => s + e[1], 0);
    const slices = top.map(([label, count], idx) => ({
      label, count, color: ICT_CAT_PALETTE[idx % ICT_CAT_PALETTE.length],
    }));
    if (restCount > 0) {
      slices.push({ label: '其他行业', count: restCount, color: '#94a3b8' });
    }
    if (uncat > 0) {
      slices.push({ label: '未分类', count: uncat, color: '#cbd5e1' });
    }
    return slices.filter(s => s.count > 0);
  }

  function showIctPie() {
    let modal = document.getElementById('tender-ict-modal');
    if (modal) { closeIctPie(); return; } // 已存在则关闭

    // 当前区县筛选下的 ICT 项目
    const ictItems = _districtItems.filter(i => i.isIct);
    const total = ictItems.length;
    const slices = buildIctSlices(ictItems);

    // 区县标签
    const activeDistrict = (DISTRICTS.find(d => d.key === _activeDistrict) || {}).label || '全部区县';

    // 图例：色块 + 标签 + 数量 + 占比
    const legendHtml = slices.map(s => {
      const pct = total > 0 ? (s.count / total * 100).toFixed(1) : 0;
      return `
        <div class="tender-ict-pie__legend-item">
          <span class="tender-ict-pie__dot" style="background:${s.color}"></span>
          <span class="tender-ict-pie__legend-label">${escapeHtml(s.label)}</span>
          <span class="tender-ict-pie__legend-num">${s.count}</span>
          <span class="tender-ict-pie__legend-pct">${pct}%</span>
        </div>`;
    }).join('');

    // 弹窗内容：空态 or 饼图 + 图例
    let bodyHtml;
    if (total === 0) {
      bodyHtml = `<div class="tender-ict-pie__empty">当前区县（${escapeHtml(activeDistrict)}）暂无 ICT 项目</div>`;
    } else {
      bodyHtml = `
        <div class="tender-ict-pie__wrap">
          <div class="tender-ict-pie__chart">${buildPieSvg(slices, total, 240)}</div>
          <div class="tender-ict-pie__legend">${legendHtml}</div>
        </div>
        <div class="tender-ict-pie__summary">共 ${total} 个 ICT 项目 · 行业分布 · 区县：${escapeHtml(activeDistrict)}（Top ${Math.min(ICT_CAT_TOP_N, slices.length)} 行业 + 其他 + 未分类）</div>`;
    }

    modal = document.createElement('div');
    modal.id = 'tender-ict-modal';
    modal.className = 'tender-modal';
    modal.innerHTML = `
      <div class="tender-modal__backdrop" onclick="Tender.closeIctPie()"></div>
      <div class="tender-modal__dialog tender-ict-modal__dialog" role="dialog" aria-modal="true" aria-labelledby="tender-ict-modal-title">
        <div class="tender-modal__head">
          <div>
            <div class="tender-modal__title" id="tender-ict-modal-title">ICT 项目行业分布</div>
            <div class="tender-ict-modal__meta">跟随当前区县筛选 · 行业占比（物业外包服务 / 市政工程 …）</div>
          </div>
          <button class="tender-modal__close" type="button" aria-label="关闭 ICT 分布" onclick="Tender.closeIctPie()">✕</button>
        </div>
        <div class="tender-modal__body">${bodyHtml}</div>
      </div>`;
    document.body.appendChild(modal);
    modal.querySelector('.tender-modal__close')?.focus();
    modal.addEventListener('keydown', (event) => {
      if (event.key === 'Escape') closeIctPie();
    });
  }

  function showSites() {
    let modal = document.getElementById('tender-sites-modal');
    if (modal) { closeSites(); return; } // 已存在则关闭

    const sites = _sitesData || {};
    // 排序：政府在前、企业在后，组内保持原始顺序（2026-08-07）
    const sortedEntries = Object.entries(sites)
      .map(([name, info]) => ({ name, info, order: info.category === '政府' ? 0 : 1 }))
      .sort((a, b) => a.order - b.order);
    const rows = sortedEntries.map(({ name, info }, idx) => {
      const total = (info.items || []).length;
      const ict = (info.ict_items || []).length;
      const method = SCRAPE_METHOD_LABELS[info.scrape_method] || info.scrape_method || '—';
      const retention = RETENTION_LABELS[info.retention] || info.retention || '—';
      const cat = info.category || '';
      const catHtml = cat
        ? `<span class="tender-sites-modal__cat tender-sites-modal__cat--${cat === '政府' ? 'gov' : 'ent'}">${escapeHtml(cat)}</span>`
        : '<span class="tender-sites-modal__muted">—</span>';
      // 2026-08-10：平台名称软链接（跳转招投标网站）
      const siteUrl = info.url || '';
      const nameHtml = siteUrl
        ? `<a class="tender-sites-modal__link" href="${escapeHtml(siteUrl)}" target="_blank" rel="noopener" title="${escapeHtml(siteUrl)}">${escapeHtml(name)}<span class="tender-sites-modal__ext">↗</span></a>`
        : `<span class="tender-sites-modal__name">${escapeHtml(name)}</span>`;
      // 2026-08-12：当月采集数 + 数据起止（最早 ~ 最新公告日期，title 附最近抓取时间）
      const monthly = info.monthly_count || 0;
      const start = info.data_start || '—';
      const until = info.data_until || '—';
      const rangeHtml = info.last_seen
        ? `<span class="tender-sites-modal__range" title="最早 ~ 最新公告日期；最近抓取：${escapeHtml(info.last_seen)}">${escapeHtml(start)} ~ ${escapeHtml(until)}</span>`
        : `${escapeHtml(start)} ~ ${escapeHtml(until)}`;
      const retentionTitle = RETENTION_TITLES[info.retention] || '';
      return `
        <tr>
          <td class="tender-sites-modal__idx">${idx + 1}</td>
          <td class="tender-sites-modal__catcell">${catHtml}</td>
          <td class="tender-sites-modal__name">${nameHtml}</td>
          <td><span class="tender-sites-modal__num">${total}</span></td>
          <td><span class="tender-sites-modal__num ${ict > 0 ? 'tender-sites-modal__num--ict' : 'tender-sites-modal__num--zero'}">${ict}</span></td>
          <td><span class="tender-sites-modal__num ${monthly > 0 ? '' : 'tender-sites-modal__num--zero'}">${monthly}</span></td>
          <td class="tender-sites-modal__untilcell">${rangeHtml}</td>
          <td><span class="tender-sites-modal__tag tender-sites-modal__tag--method">${escapeHtml(method)}</span></td>
          <td>${retentionTitle
            ? `<span class="tender-sites-modal__tag tender-sites-modal__tag--retention" title="${escapeHtml(retentionTitle)}">${escapeHtml(retention)}</span>`
            : `<span class="tender-sites-modal__tag tender-sites-modal__tag--retention">${escapeHtml(retention)}</span>`}</td>
        </tr>`;
    }).join('');
    const govCount = Object.values(sites).filter(s => s.category === '政府').length;
    const entCount = Object.values(sites).filter(s => s.category === '企业').length;

    modal = document.createElement('div');
    modal.id = 'tender-sites-modal';
    modal.className = 'tender-modal';
    modal.innerHTML = `
      <div class="tender-modal__backdrop" onclick="Tender.closeSites()"></div>
      <div class="tender-modal__dialog tender-sites-modal__dialog" role="dialog" aria-modal="true" aria-labelledby="tender-sites-modal-title">
        <div class="tender-modal__head">
          <div>
            <div class="tender-modal__title" id="tender-sites-modal-title">监控平台列表</div>
            <div class="tender-sites-modal__meta">共 ${Object.keys(sites).length} 个平台 · 政府 ${govCount} / 企业 ${entCount} · 点击平台名跳转原网站</div>
          </div>
          <button class="tender-modal__close" type="button" aria-label="关闭平台列表" onclick="Tender.closeSites()">✕</button>
        </div>
        <div class="tender-modal__body">
          <table class="tender-sites-modal__table">
            <thead>
              <tr>
                <th>序号</th>
                <th>分类</th>
                <th>平台名称</th>
                <th>公告数</th>
                <th>ICT</th>
                <th>当月采集</th>
                <th>数据起止</th>
                <th>采集方式</th>
                <th>留存方式</th>
              </tr>
            </thead>
            <tbody>${rows}</tbody>
          </table>
          <div class="tender-sites-modal__archive">
            <div class="tender-sites-modal__archive-title">📦 公告内容怎么保存</div>
            <ul>
              <li><b>数据库保存（21 个网站）</b>：公告的标题、时间、金额、中标单位等信息自动存进系统数据库，长期保存，随时可查</li>
              <li><b>江苏招标采购服务平台</b>：公告以 PDF 文件发布，除数据库外，PDF 原件和整理出的文字稿也会一起存到服务器</li>
              <li><b>中国邮政</b>：公告以 PDF 文件发布，PDF 原件存到服务器</li>
              <li><b>收录范围</b>：盐城全市及各县；邮政、三峡、一汽、奇瑞、中粮是全国性平台，只收标题含「盐城」的公告</li>
              <li><b>保存时长</b>：长期保留，不会自动删除</li>
            </ul>
          </div>
          <div class="tender-sites-modal__archive">
            <div class="tender-sites-modal__archive-title">🗺️ 区县怎么划分（2026-08-22 更新）</div>
            <ul>
              <li><b>苏服采唯一按 API 区县码</b>：320991 → 经开，320992 → 盐南；320900 及其他区县码按接口原值归类。苏服采不再使用项目地址、标题、采购单位或企业注册地址猜测区域。</li>
              <li><b>其他网站</b>：仍按各自站点的地址、文字、单位及固定归属规则处理，互不影响。</li>
              <li><b>前端合并</b>：「盐开区」= 盐南+经开，只收录最终判定为盐南/经开的项目；苏服采缺少有效 areaCode 的项目不猜测，保留未归类。</li>
            </ul>
          </div>
        </div>
      </div>
    `;
    document.body.appendChild(modal);
    modal.querySelector('.tender-modal__close')?.focus();
    modal.addEventListener('keydown', (event) => {
      if (event.key === 'Escape') closeSites();
    });
  }

  let _sitesData = null;

  // —— 顶栏「📢 更新内容」弹窗（2026-08-25：从平台弹窗独立出来，顶栏时间左侧入口）——
  const CHANGELOG_ITEMS = [
    ['区县分类等级整理', '发包人信息（招标人地址/名称）提升为第一等级：市级兜底 390 条落到具体区县，亭湖+216、盐南+91、经开+34'],
    ['江苏政采区县 0 级', '按平台区县码权威归类（11 码），13,370 条公告 100% 归属，不再靠文本猜测'],
    ['发包人信息块', '新增招标人地址/代理机构/联系人/电话/邮箱/邮编字段，用于项目区县第一等级区分'],
    ['监控网页多选修正', '从「全部」点单项 → 只选该项（选一个就是一个）；全部状态下所有选项显示勾选'],
    ['公告质感升级', '汇总卡数字强调 + hover 上浮、区县/导航选中态阴影、表格斑马纹 + hover 高亮、徽章微光过渡'],
    ['中标公示入口', '「招标公示」按钮更正为「中标公示」（内容本就是中标/成交/结果公示）'],
    ['监控体系完善', '部署状态机 + 健康快照（5 分钟）+ 发布清单 SHA 校验，误报修复（锁占用≠阻塞）'],
    ['维护手册精简', '招投标手册 1532 行 → 300 行，按抓取/分类/富化/辅助四大主线重排'],
    ['瑞康医院退役', '监控源已移除（22 站），历史数据保留备查'],
  ];
  function showChangelog() {
    const list = CHANGELOG_ITEMS.map(([tag, text]) =>
      `<li><b>${tag}</b>：${text}</li>`).join('');
    const modal = document.createElement('div');
    modal.className = 'tender-modal';
    modal.innerHTML = `
      <div class="tender-modal__backdrop"></div>
      <div class="tender-modal__dialog" role="dialog" aria-modal="true" aria-label="更新内容">
        <div class="tender-modal__head">
          <div class="tender-modal__title">📢 更新内容（2026-08-28）</div>
          <button type="button" class="tender-modal__close" aria-label="关闭">✕</button>
        </div>
        <div class="tender-modal__body">
          <ul style="margin:0;padding-left:18px;line-height:1.9;font-size:14px;color:var(--color-text);">${list}</ul>
        </div>
      </div>`;
    document.body.appendChild(modal);
    const close = () => modal.remove();
    modal.querySelector('.tender-modal__close').addEventListener('click', close);
    modal.querySelector('.tender-modal__backdrop').addEventListener('click', close);
    modal.addEventListener('keydown', (e) => { if (e.key === 'Escape') close(); });
    modal.querySelector('.tender-modal__close').focus();
  }
  // 绑定顶栏入口
  document.addEventListener('DOMContentLoaded', () => {
    const btn = document.getElementById('topbar-changelog');
    if (btn) btn.addEventListener('click', showChangelog);
  });

  return { render, showSites, closeSites, showIctPie, closeIctPie };
})();

// —— 注册为驾驶舱模块（2026-08-09 模块化重构）——
App.register({
  id: 'tender',
  title: '招投标信息栏',
  dataUrl: 'data/tender.json',
  mountId: 'tender-body',
  render: Tender.render,
});
