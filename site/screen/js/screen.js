/* 大屏主控：等比缩放 · 时钟 · screen.json 轮询 · KPI/列表渲染 · 公告轮换 */
(function () {
  'use strict';

  var DESIGN_W = 1920, DESIGN_H = 1080;
  var POLL_MS = 60 * 1000;            // 60s 轮询版本
  var RELOAD_MS = 6 * 3600 * 1000;    // 6h 整页 reload 防内存增长
  var ROTATE_MS = 7 * 1000;           // 公告轮换

  var currentVersion = null;
  var notices = [];
  var rotateIdx = 0;
  var rotateTimer = null;

  /* ---------- 等比缩放 ---------- */
  function fit() {
    var s = Math.min(window.innerWidth / DESIGN_W, window.innerHeight / DESIGN_H);
    var stage = document.getElementById('stage');
    var left = Math.max(0, (window.innerWidth - DESIGN_W * s) / 2);
    var top = Math.max(0, (window.innerHeight - DESIGN_H * s) / 2);
    stage.style.transform = 'translate(' + left + 'px,' + top + 'px) scale(' + s + ')';
  }

  /* ---------- 时钟 ---------- */
  var WEEK = ['日', '一', '二', '三', '四', '五', '六'];
  function pad(n) { return n < 10 ? '0' + n : '' + n; }
  function tickClock() {
    var now = new Date();
    document.getElementById('clock').textContent =
      pad(now.getHours()) + ':' + pad(now.getMinutes()) + ':' + pad(now.getSeconds());
    document.getElementById('date-line').textContent =
      now.getFullYear() + '年' + (now.getMonth() + 1) + '月' + now.getDate() + '日 · 星期' + WEEK[now.getDay()];
  }

  /* ---------- 工具 ---------- */
  function fmtInt(n) { return (n || 0).toLocaleString('en-US'); }
  function fmtMoneyYi(v) {
    v = v || 0;
    if (v >= 1e8) return (v / 1e8).toFixed(2);
    if (v >= 1e4) return (v / 1e4).toFixed(0);
    return v.toFixed(0);
  }
  function moneyUnit(v) { return v >= 1e8 ? '亿元' : '万元'; }

  function typeClass(t) {
    if (t === '招标公告') return 'tender';
    if (t === '中标公告') return 'award';
    if (t === '意向公告') return 'intention';
    return '';
  }

  function setStatus(ok, text) {
    var dot = document.getElementById('status-dot');
    dot.classList.toggle('warn', !ok);
    document.getElementById('status-text').textContent = text;
  }

  /* ---------- KPI ---------- */
  function renderKpi(d) {
    var k = d.kpi;
    document.getElementById('kpi-total').textContent = fmtInt(k.projects_total);
    document.getElementById('kpi-today').textContent = fmtInt(k.projects_today);
    document.getElementById('kpi-30d').textContent = fmtInt(k.projects_30d);
    var budgetEl = document.getElementById('kpi-budget');
    budgetEl.textContent = fmtMoneyYi(k.amount_budget);
    document.getElementById('kpi-budget-unit').textContent = moneyUnit(k.amount_budget);
    document.getElementById('kpi-budget-sub').textContent =
      '另有已披露中标金额 ' + fmtMoneyYi(k.amount_winning) + ' ' + moneyUnit(k.amount_winning);
    document.getElementById('kpi-ict').textContent = fmtInt(k.ict_projects);
    document.getElementById('kpi-ict-sub').textContent =
      '占比 ' + (k.ict_ratio * 100).toFixed(1) + '%';
    document.getElementById('kpi-note').textContent =
      '覆盖公告 ' + fmtInt(k.notices_total) + ' 条';
  }

  function renderHeaderMeta(d) {
    var g = d.generated_at || '';
    var m = g.match(/^(\d{4})-(\d{2})-(\d{2})T(\d{2}:\d{2})/);
    var shown = m ? (m[1] + '-' + m[2] + '-' + m[3] + ' ' + m[4]) : g;
    document.getElementById('data-updated').textContent = '数据更新：' + shown;
    var ageMs = Date.now() - new Date(g).getTime();
    if (g && ageMs > 24 * 3600 * 1000) {
      setStatus(false, '数据可能滞后');
    } else {
      setStatus(true, '数据正常');
    }
  }

  /* ---------- 即将开标 ---------- */
  /* HTML 转义：拦标签注入与属性逃逸；保留 &（源标题含 &ldquo; 等合法实体，需照常解析显示） */
  function esc(s) {
    return String(s == null ? '' : s)
      .replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  function renderUpcoming(d) {
    var box = document.getElementById('upcoming-list');
    document.getElementById('upcoming-hint').textContent =
      '共 ' + d.upcoming_total + ' 个';
    var list = (d.upcoming || []).slice(0, 5);  // 卡片高度约容 5 条，防裁切
    if (!list.length) {
      box.innerHTML = '<div class="upcoming-item"><span class="u-title" style="color:#5b6475">未来 7 天暂无开标项目</span></div>';
      return;
    }
    box.innerHTML = list.map(function (x) {
      var cls = x.days_left <= 1 ? 'd1' : (x.days_left <= 3 ? 'd3' : '');
      var label = x.days_left <= 0 ? '今日' : 'D-' + x.days_left;
      var md = (x.open_date || '').slice(5).replace('-', '/');
      return '<div class="upcoming-item">' +
        '<span class="badge-d ' + cls + '">' + label + '</span>' +
        '<span class="u-title" title="' + esc(x.title) + '">' + esc(x.title) + '</span>' +
        '<span class="u-meta">' + esc(x.district) + ' · ' + esc(md) + '</span>' +
        '</div>';
    }).join('');
  }

  /* ---------- 公告轮换 ---------- */
  function noticeRowHtml(n) {
    return '<span class="badge-type ' + typeClass(n.type) + '">' + esc(n.type) + '</span>' +
      '<span class="n-title" title="' + esc(n.title) + '">' + esc(n.title) + '</span>' +
      '<span class="n-meta"><span>' + esc(n.district) + '</span><span>' + esc(n.site) + '</span><span>' + esc(n.publish_date) + '</span></span>';
  }

  function renderNoticeRow(idx, rowId) {
    if (!notices.length) return;
    var el = document.getElementById(rowId);
    var n = notices[((idx % notices.length) + notices.length) % notices.length];
    el.innerHTML = noticeRowHtml(n);
  }

  function rotateNotices() {
    if (notices.length < 2) return;
    var r1 = document.getElementById('notice-row-1');
    var r2 = document.getElementById('notice-row-2');
    r1.style.opacity = 0; r2.style.opacity = 0;
    setTimeout(function () {
      rotateIdx += 2;
      renderNoticeRow(rotateIdx, 'notice-row-1');
      renderNoticeRow(rotateIdx + 1, 'notice-row-2');
      r1.style.opacity = 1; r2.style.opacity = 1;
    }, 500);
  }

  function startNoticeRotate() {
    if (rotateTimer) clearInterval(rotateTimer);
    rotateTimer = setInterval(rotateNotices, ROTATE_MS);
  }

  /* ---------- 行业脚注 ---------- */
  function renderIndustryNote(d) {
    var ind = d.industries;
    document.getElementById('industry-note').textContent =
      '另有 ' + fmtInt(ind.unclassified_count) + ' 个项目暂未归类行业' +
      (ind.others_count ? '，其他已归类行业 ' + fmtInt(ind.others_count) + ' 个' : '');
  }

  /* ---------- 数据加载 ---------- */
  function applyData(data) {
    currentVersion = data.generated_at;
    notices = data.latest_notices || [];
    renderKpi(data);
    renderHeaderMeta(data);
    renderUpcoming(data);
    renderIndustryNote(data);
    window.ScreenCharts.update(data);
    rotateIdx = 0;
    renderNoticeRow(0, 'notice-row-1');
    renderNoticeRow(1, 'notice-row-2');
    startNoticeRotate();
  }

  function loadScreenJson() {
    return fetch('data/screen.json?t=' + Date.now(), { cache: 'no-store' })
      .then(function (r) {
        if (!r.ok) throw new Error('HTTP ' + r.status);
        return r.json();
      });
  }

  function poll() {
    loadScreenJson().then(function (data) {
      if (data.generated_at !== currentVersion) {
        applyData(data);
      }
      setStatus(true, currentVersion ? '数据正常' : '数据正常');
    }).catch(function () {
      // 保留上一版内容，仅提示
      setStatus(false, '数据读取失败·当前为上次数据');
    });
  }

  function boot() {
    fit();
    window.addEventListener('resize', fit);
    tickClock();
    setInterval(tickClock, 1000);

    fetch('vendor/yancheng.geo.json', { cache: 'no-store' })
      .then(function (r) { return r.json(); })
      .then(function (geo) {
        window.ScreenCharts.init(geo);
        return loadScreenJson();
      })
      .then(function (data) {
        applyData(data);
      })
      .catch(function (e) {
        setStatus(false, '初始化失败：' + e.message);
      });

    setInterval(poll, POLL_MS);
    setTimeout(function () { location.reload(); }, RELOAD_MS);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }
})();
