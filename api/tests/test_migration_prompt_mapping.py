"""迁移脚本里三处纯映射的守卫。

脚本在 `scripts/`（一次性运维），不是包，所以这里按路径加载模块 —— 只为拿
`_spec_key` / `_numbering` / `_translate` 三个纯函数。它们决定"迁出来的草稿对不对"，
比脚本本身的接线更值得钉：

- key 撞车 → 第二个功能被当成"已存在"跳过（实测撞过）；
- 编号映射漏一个 → 提示词指向不存在的编号，草稿发布得了、跑起来是错的；
- 整组 `*` 代替单张 → 把"以某一张为准"改成"全都要"，语义变了却不报错。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "migrate_templates_to_specs.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("migrate_templates_to_specs", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    # 必须先登记再 exec：脚本用了 `from __future__ import annotations` + dataclass，
    # `dataclasses` 会按 `cls.__module__` 去 `sys.modules` 里找模块，找不到就炸。
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def migration() -> ModuleType:
    return _load()


def test_spec_key_stays_readable_and_unique(migration: ModuleType) -> None:
    """`ai_<hex>_product_swap` 这种自动 key：留函数名、末尾带 id 短码。"""
    key = migration._spec_key("ai_43e97f06c0545366a9aa387248c7456c_product_swap", "c640b590f0")
    assert key == "mig-product-swap-c640b5", key
    # 同一份 manifest 的两个功能必须分得开（截断会算出同一个 key）。
    other = migration._spec_key("ai_43e97f06c0545366a9aa387248c7456c_model_swap", "0a2263e484")
    assert key != other


def test_spec_key_fits_short_keys_whole(migration: ModuleType) -> None:
    assert migration._spec_key("milan_night_3030_pink", "abc123") == "mig-milan-night-3030-pink"


def test_numbering_maps_pack_members_individually(migration: ModuleType) -> None:
    """封装槽位占 N 个号，每个号指向 `.j`，不是整组 `*`。"""
    pack = migration.PackPlan(name="p", items=[("a", "", ""), ("b", "", "")])
    slots = [
        {"key": "source", "source": "asset", "binding": "fixed"},
        {"key": "views", "source": "pack", "binding": "fixed"},
    ]
    mapping = migration._numbering(slots, pack)
    assert mapping == {
        1: "{{slot:source}}",
        2: "{{slot:views.1}}",
        3: "{{slot:views.2}}",
    }


def test_translate_rewrites_numbers_and_reports_gaps(migration: ModuleType) -> None:
    mapping = {1: "{{slot:source}}", 2: "{{slot:views.1}}"}
    text, missing = migration._translate("把 {{Mixed 1}} 换成 {{Mixed 2}}", mapping)
    assert text == "把 {{slot:source}} 换成 {{slot:views.1}}"
    assert missing == []

    text, missing = migration._translate("以 {{Mixed 3}} 为准", mapping)
    assert missing == [3]
    # 找不到落点时原样留着：调用方据此整条跳过，而不是写一份引用不到的草稿。
    assert "{{Mixed 3}}" in text
