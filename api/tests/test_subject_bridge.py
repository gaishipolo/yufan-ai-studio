"""双读桥的四种状态与两种回落（M2.5-C）。

这组用例守的是**「改了读路径但不改产出」**：默认（shadow）模式下无论主体与旧包
是否一致，返回的都必须是旧路径结果。差异只进日志，不进返回值。

如果这条守不住，上线第一天就会出现「有些任务的参考图变了」，而那些任务本身
没有任何改动——排查方向会先指向模型供应商。
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager

import pytest

from core.config import settings
from core.ports.subjects import SubjectSnapshot, register_subject_lookup, reset_subject_lookup
from modules.template.subject_bridge import resolve_reference_ids

OLD = ["a1", "a2", "a3"]


class _FakeLookup:
    """只回答「这一版有哪些图」。`calls` 用来断言 off 模式没有跑第二条路。"""

    def __init__(self, result: SubjectSnapshot | None) -> None:
        # 属性不能叫 `snapshot`：那会遮掉同名方法，症状是
        # `'SubjectSnapshot' object is not callable`——一个和这里毫无关系的报错
        self.result = result
        self.calls: list[tuple[str, int]] = []

    async def snapshot(self, session, pack_id: str, version: int):  # noqa: ANN001, ANN201
        self.calls.append((pack_id, version))
        return self.result


def _snap(asset_ids: list[str]) -> SubjectSnapshot:
    return SubjectSnapshot(
        subject_id="s1",
        version=3,
        asset_ids=tuple(asset_ids),
        roles={asset_id: "" for asset_id in asset_ids},
        capabilities=frozenset({"generate"}),
    )


@pytest.fixture(autouse=True)
def _clean_port():
    reset_subject_lookup()
    yield
    reset_subject_lookup()


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@contextmanager
def _capture_warnings() -> Iterator[list[str]]:
    """抓本模块的 WARNING，**不依赖日志系统的任何全局状态**。

    这里直接替换 `logger.warning` 而不是挂 handler / 用 caplog。原因是这条用例
    曾经"单跑绿、全量红"：`create_app()` 会调 `setup_logging()`，它
    `root.handlers.clear()` 并重设级别——只要同一次会话里有别的用例先起过服务，
    捕获链就断了。红的原因与断言的东西毫无关系（**没捕获到** ≠ **没打日志**），
    这种红最容易被读成"功能坏了"。

    打日志是这里唯一被断言的行为，所以直接盯住那次调用最稳。
    """
    logger = logging.getLogger("modules.template.subject_bridge")
    messages: list[str] = []
    original = logger.warning

    def spy(msg: object, *args: object, **kwargs: object) -> None:  # noqa: ANN401, ARG001
        messages.append(str(msg) % args if args else str(msg))

    logger.warning = spy  # type: ignore[method-assign]
    try:
        yield messages
    finally:
        logger.warning = original  # type: ignore[method-assign]


@pytest.mark.anyio
async def test_off_mode_never_touches_the_new_path() -> None:
    """`off` 是"立刻退回旧路径"的开关，所以它连查都不该查。

    只"读了不用"会让一次退回仍然依赖主体表可读——主体表锁住时 prepare 就卡住，
    而运维以为已经退回去了。
    """
    lookup = _FakeLookup(_snap(["x"]))
    register_subject_lookup(lookup)
    settings.subject_lookup_mode = "off"
    try:
        assert await resolve_reference_ids(None, pack_id="p1", version=3, fallback=OLD) == OLD
        assert lookup.calls == []
    finally:
        settings.subject_lookup_mode = "shadow"


@pytest.mark.anyio
async def test_shadow_mode_returns_old_result_even_when_paths_agree() -> None:
    register_subject_lookup(_FakeLookup(_snap(OLD)))
    settings.subject_lookup_mode = "shadow"
    with _capture_warnings() as messages:
        assert await resolve_reference_ids(None, pack_id="p1", version=3, fallback=OLD) == OLD
    assert not [message for message in messages if "不一致" in message]


@pytest.mark.anyio
async def test_shadow_mode_logs_difference_but_still_returns_old() -> None:
    """差异只记日志、不改行为——观察期不能变成故障期。"""
    lookup = _FakeLookup(_snap(["a1", "a3", "a2"]))
    register_subject_lookup(lookup)
    settings.subject_lookup_mode = "shadow"
    with _capture_warnings() as messages:
        result = await resolve_reference_ids(None, pack_id="p1", version=3, fallback=OLD)
    assert result == OLD, "shadow 模式下返回值必须与旧路径一致"
    assert any("不一致" in message for message in messages), (
        messages,
        lookup.calls,
        settings.subject_lookup_mode,
    )
    # 顺序差异是最隐蔽的一种，日志里要能看出到底哪边不一样
    assert any("a3" in message and "a2" in message for message in messages)


@pytest.mark.anyio
async def test_on_mode_uses_the_subject_version() -> None:
    register_subject_lookup(_FakeLookup(_snap(["b1", "b2"])))
    settings.subject_lookup_mode = "on"
    try:
        assert await resolve_reference_ids(None, pack_id="p1", version=3, fallback=OLD) == [
            "b1",
            "b2",
        ]
    finally:
        settings.subject_lookup_mode = "shadow"


@pytest.mark.anyio
async def test_missing_subject_falls_back_in_both_modes() -> None:
    """没有映射 / 没有这一版时，`on` 也回落旧路径。

    与"注入口未注册"同一个立场：主体缺席不该让 prepare 比现在更差。
    真正需要报警的是"配了 on 却一直回落"，那条靠日志，不靠抛错。
    """
    register_subject_lookup(_FakeLookup(None))
    for mode in ("shadow", "on"):
        settings.subject_lookup_mode = mode
        assert await resolve_reference_ids(None, pack_id="p1", version=3, fallback=OLD) == OLD
    settings.subject_lookup_mode = "shadow"


@pytest.mark.anyio
async def test_unregistered_port_falls_back_instead_of_empty_list() -> None:
    """注入口没装配时返回旧路径，**绝不返回空列表**。

    空列表会被下游当成"这个包确实没有图"，于是生成时一张参考都不带——
    结果看起来只是"风格跑偏了"，而没人会想到是装配问题。
    """
    settings.subject_lookup_mode = "on"
    try:
        assert await resolve_reference_ids(None, pack_id="p1", version=3, fallback=OLD) == OLD
    finally:
        settings.subject_lookup_mode = "shadow"


@pytest.mark.anyio
async def test_invalid_mode_is_treated_as_shadow() -> None:
    """配置写错不能静默变成 `on`：那等于没观察就切了默认。"""
    register_subject_lookup(_FakeLookup(_snap(["b1"])))
    settings.subject_lookup_mode = "yes-please"
    try:
        assert await resolve_reference_ids(None, pack_id="p1", version=3, fallback=OLD) == OLD
    finally:
        settings.subject_lookup_mode = "shadow"
