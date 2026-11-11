"""`Port` 的两种形态：单例「不许抢位置」、多实现「不许重名」。

**这里此前是零测试。** 变异扫描把单例的重复注册检查改成 `if False:`，
33 条架构测试全绿——`test_port_modules_expose_the_full_shape` 只检查模块
**对外暴露的形状**（有没有 `register_*` / `reset_*` / `has_*`），不检查
`Port` 内部到底做不做重名检测。

这个守卫丢了的后果不是报错，是**静默覆盖**：两个模块的导出源注册同一个
`dataSource`，谁生效取决于 import 顺序；重名时后写的赢，前一个的数据从此
导不出去，而日志里没有任何异常。所以必须钉死。
"""

from __future__ import annotations

import pytest

from core.exceptions import RegistryError
from core.ports.host import Port


class _Impl:
    def __init__(self, tag: str) -> None:
        self.tag = tag


# --------------------------------------------------------------------------- #
# 单例形态
# --------------------------------------------------------------------------- #
def test_single_port_accepts_the_first_implementation() -> None:
    port: Port[_Impl] = Port("demo", multi=False)
    impl = _Impl("a")

    assert port.provide(impl) is impl
    assert port.get() is impl
    assert port.single() is impl
    assert port.has()
    assert len(port) == 1


def test_single_port_rejects_a_second_distinct_implementation() -> None:
    """报错消息要能定位到「谁占了位置」——只有中文「重复」不够运维排查。"""
    port: Port[_Impl] = Port("demo", multi=False)
    port.provide(_Impl("first"), owner="modules.alpha")

    with pytest.raises(RegistryError) as exc:
        port.provide(_Impl("second"), owner="modules.beta")

    message = str(exc.value)
    assert "demo" in message
    assert "_Impl" in message
    assert "modules.beta" in message


def test_single_port_tolerates_the_same_object_twice() -> None:
    """同一个对象重复注册是装配期的正常现象（模块被 import 两次），不该炸。"""
    port: Port[_Impl] = Port("demo", multi=False)
    impl = _Impl("a")

    port.provide(impl)
    port.provide(impl)

    assert port.single() is impl


def test_single_port_replace_switches_the_winner() -> None:
    """`replace=True` 是「设置语义」的注入口（审计 sink、事件订阅）才用的。"""
    port: Port[_Impl] = Port("demo", multi=False)
    first, second = _Impl("a"), _Impl("b")
    port.provide(first)

    port.provide(second, replace=True)

    assert port.single() is second


def test_single_port_require_explains_what_is_missing() -> None:
    port: Port[_Impl] = Port("demo", multi=False)

    with pytest.raises(RegistryError) as exc:
        port.require()

    assert "demo" in str(exc.value)


# --------------------------------------------------------------------------- #
# 多实现形态
# --------------------------------------------------------------------------- #
def test_multi_port_requires_a_key() -> None:
    port: Port[_Impl] = Port("demo-multi", multi=True)

    with pytest.raises(RegistryError):
        port.provide(_Impl("a"))


def test_multi_port_keeps_distinct_keys() -> None:
    port: Port[_Impl] = Port("demo-multi", multi=True)
    alpha, beta = _Impl("a"), _Impl("b")

    port.provide(alpha, key="alpha")
    port.provide(beta, key="beta")

    assert port.get("alpha") is alpha
    assert port.get("beta") is beta
    assert set(port.items()) == {"alpha", "beta"}


def test_multi_port_rejects_a_duplicate_key() -> None:
    """「谁的数据被导出去」没有正确答案，所以重名必须炸而不是后者赢。"""
    port: Port[_Impl] = Port("demo-multi", multi=True)
    port.provide(_Impl("a"), key="order")

    with pytest.raises(RegistryError) as exc:
        port.provide(_Impl("b"), key="order")

    assert "order" in str(exc.value)


def test_multi_port_replace_overrides_a_key() -> None:
    port: Port[_Impl] = Port("demo-multi", multi=True)
    second = _Impl("b")
    port.provide(_Impl("a"), key="order")

    port.provide(second, key="order", replace=True)

    assert port.get("order") is second


def test_multi_port_has_no_single() -> None:
    """多实现形态下「那一个」不存在，返回 None 而不是随手给一个。"""
    port: Port[_Impl] = Port("demo-multi", multi=True)
    port.provide(_Impl("a"), key="one")

    assert port.single() is None


# --------------------------------------------------------------------------- #
# 复位与隔离
# --------------------------------------------------------------------------- #
def test_reset_clears_everything() -> None:
    port: Port[_Impl] = Port("demo", multi=False)
    port.provide(_Impl("a"))

    port.reset()

    assert not port.has()
    assert port.single() is None


def test_items_returns_a_copy() -> None:
    """调用方拿到的是快照：改它不该影响注入口本身（装配核对会遍历它）。"""
    port: Port[_Impl] = Port("demo-multi", multi=True)
    port.provide(_Impl("a"), key="one")

    snapshot = port.items()
    snapshot.clear()

    assert set(port.items()) == {"one"}


def test_ports_are_independent_instances() -> None:
    """每个注入口是一个独立对象，不共享注册表（曾经共用模块级字典就串了）。"""
    left: Port[_Impl] = Port("left", multi=False)
    right: Port[_Impl] = Port("right", multi=False)
    left.provide(_Impl("a"))

    assert left.has()
    assert not right.has()


def test_replacement_feed_registration_rejects_a_second_reader() -> None:
    """`register_replacement_feed` 是注册语义，不是设置语义。

    它曾经 `provide(..., replace=True)`：名字写着「注册」，行为却是「后来者悄悄
    顶掉前一个」。两个模块都注册时，任务中心的列表由 import 顺序决定，而 import
    顺序没人负责——症状是「档案列表时有时无」，没有任何报错。
    """
    from core import feed

    async def first(owner: str) -> list:  # noqa: ANN401 - 与注入口签名一致
        return []

    async def second(owner: str) -> list:  # noqa: ANN401
        return []

    # 这是进程级单例：模块 import 时已经注册过真实实现，用完必须放回去，
    # 否则同一进程里后续用例看到的是「未注册 → 空列表」（真踩过）。
    original = feed._feed.single()
    feed.reset_replacement_feed()
    try:
        feed.register_replacement_feed(first)
        with pytest.raises(RegistryError, match="replacement-feed"):
            feed.register_replacement_feed(second)
        assert feed.has_replacement_feed()
    finally:
        feed.reset_replacement_feed()
        if original is not None:
            feed.register_replacement_feed(original)
