"""生图服务。

已实现：历史记录列表。
待实现：真正的生成调用（依赖 `ImageModelProvider` 与存储回源）。
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.context import CurrentUser
from core.exceptions import NotImplementedFeature
from db.repository import BaseRepository
from modules.image.models import ImageJob
from modules.image.schema import GenerateImageIn, ImageJobListOut, ImageJobOut


class ImageService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repo = BaseRepository(session, ImageJob)

    async def list_jobs(self, actor: CurrentUser) -> ImageJobListOut:
        stmt = (
            select(ImageJob)
            .where(ImageJob.owner_id == actor.id)
            .order_by(ImageJob.created_at.desc())
        )
        rows = list(await self.repo.list(stmt))
        return ImageJobListOut(
            items=[ImageJobOut.model_validate(row) for row in rows],
            total=len(rows),
        )

    async def generate(self, actor: CurrentUser, payload: GenerateImageIn, provider) -> ImageJob:
        """TODO(provider): 调用 `provider.submit_image` → 落库 → 回填 cover_url。

        同步供应商可直接返回结果；异步供应商应改为建 job 记录 + 入队轮询。
        """
        raise NotImplementedFeature("生图接口待接入模型供应商")
