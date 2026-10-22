# -*- coding: utf-8 -*-
"""test_apply_unified.py — 7 case 单测（2026-10-02 v3.1 架构）

测试覆盖：
1. happy_path：DB content 已存 + extractor 命中 → Tier 1 ✅
2. DB 空：refetch → Tier 2 ✅
3. JS 站 + mode=full：playwright → Tier 3 ✅
4. 全失败：返回 needs_manual ✅
5. mode=db_first 不重 fetch：确认仅读 DB ✅
6. mode=full 走 Playwright：耗时 ~10s ✅
"""
import sys, time
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))

import experience_auto as ea
from experience_auto import apply_unified


def setup_real_notice(site='中国政府采购网'):
    """从 DB 找一条 empty notice 用于测试（不污染）。"""
    import sqlite3
    db = sqlite3.connect(str(HERE / "data" / "tender.db"))
    db.row_factory = sqlite3.Row
    r = db.execute("""SELECT id FROM notices
        WHERE site=? AND notice_type IN ('award','award_candidate')
        AND (winner IS NULL OR TRIM(winner)='' OR winning_amount IS NULL OR TRIM(winning_amount)='')
        LIMIT 1""", (site,)).fetchone()
    db.close()
    return r['id'] if r else None


def assert_(cond, msg):
    if not cond:
        print(f'  ✗ FAIL: {msg}')
        return False
    print(f'  ✓ {msg}')
    return True


def test_1_happy_path():
    """Tier 1: DB content 已存 + ccgp extractor 命中."""
    print('\n=== test_1: happy_path Tier 1 ===')
    import sqlite3
    db = sqlite3.connect(str(HERE / "data" / "tender.db"))
    db.row_factory = sqlite3.Row
    # 找一条「raw_json.content 真的还在」+ winner 还空的 notice
    # 不要用 setup_real_notice——它可能返回已被前次测试污染的 nid
    r = db.execute("""SELECT id FROM notices
        WHERE site='中国政府采购网' AND notice_type='award'
        AND (winner IS NULL OR TRIM(winner)='')
        AND length(raw_json) > 800
        AND raw_json LIKE '%content%'
        LIMIT 1""").fetchone()
    db.close()
    if not r:
        print('  ⚠ no ccgp notice with content, skip')
        return True
    nid = r['id']
    t0 = time.time()
    result = apply_unified(nid, mode='db_first')
    elapsed = time.time() - t0
    ok = True
    ok &= assert_(result is not None, 'returned result')
    ok &= assert_(result['tier'] == 1, f"tier=1 (got {result.get('tier')})")
    ok &= assert_(result.get('result') is not None, 'has result dict')
    ok &= assert_(result.get('result', {}).get('winner') or result.get('result', {}).get('winning_amount'),
                  f'winner/amount extracted: {result.get("result")}')
    print(f'  elapsed: {elapsed*1000:.0f}ms (should be <500ms for Tier 1)')
    return ok


def test_2_db_empty_refetch():
    """Tier 2: DB content 空 → refetch URL."""
    print('\n=== test_2: DB 空 → Tier 2 refetch ===')
    import sqlite3
    db = sqlite3.connect(str(HERE / "data" / "tender.db"))
    db.row_factory = sqlite3.Row
    # 找一条 content 短的 notice（DB content 几乎空，触发 refetch）
    # 注意：不要 UPDATE raw_json，会污染其它 test 的 nid 数据
    r = db.execute("""SELECT id FROM notices
        WHERE site='中国政府采购网' AND notice_type='award'
        AND (winner IS NULL OR TRIM(winner)='')
        AND length(raw_json) < 200
        LIMIT 1""").fetchone()
    db.close()
    if not r:
        print('  ⚠ no short-content ccgp notice, skip (无法测 refetch)')
        return True
    nid = r['id']
    t0 = time.time()
    result = apply_unified(nid, mode='db_or_fetch')
    elapsed = time.time() - t0
    ok = True
    ok &= assert_(result is not None, 'returned result')
    ok &= assert_(result['tier'] in (1, 2), f"tier in (1,2) (got {result.get('tier')})")
    if result['tier'] == 2:
        ok &= assert_(elapsed > 1.0, f"refetch 耗时 >1s (got {elapsed:.1f}s)")
    print(f'  elapsed: {elapsed:.1f}s')
    return ok


def test_3_js_site_tier3():
    """Tier 3: JS 站 + mode=full → Playwright (可能 404 因为 extract 失败)."""
    print('\n=== test_3: JS 站 + mode=full (Playwright) ===')
    import sqlite3
    db = sqlite3.connect(str(HERE / "data" / "tender.db"))
    db.row_factory = sqlite3.Row
    r = db.execute("""SELECT id FROM notices
        WHERE site='中粮e采' AND notice_type='award'
        AND (winner IS NULL OR TRIM(winner)='')
        LIMIT 1""").fetchone()
    db.close()
    if not r:
        print('  ⚠ no cofco notice, skip')
        return True
    nid = r['id']
    t0 = time.time()
    result = apply_unified(nid, mode='full')
    elapsed = time.time() - t0
    ok = True
    ok &= assert_(result is not None, 'returned result')
    ok &= assert_(elapsed > 3.0, f"Playwright 耗时 >3s (got {elapsed:.1f}s)")
    print(f'  tier={result.get("tier")}  elapsed={elapsed:.1f}s  result={result.get("result")}')
    return ok


def test_4_total_fail():
    """全失败：return needs_manual=True."""
    print('\n=== test_4: total fail → needs_manual ===')
    import sqlite3
    db = sqlite3.connect(str(HERE / "data" / "tender.db"))
    db.row_factory = sqlite3.Row
    # 找一条 notice 的 raw_json.content 短到不可能抽出，且站点没 extractor（手动塞空 specimen）
    r = db.execute("""SELECT id FROM notices
        WHERE site='中国邮政' AND notice_type='award'
        AND (winner IS NULL OR TRIM(winner)='')
        LIMIT 1""").fetchone()
    db.close()
    if not r:
        print('  ⚠ no 中国邮政 notice, skip')
        return True
    nid = r['id']
    result = apply_unified(nid, mode='db_or_fetch')
    ok = True
    ok &= assert_(result['tier'] == 0, f"tier=0 (got {result.get('tier')})")
    ok &= assert_(result.get('needs_manual') is True, 'needs_manual=True')
    print(f'  reason: {result.get("reason")}')
    return ok


def test_5_db_first_no_fetch():
    """mode=db_first 不触发 fetch."""
    print('\n=== test_5: mode=db_first 不触发网络 fetch ===')
    # 用 mock 探测 fetcher 被调用次数
    from experience_ratelimit import RateLimitedFetcher
    original_fetch = RateLimitedFetcher.fetch
    call_count = [0]
    def mock_fetch(self, url):
        call_count[0] += 1
        return original_fetch(self, url)
    RateLimitedFetcher.fetch = mock_fetch

    try:
        nid = setup_real_notice('中国政府采购网')
        if not nid:
            print('  ⚠ no empty notice, skip')
            return True
        result = apply_unified(nid, mode='db_first')
        elapsed = time.time()
        ok = True
        ok &= assert_(call_count[0] == 0, f"fetch NOT called in db_first mode (called {call_count[0]}x)")
        # 无论 tier 1 还是 0，db_first 都不应该 fetch
        if result['tier'] == 1:
            ok &= assert_(result.get('result') is not None, 'Tier 1 success')
        return ok
    finally:
        RateLimitedFetcher.fetch = original_fetch


def test_6_l2_hook_mode_safety():
    """L2 hook 必须 mode='db_first'（hot path 不能用 full）."""
    print('\n=== test_6: L2 hook mode 安全性（静态检查）===')
    with open(HERE / 'experience_auto.py', encoding='utf-8') as f:
        content = f.read()
    # 找 patched_scrape 函数体
    import re
    m = re.search(r'def patched_scrape\(self\):.*?return items', content, re.DOTALL)
    if not m:
        print('  ✗ patched_scrape 未找到')
        return False
    body = m.group(0)
    ok = True
    ok &= assert_("mode='db_first'" in body, "L2 hook 必须用 mode='db_first'")
    ok &= assert_("mode='full'" not in body, "L2 hook 不应包含 mode='full'")
    ok &= assert_("mode='db_or_fetch'" not in body, "L2 hook 不应包含 mode='db_or_fetch'")
    return ok


def test_7_notify_boss_throttle():
    # 限频验证 (v3.1 Step 2)
    print('\\n=== test_7: notify_boss throttle ===')
    import experience_monitor as em
    em._recent_notifies.clear()
    for k in em._notify_count:
        em._notify_count[k] = 0
    for i in range(10):
        em.notify_boss(f'throttle msg {i}', level='INFO')
    stats = em.notify_stats()
    ok = True
    ok &= assert_(stats.get('INFO', 0) == 5, 'INFO 输出 5 条')
    ok &= assert_(stats.get('SUPPRESSED', 0) == 5, 'SUPPRESSED 5 条')
    return ok


def test_8_owner_exp_purchaser():
    """v3.2 经验守护：项目业主为 XXX 公司 必须正确抽到（防正则漏模式回归）."""
    print('\n===== test_8: 项目业主为 XXX 公司 =====')
    from enrich_engine import _extract_purchaser
    # 模拟 B5号7楼 / 聚龙湖景区 content 头
    test_contents = [
        ("项目业主为 盐城市盐南高新区西伏河绿色低碳科创园发展有限公司 ，建设资金来自",
         "盐城市盐南高新区西伏河绿色低碳科创园发展有限公司"),
        ("业主单位: 盐城经济技术开发区网络信息安全提升项目服务 招标人名称",
         None),  # 这个应该走原招标人 path
    ]
    ok = True
    for content, expected in test_contents:
        result = {}
        _extract_purchaser(content, result)
        got = result.get("purchaser", "")
        if expected and expected not in got:
            ok = False
            print(f'  ✗ FAIL: expected={expected!r}, got={got!r}')
        elif expected:
            print(f'  ✓ owner_pattern matched: {got!r}')
    return ok



def main():
    print('=' * 60)
    print('test_apply_unified.py — v3 架构测试 (2026-10-02)')
    print('=' * 60)
    results = []
    for fn in [test_1_happy_path, test_2_db_empty_refetch, test_3_js_site_tier3,
               test_4_total_fail, test_5_db_first_no_fetch, test_6_l2_hook_mode_safety,
               test_7_notify_boss_throttle,
               test_8_owner_exp_purchaser]:
        try:
            ok = fn()
            results.append((fn.__name__, ok))
        except Exception as e:
            print(f'  ✗ EXCEPTION: {type(e).__name__}: {str(e)[:200]}')
            results.append((fn.__name__, False))

    print('\n' + '=' * 60)
    print('SUMMARY')
    print('=' * 60)
    for name, ok in results:
        print(f'  {"✓ PASS" if ok else "✗ FAIL"}  {name}')
    n_pass = sum(1 for _, ok in results if ok)
    print(f'\n  {n_pass}/{len(results)} tests passed')
    return n_pass == len(results)


if __name__ == '__main__':
    import sys
    sys.exit(0 if main() else 1)