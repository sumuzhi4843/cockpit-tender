#!/usr/bin/env python3
"""Cockpit 最小备份库存扫描器：只读，不移动、不删除文件。"""
from __future__ import annotations
import hashlib, json, os, sqlite3
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "backups" / "INVENTORY.json"
REPORT = ROOT / "backups" / "INVENTORY.md"

SCAN_DIRS = [
    ROOT / "backups",
    ROOT / "tender" / "collector" / "backups",
    ROOT / "tender" / "collector" / "data" / "backups",
    ROOT / "tender" / "data" / "backups",
    ROOT / "enterprise" / "backups",
    ROOT / "enterprise" / "data",
    ROOT / "enterprise" / "db",
    ROOT / "manual" / "backups",
    ROOT / "manual" / "backup",
]
EXCLUDE_NAMES = {"INVENTORY.json", "INVENTORY.md"}

def classify(p: Path) -> str:
    s = str(p)
    if "/enterprise/" in s: return "enterprise"
    if "/manual/" in s: return "manual"
    if "/tender/" in s: return "tender"
    return "shared"

def kind(p: Path) -> str:
    n = p.name.lower()
    if n.endswith(".db") or ".db." in n: return "database"
    if n.endswith(".json") and ("tender" in n or "overview" in n): return "derived-json"
    if n in {"manifest.json", "inventory.json"}: return "metadata"
    if n.endswith(".md") or n.endswith(".html"): return "document"
    if n.endswith(".js") or n.endswith(".css") or n.endswith(".py") or n.endswith(".sh"): return "code"
    return "other"

def sha16(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""): h.update(chunk)
    return h.hexdigest()[:16]

seen = set(); files = []
for base in SCAN_DIRS:
    if not base.exists(): continue
    for p in base.rglob("*"):
        if not p.is_file() or p.name in EXCLUDE_NAMES or p in seen: continue
        seen.add(p)
        st = p.stat()
        rel = str(p.relative_to(ROOT))
        row = {"path": rel, "module": classify(p), "kind": kind(p), "size": st.st_size,
               "mtime": datetime.fromtimestamp(st.st_mtime).isoformat(timespec="seconds")}
        # 对大文件只做 size+mtime，避免库存扫描长时间占用；小文件做 hash 方便重复识别。
        if st.st_size <= 200 * 1024 * 1024: row["sha256_16"] = sha16(p)
        files.append(row)

by_hash = {}
for r in files:
    if r.get("sha256_16"):
        by_hash.setdefault((r["size"], r["sha256_16"]), []).append(r["path"])
duplicates = [{"size": k[0], "sha256_16": k[1], "paths": v} for k,v in by_hash.items() if len(v) > 1]
summary = {"files": len(files), "bytes": sum(x["size"] for x in files), "duplicate_groups": len(duplicates),
           "duplicate_bytes_estimate": sum((len(x["paths"])-1)*x["size"] for x in duplicates)}
data = {"schema_version": 1, "generated_at": datetime.now().isoformat(timespec="seconds"),
        "root": str(ROOT), "summary": summary, "duplicates": duplicates, "files": sorted(files, key=lambda x:x["size"], reverse=True)}
OUT.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
lines = ["# Cockpit 备份库存", "", f"生成时间：{data['generated_at']}", "", "## 汇总", "", f"- 文件：{summary['files']}", f"- 总大小：{summary['bytes']/1024/1024/1024:.2f} GiB", f"- 可疑重复组：{summary['duplicate_groups']}", f"- 重复释放估算：{summary['duplicate_bytes_estimate']/1024/1024/1024:.2f} GiB", "", "## 重复组（仅建议，不自动删除）", ""]
for d in sorted(duplicates, key=lambda x:x['size']*len(x['paths']), reverse=True)[:80]:
    lines += [f"### {d['size']/1024/1024:.1f} MiB · `{d['sha256_16']}`"] + [f"- `{p}`" for p in d['paths']] + [""]
lines += ["## 最大文件", ""]
for r in data['files'][:80]: lines.append(f"- {r['size']/1024/1024:.1f} MiB · {r['module']}/{r['kind']} · `{r['path']}`")
REPORT.write_text("\n".join(lines)+"\n", encoding="utf-8")
print(json.dumps({"inventory":str(OUT), "report":str(REPORT), **summary}, ensure_ascii=False))
