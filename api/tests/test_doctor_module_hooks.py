"""`app doctor` 的模块自检插槽（`ModuleSpec.doctor`）。

这个插槽的存在理由是**一次故障**：`api/diagnostics.py` 曾经直接 import
`modules.asset.models` 与 `modules.image.models` 来跑几条业务检查。删掉生图模块后
`app doctor` 在半路 `ModuleNotFoundError` 崩掉——而它的用途恰恰是「一次把所有问题
列全」，崩在第五项等于后面十几项全部失联。

所以这里固定三件事：
1. 声明了 `doctor` 的模块会被调用，并把自己的条目写进报告；
2. 某个模块自检抛异常 → 只让**这一条**变红，整份报告继续；
3. 没声明 `doctor` 的模块不会被误调（否则所有模块都要写一个空函数）。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import pytest

from api.diagnostics import DoctorReport, _check_modules


def _spec(name: str, hook: Callable[..., Awaitable[None]] | None) -> Any:
    """造一个只带 doctor 字段的最小 spec：`_check_modules` 只读这一个字段。"""
    from fastapi import APIRouter

    from api.module import ModuleSpec

    return ModuleSpec(name=name, router=APIRouter(), doctor=hook)


@pytest.fixture
def patch_modules(monkeypatch: pytest.MonkeyPatch) -> Callable[[list[Any]], None]:
    def _apply(specs: list[Any]) -> None:
        import api.discovery

        monkeypatch.setattr(api.discovery, "active_modules", lambda: tuple(specs))

    return _apply


@pytest.mark.asyncio
async def test_module_hook_writes_its_own_checks(
    patch_modules: Callable[[list[Any]], None],
) -> None:
    async def check(report: Any) -> None:
        report.add("demo.thing", "PASS", "看起来正常")

    patch_modules([_spec("demo", check)])
    report = DoctorReport()
    await _check_modules(report)

    assert [(c.name, c.level) for c in report.checks] == [("demo.thing", "PASS")]


@pytest.mark.asyncio
async def test_failing_hook_only_reddens_its_own_item(
    patch_modules: Callable[[list[Any]], None],
) -> None:
    """一条自检炸了，不能让后面模块的检查也跟着消失。"""

    async def broken(report: Any) -> None:
        raise RuntimeError("查不了库")

    async def fine(report: Any) -> None:
        report.add("demo2.ok", "PASS", "正常")

    patch_modules([_spec("demo", broken), _spec("demo2", fine)])
    report = DoctorReport()
    await _check_modules(report)

    levels = {c.name: c.level for c in report.checks}
    assert levels["module.demo"] == "FAIL"
    assert "RuntimeError" in next(c.message for c in report.checks if c.name == "module.demo")
    # 后面的模块照常执行——这是本条用例真正要守住的行为
    assert levels["demo2.ok"] == "PASS"


@pytest.mark.asyncio
async def test_modules_without_hook_are_skipped(
    patch_modules: Callable[[list[Any]], None],
) -> None:
    patch_modules([_spec("plain", None)])
    report = DoctorReport()
    await _check_modules(report)

    assert report.checks == []
