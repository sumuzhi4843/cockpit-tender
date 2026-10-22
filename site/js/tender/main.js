// tender 板块入口：加载即注册全部域模块到 F；组装 window.Tender（内联 onclick 依赖）；App.register
import { F } from './registry.js';
import { S } from "./state.js";
import './util.js';
import './badge.js';
import './fav.js';
import './api.js';
import './filters.js';
import './controls.js';
import './render.js';
import './district.js';
import './modals.js';

// 内联 onclick 属性需要全局 Tender（与原 const Tender 等效的全局面）
window.Tender = {
  render: (...a) => F.render(...a),
  showSites: (...a) => F.showSites(...a),
  closeSites: (...a) => F.closeSites(...a),
  showIctPie: (...a) => F.showIctPie(...a),
  closeIctPie: (...a) => F.closeIctPie(...a),
};

// —— 注册为驾驶舱模块（原 tender.js 尾部逻辑等价搬入）——
App.register({
  id: 'tender',
  title: '招投标信息栏',
  // 2026-09-29：API-only，首页禁止请求全量 tender.json。
  dataUrl: undefined,
  mountId: 'tender-body',
  render: (data, root) => F.render(data, root),
});
