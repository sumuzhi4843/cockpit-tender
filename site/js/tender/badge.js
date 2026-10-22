// tender 板块域模块：badge（2026-09-29 由单文件 IIFE 机械拆分；函数体零逻辑改动，
// 仅状态→S.、常量→K.、跨模块调用→F. 三类机械前缀改写。真源在 tender/frontend/tender/。）
import { S } from './state.js';
import * as K from './constants.js';
import { F } from './registry.js';

export function renderFavBtn(key, bucket) {
    const b = bucket || 'tender';
    const label = K.FAV_LABELS[b] || '待投标';
    const on = F.isFavorite(key, b);
    return `<button class="tender-fav-btn ${on ? 'tender-fav-btn--on' : ''}" data-fav-key="${F.escapeHtml(key)}" data-fav-bucket="${b}" type="button" title="${on ? '取消' + label : '加入' + label}" aria-label="${on ? '取消' + label : '加入' + label}">${on ? '★' : '☆'}</button>`;
  }

  // 收藏夹视图：从 S._allItems 中取已收藏项目（按收藏时间倒序）
  // 代挂网桶只取意向招标项目（同名正式招标项目不串入，2026-08-17）

export function renderItemBadge(item) {
    if (item.isProject) {
      const cssType = item.project_stage === 'awarded' ? 'award'
        : item.project_stage === 'candidate' || item.project_stage === 'intention' ? 'intention'
        : ['failed', 'terminated', 'suspended', 'correction', 'contract', 'unknown'].includes(item.project_stage) ? 'other'
        : 'tender';
      // 已下架类状态（数据源明确发过终止/流标/暂停/废标公告 = 硬信号，2026-08-16）
      const gone = ['failed', 'terminated', 'suspended'].includes(item.project_stage);
      const goneCls = gone ? ' tender-type-badge--gone' : '';
      const clickable = gone ? ' data-stage-tip="1" style="cursor:pointer"' : '';
      return `<span class="tender-type-badge tender-type-badge--${cssType}${goneCls}"${clickable}>${K.PROJECT_STAGE_MAP[item.project_stage] || '项目'}</span>`;
    }
    return `<span class="tender-type-badge tender-type-badge--${item.notice_type || 'tender'}">${K.TYPE_MAP[item.notice_type] || '招标'}</span>`;
  }

  // —— 区县子菜单（2026-08-04）——
  // 每个区县对应 std_district 字段（Phase 5 元数据富化打标）
  // 按钮 label 缩写规则（2026-09-20）：去后缀（区/县/市），统一 2 字
  //   - 仅影响按钮显示名，不改 key、不改 match（筛选逻辑保持原样）
  //   - 未划分至区县 → 未划分

export function pickUpdateNotice(p) {
    const notices = p.notices || [];
    if (!notices.length) return null;
    const mainUrl = p.tender_url || '';
    const mains = notices.filter(n =>
      n.role === 'tender_primary' || n.role === 'tender_reopen' || n.notice_type === 'tender');
    const main = mainUrl
      ? (notices.find(n => n.url === mainUrl) || mains[0] || notices[0])
      : (mains[0] || null);
    if (!main) return null;               // 纯更新类项目（无招标基底）不在此标记
    const mainDate = main.date || '';
    const updates = notices.filter(n =>
      n !== main && n.notice_type !== 'tender' && (n.date || '') > mainDate);
    if (!updates.length) return null;
    // 取日期最新的一条；同日期取靠后的（数组已按时间升序）
    return updates[updates.length - 1];
  }

  // —— 渲染“有更新”角标（2026-09-06，方案 A：列表标注最新更正/合同等）——
  // 纯文字克制风格，与 NEW/ICT 标签一致；点击直达更新公告原文。

export function renderUpdateBadge(item) {
    const u = item && item.update_notice;
    if (!u || !u.url) return '';
    const subtype = u.notice_subtype || u.notice_type || '';
    const label = subtype === 'correction' ? '更正'
      : subtype === 'contract' ? '合同'
      : subtype === 'terminated' ? '终止'
      : subtype === 'suspended' ? '暂停'
      : subtype === 'failed' ? '流标'
      : (u.title || '').match(/(更正|澄清|合同|终止|暂停|废标|流标)/)?.[1] || '更新';
    const d = (u.date || '').slice(5); // YYYY-MM-DD → MM-DD
    return `<a class="tender-update-tag" href="${u.url}" target="_blank" rel="noopener" title="${F.escapeHtml(u.title || '')}" aria-label="查看更新公告">${F.escapeHtml(label)} ${F.escapeHtml(d)}</a>`;
  }


  // —— 收集所有 items（从 sites 展平），标记来源和分类 ——

export function renderConfidence(value) {
    const map = { manual: '人工确认', high: '高可信', medium: '可能关联', low: '待复核', standalone: '仅中标公告', none: '暂未匹配' };
    return `<span class="tender-confidence tender-confidence--${F.escapeHtml(value || 'none')}">${map[value] || '暂未匹配'}</span>`;
  }

  // —— 移动端卡片列表（≤768px 显示，2026-08-04 手机排版优化）——
  // 整行可点击展开详情；风格沿用现有 CSS 变量，不新增色系

export function getAwardInfo(item) {
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
    const link = S._awardLinkMap.get(F.normalizeProjectName(item.title));
    const original = link
      // 2026-10-15：lazy 模式 S._allItems 只是当前页，P4 缓存兑底（不在页内的项目从缓存取）
      ? (F.findProjectCached(link.tender_pid) || S._allItems.find(i => (i.notice_type || 'tender') === 'tender' && F.normalizeProjectName(i.title) === F.normalizeProjectName(link.tender_name)))
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

export function isCandidate(item) {
    // 候选公示：stage_reason 含"候选人"（2026-08-07 与历史中标合并后区分徽章）
    return (item.stage_reason || '').indexOf('候选人') >= 0;
  }

export function awardBadge(item) {
    return F.isCandidate(item)
      ? '<span class="tender-type-badge tender-type-badge--candidate">中标候选人</span>'
      : '<span class="tender-type-badge tender-type-badge--award">中标</span>';
  }

Object.assign(F, { renderFavBtn, renderItemBadge, pickUpdateNotice, renderUpdateBadge, renderConfidence, getAwardInfo, isCandidate, awardBadge });
