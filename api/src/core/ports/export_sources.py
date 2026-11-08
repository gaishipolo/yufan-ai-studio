"""数据导出源扩展点：让「导出」认识别人的表，而不 import 别人的模块。

## 为什么需要它

导出功能天生是跨模块的：用户想导的是**任务列表**、**生图历史**、**操作留痕**，
这些表分别属于 task / image / db。按第 0 节的依赖方向，`modules.export` 不能
import `modules.task`，否则这两个模块从此只能一起删、一起换。

所以和 `principal-resolver` / `asset-references` 一样做成注入点：
**拥有数据的模块**注册一个导出源（它自己的 SQL 自己写），
**导出模块**只认这个协议，运行时按名字查。

## 谁该注册

- 表在 `modules/<某模块>/models.py` 里 → 该模块注册，注册代码放在
  `modules/<模块>/export_source.py`，并在 `__init__.py` 里 import 一次（副作用注册）。
- 表在 `db/` 里（`audit_events` 这类框架表）→ 谁需要谁直接用 db 层模型查，
  不算跨模块，不必走注入口。

## 语义约定（不遵守会导出错数据）

1. **只导出调用者有权看到的数据**。`actor_id` 是唯一的数据范围依据，
   不要因为「这个人有 read:all」就导全公司的数据——导出是「我自己那个列表的
   快照」，一键带走全量是另一种事故。
2. **`params` 里出现该源不认识的键时忽略，不要报错**。条件是从一个共享模型
   平铺过来的，不认识是正常现象；真正生效的条件由 `applied()` 回显给用户。
3. **行必须是可 CSV 化的标量**（str / int / float / bool / None / datetime）。
   嵌套结构请自己压平成 JSON 字符串——CSV 里塞半截 JSON 只会让下游解析炸。
4. **`fetch` 必须自己保证 `limit`**：`limit` 之后的行即使查出来也会被丢掉，
   白白拉一遍数据；用 `LIMIT` 下推（PERF-06）。
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from core.ports.host import Port

__all__ = [
    "ExportSource",
    "export_sources",
    "get_export_source",
    "register_export_source",
    "reset_export_sources",
]


class ExportSource(Protocol):
    """一个可导出的数据源。

    实现方通常是一个模块级单例（无状态），因为注册发生在 import 期。
    """

    #: 稳定的机器名，进 API 的 `dataSource` 字段，不要跟着中文名改
    name: str
    #: 展示名
    label: str
    #: CSV 表头，同时决定每行的列顺序
    columns: tuple[str, ...]
    #: 导出该源额外需要的权限码；None 表示只要 `export:read` 即可
    permission: str | None

    async def fetch(
        self,
        session: AsyncSession,
        *,
        actor_id: str,
        params: dict[str, Any],
        limit: int,
    ) -> list[Sequence[Any]]:
        """按条件取行，最多 `limit` 行。"""
        ...


#: 导出源注册表。多实现：任何拥有可导出表的模块各贡献一份。
_sources: Port[ExportSource] = Port("export-sources", multi=True)


def register_export_source(source: ExportSource) -> None:
    """注册（由拥有数据的模块在 import 期调用）。

    重名由 `Port` 直接拒绝：两个模块都想叫 `tasks` 时，「谁的数据被导出去」
    没有正确答案，与其按 import 顺序随机决定，不如启动期就炸。
    """
    _sources.provide(source, key=source.name)


def export_sources() -> dict[str, ExportSource]:
    """当前所有已注册的源（只在请求期调用，此时 discovery 已完成）。"""
    return _sources.items()


def has_export_source() -> bool:
    """是否已接上至少一个业务模块注册的源。

    内置的 `audit_events` 不走这里，所以返回 False 不代表「导出不可用」，
    只代表「没有别的模块贡献数据源」——前端下拉会只剩内置源那一项。
    """
    return _sources.has()


def get_export_source(name: str) -> ExportSource | None:
    return _sources.get(name)


def reset_export_sources() -> None:
    """仅测试使用：清空注册表，让用例可以造自己的源。"""
    _sources.reset()
