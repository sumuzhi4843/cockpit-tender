// tender 板块域模块：controls（2026-09-29 由单文件 IIFE 机械拆分；函数体零逻辑改动，
// 仅状态→S.、常量→K.、跨模块调用→F. 三类机械前缀改写。真源在 tender/frontend/tender/。）
import { S } from './state.js';
import * as K from './constants.js';
import { F } from './registry.js';

export function renderMultiDropdown(type, title, options, selected) {
    const sel = selected || [];
    // 2026-08-23：清空哨兵 __none__ → 全部不勾 + label“未选任何网页”
    const isNone = sel.length === 1 && sel[0] === '__none__';
    const isAll = sel.length === 0;
    const labelText = isNone ? '未选任何网页'
      : isAll ? '全部' + title
      : sel.length === 1 ? (options.find(o => o.key === sel[0]) || {}).label || sel[0]
      : `已选 ${sel.length} 项`;
    const allChecked = isAll;
    // 2026-08-23：跟其他按钮统一 ——「全部监控网页」改为列表顶部全宽选项（与站点项同样式），
    // 不再用头部独立「全选」按钮（样式不一致、不好找）
    const allItemHtml = `
      <button class="tender-multi__item ${isAll ? 'tender-multi__item--on' : ''}" data-key="__all__" data-type="${type}" type="button" role="option" aria-selected="${isAll}">
        <span class="tender-multi__check">${isAll ? '✓' : ''}</span>
        <span class="tender-multi__label">全部${F.escapeHtml(title)}</span>
      </button>
    `;
    // 2026-08-07：监控网页按政府/企业分组
    let itemsHtml;
    if (type === 'sites' && options.some(o => o.category)) {
      const govOpts = options.filter(o => o.category === '政府');
      const entOpts = options.filter(o => o.category === '企业');
      const renderGroupOpt = (label, opts, color) => {
        if (!opts.length) return '';
        const optsHtml = opts.map(o => {
          // 2026-08-23：全选（空数组）时菜单显示全勾；清空时不勾
          const on = isAll ? true : (isNone ? false : sel.includes(o.key));
          return `
            <button class="tender-multi__item ${on ? 'tender-multi__item--on' : ''}" data-key="${o.key}" data-type="${type}" type="button" role="option" aria-selected="${on}">
              <span class="tender-multi__check">${on ? '✓' : ''}</span>
              <span class="tender-multi__tag tender-multi__tag--${color}">${color === 'gov' ? '政' : '企'}</span>
              <span class="tender-multi__label">${F.escapeHtml(o.label)}</span>
            </button>`;
        }).join('');
        return `<div class="tender-multi__group">
          <div class="tender-multi__group-title">${label} <span class="tender-multi__group-count">${opts.length}</span></div>
          ${optsHtml}
        </div>`;
      };
      itemsHtml = allItemHtml + renderGroupOpt('政府', govOpts, 'gov') + renderGroupOpt('企业', entOpts, 'ent');
    } else {
      itemsHtml = allItemHtml + options.map(o => {
        const on = isAll ? true : (isNone ? false : sel.includes(o.key));
        return `
          <button class="tender-multi__item ${on ? 'tender-multi__item--on' : ''}" data-key="${o.key}" data-type="${type}" type="button" role="option" aria-selected="${on}">
            <span class="tender-multi__check">${on ? '✓' : ''}</span>
            <span class="tender-multi__label">${F.escapeHtml(o.label)}</span>
          </button>`;
      }).join('');
    }
    return `
      <div class="tender-multi tender-multi--${type}" data-multi="${type}">
        <button class="tender-multi__trigger${(!isNone && sel.length) ? ' tender-multi__trigger--active' : ''}${isNone ? ' tender-multi__trigger--empty' : ''}" data-multi-trigger="${type}" type="button" aria-haspopup="listbox" aria-expanded="false">
          <span class="tender-multi__label">${F.escapeHtml(labelText)}</span>
          <span class="tender-multi__caret">▾</span>
        </button>
        <div class="tender-multi__menu" role="listbox" aria-label="${F.escapeHtml(title)}" aria-multiselectable="true">
          <div class="tender-multi__head">
            <span class="tender-multi__head-label">${F.escapeHtml(title)}</span>
            <button class="tender-multi__clear" data-multi-clear="${type}" type="button">清空选择</button>
          </div>
          <div class="tender-multi__list">${itemsHtml}</div>
        </div>
      </div>
    `;
  }

  // 单选下拉（2026-08-07 重构：「全部」改为列表顶部全宽选项，与行业下拉风格一致、点击区更大）

export function renderSingleDropdown(type, title, options, active) {
    const sel = Array.isArray(active) ? active : (active === 'all' || !active ? [] : [active]);
    // 2026-08-24：与「全部监控网页」一致 —— 全部状态下所有选项也显示勾选
    const isAll = sel.length === 0;
    const labelText = sel.length === 0 ? '全部' + title
      : sel.length === 1 ? (options.find(o => o.key === sel[0]) || {}).label || sel[0]
      : `已选 ${sel.length} 项`;
    const allItemHtml = `
        <button class="tender-multi__item ${sel.length === 0 ? 'tender-multi__item--on' : ''}" data-key="__all__" data-single="${type}">
          <span class="tender-multi__check">${sel.length === 0 ? '✓' : ''}</span>
          <span class="tender-multi__label">全部${F.escapeHtml(title)}</span>
        </button>
    `;
    const itemsHtml = options.map(o => {
      const on = isAll ? true : sel.includes(o.key);
      return `
        <button class="tender-multi__item ${on ? 'tender-multi__item--on' : ''}" data-key="${o.key}" data-single="${type}">
          <span class="tender-multi__check">${on ? '✓' : ''}</span>
          <span class="tender-multi__label">${F.escapeHtml(o.label)}</span>
        </button>
      `;
    }).join('');
    return `
      <div class="tender-multi tender-multi--${type}" data-multi="${type}">
        <button class="tender-multi__trigger${sel.length ? ' tender-multi__trigger--active' : ''}" data-multi-trigger="${type}" type="button" aria-haspopup="listbox" aria-expanded="false">
          <span class="tender-multi__label">${F.escapeHtml(labelText)}</span>
          <span class="tender-multi__caret">▾</span>
        </button>
        <div class="tender-multi__menu">
          <div class="tender-multi__list">${allItemHtml}${itemsHtml}</div>
        </div>
      </div>
    `;
  }

  // 绑定单选下拉（2026-08-05；2026-08-17 改多选：菜单不缩回，可连续选择）

export function bindSingleDropdown(type) {
    const rootEl = document.querySelector(`[data-multi="${type}"]`);
    if (!rootEl) return;
    const trigger = rootEl.querySelector('.tender-multi__trigger');
    if (!trigger) return;
    trigger.addEventListener('click', (e) => {
      e.stopPropagation();
      document.querySelectorAll('.tender-multi--open').forEach(el => {
        if (el !== rootEl) {
          el.classList.remove('tender-multi--open');
          const otherTrigger = el.querySelector('.tender-multi__trigger');
          if (otherTrigger) otherTrigger.setAttribute('aria-expanded', 'false');
        }
      });
      rootEl.classList.toggle('tender-multi--open');
      // 2026-08-20：同步 aria-expanded
      trigger.setAttribute('aria-expanded', rootEl.classList.contains('tender-multi--open'));
    });

    trigger.addEventListener('keydown', (e) => {
      if (!['ArrowDown', 'Enter', ' '].includes(e.key) || rootEl.classList.contains('tender-multi--open')) return;
      e.preventDefault();
      trigger.click();
      requestAnimationFrame(() => {
        const firstItem = document.querySelector('.tender-multi__item[data-single="' + type + '"]');
        if (firstItem) firstItem.focus({ preventScroll: true });
      });
    });
    // 多选状态存取：type → 对应数组
    const getArr = () => {
      if (type === 'amount') return S._activeAmount;
      if (type === 'sme') return S._activeSme;
      if (type === 'ict') return S._activeIct;
      if (type === 'type') return S._activeType;
      return [];
    };
    const setArr = (arr) => {
      if (type === 'amount') S._activeAmount = arr;
      else if (type === 'sme') S._activeSme = arr;
      else if (type === 'ict') S._activeIct = arr;
      else if (type === 'type') S._activeType = arr;
    };
    rootEl.querySelectorAll('.tender-multi__item').forEach(item => {
      item.addEventListener('click', (e) => {
        e.stopPropagation();
        const raw = item.dataset.key;
        const key = raw === '__all__' ? 'all' : raw;
        let arr = getArr();
        if (key === 'all') {
          arr = [];  // 全部 = 清空
        } else {
          arr = arr.slice();
          const idx = arr.indexOf(key);
          if (idx >= 0) arr.splice(idx, 1); else arr.push(key);
        }
        setArr(arr);
        S._currentPage = 1;
        // 手动更新 ✓ 标记 + 触发器文字 + F.refreshContent（菜单保持打开，2026-08-17）
        F.renderSingleChecks(rootEl, arr);
        const labelEl = trigger.querySelector('.tender-multi__label');
        if (labelEl) {
          const titles = { ict: 'ICT', type: '分类', amount: '金额', sme: '中小微' };
          if (arr.length === 0) labelEl.textContent = '全部' + (titles[type] || '');
          else if (arr.length === 1) {
            // 2026-08-23：菜单打开时会被移到 body 下（8-21 fixed-pos 机制），
            // 容器内 querySelectorAll 查不到 item → 全局查 [data-single] 取中文 label
            const onItem = Array.from(document.querySelectorAll('[data-single="' + type + '"]'))
              .find(it => (it.dataset.key === '__all__' ? 'all' : it.dataset.key) === arr[0]);
            labelEl.textContent = onItem ? (onItem.querySelector('.tender-multi__label') || {}).textContent || arr[0] : arr[0];
          } else labelEl.textContent = `已选 ${arr.length} 项`;
        }
        F.refreshContent();
      });
    });
  }

  // 2026-08-17：更新单选下拉（现多选）的 ✓ 标记

export function renderSingleChecks(rootEl, arr) {
    // 2026-08-23：菜单打开时在 body 下（8-21 fixed-pos），容器内查不到 item → 全局查
    const type = rootEl.dataset.multi;
    document.querySelectorAll('[data-single="' + type + '"]').forEach(it => {
      const itKey = it.dataset.key === '__all__' ? 'all' : it.dataset.key;
      // 2026-08-24：全部状态下所有选项都勾选（与「全部监控网页」一致）
      const on = arr.length === 0 ? true : arr.includes(itKey);
      it.classList.toggle('tender-multi__item--on', on);
      const check = it.querySelector('.tender-multi__check');
      if (check) check.textContent = on ? '✓' : '';
    });
    // 2026-08-23 灵动：同步触发器“已筛选”状态（非全部时高亮 + 圆点）
    const t = rootEl.querySelector('.tender-multi__trigger');
    if (t) t.classList.toggle('tender-multi__trigger--active', arr.length > 0);
  }

  // 行业下拉触发（嵌在筛选栏里，2026-08-04）

export function renderCatDropdownTrigger() {
    return `
      <div class="tender-multi tender-multi--cat" data-multi="cat">
        <button class="tender-multi__trigger${(S._activeCat && S._activeCat.length) ? ' tender-multi__trigger--active' : ''}" data-multi-trigger="cat" type="button" aria-haspopup="listbox" aria-expanded="false">
          <span class="tender-multi__label" id="tender-cat-label">全部行业</span>
          <span class="tender-multi__caret">▾</span>
        </button>
        <div class="tender-multi__menu">
          <div class="tender-multi__list" id="tender-cat-menu"></div>
        </div>
      </div>
    `;
  }

  // 绑定多选下拉：trigger 开关 + 菜单项点击 + 全选/清空 + 点击外部关闭

export function bindMultiDropdown(type) {
    const rootEl = document.querySelector(`[data-multi="${type}"]`);
    if (!rootEl) return;
    const trigger = rootEl.querySelector('.tender-multi__trigger');
    if (!trigger) return;

    trigger.addEventListener('click', (e) => {
      e.stopPropagation();
      // 关闭其他下拉
      document.querySelectorAll('.tender-multi--open').forEach(el => {
        if (el !== rootEl) {
          el.classList.remove('tender-multi--open');
          const otherTrigger = el.querySelector('.tender-multi__trigger');
          if (otherTrigger) otherTrigger.setAttribute('aria-expanded', 'false');
        }
      });
      rootEl.classList.toggle('tender-multi--open');
      // 2026-08-20：同步 aria-expanded 与视觉一致（屏幕阅读器/键盘可达）
      trigger.setAttribute('aria-expanded', rootEl.classList.contains('tender-multi--open'));
    });

    trigger.addEventListener('keydown', (e) => {
      if (!['ArrowDown', 'Enter', ' '].includes(e.key) || rootEl.classList.contains('tender-multi--open')) return;
      e.preventDefault();
      trigger.click();
      requestAnimationFrame(() => {
        const firstItem = document.querySelector('.tender-multi__item[data-type="' + type + '"]');
        if (firstItem) firstItem.focus({ preventScroll: true });
      });
    });

    // 2026-08-23：头部「全选」按钮已移除（改为列表顶部「全部监控网页」项，与单选统一）
    // 清空 = 取消全部平台（2026-08-04 注释；2026-08-23 修复实现）
    // 原实现 S._sites = Object.keys(S._sitesData) 恰好等于全选全部站点（语义反了）。
    // 改为哨兵值 ['__none__']：F.filterBySites 匹配不到任何站点 → 列表空。
    const clearBtn = rootEl.querySelector('[data-multi-clear]');
    if (clearBtn) {
      clearBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        if (type === 'sites') S._sites = ['__none__'];
        S._currentPage = 1;
        F.renderMultiChecks(rootEl, type);
        F.refreshContent();
      });
    }
    // 平台项点击（多选 toggle；2026-08-23：__all__ = 全部监控网页，点它=全选）
    rootEl.querySelectorAll('.tender-multi__item').forEach(item => {
      item.addEventListener('click', (e) => {
        e.stopPropagation();
        const key = item.dataset.key;
        if (key === '__all__') {
          // 「全部监控网页」= 主开关：当前已是全部 → 取消全选（未选任何网页）；
          // 否则（未选/部分）→ 恢复全部。2026-08-24 按老板直觉修正。
          S._sites = (S._sites.length === 0) ? ['__none__'] : [];
        } else {
          // 从“未选（__none__）”点单项 → 以该项开始选择
          if (S._sites.length === 1 && S._sites[0] === '__none__') S._sites = [];
          // 从“全部”点单项 → 切到只选该项（选一个就是一个），
          // 之后每点一项累加（选两个就是两个）。2026-08-24 修正反选。
          const idx = S._sites.indexOf(key);
          if (idx >= 0) S._sites.splice(idx, 1); else S._sites.push(key);
          // 取消最后一个具体选项后保持“未选”，不能意外恢复为“全部”。
          if (S._sites.length === 0) S._sites = ['__none__'];
          // 手动选齐所有网页时自动归一为“全部”，避免冗余状态。
          const totalSites = document.querySelectorAll('.tender-multi__item[data-type="' + type + '"]:not([data-key="__all__"])').length;
          if (totalSites > 0 && S._sites.length === totalSites) S._sites = [];
        }
        // 2026-08-17 修复：不 F.render(S._data)（会重建筛选栏导致菜单缩回），
        // 改为手动更新 ✓ 标记 + 触发器文字 + F.refreshContent（只刷列表区）
        S._currentPage = 1;
        F.renderMultiChecks(rootEl, type);
        F.refreshContent();
      });
    });
    const menu = rootEl.querySelector('.tender-multi__menu');
    if (menu) {
      menu.addEventListener('keydown', (e) => {
        if (!['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(e.key)) return;
        const items = Array.from(document.querySelectorAll('.tender-multi__item[data-type="' + type + '"]'));
        if (!items.length) return;
        e.preventDefault();
        const current = items.indexOf(document.activeElement);
        let next = current;
        if (e.key === 'Home') next = 0;
        else if (e.key === 'End') next = items.length - 1;
        else if (e.key === 'ArrowDown') next = current < 0 ? 0 : (current + 1) % items.length;
        else next = current < 0 ? items.length - 1 : (current - 1 + items.length) % items.length;
        items[next].focus({ preventScroll: true });
      });
    }
  }

  // 2026-08-23：统一刷新多选下拉的三处视觉状态（勾选标记 / 全选高亮 / 触发器 label）
  // 全选/清空/单项点击都走这里，保证反馈一致：
  //   - 勾选标记：菜单里每个 item 的 ✓ 与 --on
  //   - 全选高亮：S._sites 为空（=全部）时全选按钮加粗
  //   - 触发器 label：全部 / 单站点名 / 已选 N 项 / 未选任何网页

export function renderMultiChecks(rootEl, type) {
    const sel = type === 'sites' ? S._sites : [];
    // 全选（空数组）时勾选所有 item；清空（哨兵 __none__）时全不勾。
    // 2026-08-23：视觉反馈与数据语义解耦——数据层 S._sites=[] 表示“全部”，
    // 但菜单里应显示 23 项全勾，避免“点了全选没反馈”。
    const isAll = sel.length === 0;
    const isNone = sel.length === 1 && sel[0] === '__none__';
    // 2026-08-23：菜单打开时在 body 下（8-21 fixed-pos），容器内查不到 item → 全局查
    document.querySelectorAll('.tender-multi__item[data-type="' + type + '"]').forEach(item => {
      const on = isAll ? true : (isNone ? false : sel.includes(item.dataset.key));
      item.classList.toggle('tender-multi__item--on', on);
      item.setAttribute('aria-selected', String(on));
      const check = item.querySelector('.tender-multi__check');
      if (check) check.textContent = on ? '✓' : '';
    });
    // 2. 全选按钮高亮（空数组 = 全部选中）—— 全选按钮在 menu 头部，同样可能在 body 下
    const allEl = document.querySelector('[data-multi-all="' + type + '"]');
    if (allEl) allEl.classList.toggle('tender-multi__all--on', isAll);
    // 3. 触发器 label
    const trigger = rootEl.querySelector('.tender-multi__trigger');
    F.updateMultiTrigger(rootEl, type, trigger);
  }

  // 2026-08-17：手动更新多选下拉触发器文字（不重建 DOM）

export function updateMultiTrigger(rootEl, type, trigger) {
    const sel = type === 'sites' ? S._sites : [];
    const labelEl = trigger.querySelector('.tender-multi__label');
    if (!labelEl) return;
    // 2026-08-23：哨兵值 __none__ = 清空（未选任何网页）
    if (sel.length === 1 && sel[0] === '__none__') {
      labelEl.textContent = '未选任何网页';
    } else if (sel.length === 0) {
      labelEl.textContent = '全部' + (type === 'sites' ? '监控网页' : '');
    } else if (sel.length === 1) {
      // 2026-08-23：菜单打开时在 body 下 → 全局查 item 取中文 label
      const opts = document.querySelectorAll('.tender-multi__item[data-type="' + type + '"]');
      const found = Array.from(opts).find(o => o.dataset.key === sel[0]);
      labelEl.textContent = found ? (found.querySelector('.tender-multi__label') || {}).textContent || sel[0] : sel[0];
    } else {
      labelEl.textContent = `已选 ${sel.length} 项`;
    }
    // 2026-08-23 灵动：同步触发器“已筛选”状态（清空哨兵不算筛选）
    const hasSel = sel.length > 0 && !(sel.length === 1 && sel[0] === '__none__');
    trigger.classList.toggle('tender-multi__trigger--active', hasSel);
    trigger.classList.toggle('tender-multi__trigger--empty', sel.length === 1 && sel[0] === '__none__');
  }

  // 点击外部关闭多选下拉（只绑一次，2026-08-04）

export function bindMultiDocClose() {
    if (S._multiDocBound) return;
    S._multiDocBound = true;
    document.addEventListener('click', (e) => {
      if (!e.target.closest('.tender-multi') && !e.target.closest('.tender-multi__menu[data-fixed-pos]')) {
        document.querySelectorAll('.tender-multi--open').forEach(el => {
          el.classList.remove('tender-multi--open');
          const trigger = el.querySelector('.tender-multi__trigger');
          if (trigger) trigger.setAttribute('aria-expanded', 'false');
        });
      }
    });
    document.addEventListener('keydown', (e) => {
      if (e.key !== 'Escape') return;
      document.querySelectorAll('.tender-multi--open').forEach(el => {
        el.classList.remove('tender-multi--open');
        const trigger = el.querySelector('.tender-multi__trigger');
        if (trigger) {
          trigger.setAttribute('aria-expanded', 'false');
          trigger.focus({ preventScroll: true });
        }
      });
    });
  }

  // —— 区县平铺栏（2026-08-06：原下拉菜单改为平铺一排展示）——

export function renderDropdownHtml(meta) {
    const items = K.DISTRICTS.map(d => {
      // 区县计数：lazy 模式（9-29 后 S._allItems 只是当前页 40 个）改走 meta.district_counts
      // （全库 2.5w+ 全量计数） 2026-10-15 补回：盐开区=盐南+经开，要遍历 meta.district_counts 累加
      let cnt;
      if (meta && meta.district_counts) {
        if (d.key === 'all') {
          cnt = (meta.summary && meta.summary.projects) || 0;
        } else {
          cnt = 0;
          for (const dk in meta.district_counts) {
            if (d.match(dk, null)) cnt += meta.district_counts[dk];
          }
        }
      } else {
        // 兑底：full 模式 (S._allItems 全量)
        cnt = (S._allItems || []).filter(i => d.match(i.std_district || '', i)).length;
      }
      return `<button class="tender-districts__btn ${d.key === S._activeDistrict ? 'tender-districts__btn--active' : ''}" data-district="${d.key}" type="button">${d.label}<span class="tender-districts__count">${cnt}</span></button>`;
    }).join('');
    return `<div class="tender-districts" id="tender-districts">${items}</div>`;
  }

  // —— 行业下拉菜单（2026-08-04）——
  // 排序：全部在最前，其余按数量降序，未分类强制最后

export function renderCatDropdown(items) {
    const label = document.getElementById('tender-cat-label');
    const menu = document.getElementById('tender-cat-menu');
    if (!label || !menu) return;

    // 统计行业分布
    const counts = {};
    for (const it of (items || [])) {
      const c = it.proj_major_cat || '未分类';
      counts[c] = (counts[c] || 0) + 1;
    }

    // 排序：全部 → 数量降序 → 未分类最后
    const cats = Object.keys(counts).sort((a, b) => {
      if (a === '未分类') return 1;
      if (b === '未分类') return -1;
      return (counts[b] || 0) - (counts[a] || 0);
    });
    const total = (items || []).length;

    // 标题：当前选中行业（多选 2026-08-17）
    label.textContent = S._activeCat.length === 0 ? '全部行业'
      : S._activeCat.length === 1 ? S._activeCat[0]
      : `已选 ${S._activeCat.length} 项`;

    // 菜单项：全部 + 各行业（排序后），带 ✓ 标记（行业为多选，2026-08-17）
    const allItem = `<button class="tender-multi__item ${S._activeCat.length === 0 ? 'tender-multi__item--on' : ''}" data-cat="all">
      <span class="tender-multi__check">${S._activeCat.length === 0 ? '✓' : ''}</span>
      <span class="tender-multi__label">全部行业</span>
    </button>`;
    const catItems = cats.map(c => {
      // 2026-08-24：全部行业状态下所有行业也显示勾选（与「全部监控网页」一致）
      const on = S._activeCat.length === 0 || S._activeCat.includes(c);
      return `
      <button class="tender-multi__item ${on ? 'tender-multi__item--on' : ''}" data-cat="${c}">
        <span class="tender-multi__check">${on ? '✓' : ''}</span>
        <span class="tender-multi__label">${c}</span>
      </button>`;
    }).join('');
    menu.innerHTML = allItem + catItems;

    // 绑定菜单项点击（多选 toggle，2026-08-17）
    menu.querySelectorAll('.tender-multi__item').forEach(item => {
      item.addEventListener('click', (e) => {
        e.stopPropagation();
        const key = item.dataset.cat;
        if (key === 'all') {
          S._activeCat = [];  // 全部 = 清空
        } else {
          const idx = S._activeCat.indexOf(key);
          if (idx >= 0) S._activeCat.splice(idx, 1); else S._activeCat.push(key);
        }
        S._currentPage = 1;
        // 手动更新 ✓ 标记 + 触发器文字 + F.refreshContent（菜单保持打开，2026-08-17）
        menu.querySelectorAll('.tender-multi__item').forEach(it => {
          const on = it.dataset.cat === 'all'
            ? S._activeCat.length === 0
            : (S._activeCat.length === 0 || S._activeCat.includes(it.dataset.cat));
          it.classList.toggle('tender-multi__item--on', on);
          const check = it.querySelector('.tender-multi__check');
          if (check) check.textContent = on ? '✓' : '';
        });
        label.textContent = S._activeCat.length === 0 ? '全部行业'
          : S._activeCat.length === 1 ? S._activeCat[0]
          : `已选 ${S._activeCat.length} 项`;
        // 2026-08-23 灵动：同步行业触发器“已筛选”状态
        const ddEl = document.querySelector('[data-multi="cat"]');
        const catTrigger = ddEl ? ddEl.querySelector('.tender-multi__trigger') : null;
        if (catTrigger) catTrigger.classList.toggle('tender-multi__trigger--active', S._activeCat.length > 0);
        F.refreshContent();
      });
    });
  }

  // —— 行业下拉展开/收起（2026-08-04 新 trigger 结构）——

export function toggleCatDropdown() {
    const dd = document.querySelector('[data-multi="cat"]');
    if (!dd) return;
    dd.classList.toggle('tender-multi--open');
    // 2026-08-20：同步 aria-expanded
    const t = dd.querySelector('[data-multi-trigger="cat"]');
    if (t) t.setAttribute('aria-expanded', dd.classList.contains('tender-multi--open'));
  }

export function closeCatDropdown() {
    const dd = document.querySelector('[data-multi="cat"]');
    if (dd) {
      dd.classList.remove('tender-multi--open');
      const t = dd.querySelector('[data-multi-trigger="cat"]');
      if (t) t.setAttribute('aria-expanded', 'false');
    }
  }

  // —— 绑定行业下拉触发（2026-08-04 适配多选结构）——
  // trigger 在 tender-body 内，每次 F.render 重建，须每次重新绑定；
  // document 级关闭监听只绑一次（事件委托，不受重建影响）

export function bindCatDropdown() {
    if (!S._catDocBound) {
      S._catDocBound = true;
      document.addEventListener('click', (e) => {
        if (!e.target.closest('.tender-multi--cat')) F.closeCatDropdown();
      });
    }
    const dd = document.querySelector('[data-multi="cat"]');
    if (!dd) return;
    const trigger = dd.querySelector('[data-multi-trigger="cat"]');
    if (!trigger || S._catTriggerBound.has(trigger)) return;
    S._catTriggerBound.add(trigger);
    trigger.addEventListener('click', (e) => {
      e.stopPropagation();
      F.toggleCatDropdown();
    });
  }

  // —— 区县人工修订弹窗（2026-08-16）——

export function bindDropdown() {
    if (S._dropdownBound) return;
    S._dropdownBound = true;
    // 2026-08-21：修复平板/手机 dropdown menu 被父级 overflow-x:auto + mask 裁切
    // 任意 .tender-multi 打开时，同步 menu 用 fixed 定位（脱离 row overflow/mask 限制）；
    // 关闭时复原。详见 CSS .tender-multi--open > .tender-multi__menu { position: fixed !important; }
    F.bindMenuFixedObservers();
    document.addEventListener('click', (e) => {
      // 已下架/终止状态徽章点击 → 状态提示（2026-08-16）
      const tipBtn = e.target.closest('[data-stage-tip]');
      if (tipBtn) {
        e.stopPropagation();
        const row = tipBtn.closest('tr') || tipBtn.closest('.tender-card');
        const pid = row ? (row.querySelector('[data-district-edit]')?.dataset.districtEdit ||
                           row.querySelector('[data-card-district-edit]')?.dataset.cardDistrictEdit) : '';
        if (pid) F.showStageTip(pid);
        return;
      }
      const editBtn = e.target.closest('[data-district-edit]');
      if (editBtn) {
        e.stopPropagation();
        F.openDistrictModal(editBtn.dataset.districtEdit);
        return;
      }
      // 移动端卡片区县点击（2026-08-16）
      const cardEdit = e.target.closest('[data-card-district-edit]');
      if (cardEdit) {
        e.stopPropagation();
        F.openDistrictModal(cardEdit.dataset.cardDistrictEdit);
        return;
      }
      const btn = e.target.closest('.tender-districts__btn');
      if (!btn) return;
      const key = btn.dataset.district;
      if (key === S._activeDistrict) return;
      S._activeDistrict = key;
      S._activeStage = 'all';
      S._sites = [];
      S._activeCat = [];
      S._activeAmount = [];
      S._activeSme = [];
      S._activeIct = [];
      S._activeType = [];
      S._activeDate = null;
      S._activeOpenDate = null;
      S._activeSearch = '';
      S._currentPage = 1;
      F.render(S._data);
    });
  }

  // —— 关联项目展示区（Phase 4）——

  // —— 日期筛选弹层（2026-08-08）——

export function syncMenuFixedPos() {
    // 打开中的菜单：移到 body + fixed 定位
    document.querySelectorAll('.tender-multi--open').forEach(root => {
      const trigger = root.querySelector('.tender-multi__trigger');
      const menu = root.querySelector('.tender-multi__menu');
      if (!trigger || !menu) return;
      // 2026-08-23：每次打开都重新绑定 owner（close 分支不再 delete _menuOwner，
      // 否则“关闭后再打开”时 _menuOwner 丢失 → close 分支把 menu 当无主直接 remove）
      menu._menuOwner = root;
      if (menu.parentElement !== document.body) document.body.appendChild(menu);
      const tRect = trigger.getBoundingClientRect();
      const vh = window.innerHeight, vw = window.innerWidth;
      // 先设 display 让 menu 有真实尺寸
      if (menu.style.display !== 'flex') menu.style.display = 'flex';
      const menuW = menu.offsetWidth || 180;
      const menuH = menu.offsetHeight || 100;
      let top = tRect.bottom + 6;
      if (top + menuH > vh - 8) top = Math.max(8, tRect.top - menuH - 6);
      let left = tRect.left;
      if (left + menuW > vw - 8) left = vw - menuW - 8;
      if (left < 8) left = 8;
      menu.style.position = 'fixed';
      menu.style.top = top + 'px';
      menu.style.left = left + 'px';
      menu.setAttribute('data-fixed-pos', '1');
    });
    // 已关闭的菜单：还原（直接查 [data-fixed-pos]，不限位置）
    document.querySelectorAll('.tender-multi__menu[data-fixed-pos]').forEach(menu => {
      const owner = menu._menuOwner;
      const ownerOpen = owner && document.contains(owner) && owner.classList.contains('tender-multi--open');
      if (ownerOpen) return;  // 仍打开：保留（open 分支负责定位）
      // 关闭：移回 owner；owner 已脱离 DOM（F.render 重建）→ 直接移除僵尸菜单
      if (owner && document.contains(owner)) {
        if (menu.parentElement !== owner) owner.appendChild(menu);
      } else {
        menu.remove();
      }
      menu.style.position = '';
      menu.style.top = '';
      menu.style.left = '';
      menu.style.display = '';
      menu.removeAttribute('data-fixed-pos');
      // 2026-08-23：不再 delete _menuOwner —— 删掉后“再打开”时 close 分支会
      // 把刚移到 body 的 menu 误判为无主而 remove（菜单从 DOM 消失）。
      // 绑定关系由 open 分支每次重设；F.render 重建后旧 menu 会被整体移除。
    });
  }

  // 注册/刷新 fixed-pos 观察（幂等；每次 F.render 重建筛选栏后必须重调）。
  // 2026-08-23：MutationObserver 只观察绑定时的元素，F.render 重建后的新
  // .tender-multi 不在观察列表 → 关闭时不会触发 F.syncMenuFixedPos → 菜单残留。
  // 故每次 F.render 后重新 observe 当前全部元素 + 重设 _menuOwner。

export function bindMenuFixedObservers() {
    if (typeof MutationObserver === 'undefined') return;
    if (!S._menuMO) {
      S._menuMO = new MutationObserver(F.syncMenuFixedPos);
      let raf = 0;
      const onScroll = () => {
        if (raf) return;
        raf = requestAnimationFrame(() => { raf = 0; F.syncMenuFixedPos(); });
      };
      window.addEventListener('scroll', onScroll, { passive: true, capture: true });
      window.addEventListener('resize', onScroll);
    }
    document.querySelectorAll('.tender-multi').forEach(root => {
      const menu = root.querySelector('.tender-multi__menu');
      if (menu) menu._menuOwner = root;
      S._menuMO.observe(root, { attributes: true, attributeFilter: ['class'] });
    });
  }

export function renderDatePopover() {
    if (!S._activeDate) S._activeDate = null;
    const today = new Date();
    const todayStr = F.formatYMD(today);
    const target = S._activeDateKind === 'open' ? S._activeOpenDate : S._activeDate;
    const fromDefault = target?.from || todayStr;
    const toDefault = target?.to || todayStr;

    // 预设区间（5 个） + 命中数（基于 S._districtItems）
    const presets = [
      { key: 'today', label: '今天', days: 0 },
      { key: '7d', label: '最近 7 天', days: 7 },
      { key: '30d', label: '最近 30 天', days: 30 },
      { key: 'month', label: '本月', days: -1 },
      { key: '3m', label: '最近 3 个月', days: 90 },
    ];
    const presetHtml = presets.map(p => {
      let cnt;
      if (p.days === -1) {
        // 本月
        const y = today.getFullYear(), m = today.getMonth();
        cnt = (S._districtItems || []).filter(i => {
          if (!i.date) return false;
          const d = new Date(i.date);
          return d.getFullYear() === y && d.getMonth() === m;
        }).length;
      } else if (p.days === 0) {
        cnt = (S._districtItems || []).filter(i => i.date === todayStr).length;
      } else {
        const from = new Date(today); from.setDate(from.getDate() - p.days + 1);
        const fromStr = F.formatYMD(from);
        cnt = (S._districtItems || []).filter(i => i.date >= fromStr && i.date <= todayStr).length;
      }
      return `<button class="tender-date-pop__preset" data-date-preset="${p.key}" type="button">
        <span>${p.label}</span><span class="tender-date-pop__preset-count">${cnt}</span>
      </button>`;
    }).join('');

    return `
      <div class="tender-date-pop" id="tender-date-pop">
        <div class="tender-date-pop__section">
          <div class="tender-date-pop__title">⚡ 快速区间</div>
          <div class="tender-date-pop__presets">${presetHtml}</div>
        </div>
        <div class="tender-date-pop__divider"></div>
        <div class="tender-date-pop__section">
          <div class="tender-date-pop__title">🗓 自定义区间</div>
          <div class="tender-date-pop__custom">
            <label>从</label><input type="date" id="tender-date-from" value="${fromDefault}" max="${todayStr}">
            <label>到</label><input type="date" id="tender-date-to" value="${toDefault}" max="${todayStr}">
          </div>
        </div>
        <div class="tender-date-pop__actions">
          <button class="tender-date-pop__btn" data-date-clear type="button">清空</button>
          <button class="tender-date-pop__btn tender-date-pop__btn--primary" data-date-apply type="button">应用筛选</button>
        </div>
      </div>
    `;
  }

export function bindDateTrigger(triggerEl, containerEl, openClass, kind) {
    if (!triggerEl || !containerEl) return;
    const closePop = () => {
      containerEl.classList.remove(openClass);
      const pop = document.getElementById('tender-date-pop');
      if (pop) pop.remove();
    };

    triggerEl.addEventListener('click', (e) => {
      e.stopPropagation();
      const isOpen = containerEl.classList.contains(openClass);
      // 关闭其他弹层
      document.querySelectorAll('.tender-multi--open').forEach(el => el.classList.remove('tender-multi--open'));
      closePop();
      if (isOpen) return;
      S._activeDateKind = kind;  // 记住当前弹层在编辑哪种日期
      containerEl.classList.add(openClass);
      containerEl.insertAdjacentHTML('beforeend', F.renderDatePopover());
      F.wireDatePopEvents(closePop);
    });
  }

export function bindDateFilter() {
    // 发布日期：桌面表头
    const pubDesktopTrigger = document.getElementById('tender-date-trigger');
    const pubDesktopContainer = pubDesktopTrigger ? pubDesktopTrigger.closest('th') : null;
    if (pubDesktopContainer) F.bindDateTrigger(pubDesktopTrigger, pubDesktopContainer, 'tender-th--open', 'publish');

    // 开标时间：桌面表头
    const openDesktopTrigger = document.getElementById('tender-open-trigger');
    const openDesktopContainer = openDesktopTrigger ? openDesktopTrigger.closest('th') : null;
    if (openDesktopContainer) F.bindDateTrigger(openDesktopTrigger, openDesktopContainer, 'tender-th--open', 'open');

    // 首次绑定：点击外部关闭（幂等）
    if (!F.bindDateFilter._docBound) {
      F.bindDateFilter._docBound = true;
      document.addEventListener('click', (e) => {
        if (!e.target.closest('.tender-date-pop') &&
            !e.target.closest('#tender-date-trigger') &&
            !e.target.closest('#tender-open-trigger')) {
          const pop = document.getElementById('tender-date-pop');
          if (pop) pop.remove();
          document.querySelectorAll('.tender-th--open').forEach(el => el.classList.remove('tender-th--open'));
        }
      });
    }
  }

export function wireDatePopEvents(closePop) {
    const today = new Date();
    const todayStr = F.formatYMD(today);

    // 根据 S._activeDateKind 选择操作的 state / 默认 from
    const target = S._activeDateKind === 'open' ? S._activeOpenDate : S._activeDate;
    const setTarget = (v) => {
      if (S._activeDateKind === 'open') S._activeOpenDate = v;
      else S._activeDate = v;
    };
    const defaultFrom = target ? target.from : todayStr;
    const defaultTo = target ? target.to : todayStr;

    const presets = document.querySelectorAll('[data-date-preset]');
    presets.forEach(btn => {
      btn.addEventListener('click', (e) => {
        e.stopPropagation();
        const key = btn.dataset.datePreset;
        let from, to;
        if (key === 'today') {
          from = to = todayStr;
        } else if (key === 'month') {
          const y = today.getFullYear(), m = today.getMonth();
          const first = new Date(y, m, 1);
          from = F.formatYMD(first);
          to = todayStr;
        } else {
          const days = key === '7d' ? 7 : key === '30d' ? 30 : 90;
          const f = new Date(today); f.setDate(f.getDate() - days + 1);
          from = F.formatYMD(f);
          to = todayStr;
        }
        setTarget({ from, to });
        S._currentPage = 1;
        F.render(S._data);
      });
    });

    const applyBtn = document.querySelector('[data-date-apply]');
    if (applyBtn) {
      applyBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        let from = document.getElementById('tender-date-from').value;
        let to = document.getElementById('tender-date-to').value;
        if (!from && !to) return;
        // 开标时间筛选时，「未设 from」默认用今天（开标是未来事件，不像发布那样需要取最早）
        if (!from) from = todayStr;
        if (!to) to = todayStr;
        if (from > to) [from, to] = [to, from];
        setTarget({ from, to });
        S._currentPage = 1;
        F.render(S._data);
      });
    }

    const clearBtn = document.querySelector('[data-date-clear]');
    if (clearBtn) {
      clearBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        setTarget(null);
        S._currentPage = 1;
        F.render(S._data);
      });
    }
  }

  // —— 渲染某分类的项目列表 ——

export function renderCatDropdownLazy() {
    const meta = S._serverMeta || { cat_counts: {} };
    const items = Object.entries(meta.cat_counts || {}).map(([c, n]) => ({ proj_major_cat: c }));
    items.push({ proj_major_cat: '未分类' });
    F.renderCatDropdown(items);
  }

  // 收藏夹视图（lazy 版）：收藏 key 列表 → 逐个 API 详情（收藏量小，可接受）

Object.assign(F, { renderMultiDropdown, renderSingleDropdown, bindSingleDropdown, renderSingleChecks, renderCatDropdownTrigger, bindMultiDropdown, renderMultiChecks, updateMultiTrigger, bindMultiDocClose, renderDropdownHtml, renderCatDropdown, toggleCatDropdown, closeCatDropdown, bindCatDropdown, bindDropdown, syncMenuFixedPos, bindMenuFixedObservers, renderDatePopover, bindDateTrigger, bindDateFilter, wireDatePopEvents, renderCatDropdownLazy });
