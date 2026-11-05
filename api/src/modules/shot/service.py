"""镜头词条服务。"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.repository import BaseRepository
from modules.shot.models import ShotPreset
from modules.shot.schema import ShotListOut, ShotPresetOut


class ShotService:
    def __init__(self, session: AsyncSession) -> None:
        self.repo = BaseRepository(session, ShotPreset)

    async def list_shots(self, *, keyword: str = "") -> ShotListOut:
        stmt = select(ShotPreset)
        if keyword.strip():
            stmt = stmt.where(ShotPreset.name.ilike(f"%{keyword.strip()}%"))
        stmt = stmt.order_by(ShotPreset.popularity.desc(), ShotPreset.created_at.desc())

        rows = list(await self.repo.list(stmt))
        return ShotListOut(
            items=[ShotPresetOut.model_validate(row) for row in rows],
            total=len(rows),
        )
