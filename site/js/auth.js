/* ============================================
 * auth.js · cockpit 登录认证（2026-08-09 方案 C）
 *
 * 流程：
 *   login.html  → fetch('/auth/verify', {headers: Authorization})
 *               → nginx 校验凭据（.htpasswd）→ 200/401
 *               → 通过后写入本地会话标记 → 跳转 index.html
 *   index.html  → 加载时检查会话标记 → 无则跳回 login.html
 *
 * 会话策略：
 *   - 勾选「记住我」→ localStorage（7 天有效期）
 *   - 不勾选        → sessionStorage（关浏览器失效）
 *
 * 注意：前端标记仅为 UX 层（避免每次刷新重登）；
 *       真正校验在 nginx 层（auth_basic + .htpasswd）。
 */
(function () {
  'use strict';

  var KEY = 'cockpit_auth';
  var TTL = 7 * 24 * 60 * 60 * 1000; // 7 天（毫秒）

  // —— 登录页逻辑 ——
  function initLoginPage() {
    var form = document.getElementById('login-form');
    if (!form) return;

    var userInput = document.getElementById('login-user');
    var passInput = document.getElementById('login-pass');
    var rememberChk = document.getElementById('login-remember');
    var errorEl = document.getElementById('login-error');
    var btn = document.getElementById('login-btn');
    var btnText = btn ? btn.querySelector('.login-btn__text') : null;

    // 已登录则直接跳转
    if (isAuthed()) {
      location.replace('index.html');
      return;
    }

    form.addEventListener('submit', function (e) {
      e.preventDefault();
      var user = (userInput.value || '').trim();
      var pass = passInput.value || '';
      if (!user || !pass) {
        showError(errorEl, '请输入账号和密码');
        return;
      }
      if (btn) btn.disabled = true;
      if (btnText) btnText.textContent = '验证中…';

      // 带 Basic Auth 请求 nginx 受保护端点
      var cred = btoa(unescape(encodeURIComponent(user + ':' + pass)));
      fetch('/auth/verify', {
        method: 'GET',
        headers: { 'Authorization': 'Basic ' + cred }
      }).then(function (resp) {
        if (resp.status === 200) {
          var remember = !!(rememberChk && rememberChk.checked);
          setAuth(remember);
          setCred(cred);   // 2026-08-09：存 Basic 凭据供编辑 API 使用
          location.replace('index.html');
        } else {
          showError(errorEl, '账号或密码错误');
          resetBtn(btn, btnText);
        }
      }).catch(function () {
        showError(errorEl, '网络异常，请重试');
        resetBtn(btn, btnText);
      });
    });
  }

  // —— index 页拦截 ——
  function initGuard() {
    // 仅在 index 页（无 login-form 时）执行
    if (document.getElementById('login-form')) return;
    if (!isAuthed()) {
      location.replace('login.html');
      return;
    }
    // 2026-08-19：会话一致性检查 — cockpit_auth 与 cockpit_cred 必须同时存在
    // （仅 cockpit_auth 没有 cred → JS 拿不到用户名 / API 调 401 / 体验不一致）
    // 只清一个另一个会留下不一致状态，不如同步跳回登录重新认证
    if (!getCred()) {
      try { localStorage.removeItem(KEY); } catch (e) {}
      try { sessionStorage.removeItem(KEY); } catch (e) {}
      location.replace('login.html');
      return;
    }
    // 刷新剩余有效期（记住我模式滑动续期）
    refreshExpiry();
  }

  function isAuthed() {
    try {
      var v = localStorage.getItem(KEY) || sessionStorage.getItem(KEY);
      if (!v) return false;
      var t = parseInt(v, 10);
      if (!t) return false;
      return Date.now() < t;
    } catch (e) { return false; }
  }

  // —— 存储 Basic Auth 凭据（编辑 API 用，2026-08-09）——
  var CRED_KEY = 'cockpit_cred';
  function setCred(cred) {
    try { sessionStorage.setItem(CRED_KEY, cred); } catch (e) {}
  }
  function getCred() {
    try { return sessionStorage.getItem(CRED_KEY) || ''; } catch (e) { return ''; }
  }
  // 2026-08-19：获取当前用户名（从 Basic Auth 凭据 base64 解码；供收藏多账号隔离 + 顶部栏展示）
  function getUser() {
    var cred = getCred();
    if (!cred) return '';
    try {
      var decoded = atob(cred);
      var u = (decoded.split(':')[0] || '').trim();
      return u;
    } catch (e) {
      return '';
    }
  }
  // —— 认证失效自愈（2026-09-21 手机端弹原生密码框修复）——
  // API 收到 401/403 = 当前会话凭据已不被服务端接受（典型：改密后旧浏览器
  // 恢复会话带旧凭据）。本地会话标记只是 UX 层，此时必须全部清掉并回登录页
  // 重新认证；否则旧凭据会一直发、一直失败、一直弹窗。
  function handleAuthFail(res) {
    if (!res || (res.status !== 401 && res.status !== 403)) return false;
    try {
      localStorage.removeItem(KEY);
      sessionStorage.removeItem(KEY);
      sessionStorage.removeItem(CRED_KEY);
    } catch (e) {}
    location.replace('login.html');
    return true;
  }

  // 暴露给其他模块（编辑 API 请求头 + 用户名获取 + 认证失效自愈）
  window.Auth = { getCred: getCred, getUser: getUser, handleAuthFail: handleAuthFail };

  function setAuth(remember) {
    var exp = Date.now() + (remember ? TTL : 0);
    var store = remember ? localStorage : sessionStorage;
    // 记住我：localStorage 带过期；不记住：sessionStorage 存极远期（关浏览器即失效）
    store.setItem(KEY, String(exp));
  }

  function refreshExpiry() {
    try {
      var lv = localStorage.getItem(KEY);
      if (lv && parseInt(lv, 10) > Date.now()) {
        localStorage.setItem(KEY, String(Date.now() + TTL));
      }
    } catch (e) { /* ignore */ }
  }

  function showError(el, msg) {
    if (!el) return;
    el.textContent = msg;
    el.style.display = 'block';
  }

  function resetBtn(btn, btnText) {
    if (btn) btn.disabled = false;
    if (btnText) btnText.textContent = '登 录';
  }

  // —— 自动识别当前页 ——
  var path = location.pathname.split('/').pop() || 'index.html';
  if (path === 'login.html') {
    initLoginPage();
  } else {
    initGuard();
  }
})();