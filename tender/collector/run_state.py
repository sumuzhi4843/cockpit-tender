#!/usr/bin/env python3
"""Atomic single-file runtime state for the tender pipeline.

Schema v1 keeps crawl, heartbeat, deployment, failures, manifest and fetch history
under logs/run_state.json. This module is both importable and a small CLI used by
shell scripts.
"""
from __future__ import annotations

import argparse
import json
import os
import threading
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any

LOG_DIR = Path(__file__).resolve().parent / "logs"
STATE_PATH = LOG_DIR / "run_state.json"
_LOCK_PATH = LOG_DIR / "run_state.lock"
_LOCAL_LOCK = threading.Lock()


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def empty_state() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "updated_at": _now(),
        "crawl": {},
        "heartbeat": {},
        "deployment": {},
        "failures": {},
        "manifest": {},
        "fetch_history": {},
    }


def load(path: Path = STATE_PATH) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("state root is not an object")
    except FileNotFoundError:
        data = empty_state()
    data.setdefault("schema_version", 1)
    for key in ("crawl", "heartbeat", "deployment", "failures", "manifest", "fetch_history"):
        data.setdefault(key, {})
    return data


def _atomic_write(data: dict[str, Any], path: Path = STATE_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data["schema_version"] = 1
    data["updated_at"] = _now()
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def update(section: str, value: dict[str, Any], merge: bool = False,
           path: Path = STATE_PATH) -> dict[str, Any]:
    if section not in {"crawl", "heartbeat", "deployment", "failures", "manifest", "fetch_history"}:
        raise ValueError(f"unknown section: {section}")
    import fcntl
    path.parent.mkdir(parents=True, exist_ok=True)
    with _LOCAL_LOCK, _LOCK_PATH.open("a+", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        data = load(path)
        if merge:
            current = data.get(section)
            if not isinstance(current, dict):
                current = {}
            current.update(deepcopy(value))
            data[section] = current
        else:
            data[section] = deepcopy(value)
        _atomic_write(data, path)
        return data


def get(section: str | None = None, path: Path = STATE_PATH) -> Any:
    data = load(path)
    return data if not section else data.get(section, {})


def main() -> int:
    parser = argparse.ArgumentParser(description="Read/update logs/run_state.json")
    sub = parser.add_subparsers(dest="cmd", required=True)
    pget = sub.add_parser("get")
    pget.add_argument("section", nargs="?")
    pget.add_argument("key", nargs="?")
    pset = sub.add_parser("set")
    pset.add_argument("section")
    pset.add_argument("json_value")
    pset.add_argument("--merge", action="store_true")
    args = parser.parse_args()
    if args.cmd == "get":
        value = get(args.section)
        if args.key:
            value = value.get(args.key, "") if isinstance(value, dict) else ""
        if isinstance(value, (dict, list)):
            print(json.dumps(value, ensure_ascii=False))
        elif value is not None:
            print(value)
        return 0
    value = json.loads(args.json_value)
    if not isinstance(value, dict):
        raise SystemExit("json_value must be an object")
    update(args.section, value, merge=args.merge)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
