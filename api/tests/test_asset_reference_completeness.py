"""引用来源完备性：fail-closed 的运行时行为，而不是「数够两个就放行」。

素材彻底删除不可逆。这里逐条验证「来源不齐 / 来源异常 / 声明重复」时删除都会被
挡住，以及只读查询仍能显式绕过闸门。
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from core.exceptions import Unavailable
from core.ports import assets as port


class _FakeChecker:
    def __init__(self, result: dict[str, list[str]] | None = None, raises: bool = False) -> None:
        self.result = result or {}
        self.raises = raises
        self.calls = 0

    async def references(self, asset_ids: Any) -> dict[str, list[str]]:
        self.calls += 1
        if self.raises:
            raise RuntimeError("引用库暂时不可达")
        return self.result


def _source(name: str, checker: _FakeChecker) -> port.AssetReferenceSource:
    return port.AssetReferenceSource(name=name, checker=checker, location=f"tests/{name}")


def _spec(module: str, *sources: port.AssetReferenceSource) -> SimpleNamespace:
    return SimpleNamespace(name=module, asset_reference_sources=sources)


def test_declared_but_unregistered_source_refuses_delete() -> None:
    """最危险的情形：模块声明了来源，import 期却忘了注册实现。"""
    port.reset_asset_reference_checker()
    port.bind_asset_reference_sources("task", [_source("task.refs", _FakeChecker())])
    assert not port.reference_source_gaps()

    port.declare_expected_reference_sources(
        [_spec("template", _source("pack.refs", _FakeChecker()))]
    )
    gaps = port.reference_source_gaps()
    assert gaps and "pack.refs" in gaps[0]
    with pytest.raises(Unavailable, match="拒绝删除素材"):
        asyncio.run(port.ask_asset_references(["asset-1"]))


def test_nothing_assembled_refuses_delete() -> None:
    """CLI 忘了 discover_modules() 时不能悄悄返回空映射。"""
    port.reset_asset_reference_checker()
    assert port.reference_source_gaps()
    with pytest.raises(Unavailable):
        asyncio.run(port.ask_asset_references(["asset-1"]))


def test_failing_checker_never_degrades_to_empty() -> None:
    """一个来源查询失败＝整体失败，不能当成「没人引用」。"""
    port.reset_asset_reference_checker()
    good = _FakeChecker(result={"a": ["封装《测试》"]})
    port.bind_asset_reference_sources(
        "task", [_source("task.refs", good), _source("pack.refs", _FakeChecker(raises=True))]
    )
    assert not port.reference_source_gaps()
    with pytest.raises(RuntimeError, match="引用库暂时不可达"):
        asyncio.run(port.ask_asset_references(["a"]))


def test_results_are_merged_and_deduplicated() -> None:
    port.reset_asset_reference_checker()
    port.bind_asset_reference_sources(
        "task",
        [
            _source("task.refs", _FakeChecker({"a": ["任务 1", "任务 2"]})),
            _source("pack.refs", _FakeChecker({"a": ["任务 2", "封装 3"]})),
        ],
    )
    result = asyncio.run(port.ask_asset_references(["a"]))
    assert result["a"] == ["任务 1", "任务 2", "封装 3"]


def test_readonly_query_can_opt_out_of_completeness_gate() -> None:
    port.reset_asset_reference_checker()
    port.bind_asset_reference_sources("task", [_source("task.refs", _FakeChecker({"a": ["x"]}))])
    port.declare_expected_reference_sources(
        [_spec("template", _source("pack.refs", _FakeChecker()))]
    )
    assert port.reference_source_gaps()
    result = asyncio.run(port.ask_asset_references(["a"], require_complete=False))
    assert result["a"] == ["x"]


def test_duplicate_declaration_is_a_configuration_error() -> None:
    port.reset_asset_reference_checker()
    with pytest.raises(ValueError, match="重复声明"):
        port.declare_expected_reference_sources(
            [
                _spec("task", _source("shared.refs", _FakeChecker())),
                _spec("template", _source("shared.refs", _FakeChecker())),
            ]
        )
