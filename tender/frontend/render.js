// tender 板块域模块：render（2026-09-29 由单文件 IIFE 机械拆分；函数体零逻辑改动，
// 仅状态→S.、常量→K.、跨模块调用→F. 三类机械前缀改写。真源在 tender/frontend/tender/。）
import { S } from './state.js';
import * as K from './constants.js';
import { F } from './registry.js';

export function render(data, root) {
    root = root || document.getElementById('tender-body');
    if (!root) return;

    // 2026-09-29 API-only：业务列表统一由 8002 分页接口提供。
    if (K.USE_LAZY) {
      F.renderLazyBody(root);
      return;
    }

    // 2026-08-23：F.render 重建筛选栏前，清理 body 下残留的 fixed 菜单（僵尸菜单）
    // 场景：菜单打开中（menu 已在 body）时触发 F.render（点区县/搜索等）→ 旧 root 被替换，
    // body 下的 menu 不会自动消失，必须手动移除。
    document.querySelectorAll('.tender-multi__menu[data-fixed-pos]').forEach(m => m.remove());

    if (!data || !data.summary) {
      root.innerHTML = F.emptyState('暂无数据');
      return;
    }

    S._data = data;

    // 区县下拉触发只绑一次（trigger 在 index.html 静态头部，不随 F.render 重建）
    F.bindDropdown();

    // 人工区县修订：首次渲染前先加载（2026-08-16）
    if (!S._overridesLoaded) {
      S._overridesLoaded = true;
      F.loadDistrictOverrides().then((ok) => {
        if (ok === false) {
          // 加载失败：允许下次 F.render 重试（2026-08-16 复查修复）
          S._overridesLoaded = false;
          return;
        }
        // 修订加载完重新收集条目（合并修订），再完整重渲染一次
        if (S._data) F.render(S._data, root);
      });
    }

    const s = data.summary;
    const sites = data.sites || {};
    S._allItems = F.collectAllItems(data);
    // 老收藏双桶迁移（2026-08-17）：意向项目自动搬进代挂网桶
    F.migrateLegacyFavorites();
    // 收藏服务端同步（2026-08-17）：首次拉取并集合并 + 上云
    F.startFavSync();
    S._sitesData = sites;
    _links = data.links || [];
    S._linkedTenderNames = new Set(_links.map(l => F.normalizeProjectName(l.tender_name)));
    S._awardLinkMap = new Map(_links.map(l => [F.normalizeProjectName(l.award_name), l]));

    // 按区县筛选（2026-08-04 区县子菜单）
    S._districtItems = F.filterByDistrict(S._allItems);

    // 项目进度统计（基于当前区县；主状态互斥）
    const stageCounts = { all: S._districtItems.filter(i => F.getProjectStage(i) !== 'awarded' && F.getProjectStage(i) !== 'publicity').length, upcoming: 0, countdown: 0, awarded: 0, publicity: 0, pending_result: 0, candidate: 0, intention: 0, exceptions: 0, favorites: F.getFavorites('tender').length, 'fav-intention': F.getFavorites('intention').length };
    // 2026-08-17：countdown 独立统计（与 upcoming 重叠，不漏算）
    stageCounts.countdown = S._districtItems.filter(i => F.isCountdown(i)).length;
    for (const item of S._districtItems) {
      const stage = F.getProjectStage(item);
      if (stageCounts[stage] != null) stageCounts[stage]++;
    }

    // 行业大类统计（基于当前区县，2026-08-04）
    const catCounts = { all: S._districtItems.length };
    for (const item of S._districtItems) {
      const c = item.proj_major_cat || '未分类';
      catCounts[c] = (catCounts[c] || 0) + 1;
    }

    // 当前区县汇总（从 S._districtItems 计算，非全局）
    const dTotal = S._districtItems.length;
    const dIct = S._districtItems.filter(i => i.isIct).length;
    const activeLabel = (K.DISTRICTS.find(d => d.key === S._activeDistrict) || {}).label || '';
    // 平台总数 = 监控的全部平台（全局），不随区县筛选变化
    const totalSites = Object.keys(sites).length;

    // 2026-09-23 P1.1：拆分"时间"卡中 val/unit，避免"17 小时前"占过多横向
    // 匹配 "X 单位"（数字+空格+单位），无法拆分的（"刚刚"/"昨天 X时"/"M/D"）整体作为 val
    const _timeStr = F.formatGenTime(data.generated_at);
    const _timeMatch = _timeStr.match(/^(\d+)\s*(.+)$/);
    const timeParts = _timeMatch ? { val: _timeMatch[1], unit: _timeMatch[2] } : { val: _timeStr, unit: '' };

    // 2026-09-23 4 卡"默认隐藏"：读 localStorage 决定是否折叠
    // 首次访问无 key → 默认折叠（true）；'0' = 展开；'1' 或其他 = 折叠
    // localStorage 不可用时（隐私模式）静默降级，不阻塞渲染
    let summaryCollapsed = true;
    try {
      summaryCollapsed = localStorage.getItem(K.SUMMARY_COLLAPSE_KEY) !== '0';
    } catch (e) { /* ignore */ }

    // 汇总卡片（反映当前区县）
    const summaryHtml = `
      <div class="tender-summary-wrap ${summaryCollapsed ? 'tender-summary-wrap--collapsed' : ''}">
        <button class="tender-summary__bar" type="button"
                aria-expanded="${!summaryCollapsed}"
                aria-controls="tender-summary-cards"
                title="点击展开 / 收起汇总信息">
          <span class="tender-summary__bar-icon" aria-hidden="true">📊</span>
          <span class="tender-summary__bar-text">
            <b>${totalSites}</b> 平台 · <b>${Utils.formatInt(dTotal)}</b> 项目 · <b>${dIct}</b> ICT · ${F.escapeHtml(_timeStr)}
          </span>
          <span class="tender-summary__chevron" aria-hidden="true">▾</span>
        </button>
        <div class="tender-summary" id="tender-summary-cards">
          <div class="tender-summary__card tender-summary__card--info tender-summary__card--clickable" onclick="Tender.showSites()">
            <span class="tender-summary__label">平台数</span>
            <span class="tender-summary__value">${totalSites}<span class="tender-summary__hint">→</span></span>
          </div>
          <div class="tender-summary__card tender-summary__card--clickable" onclick="window.open('/screen/', '_blank')" title="打开监控中心大屏">
            <span class="tender-summary__label">${activeLabel === '全部' ? '总项目' : (data.schema_version >= 2 ? activeLabel + '项目数' : activeLabel + '公告数')}</span>
            <span class="tender-summary__value">${Utils.formatInt(dTotal)}<span class="tender-summary__hint">→</span></span>
          </div>
          <div class="tender-summary__card tender-summary__card--accent tender-summary__card--clickable" onclick="Tender.showIctPie()">
            <span class="tender-summary__label">ICT 相关</span>
            <span class="tender-summary__value tender-summary__value--accent">${dIct}</span>
          </div>
          <div class="tender-summary__card tender-summary__card--success">
            <span class="tender-summary__label">时间</span>
            <span class="tender-summary__value-group">
              <span class="tender-summary__value" title="数据抓取于 ${F.escapeHtml(F.genTimeFullText(data.generated_at))}">${timeParts.val}</span>
              <span class="tender-summary__sub">${timeParts.unit}</span>
            </span>
          </div>
        </div>
      </div>
    `;

    // 三个主状态 + 三个辅助入口。状态互斥，避免中标公告混入待开标。
    const stageNavHtml = F.renderStageNav(stageCounts);
    // 监控网页多选（按政府/企业分组，2026-08-07）
    const siteOptions = Object.keys(sites)
      .map(sn => ({
        key: sn, label: sn, cnt: S._districtItems.filter(i => i.site === sn).length,
        category: (sites[sn] || {}).category || '',
      }))
      .sort((a, b) => a.label.localeCompare(b.label, 'zh-Hans-CN'));

    // ICT 选项（2026-08-05 替代原“标签”下拉；2026-09-22 简化为 全部/ICT 两分类，删非 ICT 冗余项）
    const ictCnt = S._districtItems.filter(i => i.isIct).length;
    const ictOptions = [
      { key: 'ict', label: 'ICT', cnt: ictCnt },
    ];

    // 政府/企业分类选项（2026-08-07：按站点归属过滤）
    const govCnt = S._districtItems.filter(i => (i.siteCategory || '') === '政府').length;
    const entCnt = S._districtItems.filter(i => (i.siteCategory || '') === '企业').length;
    const typeOptions = [
      { key: '政府', label: '政府分类', cnt: govCnt },
      { key: '企业', label: '企业分类', cnt: entCnt },
    ];

    // 金额区间选项（2026-08-05）：从 tags 统计，无金额的归"其他"
    const amtBands = ['金额<10万', '金额10-100万', '金额100-500万', '金额>500万'];
    const amtCounts = {};
    let noAmount = 0;
    for (const it of S._districtItems) {
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
    for (const it of S._districtItems) {
      const smeTag = (it.tags || []).find(t => t.startsWith('中小微'));
      if (smeTag) smeCounts[smeTag] = (smeCounts[smeTag] || 0) + 1;
      else smeNone++;
    }
    const smeOptions = smeTags.map(t => ({ key: t, label: t.replace('中小微-', ''), cnt: smeCounts[t] || 0 }))
      .concat([{ key: '不涉及', label: '不涉及', cnt: smeNone }]);

    const filterBarHtml = `
      <div class="tender-filterbar">
        <div class="tender-filterbar__row">
          ${F.renderSingleDropdown('ict', 'ICT', ictOptions, S._activeIct)}
          ${F.renderSingleDropdown('type', '分类', typeOptions, S._activeType)}
          ${F.renderCatDropdownTrigger()}
          ${F.renderMultiDropdown('sites', '监控网页', siteOptions, S._sites)}
          ${F.renderSingleDropdown('amount', '金额', amountOptions, S._activeAmount)}
          ${F.renderSingleDropdown('sme', '中小微', smeOptions, S._activeSme)}
        </div>
        <div class="tender-filterbar__search">
          <div class="tender-search">
            <input type="text" class="tender-search__input" id="tender-search-input" placeholder="搜索标题/发包人…" value="${F.escapeHtml(S._activeSearch)}" />
            ${S._activeSearch ? `<button class="tender-search__clear" id="tender-search-clear" title="清除搜索">✕</button>` : ''}
            <button class="tender-search__btn" id="tender-search-btn" type="button">搜索</button>
          </div>
        </div>
      </div>
    `;

    // 内容（多选筛选链 + 分页）。收藏夹：跨区县、不叠加其他筛选器（2026-08-06）
    let baseList;
    if (S._activeStage === 'favorites') {
      baseList = F.favoriteItems('tender');
    } else if (S._activeStage === 'fav-intention') {
      baseList = F.favoriteItems('intention');
    } else {
      baseList = F.filterByStage(F.filterBySites(F.filterByType(F.filterByIct(F.filterByDate(F.filterByOpenDate(F.filterByAmount(F.filterBySme(S._districtItems))))))));
    }
    S._currentList = F.filterByCat(F.filterBySearch(baseList));
    S._currentPage = 1;
    const contentHtml = F.renderGroup(S._currentList, S._sites.length === 1 && S._sites[0] === '__none__' ? 'sites-none' : '');

    // 区县下拉：移到汇总卡之后、项目进度(stage)之前（2026-08-06 方案B）
    const dropdownHtml = F.renderDropdownHtml();
    root.innerHTML = summaryHtml + dropdownHtml + stageNavHtml + filterBarHtml + `<div id="tender-subcontent">${contentHtml}</div>`;

    // 2026-09-23 4 卡折叠功能：绑定信息条 click —— 折叠状态切换 + localStorage 持久化
    // F.render 重建后 root.innerHTML 被覆盖，旧 button 走 GC，新的需重新绑。
    const _summaryWrap = root.querySelector('.tender-summary-wrap');
    if (_summaryWrap) {
      const _summaryBar = _summaryWrap.querySelector('.tender-summary__bar');
      if (_summaryBar) {
        _summaryBar.addEventListener('click', () => {
          const _willCollapse = !_summaryWrap.classList.contains('tender-summary-wrap--collapsed');
          _summaryWrap.classList.toggle('tender-summary-wrap--collapsed');
          _summaryBar.setAttribute('aria-expanded', String(!_willCollapse));
          try {
            localStorage.setItem(K.SUMMARY_COLLAPSE_KEY, _willCollapse ? '1' : '0');
          } catch (e) { /* 隐私模式静默 */ }
        });
      }
    }

    // 绑定多选下拉（2026-08-04）
    F.bindStageNav();
    F.bindMultiDropdown('sites');
    F.bindSingleDropdown('ict');
    F.bindSingleDropdown('type');
    F.bindSingleDropdown('amount');
    F.bindSingleDropdown('sme');
    F.bindMultiDocClose();
    F.bindCatDropdown();
    F.renderCatDropdown(S._districtItems);
    F.bindDateFilter();  // 2026-08-08 发布日期列筛选
    // 2026-08-23：每次 F.render 后重新注册 fixed-pos 观察（MO 只观察绑定时的元素，
    // 重建后的新 .tender-multi 必须重新 observe + 重设 _menuOwner，否则关闭不触发还原）
    F.bindMenuFixedObservers();

    // 绑定搜索输入（2026-08-04：回车才搜索 + 搜索按钮）
    const searchInput = document.getElementById('tender-search-input');
    const searchBtn = document.getElementById('tender-search-btn');
    const doSearch = () => {
      if (!searchInput) return;
      S._activeSearch = searchInput.value.trim();
      S._currentPage = 1;
      F.render(S._data);
    };
    if (searchInput) {
      searchInput.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') doSearch();
      });
      const clearBtn = document.getElementById('tender-search-clear');
      if (clearBtn) {
        clearBtn.addEventListener('click', () => {
          S._activeSearch = '';
          S._currentPage = 1;
          F.render(S._data);
        });
      }
    }
    if (searchBtn) {
      searchBtn.addEventListener('click', doSearch);
    }

    // 绑定分页按钮
    F.bindPagination(document.getElementById('tender-subcontent'));
    // 绑定移动端卡片点击
    F.bindCardList(document.getElementById('tender-subcontent'));
    // 绑定收藏按钮（2026-08-06）
    F.bindFavButtons(document.getElementById('tender-subcontent'));
  }

  // —— 刷新内容区（重置到第 1 页）——

export async function renderLazyBody(root) {
    root = root || document.getElementById('tender-body');
    if (!root) return;

    // 一次性初始化（区县修订/收藏迁移/同步）——与全量 F.render 对齐
    F.bindDropdown();
    if (!S._overridesLoaded) {
      S._overridesLoaded = true;
      try { await F.loadDistrictOverrides(); } catch (e) { S._overridesLoaded = false; }
    }
    F.migrateLegacyFavorites();
    F.startFavSync();

    // 1. 状态同步 + 查询（2026-09-30：收藏视图复用同一 chrome，数据源切服务端批量解析。
    // 旧收藏分支提前 return 且整段覆盖 root.innerHTML，把进度导航/筛选栏抹掉 → 进收藏视图卡死）
    const isFavView = S._activeStage === 'favorites' || S._activeStage === 'fav-intention';
    const favBucket = S._activeStage === 'favorites' ? 'tender' : 'intention';
    let resp, favDead = [];
    F.syncQueryState();
    try {
      if (isFavView) {
        const fr = await F.resolveFavorites(favBucket);
        resp = { total: fr.items.length, items: fr.items };
        favDead = fr.dead;
      } else {
        resp = await F.queryProjects();
      }
    } catch (e) {
      // API-only：服务失败明确报错，不回退全量 JSON。
      root.innerHTML = F.emptyState('数据服务暂时不可用，请稍后刷新重试');
      return;
    }
    S._lazyTotal = resp.total;
    S._serverTotal = resp.total;   // F.renderGroup 用服务端分页（跳过本地切片）

    // 2. 更新角标批量合并
    try {
      const ups = await F.fetchUpdates(resp.items.map(i => i.id));
      resp.items.forEach(it => { if (ups[it.id]) it.update_notice = ups[it.id]; });
    } catch (e) { /* 角标失败不影响列表 */ }

    const meta = await F.fetchMeta();
    const s = meta.summary;
    S._sitesData = Object.fromEntries((meta.site_meta || []).map(info => [info.name, info]));
    S._districtItems = resp.items;   // 当前页条目（渲染用）
    // 注：S._allItems 在 API-only 架构下（2026-09-29 main.js 改动后，主 render() 不再被调用）
    // 只能在此处赋值。语义上"_allItems"是"当前全量缓存"，refreshContent 是一次重拉全量后再设。
    // 但当前 queryProjects() 只拉分页后返回的 resp.items，所以 district 计数仍是当前页统计 —— 
    // 全量真值需服务端 meta.district_counts 提供（下一步接入）。
    S._allItems = resp.items;
    F.cacheProjectAll(resp.items);   // P4：当前页入缓存（弹窗/详情同步查）
    S._currentList = resp.items;

    // 3. 汇总卡片（计数用当前查询 total + meta 全局计数）
    const activeLabel = (K.DISTRICTS.find(d => d.key === S._activeDistrict) || {}).label || '';
    const ictAll = (meta.stage_counts && s.ict) || 0;
    const summaryHtml = `
      <div class="tender-summary">
        <div class="tender-summary__card tender-summary__card--info tender-summary__card--clickable" onclick="Tender.showSites()">
          <span class="tender-summary__label">平台数</span>
          <span class="tender-summary__value">${meta.site_meta.length}<span class="tender-summary__hint">→</span></span>
          <span class="tender-summary__sub">已监控 · 点击查看</span>
        </div>
        <div class="tender-summary__card">
          <span class="tender-summary__label">${activeLabel + '项目数'}</span>
          <span class="tender-summary__value">${Utils.formatInt(S._lazyTotal)}</span>
          <span class="tender-summary__sub">持续收录中</span>
        </div>
        <div class="tender-summary__card tender-summary__card--accent tender-summary__card--clickable" onclick="Tender.showIctPie()">
          <span class="tender-summary__label">ICT 相关</span>
          <span class="tender-summary__value tender-summary__value--accent">${s.ict}</span>
          <span class="tender-summary__sub">全库占比 ${(s.ict * 100 / Math.max(s.projects, 1)).toFixed(1)}% · 点击看类型分布</span>
        </div>
        <div class="tender-summary__card tender-summary__card--success">
          <span class="tender-summary__label">数据更新</span>
          <span class="tender-summary__value">${F.formatGenTime(s.generated_at || '')}</span>
        </div>
      </div>
    `;

    // 4. 筛选栏（计数来自 meta 全局口径；ctdown 入口除外）
    const dc = meta.district_counts || {};
    const sc = meta.stage_counts || {};
    const stageCounts = {
      all: Object.entries(sc).filter(([k]) => k !== 'awarded').reduce((a, b) => a + b[1], 0),
      upcoming: (sc.upcoming || 0) + (sc.upcoming_unknown_time || 0) + (sc.opening_today || 0),
      countdown: (sc.upcoming || 0) + (sc.upcoming_unknown_time || 0) + (sc.opening_today || 0),
      awarded: sc.awarded || 0,
      publicity: sc.publicity || 0,  // 2026-09-30 修复：原用 sc.awarded 导致中标公示/历史中标 两按钮数字相同
      pending_result: sc.pending_result || 0,
      candidate: sc.awarded || 0,
      intention: sc.intention || 0,
      exceptions: ['failed','terminated','suspended','correction','contract','unknown'].reduce((a, k) => a + (sc[k] || 0), 0),
      favorites: F.getFavorites('tender').length,
      'fav-intention': F.getFavorites('intention').length,
    };
    const stageNavHtml = F.renderStageNav(stageCounts);

    const siteOptions = (meta.site_meta || []).map(m => ({
      key: m.name, label: m.name, cnt: m.total, category: m.category || '',
    })).sort((a, b) => a.label.localeCompare(b.label, 'zh-Hans-CN'));
    const ictCnt = meta.summary.ict || 0;
    const ictOptions = [{ key: 'ict', label: 'ICT', cnt: ictCnt }];
    const typeCntGov = (meta.site_meta || []).filter(m => m.category === '政府').length;
    const typeCntEnt = (meta.site_meta || []).filter(m => m.category === '企业').length;
    const typeOptions = [
      { key: '政府', label: '政府分类', cnt: typeCntGov },
      { key: '企业', label: '企业分类', cnt: typeCntEnt },
    ];
    // 金额/中小微计数：meta 暂无分档计数，仅显选项不带 cnt（后续 /meta 可补）
    const amountOptions = ['0-10', '10-100', '100-500', '500-2000', '2000+']
      .map(k => ({ key: k, label: k, cnt: 0 }));
    const smeOptions = ['专门面向', '非专门但优惠']
      .map(k => ({ key: '中小微-' + k, label: k, cnt: 0 }))
      .concat([{ key: '不涉及', label: '不涉及', cnt: 0 }]);

    const filterBarHtml = `
      <div class="tender-filterbar">
        <div class="tender-filterbar__row">
          ${F.renderSingleDropdown('ict', 'ICT', ictOptions, S._activeIct)}
          ${F.renderSingleDropdown('type', '分类', typeOptions, S._activeType)}
          ${F.renderCatDropdownTrigger()}
          ${F.renderMultiDropdown('sites', '监控网页', siteOptions, S._sites)}
          ${F.renderSingleDropdown('amount', '金额', amountOptions, S._activeAmount)}
        </div>
        <div class="tender-filterbar__search">
          <div class="tender-search">
            <input type="text" class="tender-search__input" id="tender-search-input" placeholder="搜索标题/发包人…" value="${F.escapeHtml(S._activeSearch)}" />
            ${S._activeSearch ? `<button class="tender-search__clear" id="tender-search-clear" title="清除搜索">✕</button>` : ''}
            <button class="tender-search__btn" id="tender-search-btn" type="button">搜索</button>
          </div>
          ${F.renderSingleDropdown('sme', '中小微', smeOptions, S._activeSme)}
        </div>
      </div>
    `;

    // 5. 列表 + 分页（S._serverTotal 让 F.renderGroup 用 API total，跳过本地切片）
    const catOptions = Object.entries(meta.cat_counts || {});
    const contentHtml = F.renderGroup(resp.items, '', resp.total) + (isFavView ? F.renderFavDeadRows(favDead) : '');
    const dropdownHtml = F.renderDropdownHtml();
    root.innerHTML = summaryHtml + dropdownHtml + stageNavHtml + filterBarHtml + `<div id="tender-subcontent">${contentHtml}</div>`;

    // 6. 绑定（与全量 F.render 相同的绑定集）
    F.bindStageNav();
    F.bindMultiDropdown('sites');
    F.bindSingleDropdown('ict');
    F.bindSingleDropdown('type');
    F.bindSingleDropdown('amount');
    F.bindSingleDropdown('sme');
    F.bindMultiDocClose();
    F.bindCatDropdown();
    F.renderCatDropdownLazy();
    F.bindDateFilter();
    F.bindMenuFixedObservers();

    const searchInput = document.getElementById('tender-search-input');
    const searchBtn = document.getElementById('tender-search-btn');
    const doSearch = () => {
      if (!searchInput) return;
      S._activeSearch = searchInput.value.trim();
      S._currentPage = 1;
      F.render(S._data);   // lazy 分流在 F.render 入口处理
    };
    if (searchInput) {
      searchInput.addEventListener('keydown', (e) => { if (e.key === 'Enter') doSearch(); });
      const clearBtn = document.getElementById('tender-search-clear');
      if (clearBtn) clearBtn.addEventListener('click', () => { S._activeSearch = ''; S._currentPage = 1; F.render(S._data); });
    }
    if (searchBtn) searchBtn.addEventListener('click', doSearch);

    F.bindPagination(document.getElementById('tender-subcontent'));
    F.bindCardList(document.getElementById('tender-subcontent'));
    F.bindFavButtons(document.getElementById('tender-subcontent'));
    if (isFavView && favDead.length) {
      // 失效收藏「✕ 清理」：本地移除 + 防抖 PUT 同步服务端 + 重渲（chrome 完整可继续切 tab）
      document.querySelectorAll('.tender-fav-dead__unfav').forEach(btn => {
        btn.addEventListener('click', (ev) => {
          ev.stopPropagation();
          const key = btn.closest('[data-dead-key]').getAttribute('data-dead-key');
          F.toggleFavorite(key, favBucket);
          F.scheduleFavPush();
          F.render(S._data);
        });
      });
    }
  }

  // 行业下拉（lazy 版）：用 meta.cat_counts

export function renderGroup(items, emptyReason) {
    if (!items || items.length === 0) {
      if (emptyReason === 'sites-none') {
        return `<div class="tender-empty tender-empty--filtered">
          <span class="tender-empty__icon" aria-hidden="true">⌁</span>
          <strong>未选任何监控网页</strong>
          <span>请至少选择一个网页，或在上方恢复“全部监控网页”</span>
        </div>`;
      }
      return F.emptyState(S._activeStage === 'favorites' ? '暂无待投标项目，点击项目旁的 ☆ 加入待投标' : S._activeStage === 'fav-intention' ? '暂无代挂网项目，在意向招标列表中点击 ☆ 收藏意向项目' : '暂无符合条件的项目');
    }

    if (S._activeStage === 'awarded') return F.renderAwardGroup(items, S._serverTotal > 0 ? S._serverTotal : null);

    // 排序：全部/意向招标按发布日期倒序；开标倒计时按开标时间升序；
    // 其他状态保持 ICT 优先 + 开标时间降序（2026-08-06；2026-08-12 意向招标改按日期）
    items = [...items].sort((a, b) => {
      if (S._activeStage === 'favorites' || S._activeStage === 'fav-intention') {
        // 收藏夹：保持收藏顺序（F.favoriteItems 已按收藏时间倒序，2026-08-06）
        return 0;
      }
      if (S._activeStage === 'countdown') {
        // 开标倒计时：按开标时间升序（即将开标的在前，2026-08-07）
        const da = a.bid_deadline ? F.parseBidDeadline(a.bid_deadline) : new Date(8.64e15);
        const db = b.bid_deadline ? F.parseBidDeadline(b.bid_deadline) : new Date(8.64e15);
        return da - db;
      }
      if (S._activeStage === 'all' || S._activeStage === 'intention' || S._activeStage === 'upcoming') {
        // 全部/意向招标/招标公告：发布日期（latest_notice_date）倒序，最新在前
        // 意向公告无开标时间，ICT 优先无意义，应按时间展示（2026-08-12）
        // 招标公告按发布时间排序（2026-08-13 老板要求）
        return String(b.date || '').localeCompare(String(a.date || ''));
      }
      if (S._activeStage !== 'all') {
        if (a.isIct && !b.isIct) return -1;
        if (!a.isIct && b.isIct) return 1;
        const da = a.bid_deadline ? F.parseBidDeadline(a.bid_deadline) : null;
        const db = b.bid_deadline ? F.parseBidDeadline(b.bid_deadline) : null;
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
    const total = S._serverTotal > 0 ? S._serverTotal : items.length;
    const totalPages = Math.max(1, Math.ceil(total / K.PAGE_SIZE));
    if (S._currentPage > totalPages) S._currentPage = totalPages;
    if (S._currentPage < 1) S._currentPage = 1;
    const start = (S._currentPage - 1) * K.PAGE_SIZE;
    const pageItems = S._serverTotal > 0 ? items : items.slice(start, start + K.PAGE_SIZE);

    // 2026-08-13：开标倒计时视图把「开标时间」前移、「发布日期」后置；
    // 招标公告视图保持发布时间在前、开标时间最后一列（2026-08-13 老板确认）
    const swapDateCols = S._activeStage === 'countdown';
    const thPublish = `<th style="position: relative;"><button class="tender-th__filter ${S._activeDate ? 'tender-th__filter--active' : ''}" id="tender-date-trigger" type="button">📅 发布日期 <span class="tender-th__caret">▾</span>${S._activeDate ? '<span class="tender-th__dot" title="筛选生效中"></span>' : ''}</button></th>`;
    const thOpen = `<th style="position: relative;"><button class="tender-th__filter ${S._activeOpenDate ? 'tender-th__filter--active' : ''}" id="tender-open-trigger" type="button">⏰ 开标时间 <span class="tender-th__caret">▾</span>${S._activeOpenDate ? '<span class="tender-th__dot" title="筛选生效中"></span>' : ''}</button></th>`;
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
                    ${F.renderFavBtn(F.normalizeProjectName(p.title), F.favBucketOf(p))}
                    ${p.is_new ? '<span class="tender-new-tag" title="本次抓取新增" aria-label="本次新增">NEW</span>' : ''}
                    ${p.isIct ? `<span class="tender-ict-tag" title="ICT 命中：${F.escapeHtml(ictKeywords.join('、'))}">● ICT</span>` : ''}
                    ${F.renderItemBadge(p)}
                    <a href="${p.url}" target="_blank" rel="noopener" title="${F.escapeHtml(p.title)}">${F.escapeHtml(p.title)}</a>
                  </td>`;
              const tdSite = `<td class="tender-table__site">${F.escapeHtml(p.site)}</td>`;
              // 2026-08-16：区县列（人工修订蓝色高亮 + ✎ 可改）
              const tdDistrict = `<td class="tender-table__district">
                <span class="tender-district ${p.district_overridden ? 'tender-district--edited' : ''}" title="${p.district_overridden ? '人工修订（点击修改）' : '自动识别（点击修改）'}">${F.escapeHtml(p.district_label)}</span>
                <button class="tender-district__edit" data-district-edit="${p.id}" type="button" title="修改区县">✎</button>
              </td>`;
              const tdPublish = `<td class="tender-table__date">${p.date || '—'}</td>`;
              const tdTenderer = `<td>${F.escapeHtml(p.tenderer || '—')}</td>`;
              // 2026-08-17：合同预估类金额不进入主列表金额列（老板确认剔除）
              const tdAmount = p.budget_type === 'contract_estimate'
                ? '<td class="tender-table__date"><span class="tender-time-pending" title="合同预估金额，未计入金额列">估算</span></td>'
                : `<td>${F.escapeHtml(p.amount || '—')}</td>`;
              const tdOpen = `<td class="tender-table__date">${p.bid_deadline ? F.escapeHtml(F.formatBidTime(p.bid_deadline)) : (S._activeStage === 'upcoming' ? '<span class="tender-time-pending">时间待确认</span>' : '—')}</td>`;
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
      ${F.renderCardList(pageItems)}
      ${F.renderPagination(total, totalPages)}
    `;
  }

export function renderAwardGroup(items, serverTotal) {
    items = [...items].sort((a, b) => String(b.date || '').localeCompare(String(a.date || '')));
    const total = serverTotal || items.length;
    const totalPages = Math.max(1, Math.ceil(total / K.PAGE_SIZE));
    if (S._currentPage > totalPages) S._currentPage = totalPages;
    const start = (S._currentPage - 1) * K.PAGE_SIZE;
    const pageItems = serverTotal ? items : items.slice(start, start + K.PAGE_SIZE);
    const rows = pageItems.map(p => {
      const a = F.getAwardInfo(p);
      return `<tr>
        <td class="tender-table__title">${F.renderFavBtn(F.normalizeProjectName(p.title), F.favBucketOf(p))}${F.awardBadge(p)}<a href="${p.url}" target="_blank" rel="noopener" title="${F.escapeHtml(p.title)}">${F.escapeHtml(p.title)}</a></td>
        <td>${F.escapeHtml(a.winner)}</td>
        <td>${F.formatMoney(a.winning)}</td>
        <td>${F.formatMoney(a.budget)}</td>
        <td>${a.discount}</td>
        <td>${F.renderConfidence(a.confidence)}</td>
        <td class="tender-table__date">${a.link && a.link.cycle_days != null ? `${a.link.cycle_days} 天` : '—'}</td>
        <td class="tender-table__date">${F.escapeHtml(p.date || '—')}</td>
        <td class="tender-table__site">${F.escapeHtml(p.site)}</td>
        <td>${a.original ? `<a href="${a.original.url}" target="_blank" rel="noopener">查看招标公告</a>` : '<span class="tender-table__muted">暂未匹配</span>'}</td>
      </tr>`;
    }).join('');
    const cards = pageItems.map(p => {
      const a = F.getAwardInfo(p);
      return `<div class="tender-card" data-open="0">
        <div class="tender-card__main">
          <div class="tender-card__title">${F.renderFavBtn(F.normalizeProjectName(p.title), F.favBucketOf(p))}${F.awardBadge(p)}<a class="tender-card__title-text" href="${p.url}" target="_blank" rel="noopener" title="${F.escapeHtml(p.title)}">${F.escapeHtml(p.title)}</a></div>
          <div class="tender-card__sub">${F.escapeHtml(p.site)} · ${F.escapeHtml(p.date || '—')}</div>
          <div class="tender-card__meta"><span class="tender-card__meta-item"><span class="tender-card__meta-label">中标单位</span>${F.escapeHtml(a.winner)}</span><span class="tender-card__meta-item"><span class="tender-card__meta-label">中标金额</span>${F.formatMoney(a.winning)}</span></div>
        </div>
        <div class="tender-card__detail">
          <div class="tender-card__detail-row"><span class="tender-card__meta-label">原预算</span>${F.formatMoney(a.budget)}</div>
          <div class="tender-card__detail-row"><span class="tender-card__meta-label">折扣率</span>${a.discount}</div>
          <div class="tender-card__detail-row"><span class="tender-card__meta-label">关联可信度</span>${F.renderConfidence(a.confidence)}</div>
          <div class="tender-card__detail-row"><span class="tender-card__meta-label">采购周期</span>${a.link && a.link.cycle_days != null ? `${a.link.cycle_days} 天` : '—'}</div>
          ${a.original ? `<div class="tender-card__detail-row"><span class="tender-card__meta-label">原招标</span><a href="${a.original.url}" target="_blank" rel="noopener">查看招标公告 →</a></div>` : '<div class="tender-card__detail-row"><span class="tender-card__meta-label">原招标</span>暂未匹配</div>'}
        </div>
      </div>`;
    }).join('');
    return `<div class="tender-table-wrap tender-table-wrap--desktop"><table class="tender-table tender-award-table"><thead><tr><th style="min-width:260px">中标项目</th><th>中标单位</th><th>中标金额</th><th>原预算</th><th>折扣率</th><th>关联可信度</th><th>周期</th><th>公告日期</th><th>来源</th><th>原招标</th></tr></thead><tbody>${rows}</tbody></table></div><div class="tender-cardlist">${cards}</div>${F.renderPagination(total, totalPages)}`;
  }

export function renderCardList(items) {
    if (!items || items.length === 0) return '';
    return `
      <div class="tender-cardlist">
        ${items.map(p => {
          const ictKeywords = p.keywords || [];
          const badges = `
            ${p.is_new ? '<span class="tender-new-tag" title="本次抓取新增" aria-label="本次新增">NEW</span>' : ''}
            ${p.isIct ? `<span class="tender-ict-tag" title="ICT 命中：${F.escapeHtml(ictKeywords.join('、'))}">● ICT</span>` : ''}
            ${F.renderItemBadge(p)}
          `;
          const meta = [];
          // 2026-08-16：移动端卡片加区县（可编辑）
          meta.push(`<span class="tender-card__meta-item"><span class="tender-card__meta-label">区县</span><span class="tender-district ${p.district_overridden ? 'tender-district--edited' : ''}" data-card-district-edit="${p.id}" title="点击修改区县">${F.escapeHtml(p.district_label)} ✎</span></span>`);
          if (p.amount && p.budget_type !== 'contract_estimate') meta.push(`<span class="tender-card__meta-item"><span class="tender-card__meta-label">金额</span>${F.escapeHtml(p.amount)}</span>`);
          if (p.budget_type === 'contract_estimate') meta.push('<span class="tender-card__meta-item"><span class="tender-card__meta-label">金额</span><span class="tender-time-pending">估算</span></span>');
          if (p.bid_deadline) meta.push(`<span class="tender-card__meta-item"><span class="tender-card__meta-label">开标</span>${F.escapeHtml(F.formatBidTime(p.bid_deadline))}</span>`);
          if (!p.bid_deadline && S._activeStage === 'upcoming') meta.push('<span class="tender-card__meta-item"><span class="tender-card__meta-label">开标</span><span class="tender-time-pending">时间待确认</span></span>');
          if (!p.amount && !p.bid_deadline) meta.push(`<span class="tender-card__meta-item"><span class="tender-card__meta-label">来源</span>${F.escapeHtml(p.site)}</span>`);
          const detail = [
            p.tenderer ? `<div class="tender-card__detail-row"><span class="tender-card__meta-label">发包人</span>${F.escapeHtml(p.tenderer)}</div>` : '',
            p.proj_major_cat ? `<div class="tender-card__detail-row"><span class="tender-card__meta-label">行业</span>${F.escapeHtml(p.proj_major_cat)}</div>` : '',
            ictKeywords.length ? `<div class="tender-card__detail-row"><span class="tender-card__meta-label">关键词</span><span class="tender-keywords">${ictKeywords.map(kw => {
              const isStrong = K.STRONG_KW.has(kw);
              return `<span class="tender-keyword ${isStrong ? 'tender-keyword--strong' : ''}">${F.escapeHtml(kw)}</span>`;
            }).join('')}</span></div>` : '',
            `<div class="tender-card__detail-row"><span class="tender-card__meta-label">原文</span><a href="${p.url}" target="_blank" rel="noopener">查看公告 →</a></div>`,
          ].filter(Boolean).join('');
          return `
            <div class="tender-card ${p.isIct ? 'tender-card--ict' : ''}" data-open="0">
              <div class="tender-card__main">
                <div class="tender-card__title">${F.renderFavBtn(F.normalizeProjectName(p.title), F.favBucketOf(p))}${badges}<a class="tender-card__title-text" href="${p.url}" target="_blank" rel="noopener" title="${F.escapeHtml(p.title)}">${F.escapeHtml(p.title)}</a></div>
                <div class="tender-card__sub">${F.escapeHtml(p.site)} · ${F.escapeHtml(p.date || '—')}</div>
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

export function renderPagination(total, totalPages) {
    if (total <= K.PAGE_SIZE) return '';  // 不足一页不需分页
    const pages = F.buildPageList(S._currentPage, totalPages);
    const pageBtns = pages.map(p => {
      if (p === '...') return '<span class="tender-page-ellipsis">…</span>';
      return `<button class="tender-pagebtn ${p === S._currentPage ? 'tender-pagebtn--active' : ''}" data-page="${p}">${p}</button>`;
    }).join('');
    return `
      <div class="tender-pagination">
        <span class="tender-pagination__info">共 ${total} 条 · 第 ${S._currentPage}/${totalPages} 页</span>
        <div class="tender-pagination__controls">
          <button class="tender-pagebtn" data-page="${S._currentPage - 1}" ${S._currentPage <= 1 ? 'disabled' : ''}>上一页</button>
          ${pageBtns}
          <button class="tender-pagebtn" data-page="${S._currentPage + 1}" ${S._currentPage >= totalPages ? 'disabled' : ''}>下一页</button>
        </div>
      </div>
    `;
  }

  // —— 页码序列（多页时用省略号）——

export function refreshContent() {
    if (K.USE_LAZY) {
      S._currentPage = 1;
      F.renderLazyBody(document.getElementById('tender-body'));
      return;
    }
    // 收藏夹：跨区县、不叠加其他筛选器（2026-08-06）
    let baseList;
    if (S._activeStage === 'favorites') {
      baseList = F.favoriteItems('tender');
    } else if (S._activeStage === 'fav-intention') {
      baseList = F.favoriteItems('intention');
    } else {
      baseList = F.filterByStage(F.filterBySites(F.filterByType(F.filterByIct(F.filterByDate(F.filterByOpenDate(F.filterByAmount(F.filterBySme(S._districtItems))))))));
    }
    S._currentList = F.filterByCat(F.filterBySearch(baseList));
    S._currentPage = 1;
    const sub = document.getElementById('tender-subcontent');
    if (sub) {
      sub.innerHTML = F.renderGroup(S._currentList, S._sites.length === 1 && S._sites[0] === '__none__' ? 'sites-none' : '');
      F.bindPagination(sub);
      F.bindCardList(sub);
      F.bindFavButtons(sub);
    }
  }

  // —— 绑定分页按钮点击 ——

export function bindPagination(container) {
    if (!container) return;
    container.querySelectorAll('.tender-pagebtn').forEach(btn => {
      btn.addEventListener('click', () => {
        if (btn.disabled) return;
        const p = parseInt(btn.dataset.page, 10);
        if (!p || p === S._currentPage) return;
        S._currentPage = p;
        if (K.USE_LAZY) {
          const sub = document.getElementById('tender-subcontent');
          if (sub) sub.innerHTML = '<div class="loading">加载中…</div>';
          F.renderLazyBody(document.getElementById('tender-body'));
          return;
        }
        const sub = document.getElementById('tender-subcontent');
        if (sub) {
          sub.innerHTML = F.renderGroup(S._currentList);
          F.bindPagination(sub);
          F.bindCardList(sub);
          F.bindFavButtons(sub);
        }
      });
    });
  }

  // —— 绑定移动端卡片整行点击展开/收起（2026-08-04 手机排版优化）——

export function bindCardList(container) {
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

Object.assign(F, { render, renderLazyBody, renderGroup, renderAwardGroup, renderCardList, renderPagination, refreshContent, bindPagination, bindCardList });
