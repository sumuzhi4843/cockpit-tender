# -*- coding: utf-8 -*-
"""去重存储 — 兼容层（2026-08-07 重构）

去重逻辑已并入 `store_db.py`（同一模块集中"存储与去重"）。
本文件保留为 re-export 兼容层，避免旧 import 断裂；新代码请直接
`from store_db import load_store, save_store, item_hash, is_pushed, mark_pushed`。
"""
import hashlib  # noqa: F401  （保持与旧版行为一致，避免外部依赖差异）
import json  # noqa: F401
import os  # noqa: F401

from store_db import (
    is_pushed,
    item_hash,
    load_store,
    mark_pushed,
    save_store,
)

__all__ = ["load_store", "save_store", "item_hash", "is_pushed", "mark_pushed"]
