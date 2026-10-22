/* ============================================
 * user_tags.js · 账号 → 区县标签 (2026-09-23)
 * 公共映射：data/user_tags.json（保留作存档/审计源，请与之保持同步）
 * 内联数据：避免 fetch 异步时序与 module+defer 混排的不确定性，
 *          保证 tender.js 的 IIFE 执行时 USER_TAGS 已就绪。
 * 未命中 → null（保留默认「全部」）。
 * ============================================= */
window.USER_TAGS = {
  yankai: 'yankai',
  liutao: 'yankai',
  wangyw: 'yankai',
  xuezc:  'yankai',
  zq:     null,
};
window.userTagDistrict = function (username) {
  if (!username) return null;
  const tags = window.USER_TAGS;
  if (!tags || typeof tags !== "object") return null;
  return Object.prototype.hasOwnProperty.call(tags, username)
    ? (tags[username] || null)
    : null;
};

// 2026-09-23：区县 key → 显示名（供顶栏徽标使用；与 tender.js 的 DISTRICT_LABELS 保持同步）
window.USER_DISTRICT_LABELS = {
  all: '全部', shiji: '市级', yankai: '盐开区', yandu: '盐都区',
  tinghu: '亭湖区', dafeng: '大丰区', xiangshui: '响水县', binhai: '滨海县',
  funing: '阜宁县', sheyang: '射阳县', jianhu: '建湖县', dongtai: '东台市',
};
window.userTagLabel = function (districtKey) {
  const labels = window.USER_DISTRICT_LABELS;
  return (labels && districtKey && labels[districtKey]) ? labels[districtKey] : (districtKey || '');
};
