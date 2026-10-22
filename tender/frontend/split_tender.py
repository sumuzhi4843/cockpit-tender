#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tender.js IIFE → ESM 机械拆分 v2（一次性迁移工具，评审后归档）

设计（消环核心）：
  state.js      所有原顶层 let 可变状态 + _projectCache → S.xxx（按原文语句迁移，保真）
  constants.js  所有纯常量（export const），零依赖
  registry.js   export const F = {}  —— 域模块把自身函数登记进 F
  <domain>.js   import S/K/F；内部调用一律 F.fn(...)、状态一律 S.x、常量一律 K.X
                模块之间互不 import → 无循环
  main.js       副作用 import 全部域模块 → 组 window.Tender（内联 onclick 依赖）→ App.register

改写规则：整词、前后无 [\\w.$]；声明首行单独重建，不被误改；字符串里 Tender.xxx 因带点号天然免疫。
"""
import re, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE / 'tender.js.bak-20260929-esm-split'
lines = SRC.read_text(encoding='utf-8').split('\n')

iife_start = next(i for i, l in enumerate(lines) if l == 'const Tender = (() => {')
iife_end = next(i for i, l in enumerate(lines) if i > iife_start and l == '})();')
decl_pat = re.compile(r'^  (async function|function|let|const|var) ([A-Za-z_$][\w$]*)')

chunks = []          # (name, kind, [lines])
for i in range(iife_start + 1, iife_end):
    if lines[i].startswith('  return {'):  # IIFE 尾部导出语句，不属于任何顶层声明块
        break
    m = decl_pat.match(lines[i])
    if m:
        chunks.append([m.group(2), m.group(1), [lines[i]]])
    elif chunks:
        chunks[-1][2].append(lines[i])
    elif not lines[i].strip() or lines[i].strip().startswith('//'):
        pass  # 首声明前的空行/注释，丢弃（首块自带注释）
    else:
        sys.exit(f'line {i+1}: top-level line before first decl: {lines[i][:60]!r}')
C = {c[0]: c for c in chunks}
ALL = [c[0] for c in chunks]

STATE = """_currentUser _favMigrated _favSyncStarted _favPushTimer _queryState _serverMeta _lazyMode
_projectCache _lazyTotal _allItems _data _districtOverrides _activeStage _sites _activeCat
_activeAmount _activeSme _activeIct _activeType _activeDate _activeOpenDate _activeDateKind
_activeSearch _overridesLoaded _currentPage _currentList _serverTotal _linkedTenderNames
_awardLinkMap _activeDistrict _districtItems _multiDocBound _catDocBound _catTriggerBound
_toastTimer _dropdownBound _menuMO _sitesData""".split()
CONSTS = """STRONG_KW FAV_KEY_BASE FAV_INTENTION_KEY_BASE SUMMARY_COLLAPSE_KEY FAV_LABELS USE_LAZY
PUBLICITY_KEYWORDS PAGE_SIZE TYPE_MAP PROJECT_STAGE_MAP DISTRICTS DISTRICT_LABELS
EDITABLE_DISTRICTS SCRAPE_METHOD_LABELS RETENTION_LABELS RETENTION_TITLES ICT_CAT_TOP_N
ICT_CAT_PALETTE""".split()
GROUPS = {
 'util': """parseBidDeadline formatBidTime classifyItem normalizeProjectName isPublicity
getProjectStage isCountdown parseGenDate formatGenTime genTimeFullText districtLabel apiHeaders
formatMoney formatYMD escapeHtml buildPageList emptyState""",
 'badge': """renderFavBtn renderItemBadge pickUpdateNotice renderUpdateBadge renderConfidence
getAwardInfo isCandidate awardBadge""",
 'fav': """favKey legacyFavKey favBucketOf getFavorites isFavorite toggleFavorite favoriteItems
migrateLegacyFavorites mergeFavLists loadFavoritesFromServer pushFavoritesToServer
scheduleFavPush startFavSync bindFavButtons renderLazyFavorites""",
 'api': """cacheProject cacheProjectAll findProjectCached fetchProjectById navStageToApi
buildQueryParams queryProjects lazyDecorateItem fetchMeta fetchUpdates fetchIctDist
syncQueryState collectAllItems loadDistrictOverrides""",
 'filters': """filterByStage filterBySites filterDate filterByDate filterByOpenDate filterByIct
filterByType filterByAmount filterBySme filterByCat filterBySearch filterByDistrict
renderStageNav renderStageNavCount bindStageNav""".replace('filterDate ', ' '),
 'controls': """renderMultiDropdown renderSingleDropdown bindSingleDropdown renderSingleChecks
renderCatDropdownTrigger bindMultiDropdown renderMultiChecks updateMultiTrigger
bindMultiDocClose renderDropdownHtml renderCatDropdown toggleCatDropdown closeCatDropdown
bindCatDropdown bindDropdown syncMenuFixedPos bindMenuFixedObservers renderDatePopover
bindDateTrigger bindDateFilter wireDatePopEvents renderCatDropdownLazy""",
 'render': """render renderLazyBody renderGroup renderAwardGroup renderCardList renderPagination
refreshContent bindPagination bindCardList""",
 'district': """renderDistrictModal saveDistrictOverride closeDistrictModal openDistrictModal
closeStageModal showStageTip showToast""",
 'modals': """closeSites closeIctPie buildPieSvg buildIctSlices buildIctSlicesFromCounts
showIctPie showSites""",
}
GROUPS = {k: v.split() for k, v in GROUPS.items()}
FN2MOD = {n: m for m, ns in GROUPS.items() for n in ns}
assigned = set(STATE) | set(CONSTS) | set(FN2MOD)
un = [n for n in ALL if n not in assigned]
extra = [n for n in assigned if n not in C]
if un or extra:
    sys.exit(f'UNASSIGNED={un} NOTFOUND={extra}')

FNAMES = set(FN2MOD)
Sset, Kset = set(STATE), set(CONSTS)
ORDER = sorted(list(FNAMES) + list(Sset) + list(Kset), key=len, reverse=True)
TOKEN = re.compile(r'(?<![\w.$])(?<!const )(?<!let )(?<!var )(?<!function )(?<!async )(' + '|'.join(map(re.escape, ORDER)) + r')\b')

def rewrite(text):
    # 不用 (?!) 冒号守卫（会误伤三元 `? NAME :`）；改用上下文判断对象键位置：
    # 前一个非空白字符是 { 或 , 且后一个非空白字符是 : → 键名，跳过。
    out = []
    last = 0
    for m in TOKEN.finditer(text):
        name = m.group(1)
        i = m.start()
        j = i - 1
        while j >= 0 and text[j] in ' \t\n\r':
            j -= 1
        k = m.end()
        while k < len(text) and text[k] in ' \t':
            k += 1
        if j >= 0 and text[j] in '{,' and k < len(text) and text[k] == ':':
            continue
        prefix = 'S.' if name in Sset else 'K.' if name in Kset else 'F.'
        out.append(text[last:i])
        out.append(prefix + name)
        last = m.end()
    out.append(text[last:])
    return ''.join(out)

OUT = HERE / 'tender'
OUT.mkdir(exist_ok=True)

# 局部遮蔽表：函数名 → (局部标识, 重命名)。由「非顶层声明撞顶层名」全库扫描得出，仅 1 例。
LOCAL_RENAME = {'renderMultiDropdown': ('renderGroup', 'renderGroupOpt')}

# ---------- state.js ----------
sb = ["// 招投标板块共享可变状态（2026-09-29 由 tender.js IIFE 顶层 let 原样迁出，勿手改语义）",
      "export const S = {};", ""]
for n in STATE:
    _, kind, body = C[n]
    b = body[:]
    first = b[0]
    if kind == 'const':      # _projectCache
        first = re.sub(r'^  const ' + n + r'\s*=', 'S.' + n + ' =', first)
    else:
        first = re.sub(r'^  let ' + n + r'\s*=', 'S.' + n + ' =', first)
    b[0] = first
    sb.append(rewrite('\n'.join(b)).rstrip())
    sb.append('')
(OUT / 'state.js').write_text('\n'.join(sb) + '\n', encoding='utf-8')

# ---------- constants.js ----------
cb = ["// 招投标板块纯常量（零依赖；值与原 tender.js 完全一致）", ""]
for n in CONSTS:
    body = C[n][2]
    first = re.sub(r'^  const ', 'export const ', body[0])
    seg = [first] + body[1:]
    cb.append('\n'.join(seg).rstrip())
    cb.append('')
(OUT / 'constants.js').write_text('\n'.join(cb) + '\n', encoding='utf-8')

# ---------- registry.js ----------
(OUT / 'registry.js').write_text(
    "// 域函数注册表：各模块加载时把函数登记进 F，跨模块调用一律走 F.xxx（消除模块循环）\n"
    "export const F = {};\n", encoding='utf-8')

# ---------- domain modules ----------
HEADER = ("// tender 板块域模块：{mod}（2026-09-29 由单文件 IIFE 机械拆分；函数体零逻辑改动，\n"
          "// 仅状态→S.、常量→K.、跨模块调用→F. 三类机械前缀改写。真源在 tender/frontend/tender/。）\n"
          "import { S } from './state.js';\n"
          "import * as K from './constants.js';\n"
          "import { F } from './registry.js';\n\n")
for mod, fns in GROUPS.items():
    used_state = set()
    out = []
    for n in fns:
        _, kind, body = C[n]
        b = body[:]
        # 局部遮蔽保护（全库扫描唯一冲突：renderMultiDropdown 内 const renderGroup 局部箭头，
        # 与全局 renderGroup(items, emptyReason) 同名不同签名）：先把局部重命名再走前缀重写
        if n in LOCAL_RENAME:
            old, new = LOCAL_RENAME[n]
            b = [re.sub(r'(?<![\w.$])' + old + r'\b', new, l) for l in b]
        if kind == 'async function':
            b[0] = re.sub(r'^  async function', 'export async function', b[0])
        else:
            b[0] = re.sub(r'^  function', 'export function', b[0])
        head = b[0]
        seg = rewrite('\n'.join(b))
        seg = seg.split('\n')
        seg[0] = head  # 声明行不参与改写（防 export function F.name）；默认参数引用状态的场景全库 grep 验证为 0
        out.append('\n'.join(seg).rstrip())
        out.append('')
    text = HEADER.replace('{mod}', mod) + '\n'.join(out)
    text += '\nObject.assign(F, { ' + ', '.join(fns) + ' });\n'
    (OUT / f'{mod}.js').write_text(text, encoding='utf-8')

# ---------- main.js (entry) ----------
mods = [m for m in GROUPS]
mb = ["// tender 板块入口：加载即注册全部域模块到 F；组装 window.Tender（内联 onclick 依赖）；App.register",
      "import { F } from './registry.js';",
      'import { S } from "./state.js";']
for m in mods:
    mb.append(f"import './{m}.js';")
mb.append('')
mb += ["// 内联 onclick 属性需要全局 Tender（与原 const Tender 等效的全局面）",
       "window.Tender = {",
       "  render: (...a) => F.render(...a),",
       "  showSites: (...a) => F.showSites(...a),",
       "  closeSites: (...a) => F.closeSites(...a),",
       "  showIctPie: (...a) => F.showIctPie(...a),",
       "  closeIctPie: (...a) => F.closeIctPie(...a),",
       "};",
       "",
       "// —— 注册为驾驶舱模块（原 tender.js 尾部逻辑等价搬入）——",
       "App.register({",
       "  id: 'tender',",
       "  title: '招投标信息栏',",
       "  // 2026-09-29：API-only，首页禁止请求全量 tender.json。",
       "  dataUrl: undefined,",
       "  mountId: 'tender-body',",
       "  render: (data, root) => F.render(data, root),",
       "});"]
(OUT / 'main.js').write_text('\n'.join(mb) + '\n', encoding='utf-8')

# ---------- self-check ----------
import subprocess, tempfile, shutil
tdir = Path(tempfile.mkdtemp())
fail = 0
for f in sorted(OUT.glob('*.js')):
    t = tdir / (f.stem + '.mjs')
    shutil.copy(f, t)
    r = subprocess.run(['node', '--check', str(t)], capture_output=True, text=True)
    if r.returncode:
        fail += 1
        print('SYNTAX-FAIL', f.name, r.stderr[:400])
shutil.rmtree(tdir)
print('files:', sorted(p.name for p in OUT.glob('*.js')))
print('lines per file:', {p.name: len(p.read_text(encoding="utf-8").splitlines()) for p in sorted(OUT.glob("*.js"))})
print('ALL-NAMES covered:', len(ALL), '→', len(assigned))
print('syntax failures:', fail)
