// tender 板块域模块：modals（2026-09-29 由单文件 IIFE 机械拆分；函数体零逻辑改动，
// 仅状态→S.、常量→K.、跨模块调用→F. 三类机械前缀改写。真源在 tender/frontend/tender/。）
import { S } from './state.js';
import * as K from './constants.js';
import { F } from './registry.js';

export function closeSites() {
    const m = document.getElementById('tender-sites-modal');
    if (m) m.remove();
  }

  // —— ICT 项目行业分布饼图弹窗（2026-08-31：点击顶部「ICT 相关」卡片）——
  // 数据：当前区县筛选下的 ICT 项目（S._districtItems），按行业 proj_major_cat 分组
  // 展示：Top 7 行业 + 其余合并「其他行业」+ 「未分类」

export function closeIctPie() {
    const m = document.getElementById('tender-ict-modal');
    if (m) m.remove();
  }

  // SVG 饼图：根据分组数量生成扇区 path（起点 12 点方向顺时针）

export function buildPieSvg(slices, total, size) {
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

export function buildIctSlices(ictItems) {
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
    const top = entries.slice(0, K.ICT_CAT_TOP_N);
    const restCount = entries.slice(K.ICT_CAT_TOP_N).reduce((s, e) => s + e[1], 0);
    const slices = top.map(([label, count], idx) => ({
      label, count, color: K.ICT_CAT_PALETTE[idx % K.ICT_CAT_PALETTE.length],
    }));
    if (restCount > 0) {
      slices.push({ label: '其他行业', count: restCount, color: '#94a3b8' });
    }
    if (uncat > 0) {
      slices.push({ label: '未分类', count: uncat, color: '#cbd5e1' });
    }
    return slices.filter(s => s.count > 0);
  }

export function buildIctSlicesFromCounts(counts) {
    const copy = Object.assign({}, counts || {});
    const uncat = copy['未分类'] || 0;
    delete copy['未分类'];
    const entries = Object.entries(copy).sort((a, b) => b[1] - a[1]);
    const slices = entries.slice(0, K.ICT_CAT_TOP_N).map(([label, count], idx) => ({
      label, count, color: K.ICT_CAT_PALETTE[idx % K.ICT_CAT_PALETTE.length],
    }));
    const rest = entries.slice(K.ICT_CAT_TOP_N).reduce((sum, item) => sum + item[1], 0);
    if (rest > 0) slices.push({ label: '其他行业', count: rest, color: '#94a3b8' });
    if (uncat > 0) slices.push({ label: '未分类', count: uncat, color: '#cbd5e1' });
    return slices;
  }

export async function showIctPie() {
    let modal = document.getElementById('tender-ict-modal');
    if (modal) { F.closeIctPie(); return; } // 已存在则关闭

    // 区县标签
    const activeDistrict = (K.DISTRICTS.find(d => d.key === S._activeDistrict) || {}).label || '全部区县';
    modal = document.createElement('div');
    modal.id = 'tender-ict-modal';
    modal.className = 'tender-modal';
    modal.innerHTML = `<div class="tender-modal__backdrop"></div><div class="tender-modal__dialog tender-ict-modal__dialog" role="dialog" aria-modal="true"><div class="tender-modal__head"><div class="tender-modal__title">ICT 项目行业分布</div><button class="tender-modal__close" type="button" aria-label="关闭 ICT 分布">✕</button></div><div class="tender-modal__body"><div class="loading">加载中…</div></div></div>`;
    document.body.appendChild(modal);
    const close = () => F.closeIctPie();
    modal.querySelector('.tender-modal__close').addEventListener('click', close);
    modal.querySelector('.tender-modal__backdrop').addEventListener('click', close);
    modal.addEventListener('keydown', e => { if (e.key === 'Escape') close(); });

    let dist;
    try {
      dist = await F.fetchIctDist();
    } catch (e) {
      modal.querySelector('.tender-modal__body').innerHTML = '<div class="tender-ict-pie__empty">ICT 分布暂时无法加载，请稍后重试</div>';
      return;
    }
    if (!document.body.contains(modal)) return;
    const cats = dist.cats || {};
    const total = dist.total || 0;
    const slices = F.buildIctSlicesFromCounts(cats);

    // 图例：色块 + 标签 + 数量 + 占比
    const legendHtml = slices.map(s => {
      const pct = total > 0 ? (s.count / total * 100).toFixed(1) : 0;
      return `
        <div class="tender-ict-pie__legend-item">
          <span class="tender-ict-pie__dot" style="background:${s.color}"></span>
          <span class="tender-ict-pie__legend-label">${F.escapeHtml(s.label)}</span>
          <span class="tender-ict-pie__legend-num">${s.count}</span>
          <span class="tender-ict-pie__legend-pct">${pct}%</span>
        </div>`;
    }).join('');

    // 弹窗内容：空态 or 全库饼图 + 图例
    let bodyHtml;
    if (total === 0) {
      bodyHtml = `<div class="tender-ict-pie__empty">当前区县（${F.escapeHtml(activeDistrict)}）暂无 ICT 项目</div>`;
    } else {
      bodyHtml = `
        <div class="tender-ict-pie__wrap">
          <div class="tender-ict-pie__chart">${F.buildPieSvg(slices, total, 240)}</div>
          <div class="tender-ict-pie__legend">${legendHtml}</div>
        </div>
        <div class="tender-ict-pie__summary">共 ${total} 个 ICT 项目 · 行业分布 · 区县：${F.escapeHtml(activeDistrict)}（Top ${Math.min(K.ICT_CAT_TOP_N, slices.length)} 行业 + 其他 + 未分类）</div>`;
    }

    modal.querySelector('.tender-modal__body').innerHTML = bodyHtml;
    modal.querySelector('.tender-modal__close')?.focus();
  }

export function showSites() {
    let modal = document.getElementById('tender-sites-modal');
    if (modal) { F.closeSites(); return; } // 已存在则关闭

    const sites = S._sitesData || {};
    // 排序：政府在前、企业在后，组内保持原始顺序（2026-08-07）
    const sortedEntries = Object.entries(sites)
      .map(([name, info]) => ({ name, info, order: info.category === '政府' ? 0 : 1 }))
      .sort((a, b) => a.order - b.order);
    const rows = sortedEntries.map(({ name, info }, idx) => {
      const total = info.total != null ? info.total : (info.items || []).length;
      const ict = info.ict != null ? info.ict : (info.ict_items || []).length;
      const method = K.SCRAPE_METHOD_LABELS[info.scrape_method] || info.scrape_method || '—';
      const retention = K.RETENTION_LABELS[info.retention] || info.retention || '—';
      const cat = info.category || '';
      const catHtml = cat
        ? `<span class="tender-sites-modal__cat tender-sites-modal__cat--${cat === '政府' ? 'gov' : 'ent'}">${F.escapeHtml(cat)}</span>`
        : '<span class="tender-sites-modal__muted">—</span>';
      // 2026-08-10：平台名称软链接（跳转招投标网站）
      const siteUrl = info.url || '';
      const nameHtml = siteUrl
        ? `<a class="tender-sites-modal__link" href="${F.escapeHtml(siteUrl)}" target="_blank" rel="noopener" title="${F.escapeHtml(siteUrl)}">${F.escapeHtml(name)}<span class="tender-sites-modal__ext">↗</span></a>`
        : `<span class="tender-sites-modal__name">${F.escapeHtml(name)}</span>`;
      // 2026-08-12：当月采集数 + 数据起止（最早 ~ 最新公告日期，title 附最近抓取时间）
      const monthly = info.monthly_count || 0;
      const start = info.data_start || '—';
      const until = info.data_until || '—';
      const rangeHtml = info.last_seen
        ? `<span class="tender-sites-modal__range" title="最早 ~ 最新公告日期；最近抓取：${F.escapeHtml(info.last_seen)}">${F.escapeHtml(start)} ~ ${F.escapeHtml(until)}</span>`
        : `${F.escapeHtml(start)} ~ ${F.escapeHtml(until)}`;
      const retentionTitle = K.RETENTION_TITLES[info.retention] || '';
      return `
        <tr>
          <td class="tender-sites-modal__idx">${idx + 1}</td>
          <td class="tender-sites-modal__catcell">${catHtml}</td>
          <td class="tender-sites-modal__name">${nameHtml}</td>
          <td><span class="tender-sites-modal__num">${total}</span></td>
          <td><span class="tender-sites-modal__num ${ict > 0 ? 'tender-sites-modal__num--ict' : 'tender-sites-modal__num--zero'}">${ict}</span></td>
          <td><span class="tender-sites-modal__num ${monthly > 0 ? '' : 'tender-sites-modal__num--zero'}">${monthly}</span></td>
          <td class="tender-sites-modal__untilcell">${rangeHtml}</td>
          <td><span class="tender-sites-modal__tag tender-sites-modal__tag--method">${F.escapeHtml(method)}</span></td>
          <td>${retentionTitle
            ? `<span class="tender-sites-modal__tag tender-sites-modal__tag--retention" title="${F.escapeHtml(retentionTitle)}">${F.escapeHtml(retention)}</span>`
            : `<span class="tender-sites-modal__tag tender-sites-modal__tag--retention">${F.escapeHtml(retention)}</span>`}</td>
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
        </div>
      </div>
    `;
    document.body.appendChild(modal);
    modal.querySelector('.tender-modal__close')?.focus();
    modal.addEventListener('keydown', (event) => {
      if (event.key === 'Escape') F.closeSites();
    });
  }

// —— 维护期 banner（2026-10-15 stale 软提示版）——
// stale 期间：DB 可读但有数据延迟（collect/backfill/audit/screen/daily/starting）
// blocked 期间：DB 不可读（rebuild/verify/publish）
// 老板拍板：抓取不打扰、改数据短暂提示、跑完自动消失。

const PHASE_LABELS = {
  collect: '主抓中',
  backfill: '补抓中',
  daily: '日报生成中',
  audit: '审计中',
  screen: '大屏导出中',
  starting: '启动中',
  rebuild: '重建项目',
  verify: '质量校验',
  publish: '发布中',
};

export function showMaintenanceBanner(state, phase) {
  let banner = document.getElementById('tender-maintenance-banner');
  if (!banner) {
    banner = document.createElement('div');
    banner.id = 'tender-maintenance-banner';
    banner.setAttribute('role', 'status');
    banner.setAttribute('aria-live', 'polite');
    const icon = document.createElement('span');
    icon.className = 'tender-banner__icon';
    const text = document.createElement('span');
    text.className = 'tender-banner__text';
    const close = document.createElement('button');
    close.className = 'tender-banner__close';
    close.type = 'button';
    close.setAttribute('aria-label', '关闭');
    close.textContent = '×';
    close.onclick = (e) => { e.preventDefault(); F.hideMaintenanceBanner(); };
    banner.appendChild(icon);
    banner.appendChild(text);
    banner.appendChild(close);
    // 插入到 tender-body 上方（不抢主区域，30s 后会自消失）
    const body = document.getElementById('tender-body') || document.body;
    body.parentNode.insertBefore(banner, body);
  }
  const icon = banner.querySelector('.tender-banner__icon');
  const text = banner.querySelector('.tender-banner__text');
  const phaseLabel = PHASE_LABELS[phase] || phase || '';
  if (state === 'blocked') {
    banner.className = 'tender-banner tender-banner--warn';
    icon.textContent = '🛠';
    text.textContent = `数据更新中（${phaseLabel}，约 1-2 分钟），已展示上一版数据`;
  } else if (state === 'stale') {
    banner.className = 'tender-banner tender-banner--info';
    icon.textContent = '🔄';
    text.textContent = `数据采集中（${phaseLabel}），最新已发布版本已展示`;
  } else {
    F.hideMaintenanceBanner();
    return;
  }
  banner.style.display = 'flex';
}

export function hideMaintenanceBanner() {
  const banner = document.getElementById('tender-maintenance-banner');
  if (banner) banner.style.display = 'none';
}

// 30s 轮询 /api/tender/state：stale/blocked 结束自动隐藏 banner（老板要“跑完自动消失”）
export async function pollDataState() {
  try {
    const res = await fetch('/api/tender/state', { headers: F.apiHeaders() });
    if (!res.ok) return null;
    const j = await res.json();
    S._dataState = j.data_state || 'ok';
    S._dataPhase = j.phase || '';
    S._dataLastPublishedAt = j.last_published_at || '';
    S._dataStateUpdatedAt = j.updated_at || '';
    return j;
  } catch (e) {
    return null;
  }
}

export function startStalePoll() {
  if (S._stalePollTimer) return;
  S._stalePollTimer = setInterval(async () => {
    const j = await F.pollDataState();
    if (!j) return;
    if (j.data_state === 'ok') {
      F.hideMaintenanceBanner();
      F.stopStalePoll();
    } else {
      F.showMaintenanceBanner(j.data_state, j.phase);
    }
  }, 30000);
}

export function stopStalePoll() {
  if (S._stalePollTimer) {
    clearInterval(S._stalePollTimer);
    S._stalePollTimer = null;
  }
}

Object.assign(F, { closeSites, closeIctPie, buildPieSvg, buildIctSlices, buildIctSlicesFromCounts, showIctPie, showSites, showMaintenanceBanner, hideMaintenanceBanner, pollDataState, startStalePoll, stopStalePoll });
