// tender 板块域模块：api（2026-09-29 由单文件 IIFE 机械拆分；函数体零逻辑改动，
// 仅状态→S.、常量→K.、跨模块调用→F. 三类机械前缀改写。真源在 tender/frontend/tender/。）
import { S } from './state.js';
import * as K from './constants.js';
import { F } from './registry.js';

export function cacheProject(item) {
    if (item && item.id) S._projectCache.set(item.id, item);
  }

export function cacheProjectAll(items) {
    (items || []).forEach(F.cacheProject);
  }

export function findProjectCached(pid) {
    return S._projectCache.get(pid) || null;
  }

export async function fetchProjectById(pid) {
    const hit = F.findProjectCached(pid);
    if (hit) return hit;
    try {
      const res = await fetch('/api/tender/projects/' + pid, { headers: F.apiHeaders() });
      if (!res.ok) return null;
      const item = F.lazyDecorateItem(F._absorbMeta(await res.json()));
      F.cacheProject(item);
      return item;
    } catch (e) {
      return null;
    }
  }

  // 前端导航入口 → API stage 参数（与后端 _STAGE_GROUPS 同口径）

export function navStageToApi(key) {
    // 'all'/'countdown' 等特殊入口：countdown 是独立倒计时（前端判定），查询时退回 upcoming 组
    return key === 'countdown' ? 'upcoming' : key;
  }

  // —— S._queryState → URL 查询串 ——

export function buildQueryParams(qs, page) {
    const p = new URLSearchParams();
    p.set('page', page || qs.page || 1);
    p.set('size', 40);
    const stage = F.navStageToApi(qs.stage);
    if (stage && stage !== 'all') p.set('stage', stage);
    if (qs.district) p.set('district', qs.district);   // 可为逗号多值（盐开区=盐南,经开）或 none
    if (qs.cat && qs.cat.length) p.set('cat', qs.cat.join(','));
    if (qs.ict && qs.ict.length) p.set('ict', qs.ict.includes('ict') ? '1' : '0');
    if (qs.sme && qs.sme.length) p.set('sme', qs.sme.map(t => t.replace('中小微-', '')).join(','));
    if (qs.type && qs.type.length) p.set('type', qs.type.join(','));
    if (qs.site && qs.site.length) p.set('site', qs.site.join(','));
    if (qs.amount && qs.amount.length) p.set('amount', qs.amount.join(','));
    if (qs.date && qs.date.from) p.set('date_from', qs.date.from);
    if (qs.date && qs.date.to) p.set('date_to', qs.date.to);
    if (qs.openDate && qs.openDate.from) p.set('open_from', qs.openDate.from);
    if (qs.openDate && qs.openDate.to) p.set('open_to', qs.openDate.to);
    if (qs.q) p.set('q', qs.q);
    if (qs.sort && qs.sort !== 'latest') p.set('sort', qs.sort);
    return p.toString();
  }

  // —— 调 /api/tender/projects：返回 {total, page, size, items} ——
  // items 字段已由后端对齐 F.collectAllItems 的 item shape；此处只补前端特有派生字段。
  // 2026-10-15：stale 期响应附 _meta，调用方据此展示 banner；blocked 期抛 MaintenanceError。

export async function queryProjects(overrides) {
    const qs = overrides ? Object.assign({}, S._queryState, overrides) : S._queryState;
    const qsStr = F.buildQueryParams(qs);
    const res = await fetch('/api/tender/projects?' + qsStr, {
      headers: F.apiHeaders(),
    });
    if (res.status === 503) {
      const err = await res.json().catch(() => ({}));
      F._absorbMeta(err);
      const e = new Error('projects blocked: ' + (err.phase || ''));
      e.name = 'MaintenanceError';
      e.dataState = 'blocked';
      e.phase = err.phase || '';
      throw e;
    }
    if (!res.ok) throw new Error('projects ' + res.status);
    const d = F._absorbMeta(await res.json());
    d.items = (d.items || []).map(it => F.lazyDecorateItem(it));
    return d;
  }

  // —— 给 API item 补齐前端派生字段（与 F.collectAllItems 输出 shape 一致）——

export function lazyDecorateItem(it) {
    // 区县修订覆盖（后端 detail 已带，列表批量不带 → 前端查 S._districtOverrides）
    if (S._districtOverrides[it.id] != null) {
      it.std_district = S._districtOverrides[it.id];
      it.district_overridden = true;
    }
    it.district_label = F.districtLabel(it.std_district || '');
    it.isIct = it.ict === 1 || it.isIct === true;
    it.isProject = true;
    it.keywords = it.keywords || [];
    it.tags = it.tags || [];
    it.is_new = false; // is_new 由 meta.new_ids 批量置位（P3 对齐）
    it.amount = it.budget ? F.formatMoney(it.budget) : '';
    it.notice_type = it.stage === 'awarded' || it.stage === 'candidate' ? 'award'
      : it.stage === 'intention' ? 'intention'
      : ['failed', 'terminated', 'suspended', 'correction', 'contract', 'unknown'].includes(it.stage) ? 'other'
      : 'tender';
    return it;
  }

  // —— 调 /api/tender/meta：首屏元数据 + 计数（替代内存遍历计数）——
  // 2026-10-15：stale 期响应附 _meta；blocked 期抛 MaintenanceError。

export async function fetchMeta(force) {
    if (S._serverMeta && !force) return S._serverMeta;
    const res = await fetch('/api/tender/meta', { headers: F.apiHeaders() });
    if (res.status === 503) {
      const err = await res.json().catch(() => ({}));
      F._absorbMeta(err);
      const e = new Error('meta blocked: ' + (err.phase || ''));
      e.name = 'MaintenanceError';
      e.dataState = 'blocked';
      e.phase = err.phase || '';
      throw e;
    }
    if (!res.ok) throw new Error('meta ' + res.status);
    S._serverMeta = F._absorbMeta(await res.json());
    return S._serverMeta;
  }

  // —— 调 /api/tender/updates：列表页更新角标批量数据 ——

export async function fetchUpdates(ids) {
    if (!ids || !ids.length) return {};
    const res = await fetch('/api/tender/updates?ids=' + ids.join(','), { headers: F.apiHeaders() });
    if (!res.ok) return { updates: {} };
    const j = F._absorbMeta(await res.json());
    return (j && j.updates) || {};
  }

export async function fetchIctDist() {
    F.syncQueryState();
    const p = new URLSearchParams(F.buildQueryParams(S._queryState, 1));
    p.delete('page');
    p.delete('size');
    p.delete('cat');
    p.set('ict', '1');
    const res = await fetch('/api/tender/ict_dist?' + p.toString(), { headers: F.apiHeaders() });
    if (!res.ok) throw new Error('ict_dist ' + res.status);
    return F._absorbMeta(await res.json());
  }

  // —— 吸走 _meta 段、回写 S._dataState*、返回不含 _meta 的对象（2026-10-15）——
  // 后端 stale 期会在 JSON 响应里塞 _meta={data_state, phase, updated_at, last_published_at}。
  // 这里把 _meta 摘出来写进 S 全局、避免污染业务字段。
  // 兼容 503 响应：error+data_state 顶层结构也走同一通道（_absorbMeta 一样把 data_state 写进 S）。
export function _absorbMeta(j) {
    if (j && typeof j === 'object') {
      const m = j._meta || (j.data_state ? j : null);
      if (m) {
        S._dataState = m.data_state || 'ok';
        S._dataPhase = m.phase || '';
        S._dataLastPublishedAt = m.last_published_at || '';
        S._dataStateUpdatedAt = m.updated_at || '';
      } else {
        S._dataState = 'ok';
        S._dataPhase = '';
        S._dataLastPublishedAt = '';
        S._dataStateUpdatedAt = '';
      }
      if (j._meta) {
        const out = { ...j };
        delete out._meta;
        return out;
      }
    }
    return j;
  }

  // ══════════════ 懒加载数据层结束 ══════════════

  // ══════════════ 懒加载渲染主链路（2026-09-07 P3）══════════════
  // 把 _activeXxx 交互状态同步进 S._queryState → F.queryProjects → 复用现有渲染函数。
  // 计数来自 /meta（全局口径）；列表/分页用 API 返回（服务端已排序+切片）。

export function syncQueryState() {
    S._queryState.stage = S._activeStage || 'all';
    // 2026-09-07：区县 key → 后端 district 值（K.DISTRICTS 新增 district 字段；
    // 盐开区='盐南,经开' 多值、unassigned='none'、all=''不传）
    const dDef = K.DISTRICTS.find(d => d.key === (S._activeDistrict || 'all'));
    S._queryState.district = dDef ? (dDef.district || '') : (S._activeDistrict || '');
    S._queryState.cat = S._activeCat || [];
    S._queryState.ict = S._activeIct || [];
    S._queryState.sme = S._activeSme || [];
    S._queryState.amount = S._activeAmount || [];
    S._queryState.type = S._activeType || [];
    S._queryState.site = S._sites || [];
    S._queryState.date = S._activeDate;
    S._queryState.openDate = S._activeOpenDate;
    S._queryState.q = S._activeSearch || '';
    S._queryState.page = S._currentPage || 1;
  }

export function collectAllItems(data) {
    if (data.schema_version >= 2 && Array.isArray(data.projects)) {
      return data.projects.map(p => {
        // 人工修订优先：overrides[project_id] 覆盖自动打标（2026-08-16）
        const rawDistrict = S._districtOverrides[p.id] != null ? S._districtOverrides[p.id] : (p.district || '');
        return {
          id: p.id,
          title: p.name,
          url: p.award_url || p.tender_url || ((p.notices || []).at(-1) || {}).url || '',
          date: p.latest_notice_date,
          tenderer: p.purchaser,
          amount: p.budget ? F.formatMoney(p.budget) : '',
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
          district_label: F.districtLabel(rawDistrict),
          district_overridden: Object.prototype.hasOwnProperty.call(S._districtOverrides, p.id),
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

export async function loadDistrictOverrides() {
    try {
      const res = await fetch('/api/tender/district_overrides', { cache: 'no-cache', headers: F.apiHeaders() });
      if (res.ok) {
        const data = await res.json();
        S._districtOverrides = data || {};
        return true;
      }
      console.warn('区县修订加载失败:', res.status);
      S._districtOverrides = {};
      return false;
    } catch (e) {
      console.warn('区县修订加载失败:', e);
      S._districtOverrides = {};
      return false;
    }
  }

Object.assign(F, { cacheProject, cacheProjectAll, findProjectCached, fetchProjectById, navStageToApi, buildQueryParams, queryProjects, lazyDecorateItem, fetchMeta, fetchUpdates, fetchIctDist, syncQueryState, collectAllItems, loadDistrictOverrides, _absorbMeta });
