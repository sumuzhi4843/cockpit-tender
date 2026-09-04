# -*- coding: utf-8 -*-
"""公告关联 v2 — 兼容层（2026-08-07 重构）

关联逻辑已并入 `project_engine.py`（可信关联 + 项目生命周期同层集中）。
本文件保留为 re-export 兼容层，避免旧 import 断裂；新代码请直接
`from project_engine import build`。
"""
from __future__ import annotations

from project_engine import build, report  # noqa: F401

__all__ = ["build", "report"]
