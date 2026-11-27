"""任务模块对 `TaskExternalIdLookup` 的实现。"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.context import CurrentUser
from modules.task.models import VideoTask
from modules.task.permissions import PERM_READ_ALL


class SqlTaskExternalIdLookup:
    async def visible_external_ids(
        self,
        session: AsyncSession,
        actor: CurrentUser,
        external_ids: Sequence[str],
    ) -> frozenset[str]:
        wanted = {str(item).strip() for item in external_ids if str(item).strip()}
        if not wanted:
            return frozenset()
        stmt = select(VideoTask.external_id).where(VideoTask.external_id.in_(wanted))
        if not actor.has(PERM_READ_ALL):
            stmt = stmt.where(VideoTask.author_id == actor.id)
        rows = await session.scalars(stmt)
        return frozenset(item for item in rows if item)