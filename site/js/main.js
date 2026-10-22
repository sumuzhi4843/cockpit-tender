/* ============================================
 * main.js · 入口：时钟 + 模块调度（2026-08-09 模块化重构）
 *
 * 各板块通过 App 注册表独立加载/渲染/报错：
 *   - tender 由 App 加载，数据统一来自 8002 API
 * ============================================ */

(async function () {

  // —— 顶部栏账号 chip + 下拉菜单（2026-08-19）——
  // 组件：
  //   <div class="topbar__user-menu">
  //     <button class="topbar__user" id="topbar-user" aria-haspopup="menu" aria-expanded> 👤 user ▾ </button>
  //     <div class="topbar__dropdown" id="topbar-dropdown" role="menu" hidden>
  //       head: 账号名
  //       stats: 待投标 / 代挂网 收藏数
  //       item: 切换账号 / 退出登录
  //     </div>
  // —— 账号名渲染 —
  function renderTopbarUser() {
    var u = '';
    try { u = (window.Auth && window.Auth.getUser) ? window.Auth.getUser() : ''; } catch (e) {}
    var name = u || '匿名';
    var a = document.getElementById('topbar-user-name');
    var b = document.getElementById('topbar-dropdown-name');
    if (a) a.textContent = name;
    if (b) b.textContent = name;
    // 顶部 title 属性以 tooltip 提示账号
    var btn = document.getElementById('topbar-user');
    // 2026-09-23：账号-区县徽标
    var tagEl = document.getElementById('topbar-user-tag');
    if (tagEl) {
      var d = (typeof window.userTagDistrict === 'function') ? window.userTagDistrict(name) : null;
      var lbl = (typeof window.userTagLabel === 'function') ? window.userTagLabel(d) : (d || '');
      if (d && lbl && d !== 'all') {
        tagEl.textContent = lbl;
        tagEl.title = '按账号自动定位到「' + lbl + '」';
        tagEl.className = 'topbar__user-tag topbar__user-tag--located';
        tagEl.hidden = false;
      } else {
        tagEl.textContent = '默认全部';
        tagEl.title = '未配置区县标签，主页默认显示全部区县';
        tagEl.className = 'topbar__user-tag topbar__user-tag--default';
        tagEl.hidden = false;
      }
    }
    if (btn) btn.title = '当前账号：' + name + '\n点击展开菜单';
  }
  renderTopbarUser();

  // —— 收藏数统计（2026-08-19）：从 GET /api/tender/favorites 拿两条 bucket 长度 —
  async function refreshTopbarStats() {
    var tEl = document.getElementById('topbar-stat-tender');
    var iEl = document.getElementById('topbar-stat-intention');
    if (!tEl || !iEl) return;
    try {
      // 2026-09-10：改为显式带 Authorization 头。原 credentials:'include' 依赖浏览器
      // 缓存凭据，无缓存时会收到 401 挑战（WWW-Authenticate）→ 浏览器弹原生密码框。
      var hdrs = {};
      try {
        var c0 = (window.Auth && window.Auth.getCred) ? window.Auth.getCred() : '';
        if (c0) hdrs['Authorization'] = 'Basic ' + c0;
      } catch (e) {}
      var res = await fetch('/api/tender/favorites', { headers: hdrs, credentials: 'include', cache: 'no-cache' });
      if (window.Auth && window.Auth.handleAuthFail(res)) return; // 2026-09-21 凭据失效→重登录
      if (!res.ok) return;
      var d = await res.json();
      tEl.textContent = (d.tender || []).length;
      iEl.textContent = (d.intention || []).length;
    } catch (e) {
      // 离线/失败静默，下次点击下拉时再试
    }
  }
  refreshTopbarStats();

  // —— 下拉开关：点击 chip 展开/收起，点击外部 / Esc 关闭 —
  var userBtn = document.getElementById('topbar-user');
  var dropdown = document.getElementById('topbar-dropdown');
  function setDropdownOpen(open) {
    if (!userBtn || !dropdown) return;
    dropdown.hidden = !open;
    userBtn.setAttribute('aria-expanded', open ? 'true' : 'false');
  }
  if (userBtn && dropdown) {
    userBtn.addEventListener('click', function (e) {
      e.stopPropagation();
      setDropdownOpen(dropdown.hidden);
      // 展开时刷新统计（防过期）
      if (!dropdown.hidden) refreshTopbarStats();
    });
    // 外部点击关闭
    document.addEventListener('click', function (e) {
      if (!dropdown.hidden && !e.target.closest('.topbar__user-menu')) {
        setDropdownOpen(false);
      }
    });
    // Esc 关闭 + 焦点回到 chip
    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape' && !dropdown.hidden) {
        setDropdownOpen(false);
        try { userBtn.focus(); } catch (e2) {}
      }
    });
  }

  

  // —— 退出 / 切换账号：清前端存储 + 跳回登录页 ——
  // 2026-09-10：不再请求 /logout（该端点是 401 挑战端点 WWW-Authenticate，
  // 整页跳转或 fetch 都可能触发浏览器原生密码框）。
  // 本应用所有 API 调用均已显式带 Authorization 头，不依赖浏览器缓存的 Basic 凭据，
  // 故退出 = 清前端会话 → 直接回登录页（带 ?bye=1 由登录页显示“已安全退出”提示），
  // 全程无 401、无定时器依赖。
  function doLogout() {
    try { localStorage.clear(); } catch (e) {}
    try { sessionStorage.clear(); } catch (e) {}
    location.replace('/login.html?bye=1');
  }
  function doLogoutWithConfirm(action) {
    // action: 'logout' | 'switch'
    var u = '';
    try { u = (window.Auth && window.Auth.getUser) ? window.Auth.getUser() : ''; } catch (e) {}
    var label = action === 'switch' ? '切换账号' : '退出登录';
    var msg;
    if (action === 'switch') {
      msg = u
        ? ('将切换账号【' + u + '】，退出后请用新账号重新登录。\n\n继续？')
        : '退出后请重新登录。\n\n继续？';
    } else {
      msg = u
        ? ('确定退出账号【' + u + '】？')
        : '确定退出？';
    }
    if (confirm(msg)) doLogout();
  }
  var logoutBtn = document.getElementById('topbar-logout');
  if (logoutBtn) {
    logoutBtn.addEventListener('click', function (e) {
      e.preventDefault();
      setDropdownOpen(false);
      doLogoutWithConfirm('logout');
    });
  }
  var switchBtn = document.getElementById('topbar-switch');
  if (switchBtn) {
    switchBtn.addEventListener('click', function (e) {
      e.preventDefault();
      setDropdownOpen(false);
      doLogoutWithConfirm('switch');
    });
  }

  // —— 时钟（年月日） ——
  // 2026-09-23 P0.2：≤480px 改用紧凑 MM-DD 格式（避免 "2026年09月23日" 在窄屏被强制换行）
  function tickClock() {
    const d = new Date();
    const pad = n => String(n).padStart(2, '0');
    const compact = window.innerWidth <= 480;
    document.getElementById('now').textContent = compact
      ? `${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
      : `${d.getFullYear()}年${pad(d.getMonth() + 1)}月${pad(d.getDate())}日`;
  }
  tickClock();
  setInterval(tickClock, 60000); // 每分钟更新即可

  // —— 初始化模块框架（生成 tab + 容器）——
  App.init();

  // —— 首次加载全部板块 ——
  async function refresh() {
    await App.refresh();
  }
  refresh();

  // —— 窗口尺寸变化：重排图表 ——
  // 2026-09-09 A1：echarts 已从首页移除（大屏自带 vendor），无 echarts 时直接跳过。
  window.addEventListener('resize', () => {
    if (typeof echarts === 'undefined') return;
    document.querySelectorAll('.chart__body').forEach(el => {
      const inst = echarts.getInstanceByDom(el);
      if (inst) inst.resize();
    });
  });

})();
