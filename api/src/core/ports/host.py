"""注入口宿主：注册表逻辑只写一遍。

## 之前是什么样

五个注入口各写了一份几乎相同的注册表：模块级 `_registry`、重名 `RegistryError`、
`reset_*()`、`has_*()`、`items()`。第六个出现时还得再抄一遍，而**抄漏一项不会报错**：
少个 `has_*` 只让 `app modules` 少一行，少个重名检查就让两个实现悄悄互相覆盖。

为了防这个，`tests/test_architecture.py::test_port_modules_expose_the_full_shape`
要求每个注入口模块都暴露 `register_*` / `reset_*` / `has_*`——**用测试弥补
「形状没有统一保证」**。

宿主把**内部实现**统一了：重名检查、复位、`items()` 只写一遍，不可能再抄漏。
但那条守卫仍然保留，因为它检查的是另一样东西——模块**对外**还暴不暴露
`register_*` / `reset_*` / `has_*`。这三样仍然是公开接口：`PORT_CATALOG` 的
`is_registered` 是模块给的调用值，`app modules` 的清单也按名字取。

所以现在是两层：`Port` 保证「库里不会漏一条检查」，守卫保证「模块仍然挂得住」。
把 `is_registered` 也从模块收进 `Port`（`asset-references`、`export-sources`
这两条要的是自定义判定，不是简单的 `has()`）之后，守卫才可以删。那是一次
独立的改动，不该混在这轮里做完。

## 两种形态，差别只在基数

- `multi=False`（单例）：权威来源只能有一个。重复注册是配置错误，
  与其按 import 顺序随机决定谁生效，不如立刻炸。
- `multi=True`（注册表）：多个模块各贡献一份，按名字查。重名同样炸——
  「谁的数据被导出去」没有正确答案。

## 为什么键是字符串而不是类型

注入口的键会出现在 API（导出源的 `dataSource`）、配置、日志里。用类名当键
在重构时会静默改名，用字符串是**稳定的契约**。代价是重名只能运行期发现，
所以 `provide` 把重名检测做成了硬错误。
"""

from __future__ import annotations

from typing import Generic, TypeVar

from core.exceptions import RegistryError

__all__ = ["Port"]

T = TypeVar("T")

#: 单例形态下唯一的键。取值不重要，只要不与多实现的 key 冲突即可。
_SINGLE_KEY = ""


class Port(Generic[T]):
    """一个有名字的注入口。

    `name` 会出现在 `app modules` 和错误消息里，用短横线连接的小写（`export-sources`）。
    """

    __slots__ = ("_impls", "_multi", "name")

    def __init__(self, name: str, *, multi: bool) -> None:
        self.name = name
        self._multi = multi
        self._impls: dict[str, T] = {}

    # ---------- 写 ----------

    def provide(
        self,
        impl: T,
        *,
        key: str | None = None,
        owner: str = "",
        replace: bool = False,
    ) -> T:
        """注册一个实现，返回它本身（便于当装饰器返回原函数）。

        单例注入口不用传 `key`；多实现必须传，否则 `get()` 无从查起。

        `replace` 是给「设置语义」的注入口用的（`set_audit_sink` 这类）：
        它们的契约本来就是「后写的生效」，而 `register_*` 的契约是「不许两个实现
        抢同一个位置」。两种语义在改造前就存在且各不相同，这里把它显式化，
        而不是统一成一种——统一会让其中一个失去原有保证。
        """
        if self._multi:
            if not key:
                raise RegistryError(
                    f"注入口 {self.name} 支持多个实现，注册时必须给 key"
                )
            existing = self._impls.get(key)
            if existing is not None and existing is not impl and not replace:
                raise RegistryError(
                    f"注入口 {self.name} 的 {key!r} 重复注册："
                    f"{type(existing).__name__}{self._who(owner)} 已生效，"
                    f"拒绝 {type(impl).__name__}"
                )
            self._impls[key] = impl
            return impl

        # 单例：键固定，保证 `get()` 也能用同一个字符串取到
        existing = self._impls.get(_SINGLE_KEY)
        if existing is not None and existing is not impl and not replace:
            raise RegistryError(
                f"注入口 {self.name} 只允许一个实现："
                f"{type(existing).__name__} 已生效，拒绝 {type(impl).__name__}{self._who(owner)}。"
                f"两个实现同时生效时「谁说了算」没有正确答案。"
            )
        self._impls[_SINGLE_KEY] = impl
        return impl

    def reset(self) -> None:
        """仅测试使用：清空注册表。"""
        self._impls.clear()

    # ---------- 读 ----------

    def get(self, key: str = _SINGLE_KEY) -> T | None:
        return self._impls.get(key)

    def require(self, key: str = _SINGLE_KEY) -> T:
        """取实现，没有就说清楚缺什么。"""
        impl = self._impls.get(key)
        if impl is None:
            raise RegistryError(f"注入口 {self.name} 还没有实现（{key!r}）")
        return impl

    def items(self) -> dict[str, T]:
        """当前所有实现。单例形态下键是 `_SINGLE_KEY`，调用方通常用 `single()`。"""
        return dict(self._impls)

    def single(self) -> T | None:
        """单例形态的读取入口；多实现形态返回 `None`（没有「那一个」）。"""
        if self._multi:
            return None
        return self._impls.get(_SINGLE_KEY)

    def has(self) -> bool:
        return bool(self._impls)

    def __len__(self) -> int:
        return len(self._impls)

    def __repr__(self) -> str:
        shape = "multi" if self._multi else "single"
        return f"<Port {self.name} {shape} n={len(self._impls)}>"

    # ---------- 内部 ----------

    @staticmethod
    def _who(owner: str) -> str:
        return f"（{owner}）" if owner else ""
