/* ============================================
 * app.js · 驾驶舱模块注册中心（2026-08-09 模块化重构）
 *
 * 目标：
 *   1. 每个板块是一个独立模块：独立 JS + 独立 CSS + 独立数据文件
 *   2. 模块之间完全隔离：一个板块数据崩溃/渲染异常，不影响其他板块
 *   3. 新增板块零改动：写一个模块文件 + 注册一行即可，无需改 index.html
 *
 * 用法：
 *   App.register({
 *     id: 'enterprise',            // 唯一 id（tab 按钮 data-tab 同值）
 *     title: '企业信息栏',          // tab 显示名
 *     dataUrl: 'data/enterprise.json',  // 可选：数据文件；不传则 render 收 null
 *     render: function(data, root) {},  // 渲染函数，root = 本板块容器
 *     mountId: 'enterprise-body',       // 可选：容器 id，默认 body-<id>
 *     lazy: false,                      // 可选：true=首次切到该 tab 才加载
 *   });
 *   App.init();    // 生成 tab 栏 + 各板块容器（放 DOMContentLoaded 后）
 *   App.refresh(); // 并行加载所有板块（相互隔离）
 * ============================================ */

const App = (() => {

  const modules = [];        // 注册表（顺序 = tab 顺序）
  const byId = {};           // id → module
  let initialized = false;

  // —— 注册模块 ——
  function register(mod) {
    if (!mod || !mod.id || typeof mod.render !== 'function') {
      console.error('[App] 模块注册失败（缺 id 或 render）', mod);
      return;
    }
    if (byId[mod.id]) {
      console.warn('[App] 模块重复注册：', mod.id);
      return;
    }
    modules.push(mod);
    byId[mod.id] = mod;
  }

  // —— 初始化：建 tab 栏 + 各板块容器 ——
  function init() {
    if (initialized) return;
    initialized = true;

    const tabsEl = document.getElementById('tabs');
    const mount = document.getElementById('app-mount');
    if (!tabsEl || !mount) {
      console.error('[App] 缺少 #tabs 或 #app-mount 容器');
      return;
    }

    // tab 按钮
    tabsEl.innerHTML = modules.map((m, i) =>
      `<button class="tab ${i === 0 ? 'tab--active' : ''}" data-tab="${m.id}" type="button">${m.title}</button>`
    ).join('');

    // 各板块容器
    mount.innerHTML = modules.map((m, i) => {
      const mid = m.mountId || ('body-' + m.id);
      return `<section class="tab-content ${i === 0 ? 'tab-content--active' : ''}" id="tab-${m.id}">
        <div class="panel">
          <div class="panel__body" id="${mid}"><div class="loading">加载中…</div></div>
        </div>
      </section>`;
    }).join('');

    // tab 切换
    tabsEl.addEventListener('click', (e) => {
      const btn = e.target.closest('.tab');
      if (!btn) return;
      switchTab(btn.dataset.tab);
    });
  }

  // —— 切换 tab ——
  function switchTab(id) {
    if (!byId[id]) return;
    document.querySelectorAll('.tab').forEach(t =>
      t.classList.toggle('tab--active', t.dataset.tab === id));
    document.querySelectorAll('.tab-content').forEach(c =>
      c.classList.toggle('tab-content--active', c.id === 'tab-' + id));
    const mod = byId[id];
    if (mod && mod.lazy && !mod._loaded) {
      loadModule(mod);
    }
  }

  // —— 加载单个模块（全程 try/catch，隔离）——
  async function loadModule(mod) {
    const root = document.getElementById(mod.mountId || ('body-' + mod.id));
    if (!root) return;
    root.innerHTML = '<div class="loading">加载中…</div>';
    try {
      let data = null;
      if (mod.dataUrl) {
        data = await Utils.fetchJSON(mod.dataUrl);
      }
      try {
        mod.render(data, root);
        mod._loaded = true;
      } catch (err) {
        console.error(`[App] ${mod.id} 渲染失败：`, err);
        root.innerHTML = errorHtml(mod, '渲染异常', err);
      }
    } catch (err) {
      console.error(`[App] ${mod.id} 数据加载失败：`, err);
      root.innerHTML = errorHtml(mod, '数据加载失败', err);
    }
  }

  function errorHtml(mod, stage, err) {
    return `<div class="module-error">
      <div class="module-error__icon">⚠️</div>
      <div class="module-error__title">${mod.title} · ${stage}</div>
      <div class="module-error__hint">该板块暂时无法显示，其他板块不受影响。</div>
      <div class="module-error__detail">${String(err && err.message || err || '未知错误')}</div>
      <button type="button" class="module-error__retry" data-retry="${mod.id}">重试</button>
    </div>`;
  }

  // —— 并行刷新所有板块（相互隔离）——
  async function refresh() {
    if (!initialized) init();
    await Promise.allSettled(modules.map(m => loadModule(m)));
  }

  // —— 重试单个模块（错误提示里的按钮）——
  function bindRetry() {
    document.addEventListener('click', (e) => {
      const btn = e.target.closest('[data-retry]');
      if (btn) {
        const mod = byId[btn.dataset.retry];
        if (mod) loadModule(mod);
      }
    });
  }

  function get(id) { return byId[id] || null; }

  return { register, init, refresh, switchTab, get, bindRetry };
})();

// 文档就绪后自动绑定重试按钮（事件委托，一次即可）
document.addEventListener('DOMContentLoaded', () => App.bindRetry());
