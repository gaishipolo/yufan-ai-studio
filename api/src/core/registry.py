"""命名注册表：进程内「按名字注册 → 按名字解析」的通用容器。

它是本项目所有扩展点的公共底座，被三处使用：

- 基础设施驱动：`storage` / `queue` / `ai` 三类，名字即配置里的字符串
- 生成能力：`pipeline`，名字即任务类型（video / image / ...）
- 事件订阅：由 `core.events` 复用

设计约束：
1. 注册只发生在模块 import 期，解析集中在启动阶段，避免运行期 import 的不确定性。
2. 重复注册默认抛错 —— 并行开发时两人撞名必须立刻暴露，而不是后者静默覆盖前者。
   确实需要替换（如测试替身）时显式传 `override=True`。
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Generic, TypeVar

T = TypeVar("T")


class RegistryError(RuntimeError):
    """注册表使用错误（重名、未注册、条目类型不符）。"""


class NamedRegistry(Generic[T]):
    """一个 `str -> T` 的注册表。"""

    __slots__ = ("_kind", "_items")

    def __init__(self, kind: str) -> None:
        self._kind = kind
        self._items: dict[str, T] = {}

    # ------------------------------------------------------------------ 注册
    def register(self, name: str, *, override: bool = False) -> Callable[[T], T]:
        """装饰器写法：`@registry.register("local")`。"""

        def decorator(value: T) -> T:
            self.add(name, value, override=override)
            return value

        return decorator

    def add(self, name: str, value: T, *, override: bool = False) -> T:
        if not name:
            raise RegistryError(f"{self._kind} 注册名不能为空")
        if name in self._items and not override:
            raise RegistryError(
                f"{self._kind} 已存在同名注册: {name!r}"
                f"（现有 {self._items[name]!r}，新增 {value!r}）。"
                "若是故意替换请显式传 override=True。"
            )
        self._items[name] = value
        return value

    # ------------------------------------------------------------------ 解析
    def get(self, name: str) -> T:
        try:
            return self._items[name]
        except KeyError as exc:
            raise RegistryError(
                f"未注册的 {self._kind}: {name!r}；已注册: {sorted(self._items)}"
            ) from exc

    def find(self, name: str) -> T | None:
        return self._items.get(name)

    def require(self, *, expect: type | tuple[type, ...] | None = None) -> T:
        """取唯一实现（用于「一类只允许一个」的场景）。"""
        if len(self._items) != 1:
            raise RegistryError(
                f"{self._kind} 期望恰好一个实现，实际 {len(self._items)} 个: {sorted(self._items)}"
            )
        value = next(iter(self._items.values()))
        if expect is not None and not isinstance(value, expect):
            raise RegistryError(f"{self._kind} 的实现类型不符: {type(value)!r}")
        return value

    # ------------------------------------------------------------------ 视图
    @property
    def kind(self) -> str:
        return self._kind

    def names(self) -> list[str]:
        return sorted(self._items)

    def entries(self) -> dict[str, T]:
        return dict(self._items)

    def __contains__(self, name: object) -> bool:
        return name in self._items

    def __len__(self) -> int:
        return len(self._items)

    def __iter__(self) -> Iterator[tuple[str, T]]:
        return iter(self._items.items())

    # ------------------------------------------------------------------ 测试
    def clear(self) -> None:
        """仅测试使用。"""
        self._items.clear()
