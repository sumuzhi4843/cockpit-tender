# -*- coding: utf-8 -*-
"""公告分类器 v2 — 兼容层（2026-08-07 重构）

分类逻辑已并入 `enrich_engine.py`（同一模块集中"识别与分类"）。
本文件保留为 re-export 兼容层，避免旧 import 断裂；新代码请直接
`from enrich_engine import classify_notice, classify_item`。
"""
from __future__ import annotations

from enrich_engine import (
    CLASSIFICATION_VERSION,
    Classification,
    classify_item,
    classify_notice,
)

__all__ = ["CLASSIFICATION_VERSION", "Classification", "classify_item", "classify_notice"]
