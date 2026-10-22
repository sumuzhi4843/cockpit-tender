// -*- coding: utf-8 -*-
// 运营商中标动态栏 v1（2026-08-13 框架搭建；08-13 由“项目经验信息栏”更名）
// 数据源：企查查 MCP（qcc-tender）→ experience.json
// 定位：江苏省除盐城外其他地市运营商中标信息
// 数据结构：{ meta: {generated_at, scope, total}, items: [{...}] }

(function () {

  const PAGE_SIZE = 20;

  let _data = null;
  let _root = null;
  let _list = [];
  let _filtered = [];
  let _page = 1;
  let _view = 'list';   // 'list' | 'graph'（2026-08-13 关系图视图）
  let _fCity = 'all', _fOperator = 'all', _fSearch = '';

  // —— 工具 ——
  function fmt(n) {
    if (n == null || n === '') return '—';
    const v = Number(n);
    return isNaN(v) ? n : v.toLocaleString('zh-CN');
  }
  function esc(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;')
      .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }
  function money(v) {
    if (!v && v !== 0) return '—';
    const n = Number(v);
    if (!n) return '—';
    if (n >= 10000) return (n / 10000).toFixed(2) + ' 万元';
    return n.toLocaleString('zh-CN') + ' 元';
  }

  // —— 汇总卡 ——
  function renderSummary() {
    const meta = (_data && _data.meta) || {};
    const total = _list.length;
    const cities = new Set(_list.map(i => i.city).filter(Boolean));
    const sumAmount = _list.reduce((s, i) => s + (Number(i.amount) || 0), 0);

    return `
    <div class="exp-head">
      <div class="exp-head__brand">
        <span class="exp-head__icon">📋</span>
        <div>
          <div class="exp-head__title">运营商中标动态</div>
          <div class="exp-head__sub">${esc(meta.scope || '江苏省（除盐城外）运营商中标信息')} · 数据来源：企查查 MCP</div>
        </div>
      </div>
      <div class="exp-head__meta">
        <span>更新 ${esc(meta.generated_at || '—')}</span>
      </div>
    </div>

    <div class="exp-kpi">
      <div class="exp-kpi__card exp-kpi__card--primary"><span class="exp-kpi__label">项目总数</span><span class="exp-kpi__value">${fmt(total)}</span><span class="exp-kpi__sub">条中标信息</span></div>
      <div class="exp-kpi__card"><span class="exp-kpi__label">覆盖地市</span><span class="exp-kpi__value">${fmt(cities.size)}</span><span class="exp-kpi__sub">江苏省（除盐城）</span></div>
      <div class="exp-kpi__card"><span class="exp-kpi__label">中标金额合计</span><span class="exp-kpi__value">${money(sumAmount)}</span><span class="exp-kpi__sub">已收录</span></div>
      <div class="exp-kpi__card"><span class="exp-kpi__label">数据状态</span><span class="exp-kpi__value">${total ? '正常' : '空'}</span><span class="exp-kpi__sub">${total ? '可查看明细' : '待采集数据'}</span></div>
    </div>`;
  }

  // —— 中标明细弹窗（2026-08-13：点击项目行查看详情）——
  function showDetail(it) {
    const modal = document.createElement('div');
    modal.className = 'exp-modal';
    modal.innerHTML = `
      <div class="exp-modal__backdrop"></div>
      <div class="exp-modal__panel" role="dialog" aria-label="中标明细">
        <button type="button" class="exp-modal__close" aria-label="关闭">✕</button>
        <div class="exp-detail">
          <div class="exp-detail__tags">
            <span class="exp-detail__tag exp-detail__tag--op">${esc(it.operator || '—')}</span>
            <span class="exp-detail__tag">${esc(it.city || '—')}</span>
            ${it.industry ? `<span class="exp-detail__tag exp-detail__tag--ind">${esc(it.industry)}</span>` : ''}
          </div>
          <h3 class="exp-detail__title">${esc(it.title)}</h3>
          ${row('招采单位', it.procurement_unit)}
          ${row('中标单位', it.winner)}
          ${row('中标金额', money(it.amount))}
          ${row('公告日期', it.date)}
          ${row('运营商', it.operator)}
          ${row('地市', it.city)}
          ${row('行业', it.industry)}
          ${row('标讯ID', it.bid_id)}
        </div>
      </div>`;
    document.body.appendChild(modal);
    const close = () => { modal.remove(); document.removeEventListener('keydown', onKey); };
    const onKey = (ev) => { if (ev.key === 'Escape') close(); };
    modal.querySelector('.exp-modal__close').addEventListener('click', close);
    modal.querySelector('.exp-modal__backdrop').addEventListener('click', close);
    document.addEventListener('keydown', onKey);
    modal.querySelector('.exp-modal__close').focus();
  }

  function row(k, v) {
    return v ? `<div class="exp-detail__row"><span class="exp-detail__k">${k}</span><span class="exp-detail__v">${esc(v)}</span></div>` : '';
  }

  // —— 列表 ——
  function renderList() {
    if (!_list.length) {
      return `<div class="exp-empty">
        <div class="exp-empty__icon">📭</div>
        <div class="exp-empty__title">暂无中标动态数据</div>
        <div class="exp-empty__hint">板块框架已就绪，等待企查查 MCP 采集数据后填充。</div>
      </div>`;
    }

    const cities = [...new Set(_list.map(i => i.city).filter(Boolean))];
    const operators = [...new Set(_list.map(i => i.operator).filter(Boolean))];

    const pages = Math.max(1, Math.ceil(_filtered.length / PAGE_SIZE));
    if (_page > pages) _page = pages;
    const slice = _filtered.slice((_page - 1) * PAGE_SIZE, _page * PAGE_SIZE);

    const rows = slice.map((it, i) => `
      <tr class="exp-row" data-expidx="${(_page - 1) * PAGE_SIZE + i}">
        <td class="exp-tbl__idx">${(_page - 1) * PAGE_SIZE + i + 1}</td>
        <td class="exp-tbl__name" title="${esc(it.title)}">${esc(it.title)}</td>
        <td>${esc(it.operator) || '—'}</td>
        <td>${esc(it.city) || '—'}</td>
        <td>${esc(it.winner) || '—'}</td>
        <td class="exp-tbl__num">${money(it.amount)}</td>
        <td>${esc(it.date) || '—'}</td>
        <td>${esc(it.industry) || '—'}</td>
      </tr>`).join('');

    return `
    <div class="exp-filters">
      <select class="exp-select" id="exp-f-city">
        <option value="all">全部地市</option>
        ${cities.map(v => `<option value="${esc(v)}" ${_fCity === v ? 'selected' : ''}>${esc(v)}</option>`).join('')}
      </select>
      <select class="exp-select" id="exp-f-operator">
        <option value="all">全部运营商</option>
        ${operators.map(v => `<option value="${esc(v)}" ${_fOperator === v ? 'selected' : ''}>${esc(v)}</option>`).join('')}
      </select>
      <div class="exp-search-wrap">
        <input class="exp-search" id="exp-f-search" type="search" placeholder="搜索项目 / 中标单位…" value="${esc(_fSearch)}">
      </div>
      ${_fCity !== 'all' || _fOperator !== 'all' || _fSearch ? '<button type="button" class="exp-clear" id="exp-f-clear">清空筛选</button>' : ''}
    </div>
    <div class="exp-list-meta">共 <b>${fmt(_filtered.length)}</b> 条 · 第 ${_page}/${pages} 页</div>
    <div class="exp-tbl-wrap">
      <table class="exp-tbl">
        <thead><tr><th>序号</th><th>项目名称</th><th>运营商</th><th>地市</th><th>中标单位</th><th>中标金额</th><th>公告日期</th><th>行业</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
    </div>
    ${_filtered.length === 0 ? '<div class="exp-empty">没有符合条件的数据</div>' : ''}
    <div class="exp-pager">
      <button type="button" class="exp-pager__btn" id="exp-page-prev" ${_page <= 1 ? 'disabled' : ''}>‹ 上一页</button>
      <span class="exp-pager__info">第 ${_page} / ${pages} 页</span>
      <button type="button" class="exp-pager__btn" id="exp-page-next" ${_page >= pages ? 'disabled' : ''}>下一页 ›</button>
    </div>`;
  }

  // —— 关系图（2026-08-13：项目 ↔ 招采单位/中标单位/运营商/地市 关联知识图）——
  function renderGraph() {
    if (!_filtered.length) {
      return `<div class="exp-empty">没有可展示的数据</div>`;
    }
    // 限制项目量：最多前 60 个，避免图过密
    const items = _filtered.slice(0, 60);
    const nodes = [];
    const links = [];
    const nodeMap = {};   // name → index（按 name 去重，与 echarts 内部一致）
    const cat = { project: 0, buyer: 1, winner: 2, operator: 3, city: 4 };

    const addNode = (name, c) => {
      if (!name) return null;
      if (!(name in nodeMap)) {
        nodeMap[name] = nodes.length;
        nodes.push({ name: String(name), category: c, symbolSize: c === 0 ? 26 : 16 });
      }
      return nodeMap[name];
    };

    items.forEach((it) => {
      // 项目名可能重复 → 加序号后缀保证唯一
      const pName = it.title + ' [' + it.date + ']';
      const pIdx = addNode(pName, cat.project);
      const bIdx = addNode(it.procurement_unit, cat.buyer);
      const wIdx = addNode(it.winner, cat.winner);
      const oIdx = addNode(it.operator, cat.operator);
      const cIdx = addNode(it.city, cat.city);
      if (pIdx != null && bIdx != null) links.push({ source: pIdx, target: bIdx });
      if (pIdx != null && wIdx != null) links.push({ source: pIdx, target: wIdx });
      if (pIdx != null && oIdx != null) links.push({ source: pIdx, target: oIdx });
      if (pIdx != null && cIdx != null) links.push({ source: pIdx, target: cIdx });
    });

    const box = document.getElementById('exp-graph');
    if (!box) return '';
    const chart = echarts.init(box);
    chart.setOption({
      tooltip: {},
      legend: [{ data: ['项目', '招采单位', '中标单位', '运营商', '地市'], top: 0 }],
      series: [{
        type: 'graph',
        layout: 'force',
        roam: true,
        draggable: true,
        force: { repulsion: 120, edgeLength: [60, 120], gravity: 0.1 },
        label: { show: true, fontSize: 10, overflow: 'truncate', width: 80 },
        categories: [
          { name: '项目', itemStyle: { color: '#1d6fe0' } },
          { name: '招采单位', itemStyle: { color: '#e67e22' } },
          { name: '中标单位', itemStyle: { color: '#27ae60' } },
          { name: '运营商', itemStyle: { color: '#8e44ad' } },
          { name: '地市', itemStyle: { color: '#95a5a6' } },
        ],
        data: nodes,
        links: links,
        lineStyle: { color: '#b7c4d6', curveness: 0.1, width: 1 },
        emphasis: { focus: 'adjacency', lineStyle: { width: 3, color: '#1d6fe0' } },
      }],
    });
    // 窗口变化重排
    window.addEventListener('resize', () => chart.resize());
    return '';
  }

  function applyFilters() {
    if (!_list.length) { _filtered = []; return; }
    const kw = _fSearch.trim().toLowerCase();
    _filtered = _list.filter(it => {
      if (_fCity !== 'all' && it.city !== _fCity) return false;
      if (_fOperator !== 'all' && it.operator !== _fOperator) return false;
      if (kw) {
        const hay = (it.title + '|' + it.winner + '|' + it.operator).toLowerCase();
        if (!hay.includes(kw)) return false;
      }
      return true;
    });
    _page = 1;
  }

  function rerender() {
    if (_view === 'list') {
      const box = document.getElementById('exp-view');
      if (box) box.innerHTML = renderList();
    } else {
      const graphBox = document.getElementById('exp-graph');
      if (graphBox) { graphBox.innerHTML = ''; renderGraph(); }
    }
  }

  function bind() {
    const root = _root;
    if (!root) return;
    root.querySelectorAll('.exp-subnav__btn').forEach(b => {
      b.addEventListener('click', () => switchView(b.dataset.expview));
    });
    root.addEventListener('change', (e) => {
      if (e.target.id === 'exp-f-city') { _fCity = e.target.value; applyFilters(); rerender(); }
      else if (e.target.id === 'exp-f-operator') { _fOperator = e.target.value; applyFilters(); rerender(); }
    });
    root.addEventListener('input', (e) => {
      if (e.target.id === 'exp-f-search') {
        clearTimeout(_searchTimer);
        _searchTimer = setTimeout(() => { _fSearch = e.target.value; applyFilters(); rerender(); }, 300);
      }
    });
    root.addEventListener('click', (e) => {
      if (e.target.closest('#exp-page-prev')) { _page--; rerender(); return; }
      if (e.target.closest('#exp-page-next')) { _page++; rerender(); return; }
      if (e.target.closest('#exp-f-clear')) { _fCity = _fOperator = 'all'; _fSearch = ''; applyFilters(); rerender(); return; }
      const row = e.target.closest('.exp-row');
      if (row) { showDetail(_filtered[+row.dataset.expidx]); return; }
    });
  }

  // —— 模块入口 ——
  function render(data, root) {
    _data = data;
    _root = root;
    _list = (data && data.items) || [];
    _filtered = [..._list];
    _fCity = _fOperator = 'all';
    _fSearch = '';
    _page = 1;
    _view = 'list';
    const meta = (data && data.meta) || {};
    root.innerHTML = `
      <div class="exp-wrap">
        <div class="exp-subnav">
          <button type="button" class="exp-subnav__btn exp-subnav__btn--active" data-expview="list">项目列表</button>
          <button type="button" class="exp-subnav__btn" data-expview="graph">关联关系图</button>
          <span class="exp-subnav__meta">${esc(meta.scope || '')} · 更新 ${esc(meta.generated_at || '—')}</span>
        </div>
        <div id="exp-summary"></div>
        <div id="exp-view"></div>
        <div id="exp-graph" style="height:560px; display:none;"></div>
      </div>`;
    document.getElementById('exp-summary').innerHTML = renderSummary();
    document.getElementById('exp-view').innerHTML = renderList();
    bind();
  }

  function switchView(v) {
    _view = v;
    document.querySelectorAll('.exp-subnav__btn').forEach(b =>
      b.classList.toggle('exp-subnav__btn--active', b.dataset.expview === v));
    const listBox = document.getElementById('exp-view');
    const graphBox = document.getElementById('exp-graph');
    if (v === 'list') {
      listBox.style.display = '';
      graphBox.style.display = 'none';
      listBox.innerHTML = renderList();
    } else {
      listBox.style.display = 'none';
      graphBox.style.display = '';
      graphBox.innerHTML = '';
      renderGraph();
    }
  }

  App.register({
    id: 'experience',
    title: '运营商中标动态栏',
    dataUrl: 'data/experience.json',
    mountId: 'experience-body',
    render: render,
  });

})();
