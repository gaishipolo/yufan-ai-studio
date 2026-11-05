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
from modules.image.permissions import PERM_READ_ALL
from modules.image.schema import GenerateImageIn, ImageJobListOut, ImageJobOut


class ImageService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repo = BaseRepository(session, ImageJob)

    async def list_jobs(self, actor: CurrentUser) -> ImageJobListOut:
        """历史列表：数据范围只看 `image:read:all`，与 task 模块同一套规则。

        只按 `owner_id` 过滤时，管理员在「全部」视图里也只看得到自己的历史，
        而任务列表里能看到全站——同一份工作台两套答案，用户只会以为是 bug
        （R4-07 / R6-09）。
        """
        stmt = select(ImageJob).order_by(ImageJob.created_at.desc())
        if not actor.has(PERM_READ_ALL):
            stmt = stmt.where(ImageJob.owner_id == actor.id)
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
