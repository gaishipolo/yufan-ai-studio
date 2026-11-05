"""模块注册表（自动发现版）。

历史版本这里是一份手写的 `MODULES` 列表，新增模块必须改本文件 ——
现已改为目录扫描，本文件只保留对外接口，不再需要维护。
"""

from __future__ import annotations

from api.discovery import (
    active_modules,
    describe,
    discover_modules,
    reset_discovery_cache,
)
from api.module import ModuleSpec

__all__ = [
    "ModuleSpec",
    "active_modules",
    "describe",
    "discover_modules",
    "reset_discovery_cache",
]
