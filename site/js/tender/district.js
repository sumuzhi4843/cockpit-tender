// tender 板块域模块：district（2026-09-29 由单文件 IIFE 机械拆分；函数体零逻辑改动，
// 仅状态→S.、常量→K.、跨模块调用→F. 三类机械前缀改写。真源在 tender/frontend/tender/。）
import { S } from './state.js';
import * as K from './constants.js';
import { F } from './registry.js';

export function renderDistrictModal(projectId) {
    const item = (S._allItems || []).find(i => i.id === projectId);
    if (!item) return null;
    const current = item.district_raw || '';
    const options = K.EDITABLE_DISTRICTS.map(d => {
      const on = d.vals.includes(current);
      return `
        <button class="tender-district-opt ${on ? 'tender-district-opt--on' : ''}" data-district-val="${d.save}" type="button">
          <span class="tender-district-opt__check">${on ? '✓' : ''}</span>
          ${d.label}
        </button>`;
    }).join('');
    const clearBtn = current
      ? `<button class="tender-district-opt tender-district-opt--clear" data-district-val="" type="button"><span class="tender-district-opt__check"></span>不标注（恢复自动）</button>`
      : '';
    const modal = document.createElement('div');
    modal.className = 'tender-district-modal';
    modal.innerHTML = `
      <div class="tender-district-modal__card" role="dialog" aria-modal="true" aria-label="修改区县">
        <button type="button" class="tender-district-modal__close" aria-label="关闭">✕</button>
        <div class="tender-district-modal__title">修改区县归属</div>
        <div class="tender-district-modal__sub">${F.escapeHtml(item.title)}</div>
        <div class="tender-district-modal__opts">${options}${clearBtn}</div>
        <div class="tender-district-modal__hint">修改后立即生效：筛选对应区县即可看到该项目。</div>
      </div>`;
    return modal;
  }

export async function saveDistrictOverride(projectId, district) {
    try {
      const res = await fetch('/api/tender/district', {
        method: 'PUT',
        headers: F.apiHeaders({ 'Content-Type': 'application/json' }),
        body: JSON.stringify({ project_id: projectId, district: district }),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        alert('保存失败：' + (err.error || res.status));
        return false;
      }
      const data = await res.json();
      S._districtOverrides = Object.assign({}, S._districtOverrides);
      if (data.district === '') {
        delete S._districtOverrides[projectId];
      } else {
        S._districtOverrides[projectId] = data.district;
      }
      return true;
    } catch (e) {
      console.error('区县修订保存失败:', e);
      alert('保存失败：网络异常');
      return false;
    }
  }

  // —— 轻量提示（2026-08-16：保存区县修订后的反馈）——

export function closeDistrictModal() {
    const m = document.getElementById('tender-district-modal');
    if (m) m.remove();
  }

  // 关闭项目状态弹窗（2026-08-16 复查：与区县弹窗互斥）

export function openDistrictModal(pid) {
    F.closeDistrictModal();
    F.closeStageModal();
    const modal = F.renderDistrictModal(pid);
    if (!modal) return;
    modal.id = 'tender-district-modal';
    modal._pid = pid;
    document.body.appendChild(modal);
    modal.querySelector('.tender-district-modal__close')?.focus();
    // 关闭：✕ / 点遮罩 / Esc（2026-08-16 复查修复）
    const closeHandler = () => F.closeDistrictModal();
    modal.querySelector('.tender-district-modal__close').addEventListener('click', closeHandler);
    modal.addEventListener('click', (e) => {
      if (e.target === modal) closeHandler();
    });
    const onKey = (e) => {
      if (e.key === 'Escape') {
        closeHandler();
        document.removeEventListener('keydown', onKey);
      }
    };
    document.addEventListener('keydown', onKey);
    // 选项点击 → 保存
    modal.querySelectorAll('[data-district-val]').forEach(btn => {
      btn.addEventListener('click', async () => {
        const val = btn.dataset.districtVal;
        const ok = await F.saveDistrictOverride(pid, val);
        if (ok) {
          F.closeDistrictModal();
          // lazy 模式 S._allItems 只是当前页，P4 缓存兑底
          const item = F.findProjectCached(pid) || (S._allItems || []).find(i => i.id === pid);
          const newLabel = val === '' ? '不标注' : F.districtLabel(val);
          // 当前区县筛选下项目移出视图时，提示用户（2026-08-16）
          const d = K.DISTRICTS.find(x => x.key === S._activeDistrict);
          const movedOut = d && d.key !== 'all' && item && !d.match(val);
          if (movedOut) {
            F.showToast(`已改为${newLabel}，当前「${d.label}」筛选下不再显示，可在${d.label}以外查看`);
          } else {
            F.showToast(`已改为${newLabel}`);
          }
          F.render(S._data);  // 重新渲染（筛选、计数、区县列全部刷新）
        }
      });
    });
  }

  // —— 项目状态提示弹窗（2026-08-16：已下架/终止/流标类硬信号提示）——

export function closeStageModal() {
    const m = document.getElementById('tender-stage-modal');
    if (m) m.remove();
  }

  // —— 绑定区县 ✎ 编辑（2026-08-16，事件委托已并入 F.bindDropdown；本函数保留空壳以防遗漏）——

export function showStageTip(pid) {
    // lazy 模式 S._allItems 只是当前页，P4 缓存兑底
    const item = F.findProjectCached(pid) || (S._allItems || []).find(i => i.id === pid);
    if (!item) return;
    const stageText = K.PROJECT_STAGE_MAP[item.project_stage] || item.project_stage || '未知状态';
    const stageReason = item.stage_reason || '';
    const map = {
      'failed': '该项目数据源已发布流标/废标公告，采购未成交。数据保留供追溯。',
      'terminated': '该项目数据源已发布终止公告，项目已终止。数据保留供追溯。',
      'suspended': '该项目数据源已发布暂停公告，项目暂停中。数据保留供追溯。',
      'correction': '该项目数据源发布过更正/澄清公告，项目仍在进行。',
      'contract': '该项目数据源已发布合同公告，项目已进入合同阶段。',
    };
    const tipText = map[item.project_stage] || '该项目当前状态：' + stageText + '。';
    // 2026-09-29 措辞重构（老板拍板）：「数据源最后见到」歧义 → 拆两行。
    // 第一行「最新公告」= 项目事件（publish_date + subtype 中文）；
    // 第二行 = 系统追踪状态：last_seen 实为抓取命中时间（在售项目每轮刷新），
    // 距最近一轮抓取（data.generated_at）≤26h 视为仍在挂网，超时才用「最后见到」措辞。
    const LATEST_SUBTYPE_ZH = {
      tender_notice: '招标公告', award_final: '中标/成交', correction: '更正/澄清',
      contract: '合同公告', intention: '采购意向', award_candidate: '中标候选人',
      terminated: '终止公告', failed: '流标/废标', suspended: '暂停公告', tender_reopen: '重新招标',
    };
    const subZh = LATEST_SUBTYPE_ZH[item.notice_subtype] ? ' · ' + LATEST_SUBTYPE_ZH[item.notice_subtype] : '';
    const latestLine = item.date ? `<br>最新公告：${F.escapeHtml(item.date)}${subZh}` : '';
    let seenLine = '';
    if (item.last_seen) {
      const genAt = S._data && S._data.generated_at
        ? new Date(String(S._data.generated_at).replace(' ', 'T')) : new Date();
      const seenAt = new Date(String(item.last_seen).replace(' ', 'T'));
      const fresh = !isNaN(genAt.getTime()) && !isNaN(seenAt.getTime())
        && (genAt.getTime() - seenAt.getTime()) <= 26 * 3600 * 1000;
      const stamp = F.escapeHtml(String(item.last_seen).slice(5, 16));  // MM-DD HH:mm（去秒）
      seenLine = fresh
        ? `<br>公告仍在数据源列表挂网 · 系统核对至 ${stamp}`
        : `<br>该项目已不在数据源列表 · 最后核对 ${stamp}，数据保留供追溯`;
    }
    const lastSeenLine = latestLine + seenLine;
    F.closeDistrictModal();
    F.closeStageModal();
    const modal = document.createElement('div');
    modal.className = 'tender-district-modal';
    modal.innerHTML = `
      <div class="tender-district-modal__card" role="dialog" aria-modal="true" aria-label="项目状态">
        <button type="button" class="tender-district-modal__close" aria-label="关闭">✕</button>
        <div class="tender-district-modal__title">项目状态：${F.escapeHtml(stageText)}</div>
        <div class="tender-district-modal__sub">${F.escapeHtml(item.title)}</div>
        <div class="tender-district-modal__hint">${F.escapeHtml(tipText)}${stageReason ? '<br>原因：' + F.escapeHtml(stageReason) : ''}${lastSeenLine}</div>
      </div>`;
    modal.id = 'tender-stage-modal';
    document.body.appendChild(modal);
    const close = () => modal.remove();
    modal.querySelector('.tender-district-modal__close').addEventListener('click', close);
    modal.addEventListener('click', (e) => { if (e.target === modal) close(); });
  }

  // —— 绑定区县按钮点击（2026-08-06：原下拉委托改为平铺按钮直接委托）——

export function showToast(msg, isErr) {
    let el = document.getElementById('tender-toast');
    if (!el) {
      el = document.createElement('div');
      el.id = 'tender-toast';
      el.className = 'tender-toast';
      document.body.appendChild(el);
    }
    el.textContent = msg;
    el.classList.toggle('tender-toast--error', !!isErr);
    el.classList.add('tender-toast--show');
    clearTimeout(S._toastTimer);
    S._toastTimer = setTimeout(() => el.classList.remove('tender-toast--show'), 2600);
  }

Object.assign(F, { renderDistrictModal, saveDistrictOverride, closeDistrictModal, openDistrictModal, closeStageModal, showStageTip, showToast });
