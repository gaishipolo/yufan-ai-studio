"""老归档盘 JSON 的读取约定（2026-11 重建前那套 `catalog/*.json`）。

## 为什么在 core

导入器当年被拆成两半（`modules/template_pack` 导素材包与版本、已下线的替换链
导调用档案），因为两边各自持有自己的表，而业务模块之间不许互相 import。
「老 JSON 字段有两种写法、坏文件只跳过不中断」这些约定两半都要用，所以放这里，
而不是各抄一份 —— 抄出来的两份迟早对同一个字段给出两种解读。

## 老 JSON 为什么有两种写法

`model_dump()` 出 snake_case、`model_dump(by_alias=True)` 出 camelCase，
历史上两种都写进过盘里。`pick()` 两种都认：猜错一种的表现是「导入成功但字段
全是默认值」，比报错难查得多。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from infra.archive import ArchiveStorage

__all__ = [
    "as_datetime",
    "dict_or_none",
    "load_json",
    "pick",
    "read_json",
    "read_text",
]


def pick(value: dict[str, Any], *keys: str, default: Any = None) -> Any:
    for key in keys:
        if key in value and value[key] is not None:
            return value[key]
    return default


def as_datetime(value: Any) -> datetime | None:
    if isinstance(value, str) and value:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    return None


def dict_or_none(value: Any) -> dict[str, Any] | None:
    return value if isinstance(value, dict) else None


def load_json(storage: ArchiveStorage, prefix: str, notes: list[str]) -> list[dict]:
    """枚举前缀下所有 JSON。单个文件坏掉只跳过它，不中断整次导入。"""
    rows: list[dict] = []
    for key in storage.list_keys(prefix, suffix=".json"):
        try:
            value = storage.read(key)
        except (OSError, ValueError) as exc:
            notes.append(f"读取 {key} 失败：{exc}")
            continue
        if isinstance(value, dict):
            rows.append(value)
        elif isinstance(value, list):
            rows.extend(item for item in value if isinstance(item, dict))
    return rows


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except (OSError, ValueError):
        return ""
