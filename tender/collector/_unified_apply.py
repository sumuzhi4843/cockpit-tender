# -*- coding: utf-8 -*-
"""_unified_apply.py — 一性回灌入口（CLI）。

用法：
    python3 _unified_apply.py --site "中国政府采购网" --mode db_or_fetch --max 5000
    python3 _unified_apply.py --site "中粮e采" --mode full --max 200

cron 集成（site_refresher.sh deep-a 末段示例）：
    python3 _unified_apply.py --site "中国政府采购网" --mode db_or_fetch --max 9999 >> logs/backfill/unified.log 2>&1
"""
import sys
import time
import re
import argparse
import json as _json
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))

import experience_auto as ea
from experience_auto import apply_unified


def main():
    parser = argparse.ArgumentParser(description='项目信息补充 v3 入口（一性）')
    parser.add_argument('--site', required=True, help='站名（如：中国政府采购网）')
    parser.add_argument('--notice-type', default='award', help='notice_type（默认 award）')
    parser.add_argument('--max', type=int, default=500, help='最多处理多少条')
    parser.add_argument('--mode', default='db_or_fetch',
                        choices=['db_first', 'db_or_fetch', 'full'],
                        help='apply_unified 的 mode')
    parser.add_argument('--dry-run', action='store_true', help='只统计不写 DB')
    args = parser.parse_args()

    import sqlite3
    db_path = HERE / "data" / "tender.db"
    db = sqlite3.connect(str(db_path))
    db.row_factory = sqlite3.Row

    notice_ids = [r['id'] for r in db.execute("""SELECT id FROM notices
        WHERE site=? AND notice_type=?
        AND (winner IS NULL OR TRIM(winner)='' OR winning_amount IS NULL OR TRIM(winning_amount)='')
        ORDER BY id LIMIT ?""", (args.site, args.notice_type, args.max)).fetchall()]
    db.close()

    if not notice_ids:
        print(f'[unified_apply] {args.site}: 无空字段 notice，exit')
        return 0

    print(f'[unified_apply] site={args.site} mode={args.mode} max={args.max} sample={len(notice_ids)}')
    t0 = time.time()
    stats = {'tier1': 0, 'tier2': 0, 'tier3': 0, 'fail': 0, 'skipped': 0, 'no_match': 0}
    for nid in notice_ids:
        if args.dry_run:
            # dry-run: 只读不写，直接调 extractor 不走写回
            import sqlite3, json as _json
            from experience_apply import load_samples, find_matching
            from experience_auto import _get_fetcher, _resolve_extractor, _is_js_site
            db = sqlite3.connect(str(HERE / "data" / "tender.db"))
            db.row_factory = sqlite3.Row
            r = db.execute("SELECT id, site, notice_type, url, raw_json FROM notices WHERE id=?", (nid,)).fetchone()
            db.close()
            raw = _json.loads(r['raw_json'] or '{}')
            samples = load_samples()
            matched = None
            for s in samples:
                if s.get('site') == r['site'] and (re.search(s.get('url_pattern', ''), r['url']) if s.get('url_pattern') else True):
                    matched = s; break
            if matched:
                extractor, _ = _resolve_extractor(matched['extractor_function'])
                content = raw.get('content', '')
                tier = 0
                if content and len(content) > 800:
                    try:
                        result = extractor(content)
                        if result and (result.get('winner') or result.get('winning_amount')):
                            tier = 1
                    except Exception: pass
                if tier == 1: stats['tier1'] += 1
                elif tier == 0: stats['fail'] += 1
            else:
                stats['no_match'] += 1
            continue
        result = apply_unified(nid, mode=args.mode)
        if result is None:
            stats['skipped'] += 1
            continue
        tier = result.get('tier', 0)
        if result.get('reason') == 'no matching experience':
            stats['no_match'] += 1
            continue
        if tier == 1: stats['tier1'] += 1
        elif tier == 2: stats['tier2'] += 1
        elif tier == 3: stats['tier3'] += 1
        else: stats['fail'] += 1

    elapsed = time.time() - t0
    print(f'\n[unified_apply] {args.site} ({args.mode}):')
    print(f'  total: {len(notice_ids)}  elapsed: {elapsed:.1f}s')
    for k, v in stats.items():
        print(f'  {k}: {v}')
    return 0


if __name__ == '__main__':
    sys.exit(main())