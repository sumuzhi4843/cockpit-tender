#!/usr/bin/env python3
"""Unified maintenance entry point for tender data repair jobs.

Examples:
  python3 maintenance.py list
  python3 maintenance.py backfill open-dates --days 30
  python3 maintenance.py retry sdicc
  python3 maintenance.py ocr jszbcg
  python3 maintenance.py rebuild ict

Arguments after the job name are passed through unchanged to the maintained script.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent

JOBS = {
    ("backfill", "open-dates"): "backfill_open_dates.py",
    ("backfill", "chinapost-pdfs"): "backfill_chinapost_pdfs.py",
    ("backfill", "chinapost-detail"): "backfill_chinapost_detail.py",
    ("backfill", "jszbcg-pdfs"): "backfill_jszbcg_pdfs.py",
    ("backfill", "ccgp-details"): "backfill_ccgp_details.py",
    ("backfill", "sufucai-detail"): "backfill_sufucai_detail.py",
    ("backfill", "sdicc"): "backfill_sdicc.py",
    ("backfill", "fake-missing-m3"): "fake_missing_m3_backfill.py",
    ("backfill", "jszbcg-ocr-m3"): "jszbcg_ocr_m3_backfill.py",
    ("retry", "sdicc"): "retry_sdicc.py",
    ("retry", "jszbcg-m3-errors"): "retry_jszbcg_m3_errors.py",
    ("ocr", "jszbcg"): "ocr_jszbcg_pdfs.py",
    ("ocr", "jszbcg-organize-m3"): "organize_jszbcg_ocr_with_m3.py",
    ("rebuild", "ict"): "rebuild_ict.py",
    ("rebuild", "projects"): "__projects__",
    ("rebuild", "validated"): "__validated__",
}


def list_jobs() -> None:
    for (group, name), script in sorted(JOBS.items()):
        print(f"{group:9} {name:24} {script}")


def run_internal(name: str) -> int:
    sys.path.insert(0, str(HERE))
    from project_engine import rebuild
    rebuild()
    if name == "__validated__":
        from verify_quality import check
        if check() != 0:
            print("quality check failed", file=sys.stderr)
            return 1
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Tender maintenance command")
    parser.add_argument("group", help="list/backfill/retry/ocr/rebuild")
    parser.add_argument("job", nargs="?")
    args, passthrough = parser.parse_known_args()
    if args.group == "list":
        list_jobs()
        return 0
    key = (args.group, args.job or "")
    script = JOBS.get(key)
    if not script:
        parser.error(f"unknown job: {' '.join(key)}; run 'maintenance.py list'")
    if script.startswith("__"):
        return run_internal(script)
    env = os.environ.copy()
    env["PYTHONPATH"] = str(HERE) + os.pathsep + env.get("PYTHONPATH", "")
    return subprocess.call([sys.executable, str(HERE / script), *passthrough], cwd=HERE, env=env)


if __name__ == "__main__":
    raise SystemExit(main())
