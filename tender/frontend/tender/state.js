// 招投标板块共享可变状态（2026-09-29 由 tender.js IIFE 顶层 let 原样迁出，勿手改语义）
export const S = {};

S._currentUser = 'anonymous';
  try {
    const cred = (window.Auth && window.Auth.getCred) ? window.Auth.getCred() : '';
    if (cred) {
      const decoded = atob(cred);
      const u = (decoded.split(':')[0] || '').trim();
      if (u) S._currentUser = u;
    }
  } catch (e) { /* ignore */ }
  // 基于当前用户动态计算 key；保留读老 KEY（未迁移的用户）作为 fallback

S._favMigrated = false;

S._favSyncStarted = false;

  // 并集合并：服务端在前（新收藏在前），本地独有的补后；去重

S._favPushTimer = null;

S._queryState = {
    stage: 'all', district: '', cat: [], ict: [], sme: [], amount: [],
    type: [], site: [], date: null, openDate: null, q: '', sort: 'latest', page: 1,
  };

S._serverMeta = null;   // /meta 响应缓存（计数 + 站点元数据）

S._lazyMode = false;    // 运行时实际模式（K.USE_LAZY && API 可用；API 挂自动降级）

  // —— 项目缓存层（2026-09-07 P4）：弹窗/详情按 id 查找的统一入口 ——
  // lazy 模式：渲染时当前页入缓存；跨页项目未命中时 API 兕底并回填。
  // 全量模式：F.collectAllItems 时全量入缓存，行为与旧 S._allItems.find 等价。

S._projectCache = new Map();   // id → item

S._lazyTotal = 0;    // 当前查询的总条数（分页 UI 用）

S._allItems = [];   // 缓存全部条目

S._data = null;     // 缓存最近一次 F.render 的数据（下拉切换区县用）

S._districtOverrides = {};  // 人工区县修订（2026-08-16）：project_id → 区县原始值；F.loadDistrictOverrides 加载
  // 2026-08-04 多选筛选：空数组 = 全部

S._activeStage = 'all'; // 当前项目进度（主状态或辅助入口，单选；2026-08-06 默认全部）

S._sites = [];      // 监控网页多选（平台名）

S._activeCat = [];      // 行业大类多选（2026-08-17 改多选；[] = 全部）

S._activeAmount = [];   // 金额区间多选（2026-08-17 改多选）

S._activeSme = [];      // 中小微多选（2026-08-17 改多选）

S._activeIct = [];      // ICT 筛选（2026-09-22 简化：空=全部，['ict']=只看 ICT）

S._activeType = [];     // 政府/企业分类多选（2026-08-17 改多选）

S._activeDate = null;      // 发布日期筛选（2026-08-08：{from:'YYYY-MM-DD', to:'YYYY-MM-DD'} 或 null）

S._activeOpenDate = null;  // 开标时间筛选（2026-08-08：{from:'YYYY-MM-DD', to:'YYYY-MM-DD'} 或 null）

S._activeDateKind = 'publish';  // 当前弹层在编辑哪个：'publish' | 'open'（2026-08-08）

S._activeSearch = '';      // 标题关键词搜索（2026-08-04 P2）      // 标题关键词搜索（2026-08-04 P2）

S._overridesLoaded = false; // 区县修订是否已从 API 加载（2026-08-16）

  // —— 分页状态（Phase 4 分页）——

S._currentPage = 1;   // 当前页

S._currentList = [];  // 当前筛选后的列表（分页基于它切片）

S._serverTotal = 0;    // API 当前查询总数；>0 时列表只渲染服务端当前页

S._linkedTenderNames = new Set(); // 已关联中标公告的招标项目名

S._awardLinkMap = new Map();      // 中标公告名 → 关联信息

  // 公告类型映射

S._activeDistrict = 'all';   // 当前激活区县（2026-08-06：默认全部）

S._districtItems = [];          // 当前区县筛选后的条目

S._multiDocBound = false;

S._catDocBound = false;

S._catTriggerBound = new WeakSet();  // 防重复绑定（2026-08-04）

S._toastTimer = null;

// —— 维护期数据状态（2026-10-15 stale 软提示版，老板拍板：抓取不打扰、改数据短暂提示）——
S._dataState = 'ok';          // 'ok' | 'stale' | 'blocked'（每次 /meta、/projects、/updates、/ict_dist 成功后回写）
S._dataPhase = '';            // 当前 phase：collect/backfill/rebuild/verify/publish/...
S._dataLastPublishedAt = '';  // 上次成功发布的 manifest.generated_at（stale 期用于提示"已展示最新已发布版本"）
S._dataStateUpdatedAt = '';   // run_state.deployment.updated_at（stale/blocked 起始时间）
S._stalePollTimer = null;     // 30s 轮询 /api/tender/state 的 timer（id，setInterval 返回值）

// —— 收藏项缓存（2026-10-15）——
// 9-29 ESM 拆分后 S._allItems 只是当前页 40 个，但 favoriteItems / migrateLegacyFavorites 还在用它
// 查代挂网收藏。增加这个缓存，resolveFavorites (async, 走 API) 写入，favoriteItems 优先读。
S._favItemsCache = { tender: [], intention: [] };

S._dropdownBound = false;

  // 2026-08-23 重构：fixed-pos 机制抽成顶层函数（原为 F.bindDropdown 内闭包）。
  // 原实现两个问题导致菜单关不掉（见下方注释），本次一并修复。

S._menuMO = null;

  // 同步 dropdown menu 的 fixed 定位（2026-08-21 引入）：
  // 任意 .tender-multi 打开时，把 menu 临时 append 到 body 下 + fixed 定位
  // （脱离 row 的 overflow-x:auto + mask-image 限制，避免被裁切）；关闭时还原。
  // 2026-08-23 修复：关闭分支不再用后代选择器（menu 移到 body 后不再是
  // .tender-multi 的后代，旧选择器匹配不到 → 菜单永远残留），改为直接查所有
  // [data-fixed-pos] 的 menu，按 owner 是否仍 open 决定保留/移回/移除。

S._sitesData = null;

  // —— 2026-09-20 删除：顶栏「📢 更新内容」入口与对应弹窗（CHANGELOG_ITEMS / showChangelog / 绑定）已整体下线 ——

