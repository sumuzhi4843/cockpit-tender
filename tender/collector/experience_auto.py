# -*- coding: utf-8 -*-
"""2026-10-01 L2 半自动钩子。

L1 → L2 切换说明：
- L1：手工跑 experience_apply.py --run 验证
- L2：BaseScraper.scrape() 末尾自动套用经验库（补 winner/winning_amount/manager 等空字段）

触发：每个 BaseScraper 子类调用 scrape() 后，对每条 item 找匹配经验（site+notice_type+url_pattern）；
若有空字段，自动 fetch 详情页调对应抽取器，原地修改 item dict。

失败/异常：log 记录（不影响主流程）；不抛错。

启用方式：
  from experience_auto import install
  install()    # 幂等，多次调用安全

典型调用：
  在 run.py / maintenance.py / deploy.sh 入口处 import + install() 一次。
"""
import json
import re
import sys
import time
import urllib.request
import importlib
import inspect
from pathlib import Path
from collections import defaultdict
from urllib.parse import urlparse

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))  # 让 experience_apply / experience_ratelimit / experience_monitor 可用
from experience_apply import load_samples, find_matching, get  # noqa: E402
from experience_ratelimit import RateLimitedFetcher, AbortError, load_config  # noqa: E402
from experience_monitor import BackfillLog, notify_boss, check_abort_conditions  # noqa: E402

_INSTALLED = False
_fetcher = None  # 全局 RateLimitedFetcher 单例（按 rate_limits.yaml 加载）


def _resolve_extractor(func_ref):
    """Path like 'a.b.c.ClassName.method_name' → (callable, arity) 或 (None, 0)。"""
    parts = func_ref.split(".")
    if len(parts) < 2:
        return None, 0
    func_name = parts[-1]
    second_last = parts[-2]
    is_class_method = second_last[:1].isupper() and "_" not in second_last
    project_root = HERE.parent.parent  # projects/cockpit/
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))
    try:
        if is_class_method and len(parts) >= 3:
            module_path = ".".join(parts[:-2])
            mod = importlib.import_module(module_path)
            cls = getattr(mod, second_last)
            extractor = getattr(cls, func_name)
        else:
            module_path = ".".join(parts[:-1])
            mod = importlib.import_module(module_path)
            extractor = getattr(mod, func_name)
    except Exception:
        return None, 0
    arity = len(inspect.signature(extractor).parameters)
    return extractor, arity


# 全局 fetcher 单例（带限速 + 重试 + CAPTCHA 检测）
def _get_fetcher():
    global _fetcher
    if _fetcher is None:
        _fetcher = RateLimitedFetcher()
    return _fetcher


def _fetch(url, timeout=15):
    """GET 一个 URL，失败返回 ''。L2 自动套用场景调。"""
    fetcher = _get_fetcher()
    text, err = fetcher.fetch(url)
    if err:
        # AbortError 不 catch —— 调用方需处理
        return ""
    return text


def _apply_one(item, exp, url):
    """对单条 item 跑一条经验，返回 [(field, value), ...] 已填字段。"""
    func_ref = exp.get("extractor_function", "")
    if not func_ref:
        return []
    extractor, arity = _resolve_extractor(func_ref)
    # 多参 extractor（如 _extract_bid_section_id(data, nt)、_infer_date(day_int)）
    # 是内部 helper，不是 fetcher —— scraper 主流程里已调过。不在 L2 自动套用范围内。
    if not extractor or arity != 1:
        return []
    html = _fetch(url)
    if not html:
        return []
    try:
        result = extractor(html)
    except Exception:
        return []
    if not isinstance(result, dict):
        return []
    applied = []
    for field, value in result.items():
        if value and not item.get(field):
            item[field] = value
            applied.append((field, value))
    return applied


def auto_apply_to_item(item, site=None, notice_type=None):
    """L2 主入口（单条）：对单条 item 找匹配经验并补字段（原地修改）。

    Returns [(exp_id, field, value), ...] 已填字段。
    """
    url = item.get("actual_detail_url") or item.get("url", "")
    if not url:
        return []
    site = site or item.get("site", "")
    notice_type = notice_type or item.get("notice_type", "")
    if not site or not notice_type:
        return []
    matched = find_matching(site, notice_type, url)
    applied = []
    for s in matched:
        try:
            for field, value in _apply_one(item, s, url):
                applied.append((s["id"], field, value))
        except Exception:
            pass
    return applied


def auto_apply_to_items(items, site=None, notice_type=None):
    """对 list of items 自动套用经验。Returns total applied list。"""
    out = []
    for it in items:
        out.extend(auto_apply_to_item(it, site, notice_type))
    return out


def _is_js_site(url):
    """简单判断是否 JS 渲染站（已知站点清单）。"""
    js_hosts = ('ecai.cofco.com', 'www.jszbcg.com', 'ycggzy.jszwfw.gov.cn')
    return any(h in url for h in js_hosts)


def _write_back_unified(db, notice_id, raw, result, tier):
    """统一写回：winner/winning_amount/manager + raw_json + log。"""
    winner = result.get('winner', '')
    amount = result.get('winning_amount', '')
    manager = result.get('manager', '')
    term_days = result.get('term_days')
    if not (winner or amount or manager or term_days):
        return False
    cur_raw = json.loads(raw or '{}')
    for k in ('winner', 'winning_amount', 'manager', 'term_days'):
        v = result.get(k, '')
        if v:
            cur_raw[k] = v
    db.execute(
        "UPDATE notices SET winner=?, winning_amount=?, raw_json=? WHERE id=?",
        (winner, amount, json.dumps(cur_raw, ensure_ascii=False), notice_id),
    )
    notify_boss(
        f"[tier={tier}] {notice_id[:14]} winner={winner[:25]!r} amount={amount!r}",
        level='INFO'
    )
    return True


def apply_unified(notice_id, db_path=None, mode='db_first'):
    """项目信息补充机制 v3 入口——3-tier 级联（DB → refetch → Playwright）。

    Args:
        notice_id: notice.id
        mode:
            'db_first' — 仅 Tier 1（DB content 直抽，10ms/条）。用于 L2 hook / 实时补。
            'db_or_fetch' — Tier 1+2（DB 空时 refetch URL）。用于 cron 凌晨补。
            'full' — Tier 1+2+3（含 Playwright 渲染）。用于一性补大站 JS 慢手动。
            'manual' — 全跑（Tier 1+2+3 全尝试），人标记 needs_manual 后跳过。

    Returns:
        {'tier': 1|2|3|0, 'result': dict or None, 'needs_manual': bool}
        tier=0 表示全失败。
    """
    import sqlite3
    db_path = db_path or (HERE / "data" / "tender.db")
    samples = load_samples()
    db = sqlite3.connect(str(db_path))
    db.row_factory = sqlite3.Row
    r = db.execute(
        "SELECT id, site, notice_type, winner, winning_amount, raw_json, url FROM notices WHERE id=?",
        (notice_id,),
    ).fetchone()
    if not r:
        db.close()
        return {'tier': 0, 'result': None, 'needs_manual': True, 'reason': 'notice not found'}

    raw = json.loads(r['raw_json'] or '{}')

    # 找匹配 experience
    matched = None
    for s in samples:
        if s.get('site') and s['site'] != r['site']:
            continue
        if 'notice_types' in s and r['notice_type'] not in s['notice_types']:
            continue
        url_pat = s.get('url_pattern', '')
        if url_pat and not re.search(url_pat, r['url']):
            continue
        matched = s
        break
    if not matched:
        db.close()
        return {'tier': 0, 'result': None, 'needs_manual': True, 'reason': 'no matching experience'}

    extractor, _ = _resolve_extractor(matched['extractor_function'])
    if not extractor:
        db.close()
        return {'tier': 0, 'result': None, 'needs_manual': True, 'reason': 'extractor not loadable'}

    # Tier 1: DB content 直抽（10ms）
    if mode in ('db_first', 'db_or_fetch', 'full', 'manual'):
        content = raw.get('content', '')
        if content and len(content) > 800:
            try:
                result = extractor(content)
            except Exception:
                result = None
            if result and isinstance(result, dict) and (result.get('winner') or result.get('winning_amount')):
                _write_back_unified(db, notice_id, r['raw_json'], result, tier=1)
                db.commit()
                db.close()
                return {'tier': 1, 'result': result, 'needs_manual': False}

    # Tier 2: refetch URL（500ms + 风控）
    if mode in ('db_or_fetch', 'full', 'manual'):
        fetcher = _get_fetcher()
        try:
            content, err = fetcher.fetch(r['url'])
        except Exception:
            content, err = None, 'fetch exception'
        if content and not err:
            try:
                result = extractor(content)
            except Exception:
                result = None
            if result and isinstance(result, dict) and (result.get('winner') or result.get('winning_amount')):
                _write_back_unified(db, notice_id, r['raw_json'], result, tier=2)
                db.commit()
                db.close()
                return {'tier': 2, 'result': result, 'needs_manual': False}

    # Tier 3: Playwright（5-10s/页，仅 JS 站 + mode=full/manual）
    if mode in ('full', 'manual') and _is_js_site(r['url']):
        from experience_playwright import fetch as playwright_fetch
        try:
            content, pw_err = playwright_fetch(r['url'])
        except Exception as e:
            content, pw_err = '', f'pw-import-error: {e}'
        if content and not pw_err:
            try:
                result = extractor(content)
            except Exception:
                result = None
            if result and isinstance(result, dict) and (result.get('winner') or result.get('winning_amount')):
                _write_back_unified(db, notice_id, r['raw_json'], result, tier=3)
                db.commit()
                db.close()
                return {'tier': 3, 'result': result, 'needs_manual': False}

    db.close()
    return {'tier': 0, 'result': None, 'needs_manual': True, 'reason': 'all tiers failed'}


def _unified_loop(notice_ids, mode='db_or_fetch', label=''):
    """批量循环 apply_unified（薄壳共用 helper）。"""
    stats = {'tier1': 0, 'tier2': 0, 'tier3': 0, 'fail': 0, 'skipped': 0}
    for nid in notice_ids:
        r = apply_unified(nid, mode=mode)
        if r is None:
            stats['skipped'] += 1
            continue
        tier = r.get('tier', 0)
        if tier == 1: stats['tier1'] += 1
        elif tier == 2: stats['tier2'] += 1
        elif tier == 3: stats['tier3'] += 1
        else: stats['fail'] += 1
    print(f"[unified_loop{label}] {stats}")
    return stats


def install():
    """L2 启用：把 apply_unified 钩进 BaseScraper.scrape()。幂等。

    ⚠️ hot path 必须用 mode='db_first'——禁止 'full'/'db_or_fetch'！
       ('full' 会触发 Playwright，主抓速度爆降 50-100 倍)
    """
    global _INSTALLED
    if _INSTALLED:
        return False
    try:
        from scrapers import BaseScraper
    except ImportError:
        print("  [experience_auto] BaseScraper 未找到，钩子未装")
        return False

    _original_scrape = BaseScraper.scrape

    def patched_scrape(self):
        items = _original_scrape(self)
        # L2 v3: 实时补（仅 Tier 1, 零网络，10ms/条）
        try:
            site = getattr(self, "name", "")
            for item in items:
                nid = item.get('id') or item.get('_id') or item.get('notice_id')
                if nid:
                    result = apply_unified(nid, mode='db_first')
                    if result and result.get('tier') and result['tier'] > 0:
                        # 同步 item dict（实时用，给下游看到）
                        for k, v in (result.get('result') or {}).items():
                            if v and not item.get(k):
                                item[k] = v
        except Exception as e:
            print(f"  [{getattr(self, 'name', '?')}] L2 钩子异常: {e}")
        return items

    BaseScraper.scrape = patched_scrape
    _INSTALLED = True
    print(f"[experience_auto] L2 钩子已装（BaseScraper.scrape 已 patch）")
    return True


def apply_from_db_content(db_path=None, max_n=500, sites_filter=None):
    """DEPRECATED 2026-10-02 — 项目信息补充机制 v3 后，所有入口统一为 apply_unified()。

    本函数保留为薄壳以兼容旧调用，内部走 _unified_loop(mode='db_first') 循环。

    Returns: {"scanned": int, "updated": int, "details": [...]} (旧格式)
    """
    import sqlite3
    db_path = db_path or (HERE / "data" / "tender.db")
    samples = load_samples()
    site_set = sorted({s["site"] for s in samples if s.get("site")})
    if sites_filter:
        site_set = [s for s in site_set if s in sites_filter]
    if not site_set:
        return {"scanned": 0, "updated": 0, "details": []}
    placeholders = ",".join("?" * len(site_set))
    db = sqlite3.connect(str(db_path))
    db.row_factory = sqlite3.Row
    rows = db.execute(
        f"""SELECT id, site, notice_type, url, title, winner, winning_amount, raw_json
            FROM notices
            WHERE site IN ({placeholders}) AND notice_type='award'
            AND (winner IS NULL OR TRIM(winner)='' OR winning_amount IS NULL OR TRIM(winning_amount)='')
            AND raw_json != ''
            ORDER BY site, id LIMIT ?""",
        (*site_set, max_n),
    ).fetchall()

    n_scanned = 0
    n_updated = 0
    details = []

    for r in rows:
        n_scanned += 1
        try:
            raw = json.loads(r["raw_json"] or "{}")
            content = raw.get("content", "")
            if not content:
                continue
            matched_exp_id = ""
            result = {}
            for s in samples:
                if s.get("site") and s["site"] != r["site"]:
                    continue
                if "notice_types" in s and r["notice_type"] not in s["notice_types"]:
                    continue
                url_pat = s.get("url_pattern", "")
                if url_pat and not re.search(url_pat, r["url"]):
                    continue
                func_ref = s.get("extractor_function", "")
                if not func_ref:
                    continue
                extractor, arity = _resolve_extractor(func_ref)
                if not extractor or arity != 1:
                    continue
                try:
                    result = extractor(content)
                except Exception:
                    continue
                if not isinstance(result, dict):
                    continue
                matched_exp_id = s["id"]
                break

            winner = result.get("winner", "")
            amount = result.get("winning_amount", "")
            manager = result.get("manager", "")
            if winner or amount or manager:
                # 写 DB
                for k in ("winner", "winning_amount", "manager"):
                    v = result.get(k, "")
                    if v:
                        raw[k] = v
                db.execute(
                    "UPDATE notices SET winner=?, winning_amount=?, raw_json=? WHERE id=?",
                    (winner, amount, json.dumps(raw, ensure_ascii=False), r["id"]),
                )
                n_updated += 1
                details.append((r["id"], winner, amount, matched_exp_id))
        except Exception:
            pass

    db.commit()
    db.close()
    notify_boss(
        f"✅ apply_from_db_content done | scanned={n_scanned} updated={n_updated} | sites={site_set}"
    )
    return {"scanned": n_scanned, "updated": n_updated, "details": details}


def apply_pending_to_db(db_path=None, max_n=100, sites_filter=None, dry_run=False):
    """L2 备选入口（带风控 + 干跑支持）：扫库回灌空 winner/winning_amount。

    Args:
        db_path: 默认 data/tender.db
        max_n: 最多处理多少条
        sites_filter: 只跑某些站（例：'/site': ['盐城市都市建设投资集团有限公司']）; None = 全量
        dry_run: True 只跑不写 DB（用于 Phase A 抽样验证）

    Returns: {"scanned": int, "updated": int, "details": [...], "stats": {...}}
    """
    import sqlite3
    db_path = db_path or (HERE / "data" / "tender.db")
    samples = load_samples()
    site_set = sorted({s["site"] for s in samples if s.get("site")})
    if sites_filter:
        site_set = [s for s in site_set if s in sites_filter]
    if not site_set:
        return {"scanned": 0, "updated": 0, "details": []}
    placeholders = ",".join("?" * len(site_set))
    db = sqlite3.connect(str(db_path))
    db.row_factory = sqlite3.Row
    rows = db.execute(
        f"""SELECT id, site, notice_type, url, title, winner, winning_amount, raw_json
            FROM notices
            WHERE site IN ({placeholders}) AND notice_type='award'
            AND (winner IS NULL OR winner='' OR winning_amount IS NULL OR winning_amount='')
            ORDER BY site, id LIMIT ?""",
        (*site_set, max_n),
    ).fetchall()

    fetcher = _get_fetcher()

    # 按 site 分组（每站单独 log + 独立 abort）
    rows_by_site = defaultdict(list)
    for r in rows:
        rows_by_site[r['site']].append(r)

    n_scanned = 0
    n_updated = 0
    details = []

    for site_name, site_rows in rows_by_site.items():
        if not site_rows:
            continue
        log = BackfillLog(site_name, dry_run=dry_run)
        host = urlparse(site_rows[0]['url']).netloc
        rate = fetcher.get_limit(host)
        batch_size = rate.batch_size
        batch_pause = rate.batch_pause_s
        max_per_session = rate.max_per_session

        # 截到 max_per_session 防 WAF 累计计数
        site_rows = site_rows[:max_per_session]

        n_success = n_fail = 0
        aborted = False
        for i, r in enumerate(site_rows):
            n_scanned += 1
            t0 = time.time()
            try:
                content, err = fetcher.fetch(r['url'])
            except AbortError as e:
                notify_boss(f"🛑 {site_name} AbortError: {e}", level="ABORT")
                log.write(r['id'], 'captcha', duration_ms=int((time.time()-t0)*1000), error=str(e)[:80])
                aborted = True
                break
            if err or not content:
                log.write(r['id'], 'fail', duration_ms=int((time.time()-t0)*1000), error=err or '')
                n_fail += 1
                should_abort, reason = check_abort_conditions(log, n_fail, i + 1)
                if should_abort:
                    notify_boss(f"⚠️ {site_name} abort: {reason}", level="ABORT")
                    aborted = True
                    break
                continue

            # 找匹配经验 + 抽字段
            matched_exp_id = ""
            result = {}
            for s in samples:
                if s.get("site") and s["site"] != r["site"]:
                    continue
                if "notice_types" in s and r["notice_type"] not in s["notice_types"]:
                    continue
                url_pat = s.get("url_pattern", "")
                if url_pat and not re.search(url_pat, r["url"]):
                    continue
                func_ref = s.get("extractor_function", "")
                if not func_ref:
                    continue
                extractor, arity = _resolve_extractor(func_ref)
                if not extractor or arity != 1:
                    continue
                try:
                    result = extractor(content)
                except Exception:
                    continue
                if not isinstance(result, dict):
                    continue
                matched_exp_id = s["id"]
                break

            winner = result.get("winner", "")
            amount = result.get("winning_amount", "")
            manager = result.get("manager", "")
            if winner or amount or manager:
                if dry_run:
                    log.write(r['id'], 'success_dryrun', duration_ms=int((time.time()-t0)*1000),
                              winner=winner, amount=amount)
                    n_success += 1
                else:
                    cur_raw = json.loads(r["raw_json"] or "{}")
                    for k in ("winner", "winning_amount", "manager"):
                        v = result.get(k, "")
                        if v:
                            cur_raw[k] = v
                    db.execute(
                        "UPDATE notices SET winner=?, winning_amount=?, raw_json=? WHERE id=?",
                        (winner, amount, json.dumps(cur_raw, ensure_ascii=False), r["id"]),
                    )
                    n_updated += 1
                    details.append((r["id"], winner, amount, matched_exp_id))
                    log.write(r['id'], 'success', duration_ms=int((time.time()-t0)*1000),
                              winner=winner, amount=amount)
                    n_success += 1
            else:
                log.write(r['id'], 'empty_result', duration_ms=int((time.time()-t0)*1000),
                          error='no winner/amount in page')
                n_fail += 1
                should_abort, reason = check_abort_conditions(log, n_fail, i + 1)
                if should_abort:
                    notify_boss(f"⚠️ {site_name} abort: {reason}", level="ABORT")
                    aborted = True
                    break

            # batch pause
            if (i + 1) % batch_size == 0:
                log.summarize(total=i + 1, success=n_success, fail=n_fail)
                time.sleep(batch_pause)

        log.summarize(total=len(site_rows), success=n_success, fail=n_fail)
        log.close()
        if aborted:
            # 这一站 abort 跳到下一站（不继续同站）
            continue

    db.commit()
    db.close()

    stats = fetcher.get_stats()
    notify_boss(
        f"✅ Backfill done | scanned={n_scanned} updated={n_updated} dry_run={dry_run} | "
        f"hosts={list(rows_by_site.keys())} | stats={stats}"
    )
    return {"scanned": n_scanned, "updated": n_updated, "details": details,
            "stats": stats, "dry_run": dry_run}


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description="L2 半自动钩子工具")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--install", action="store_true", help="装 L2 钩子到 BaseScraper")
    g.add_argument("--apply-pending", action="store_true", help="扫库回灌空字段（不依赖 scraper 重跑）")
    p.add_argument("--max", type=int, default=100, help="apply-pending 的最大处理数")
    args = p.parse_args()

    if args.install:
        ok = install()
        sys.exit(0 if ok else 1)
    elif args.apply_pending:
        result = apply_pending_to_db(max_n=args.max)
        print(f"L2 apply-pending: scanned={result['scanned']} updated={result['updated']}")
        for d in result["details"][:5]:
            print(f"  notice={d[0][:14]}  winner={d[1]!r}  amount={d[2]!r}  via={d[3]}")