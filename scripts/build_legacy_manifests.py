#!/usr/bin/env python3
"""为 7 个 cockpit 历史快照补录 MANIFEST.json。"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
BACKUPS = ROOT / "backups"
TZ = ZoneInfo("Asia/Shanghai")

LEGACY_BACKUPS = (
    "responsive_v3_20260818_1208",
    "responsive_v4_20260818_1259",
    "multi_user_20260819_0042",
    "20260812_daterange",
    "20260813_entdetail_removed",
    "20260813_entgroup_style",
    "frontend_20260811",
)


def sha256_16(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()[:16]


def build_manifest(directory: Path) -> dict:
    target = directory / "MANIFEST.json"
    existing = None
    if target.is_file():
        with target.open(encoding="utf-8") as file:
            existing = json.load(file)

    files = sorted(
        path for path in directory.rglob("*")
        if path.is_file() and path.name != "MANIFEST.json"
    )
    relpaths = [path.relative_to(directory).as_posix() for path in files]
    if existing and existing.get("source") == "legacy_manual_snapshot":
        timestamp = existing["timestamp"]
    else:
        timestamp = datetime.fromtimestamp(
            directory.stat().st_mtime,
            TZ,
        ).strftime("%Y%m%d_%H%M%S")

    return {
        "timestamp": timestamp,
        "timestamp_source": "directory_mtime",
        "type": "legacy",
        "feature": directory.name,
        "reason": "历史备份补录（原备份无 MANIFEST）",
        "source": "legacy_manual_snapshot",
        "file_count": len(relpaths),
        "total_bytes": sum(path.stat().st_size for path in files),
        "files": relpaths,
        "checksums": {
            relpath: sha256_16(directory / relpath)
            for relpath in relpaths
        },
    }


def write_manifest(directory: Path, manifest: dict) -> None:
    target = directory / "MANIFEST.json"
    temporary = directory / ".MANIFEST.json.tmp"
    with temporary.open("w", encoding="utf-8") as file:
        json.dump(manifest, file, ensure_ascii=False, indent=2)
        file.write("\n")
    os.replace(temporary, target)


def main() -> None:
    for name in LEGACY_BACKUPS:
        directory = BACKUPS / name
        if not directory.is_dir():
            raise SystemExit(f"历史备份目录不存在：{directory}")
        manifest = build_manifest(directory)
        write_manifest(directory, manifest)
        print(
            f"✓ {name}: {manifest['file_count']} 个文件，"
            f"{manifest['total_bytes']} bytes"
        )

    print(f"\n已生成 {len(LEGACY_BACKUPS)} 个 legacy MANIFEST.json")


if __name__ == "__main__":
    main()
