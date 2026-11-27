"""按当前账号收窄「上游任务号」的只读注入口。

供应商的进度接口常按**上游 task_id** 批量查询，而上游账号可能是全团队共用的：
不先与 `video_tasks` 做归属比对，任何持 `task:read` 的账号都能读到别人任务的
排队位次与失败原因。业务模块之间又不许互相 import，于是由任务模块注册一个
只读查询，模型驱动只认这份契约。

未注册时返回空集（fail-closed：少看见，而不是多看见）。
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from sqlalchemy.ext.asyncio import AsyncSession

from core.context import CurrentUser
from core.ports.host import Port


@runtime_checkable
class TaskExternalIdLookup(Protocol):
    async def visible_external_ids(
        self,
        session: AsyncSession,
        actor: CurrentUser,
        external_ids: Sequence[str],
    ) -> frozenset[str]:
        """这些上游任务号里，当前账号能看的有哪些（`task:read:all` 可看全部）。"""
        ...


_lookup: Port[TaskExternalIdLookup] = Port("task-external-ids", multi=False)


def register_task_external_id_lookup(lookup: TaskExternalIdLookup) -> TaskExternalIdLookup:
    return _lookup.provide(lookup, owner="modules.task")


def reset_task_external_id_lookup() -> None:
    """仅测试使用。"""
    _lookup.reset()


def has_task_external_id_lookup() -> bool:
    return _lookup.has()


async def visible_external_ids(
    session: AsyncSession,
    actor: CurrentUser,
    external_ids: Sequence[str],
) -> frozenset[str]:
    lookup = _lookup.single()
    if lookup is None:
        return frozenset()
    return await lookup.visible_external_ids(session, actor, external_ids)