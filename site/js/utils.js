/* ============================================
 * utils.js · 通用工具（2026-08-09 清理死代码）
 * 保留：fetchJSON / formatNum / formatInt
 * 已删（全站无引用）：formatPct / nowTime / nowDateTime /
 *   CHART_COLORS / BASE_OPTION / initChart / toast
 * ============================================ */

const Utils = (() => {

  /**
   * 拉 JSON（2026-09-07 加载优化：改 ETag 协商缓存）
   * 原实现拼 Date.now() cache buster + no-store，导致每次打开都全量重拉
   * 静态 JSON 工具：使用协商缓存，避免重复传输。
   *   - 数据没变（同一次部署内重复打开）→ nginx 304，浏览器用本地副本，秒开
   *   - 主抓后文件变更 → ETag/Last-Modified 变化 → 自动拉新，不会拿到旧数据
   * API 类带 headers 的调用不受影响（opts.headers 透传保留）。
   */
  async function fetchJSON(url, opts = {}) {
    const headers = Object.assign({}, opts.headers || {});
    const res = await fetch(url, { headers: headers });
    if (!res.ok) throw new Error(`fetch ${url} → ${res.status}`);
    return await res.json();
  }

  /**
   * 数字千分位 + 缩写（12345 → 1.2万）
   */
  function formatNum(n) {
    if (n == null) return '—';
    if (Math.abs(n) >= 100000000) return (n / 100000000).toFixed(2) + '亿';
    if (Math.abs(n) >= 10000)     return (n / 10000).toFixed(2) + '万';
    return n.toLocaleString('zh-CN');
  }

  /**
   * 整数（带千分位）
   */
  function formatInt(n) {
    if (n == null) return '—';
    return Math.round(n).toLocaleString('zh-CN');
  }

  return {
    fetchJSON, formatNum, formatInt
  };
})();