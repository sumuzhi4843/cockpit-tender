// tender 板块域模块：util（2026-09-29 由单文件 IIFE 机械拆分；函数体零逻辑改动，
// 仅状态→S.、常量→K.、跨模块调用→F. 三类机械前缀改写。真源在 tender/frontend/tender/。）
import { S } from './state.js';
import * as K from './constants.js';
import { F } from './registry.js';

export function parseBidDeadline(str) {
    if (!str || !str.trim()) return null;
    const s = String(str).trim();

    // 中文格式：2026年7月27日 15:00
    const m = s.match(/(\d{4})年(\d{1,2})月(\d{1,2})日\s*(\d{1,2}):(\d{2})/);
    if (m) return new Date(+m[1], +m[2] - 1, +m[3], +m[4], +m[5]);

    // 标准格式：2026-08-07 09:00:00 | 2026-08-07 09:00 | 2026-08-07
    const parts = s.match(/^(\d{4})[-/](\d{1,2})[-/](\d{1,2})(?:\s+(\d{1,2}):(\d{2})(?::(\d{2}))?)?$/);
    if (parts) {
      let hour = +(parts[4] || 0);
      let day = +parts[3];
      if (hour === 24) { hour = 0; day += 1; }  // 24:00 → 次日 00:00
      return new Date(+parts[1], +parts[2] - 1, day, hour, +(parts[5] || 0), +(parts[6] || 0));
    }

    return null;
  }

  // ===== 收藏夹（2026-08-06）=====
  // localStorage 持久化，键为归一化项目名；数组顺序 = 收藏时间倒序（新在前）
  // ===== 收藏夹（2026-08-06）=====
  // 2026-08-17 双桶化：待投标桶（tender，招投标项目）+ 代挂网桶（intention，意向招标项目）
  // 2026-08-19 多账号隔离：localStorage key 加 user 前缀，避免同一浏览器不同账号串数据

export function formatBidTime(str) {
    if (!str) return '';
    const hasTime = /\d{1,2}:\d{2}/.test(str);
    const d = F.parseBidDeadline(str);
    if (!d) return str;
    const pad = n => String(n).padStart(2, '0');
    const datePart = `${d.getFullYear()}年${pad(d.getMonth() + 1)}月${pad(d.getDate())}日`;
    if (!hasTime) return datePart;  // 纯日期不带小时，避免显示 00时
    return `${datePart}${pad(d.getHours())}时`;
  }

  // —— 分类单条 item ——
  // 返回 'upcoming' | 'countdown' | 'history'
  // countdown = 现在起到 7 天内（含今天）未开标的项目，2026-08-07

export function classifyItem(item) {
    const d = F.parseBidDeadline(item.bid_deadline);
    const now = new Date();
    const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
    const tomorrow = new Date(today); tomorrow.setDate(tomorrow.getDate() + 1);
    const horizon = new Date(now.getTime() + 7 * 24 * 60 * 60 * 1000);

    if (d) {
      if (d < today) return 'history';
      if (d >= today && d < tomorrow) return 'countdown';
      if (d >= tomorrow && d <= horizon) return 'countdown';
      return 'upcoming';
    }

    // 无开标时间不能推断已经开标，归待开标并在列表标记“时间待确认”。
    return 'upcoming';
  }

  // ══════════════ 懒加载数据层（2026-09-07 P2，方案 docs/lazy-loading-plan.md）══════════════
  // API-only：SQLite/8002 是唯一项目数据出口。

export function normalizeProjectName(name) {
    return String(name || '')
      .replace(/[【】\[\]()（）]/g, '')
      .replace(/(?:采购|招标|询价|竞争性磋商|比选)?(?:公告|结果公示|中标结果公示|中标候选人公示|成交公告|成交结果公示)$/g, '')
      .replace(/\s+/g, '')
      .trim();
  }

  // 主状态与公告类型分开判定，确保一个公告只归属一个入口。
  // 2026-08-07：中标公示（publicity）从历史中标拆出——「中标/成交/中标候选人公示」类公示

export function isPublicity(item) {
    if (!item.isProject) return false;
    if (item.project_stage !== 'awarded') return false;
    const name = item.title || '';
    return K.PUBLICITY_KEYWORDS.some(kw => name.indexOf(kw) >= 0);
  }

export function getProjectStage(item) {
    if (item.isProject) {
      // 中标公示优先于历史中标（同一项目不应同时出现在两边）
      if (F.isPublicity(item)) return 'publicity';
      const stageMap = {
        upcoming: 'upcoming', upcoming_unknown_time: 'upcoming',
        opening_today: 'upcoming', awarded: 'awarded', candidate: 'awarded',
        pending_result: 'pending_result', intention: 'intention',
        failed: 'exceptions', terminated: 'exceptions', suspended: 'exceptions',
        correction: 'exceptions', contract: 'exceptions', unknown: 'exceptions',
      };
      // 2026-08-17：招标公告=全量（upcoming/时间待确认/今日开标都归 upcoming），
      // 开标倒计时由 F.isCountdown() 独立判定（与 upcoming 重叠不互斥），不再分流
      return stageMap[item.project_stage] || 'exceptions';
    }
    const type = item.notice_type || 'tender';
    if (type === 'award') return 'awarded';
    if (type === 'intention') return 'intention';
    if (type !== 'tender') return 'other';

    const timeStage = F.classifyItem(item);
    if (timeStage === 'upcoming') return 'upcoming';
    return S._linkedTenderNames.has(F.normalizeProjectName(item.title)) ? 'awarded-linked' : 'pending_result';
  }

  // 开标倒计时独立判定（2026-08-17）：截止当日 + 有开标时间 + 未开标 + 未来7天内

export function isCountdown(item) {
    if (!item || !item.bid_deadline) return false;
    const d = F.parseBidDeadline(item.bid_deadline);
    if (!d) return false;
    const now = new Date();
    const horizon = new Date(now.getTime() + 7 * 24 * 60 * 60 * 1000);
    return d > now && d <= horizon;
  }

export function parseGenDate(t) {
    // "2026-09-21 23:05:00" → Date（手动解析，避开 Safari 对 new Date(字符串) 的严格行为）
    const m = String(t || '').trim().match(/^(\d{4})-(\d{1,2})-(\d{1,2})[ T](\d{1,2}):(\d{1,2})/);
    return m ? new Date(+m[1], +m[2] - 1, +m[3], +m[4], +m[5]) : null;
  }

export function formatGenTime(genAt) {
    // 2026-09-21 方案 A：相对时间（数据新鲜度信号），宽度与数字卡同量级；精确时间走 value 的 title tooltip
    const d = F.parseGenDate(genAt);
    if (!d) return F.escapeHtml(String(genAt || '').trim() || '—');
    const diffMin = Math.floor((Date.now() - d.getTime()) / 60000);
    if (diffMin < 1) return '刚刚';
    if (diffMin < 60) return F.escapeHtml(diffMin + ' 分钟前');
    if (diffMin < 24 * 60) return F.escapeHtml(Math.floor(diffMin / 60) + ' 小时前');
    const today = new Date();
    const dayDiff = Math.round((new Date(today.getFullYear(), today.getMonth(), today.getDate())
      - new Date(d.getFullYear(), d.getMonth(), d.getDate())) / 86400000);
    if (dayDiff === 1) return F.escapeHtml('昨天 ' + d.getHours() + '时');
    return F.escapeHtml((d.getMonth() + 1) + '/' + d.getDate());
  }

export function genTimeFullText(genAt) {
    const d = F.parseGenDate(genAt);
    if (!d) return String(genAt || '—');
    const p = n => String(n).padStart(2, '0');
    return d.getFullYear() + '/' + (d.getMonth() + 1) + '/' + d.getDate() + ' ' + p(d.getHours()) + ':' + p(d.getMinutes());
  }

export function districtLabel(raw) {
    return K.DISTRICT_LABELS[raw] || raw || '—';
  }

  // —— 加载人工区县修订（2026-08-16）——

export function apiHeaders(extra) {
    const headers = Object.assign({}, extra || {});
    const cred = (window.Auth && window.Auth.getCred) ? window.Auth.getCred() : '';
    if (cred) headers['Authorization'] = 'Basic ' + cred;
    return headers;
  }

export function formatMoney(value) {
    const n = Number(value);
    if (!Number.isFinite(n) || n <= 0) return '—';
    if (n >= 100000000) return `${(n / 100000000).toFixed(2)}亿元`;
    if (n >= 10000) return `${(n / 10000).toFixed(2)}万元`;
    return `${n.toLocaleString('zh-CN', { maximumFractionDigits: 2 })}元`;
  }

  // —— 整数带千分位（2026-10-15 补：9-29 ESM 拆分时漏了 Utils→F 的重写，render.js 三处
  // 还在用 Utils.formatInt，但 formatInt 函数本身也没注册到 F。补回。）
export function formatInt(n) {
    const v = Number(n);
    if (!Number.isFinite(v)) return '—';
    return v.toLocaleString('en-US');
  }

export function formatYMD(d) {
    const y = d.getFullYear();
    const m = String(d.getMonth() + 1).padStart(2, '0');
    const day = String(d.getDate()).padStart(2, '0');
    return `${y}-${m}-${day}`;
  }

export function escapeHtml(text) {
    if (text == null) return '';
    const map = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
    return String(text).replace(/[&<>"']/g, m => map[m]);
  }

  // —— 平台列表模态弹窗 ——

  // 采集方式 / 留存方式 标签映射（2026-08-07：平台列表弹窗删网址、增两列）

export function buildPageList(cur, total) {
    if (total <= 7) return Array.from({ length: total }, (_, i) => i + 1);
    const pages = [1];
    const left = Math.max(2, cur - 1);
    const right = Math.min(total - 1, cur + 1);
    if (left > 2) pages.push('...');
    for (let i = left; i <= right; i++) pages.push(i);
    if (right < total - 1) pages.push('...');
    pages.push(total);
    return pages;
  }

  // —— 空状态 ——

export function emptyState(msg) {
    return `
      <div class="tender-empty">
        <span class="tender-empty__icon">📋</span>
        <span>${F.escapeHtml(msg)}</span>
      </div>
    `;
  }

  // —— HTML 转义 ——

Object.assign(F, { parseBidDeadline, formatBidTime, classifyItem, normalizeProjectName, isPublicity, getProjectStage, isCountdown, parseGenDate, formatGenTime, genTimeFullText, districtLabel, apiHeaders, formatMoney, formatInt, formatYMD, escapeHtml, buildPageList, emptyState });
