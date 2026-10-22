// tender 板块域模块：fav（2026-09-29 由单文件 IIFE 机械拆分；函数体零逻辑改动，
// 仅状态→S.、常量→K.、跨模块调用→F. 三类机械前缀改写。真源在 tender/frontend/tender/。）
import { S } from './state.js';
import * as K from './constants.js';
import { F } from './registry.js';

export function favKey(bucket) {
    const base = bucket === 'intention' ? K.FAV_INTENTION_KEY_BASE : K.FAV_KEY_BASE;
    return `fav_${S._currentUser}_${base}`;
  }

export function legacyFavKey(bucket) {
    return bucket === 'intention' ? K.FAV_INTENTION_KEY_BASE : K.FAV_KEY_BASE;
  }

export function favBucketOf(item) {
    return item && item.project_stage === 'intention' ? 'intention' : 'tender';
  }

export function getFavorites(bucket) {
    const b = bucket || 'tender';
    // 优先读新 KEY（按用户隔离）；不存在则 fallback 老 KEY（多账号上线前已用账号的兼容）
    try {
      let raw = localStorage.getItem(F.favKey(b));
      if (raw == null) raw = localStorage.getItem(F.legacyFavKey(b));
      const arr = JSON.parse(raw || '[]');
      return Array.isArray(arr) ? arr : [];
    } catch (e) {
      return [];
    }
  }

export function isFavorite(key, bucket) {
    return F.getFavorites(bucket).includes(key);
  }

export function toggleFavorite(key, bucket) {
    const b = bucket || 'tender';
    let favs = F.getFavorites(b);
    if (favs.includes(key)) {
      favs = favs.filter(k => k !== key);
    } else {
      favs.unshift(key); // 新收藏放最前（收藏时间倒序）
    }
    try {
      localStorage.setItem(F.favKey(b), JSON.stringify(favs));
      // 清理可能遗留的老 KEY（跨账号串数据修复）
      try { localStorage.removeItem(F.legacyFavKey(b)); } catch (e2) {}
    } catch (e) { /* 隐私模式等场景忽略 */ }
    return favs.includes(key);
  }

  // 收藏按钮 HTML：star=☆ 空心 / ★ 实心；文案随桶（待投标/代挂网）

export function favoriteItems(bucket) {
    const b = bucket || 'tender';
    const favs = F.getFavorites(b);
    // 2026-10-15：lazy 模式 S._allItems 只是当前页 40 个，代挂网收藏不在页内会漏。
    // 优先用 resolveFavorites 填充的缓存（async，API 全量解析），没有再兑底 S._allItems。
    let pool;
    if (S._favItemsCache && S._favItemsCache[b] && S._favItemsCache[b].length) {
      pool = S._favItemsCache[b];
    } else {
      console.warn('[fav] favoriteItems(' + b + ') 兑底 S._allItems，收藏不在当前页会漏出。需要预热 resolveFavorites。');
      pool = b === 'intention' ? S._allItems.filter(i => i.project_stage === 'intention') : S._allItems;
    }
    const byKey = new Map(pool.map(i => [F.normalizeProjectName(i.title), i]));
    const out = [];
    for (const key of favs) {
      const it = byKey.get(key);
      if (it) out.push(it);
    }
    return out;
  }

  // 老收藏迁移（2026-08-17 双桶化）：旧待投标桶里已收藏的意向项目 → 自动搬进代挂网桶，幂等
  // 2026-08-19：迁移作用在新 KEY（按当前用户）；老 KEY 跨账号为隔离写，只读不写。

export function migrateLegacyFavorites() {
    if (S._favMigrated) return;
    S._favMigrated = true;
    try {
      const legacy = F.getFavorites('tender');
      if (!legacy.length) return;
      // 迁移前备份旧桶完整快照（2026-08-17 防丢）：与历史备份取并集，迁移异常可从备份恢复
      const bakKey = F.legacyFavKey('tender') + '_backup';
      try {
        const prevBak = JSON.parse(localStorage.getItem(bakKey) || '[]');
        const bak = Array.isArray(prevBak) ? prevBak : [];
        let changed = false;
        for (const k of legacy) if (!bak.includes(k)) { bak.push(k); changed = true; }
        if (changed) localStorage.setItem(bakKey, JSON.stringify(bak));
      } catch (e) { /* 忽略 */ }
      const intentionKeys = new Set(
        // 2026-10-15：lazy 模式 S._allItems 只是当前页，意向项目不在页内会被遗漏。
        // 兑底用缓存；都没有时跳过迁移（以后用户进收藏页 resolveFavorites 会重新起一次）
        ((S._favItemsCache && S._favItemsCache.intention.length ? S._favItemsCache.intention : S._allItems.filter(i => i.project_stage === 'intention'))).map(i => F.normalizeProjectName(i.title))
      );
      const moved = legacy.filter(k => intentionKeys.has(k));
      if (!moved.length) return;
      const intentionFavs = F.getFavorites('intention');
      let changed2 = false;
      for (const k of moved) {
        if (!intentionFavs.includes(k)) { intentionFavs.unshift(k); changed2 = true; }
      }
      if (changed2) localStorage.setItem(F.favKey('intention'), JSON.stringify(intentionFavs));
      localStorage.setItem(F.favKey('tender'), JSON.stringify(legacy.filter(k => !intentionKeys.has(k))));
    } catch (e) { /* 忽略 */ }
  }

  // ===== 收藏服务端同步（2026-08-17 防丢根治）=====
  // 服务端为主（SQLite favorites 表），localStorage 当本地缓存；启动拉取并集合并，改动防抖推送

export function mergeFavLists(serverArr, localArr) {
    const out = [];
    const seen = new Set();
    for (const k of [...(serverArr || []), ...(localArr || [])]) {
      if (!k || seen.has(k)) continue;
      seen.add(k);
      out.push(k);
    }
    return out;
  }

  // 启动拉取：GET 服务端 → 与本地并集 → 写回本地；与本地或服务端有差异即触发推送（老数据自动上云）

export async function loadFavoritesFromServer() {
    try {
      const res = await fetch('/api/tender/favorites', { cache: 'no-cache', headers: F.apiHeaders() });
      if (!res.ok) return false;
      const data = await res.json();
      const sameArr = (a1, a2) => a1.length === a2.length && a1.every((k, i) => k === a2[i]);
      let localDirty = false;   // 合并结果 ≠ 本地 → 写回本地
      let serverDirty = false;  // 合并结果 ≠ 服务端 → 推送上云（含本地独有/老收藏）
      for (const b of ['tender', 'intention']) {
        const serverArr = Array.isArray(data[b]) ? data[b] : [];
        const localArr = F.getFavorites(b);
        const merged = F.mergeFavLists(serverArr, localArr);
        if (!sameArr(merged, localArr)) {
          localStorage.setItem(F.favKey(b), JSON.stringify(merged));
          localDirty = true;
        }
        if (!sameArr(merged, serverArr)) serverDirty = true;
      }
      if (localDirty || serverDirty) F.scheduleFavPush();
      return true;
    } catch (e) {
      return false;  // 离线/服务异常：本地照常，下次再同步
    }
  }

export async function pushFavoritesToServer() {
    S._favPushTimer = null;
    try {
      const payload = { tender: F.getFavorites('tender'), intention: F.getFavorites('intention') };
      const res = await fetch('/api/tender/favorites', {
        method: 'PUT',
        headers: F.apiHeaders({ 'Content-Type': 'application/json' }),
        body: JSON.stringify(payload),
      });
      return res.ok;
    } catch (e) {
      return false;  // 推送失败静默，下次改动/加载再推
    }
  }

export function scheduleFavPush() {
    if (S._favPushTimer) clearTimeout(S._favPushTimer);
    S._favPushTimer = setTimeout(F.pushFavoritesToServer, 1000);
  }

export function startFavSync() {
    if (S._favSyncStarted) return;
    S._favSyncStarted = true;
    F.loadFavoritesFromServer();
  }

  // 绑定收藏按钮点击（事件委托，容器级）

export function bindFavButtons(container) {
    if (!container) return;
    container.querySelectorAll('.tender-fav-btn').forEach(btn => {
      btn.addEventListener('click', (e) => {
        e.stopPropagation();
        e.preventDefault();
        const key = btn.dataset.favKey;
        const bucket = btn.dataset.favBucket || 'tender';
        const label = K.FAV_LABELS[bucket] || '待投标';
        const on = F.toggleFavorite(key, bucket);
        btn.classList.toggle('tender-fav-btn--on', on);
        btn.title = on ? '取消' + label : '加入' + label;
        btn.setAttribute('aria-label', on ? '取消' + label : '加入' + label);
        btn.textContent = on ? '★' : '☆';
        // 收藏夹视图内取消收藏 → 移出列表
        if ((S._activeStage === 'favorites' || S._activeStage === 'fav-intention') && !on) {
          const card = btn.closest('.tender-card');
          const row = btn.closest('tr');
          if (card) { card.remove(); }
          if (row) { row.remove(); }
          F.refreshContent();
        }
        // 服务端同步（防抖 1s，2026-08-17）
        F.scheduleFavPush();
        // 刷新导航计数
        const stageNav = document.querySelector('.tender-stage');
        if (stageNav) F.renderStageNavCount();
      });
    });
  }

// —— 收藏视图数据源（2026-09-30 方案B，老板拍板）——
// 旧实现：逐条 q=LIKE 串行搜索 + 归一化全等（且经 _allItems 恒空 → 视图永远 0 条）。
// 新实现：localStorage 收藏键（启动已与服务端并集同步）→ POST /api/tender/fav_projects
// 单请求批量归一解析；解析不到的键=原项目已下架/改名 → 返回 dead，由列表渲染「已失效」灰行。
export async function resolveFavorites(bucket) {
    const keys = (F.getFavorites(bucket) || []).slice(0, 500);
    const results = [];
    const dead = [];
    if (keys.length) {
      const res = await fetch('/api/tender/fav_projects', {
        method: 'POST',
        cache: 'no-cache',
        headers: F.apiHeaders({ 'Content-Type': 'application/json' }),
        body: JSON.stringify({ keys }),
      });
      if (!res.ok) throw new Error('fav_projects http ' + res.status);
      const d = await res.json();
      const items = (d && d.items) || [];
      const byNorm = new Map(items.map(it => [F.normalizeProjectName(it.title), it]));
      const seen = new Set();
      for (const k of keys) {
        const it = byNorm.get(F.normalizeProjectName(k));
        if (it) {
          if (!seen.has(it.id)) { seen.add(it.id); results.push(it); }
        } else {
          dead.push(k);
        }
      }
    }
    // 2026-10-15：写缓存供 favoriteItems 同步读
    if (S._favItemsCache) S._favItemsCache[bucket] = results;
    return { items: results, dead };
  }

  export function renderFavDeadRows(dead) {
    if (!dead || !dead.length) return '';
    return `
      <div class="tender-fav-dead">
        <div class="tender-fav-dead__title">🗑 已失效收藏（${dead.length}）——原项目已下架或改名，可清理</div>
        ${dead.map(k => `
        <div class="tender-fav-dead__row" data-dead-key="${F.escapeHtml(k)}">
          <span class="tender-fav-dead__name">${F.escapeHtml(k)}</span>
          <button type="button" class="tender-fav-dead__unfav" title="移除这条失效收藏">✕ 清理</button>
        </div>`).join('')}
      </div>`;
  }

  
  // ══════════════ 懒加载渲染结束 ══════════════

  // —— 2026-09-06：挑“更新公告”（晚于主招标公告的更正/合同/暂停等），
  //   供列表标题旁渲染角标。主公告 = tender_url 对应的那条（role=tender_primary/tender_reopen
  //   或 notice_type=tender 中最早的一条）；更新公告 = date 晚于主公告的 non-tender 公告，
  //   取最新。无主公告（纯 correction/contract 项目）由 stage 自身展示，不重复标。

Object.assign(F, { favKey, legacyFavKey, favBucketOf, getFavorites, isFavorite, toggleFavorite, favoriteItems, migrateLegacyFavorites, mergeFavLists, loadFavoritesFromServer, pushFavoritesToServer, scheduleFavPush, startFavSync, bindFavButtons, resolveFavorites, renderFavDeadRows });
