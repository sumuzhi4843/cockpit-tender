// tender 板块域模块：filters（2026-09-29 由单文件 IIFE 机械拆分；函数体零逻辑改动，
// 仅状态→S.、常量→K.、跨模块调用→F. 三类机械前缀改写。真源在 tender/frontend/tender/。）
import { S } from './state.js';
import * as K from './constants.js';
import { F } from './registry.js';

export function filterByStage(items) {
    // 全部：进行中项目（排除已中标，已中标仅在历史中标入口看，2026-08-07）
    if (S._activeStage === 'all') return (items || []).filter(i => F.getProjectStage(i) !== 'awarded');
    // 收藏夹：只显示已收藏项目（跨区县、不叠加其他筛选器，2026-08-06）
    if (S._activeStage === 'favorites') return F.favoriteItems('tender');
    // 代挂网：只显示已收藏的意向招标项目（2026-08-17）
    if (S._activeStage === 'fav-intention') return F.favoriteItems('intention');
    // 开标倒计时：独立判定（2026-08-17），与「招标公告」重叠不互斥
    if (S._activeStage === 'countdown') return (items || []).filter(i => F.isCountdown(i));
    return (items || []).filter(i => F.getProjectStage(i) === S._activeStage);
  }

export function filterBySites(items) {
    if (!S._sites || S._sites.length === 0) return items || [];
    if (S._sites.length === 1 && S._sites[0] === '__none__') return [];
    return (items || []).filter(i => S._sites.includes(i.site));
  }

  // —— 按发布日期范围筛选（2026-08-08）——

export function filterByDate(items) {
    if (!S._activeDate) return items || [];
    const { from, to } = S._activeDate;
    return (items || []).filter(i => {
      if (!i.date) return false;
      // date 为 YYYY-MM-DD 字符串，字典序可直接比较
      return i.date >= from && i.date <= to;
    });
  }

  // —— 按开标时间范围筛选（2026-08-08）——

export function filterByOpenDate(items) {
    if (!S._activeOpenDate) return items || [];
    const { from, to } = S._activeOpenDate;
    return (items || []).filter(i => {
      if (!i.bid_deadline) return false;
      const d = F.parseBidDeadline(i.bid_deadline);
      if (!d) return false;
      const ds = `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`;
      return ds >= from && ds <= to;
    });
  }

  // —— 按 ICT 筛选（2026-08-05 替代原“标签”下拉；2026-09-22 简化为 全部/ICT 两态）——

export function filterByIct(items) {
    if (!S._activeIct || S._activeIct.length === 0) return items || [];
    return (items || []).filter(i => S._activeIct.includes('ict') && i.isIct);
  }

  // —— 按政府/企业分类筛选（2026-08-07：按站点归属；2026-08-17 改多选）——

export function filterByType(items) {
    if (!S._activeType || S._activeType.length === 0) return items || [];
    return (items || []).filter(i => S._activeType.includes(i.siteCategory || ''));
  }

  // —— 按金额区间筛选（2026-08-05；2026-08-17 改多选）——

export function filterByAmount(items) {
    if (!S._activeAmount || S._activeAmount.length === 0) return items || [];
    return (items || []).filter(i => {
      const tags = i.tags || [];
      for (const band of S._activeAmount) {
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

export function filterBySme(items) {
    if (!S._activeSme || S._activeSme.length === 0) return items || [];
    return (items || []).filter(i => {
      const tags = i.tags || [];
      for (const smeTag of S._activeSme) {
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

export function filterByCat(items) {
    if (!S._activeCat || S._activeCat.length === 0) return items || [];
    return (items || []).filter(i => S._activeCat.includes(i.proj_major_cat || '未分类'));
  }

  // —— 按标题关键词搜索（2026-08-04 P2）——

export function filterBySearch(items) {
    if (!S._activeSearch) return items || [];
    const kw = S._activeSearch.toLowerCase();
    return (items || []).filter(i => {
      const title = (i.title || '').toLowerCase();
      const tenderer = (i.tenderer || '').toLowerCase();
      return title.includes(kw) || tenderer.includes(kw);
    });
  }

  // —— 按区县筛选（2026-08-04）——

export function filterByDistrict(items) {
    const d = K.DISTRICTS.find(x => x.key === S._activeDistrict);
    if (!d) return items || [];
    return (items || []).filter(i => d.match(i.std_district || '', i));
  }

  // ===== 多选下拉菜单（2026-08-04 重构）=====
  // 子标签 / 公告类型 / 监控网页 三个多选筛选共用一套逻辑

  // 渲染多选下拉（返回 HTML 字符串）
  // type: 'status'|'sites'；options: [{key,label,cnt}]；selected: 当前选中 key 数组

export function renderStageNav(counts) {
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
    const button = item => `<button class="tender-stage__btn ${S._activeStage === item.key ? 'tender-stage__btn--active' : ''}" data-stage="${item.key}" type="button">${item.label}<span class="tender-stage__count">${counts[item.key] || 0}</span></button>`;
    return `
      <div class="tender-stage" aria-label="项目进度">
        <div class="tender-stage__primary">${primary.map(button).join('')}</div>
        <div class="tender-stage__secondary">${secondary.map(button).join('')}</div>
      </div>
    `;
  }

export function renderStageNavCount() {
    // 只更新收藏夹计数，避免整页重排（2026-08-17：待投标 + 代挂网双计数）
    const favCnt = F.getFavorites('tender').length;
    const favIntCnt = F.getFavorites('intention').length;
    document.querySelectorAll('.tender-stage__btn[data-stage="favorites"] .tender-stage__count')
      .forEach(el => { el.textContent = favCnt; });
    document.querySelectorAll('.tender-stage__btn[data-stage="fav-intention"] .tender-stage__count')
      .forEach(el => { el.textContent = favIntCnt; });
  }

  // —— 开标时间显示：日期+小时，中文格式（2026-08-06）——

export function bindStageNav() {
    document.querySelectorAll('.tender-stage__btn').forEach(btn => {
      btn.addEventListener('click', () => {
        S._activeStage = btn.dataset.stage || 'all';
        S._currentPage = 1;
        F.render(S._data);
      });
    });
  }

  // —— 按监控网页多选筛选（2026-08-04）——

Object.assign(F, { filterByStage, filterBySites, filterByDate, filterByOpenDate, filterByIct, filterByType, filterByAmount, filterBySme, filterByCat, filterBySearch, filterByDistrict, renderStageNav, renderStageNavCount, bindStageNav });
