"""分镜模板服务。"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.repository import BaseRepository
from modules.storyboard.models import Storyboard
from modules.storyboard.schema import StoryboardListOut, StoryboardOut


class StoryboardService:
    def __init__(self, session: AsyncSession) -> None:
        self.repo = BaseRepository(session, Storyboard)

    async def list_storyboards(self) -> StoryboardListOut:
        stmt = select(Storyboard).order_by(Storyboard.created_at.desc())
        rows = list(await self.repo.list(stmt))
        return StoryboardListOut(
            items=[StoryboardOut.model_validate(row) for row in rows],
            total=len(rows),
        )

    async def get_storyboard(self, storyboard_id: str) -> Storyboard:
        return await self.repo.get_or_404(storyboard_id, "分镜模板不存在")
