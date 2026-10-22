/* ============================================
 * foldable.js · 折叠屏检测与适配（2026-08-18 v4）
 *
 * 检测能力：
 *   - window.getScreenDetails()：返回 ScreenDetails，含 folds（折叠位置）
 *     https://wicg.github.io/window-placement/
 *   - CSS screen-spanning media query（已在 foldable.css 中使用）
 *
 * 行为：
 *   1. 检测到折叠屏展开态 → 给 body 加 data-foldable="vertical"/"horizontal"
 *   2. 折叠位置变化（旋转/展开）→ 自动更新 body 属性
 *   3. 不支持的浏览器 / 普通设备 → 静默无效，CSS 断点体系自动兜底
 *
 * ============================================ */
(function () {
  'use strict';

  var KEY = 'cockpit_foldable';
  var fold = null;       // 当前折叠位置（来自 getScreenDetails）
  var spanning = null;   // CSS @media (screen-spanning) 的命中状态

  function setFoldable(orient) {
    if (orient) {
      document.body.setAttribute('data-foldable', orient);
      // 暴露给其他模块
      window._cockpitFoldable = { orient: orient, fold: fold, spanning: spanning };
    } else {
      document.body.removeAttribute('data-foldable');
      window._cockpitFoldable = null;
    }
  }

  // 检测 CSS @media (screen-spanning) 状态
  function checkSpanning() {
    if (window.matchMedia) {
      var v = window.matchMedia('(screen-spanning: single-fold-vertical)');
      var h = window.matchMedia('(screen-spanning: single-fold-horizontal)');
      var n = window.matchMedia('(screen-spanning: none)');
      function update() {
        if (v.matches) spanning = 'vertical';
        else if (h.matches) spanning = 'horizontal';
        else if (n.matches) spanning = 'none';
        else spanning = null;
      }
      try {
        v.addEventListener('change', update);
        h.addEventListener('change', update);
      } catch (e) {
        // 老浏览器回退
        v.addListener && v.addListener(update);
        h.addListener && h.addListener(update);
      }
      update();
    }
  }

  // 检测 getScreenDetails（折叠位置）
  async function checkScreenDetails() {
    if (!('getScreenDetails' in window)) return null;
    try {
      var details = await window.getScreenDetails();
      // details.currentFold 或 details.folds[0]
      fold = details.currentFold || (details.folds && details.folds[0]) || null;
      // 监听变化（折叠/展开/旋转）
      details.addEventListener('change', function () {
        fold = details.currentFold || (details.folds && details.folds[0]) || null;
        evaluate();
      });
      return details;
    } catch (e) {
      return null;
    }
  }

  // 综合判定
  function evaluate() {
    // 优先 CSS spanning（更可靠）；fold 位置仅用于布局细节
    if (spanning === 'vertical' || spanning === 'horizontal') {
      setFoldable(spanning);
    } else if (fold) {
      // 根据 fold 方向判断
      setFoldable(fold.vertical ? 'horizontal' : 'vertical');
    } else {
      // 用 viewport 启发式：宽 >= 600 且高 >= 500 + 大屏手机/平板断点
      var w = window.innerWidth;
      var h = window.innerHeight;
      // 折叠屏完全展开态通常 600~900 CSS 像素
      // 但不能简单用 viewport 区分普通手机横屏和平板/折叠屏
      // 默认不主动标记，由 CSS 断点体系处理
      setFoldable(null);
    }
  }

  // 启动检测
  async function init() {
    checkSpanning();
    var sp = checkScreenDetails();
    if (sp && typeof sp.then === 'function') {
      await sp.catch(function () {});
    }
    evaluate();
    // 兜底：resize 时根据 spanning 状态更新
    var resizeTimer;
    window.addEventListener('resize', function () {
      clearTimeout(resizeTimer);
      resizeTimer = setTimeout(evaluate, 150);
    });
  }

  // DOM 就绪后启动
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();