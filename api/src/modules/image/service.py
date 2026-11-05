"""生图服务。

已实现：历史记录列表。
待实现：真正的生成调用（依赖 `ImageModelProvider` 与存储回源）。
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.context import CurrentUser
from core.exceptions import NotImplementedFeature
from core.response import Page, PageParams
from db.repository import BaseRepository
from modules.image.models import ImageJob
from modules.image.permissions import PERM_READ_ALL
from modules.image.schema import GenerateImageIn, ImageJobOut


class ImageService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repo = BaseRepository(session, ImageJob)

    async def list_jobs(self, actor: CurrentUser, page: PageParams) -> Page[ImageJobOut]:
        """历史列表：数据范围只看 `image:read:all`，与 task 模块同一套规则。

        只按 `owner_id` 过滤时，管理员在「全部」视图里也只看得到自己的历史，
        而任务列表里能看到全站——同一份工作台两套答案，用户只会以为是 bug
        （R4-07 / R6-09）。

        分页不是可选项：范围打开之后，这个接口对 `image:*` 的账号就是全表返回，
        而前端一进生图历史页就会调它。`total` 走 `count(*)`，不是 `len(rows)`——
        后者在「有 200 条、这页 24 条」时会把总数报成 24（R7-02）。
        """
        stmt = select(ImageJob).order_by(ImageJob.created_at.desc())
        if not actor.has(PERM_READ_ALL):
            stmt = stmt.where(ImageJob.owner_id == actor.id)
        rows, total = await self.repo.paginate(stmt, page)
        return Page.create(
            [ImageJobOut.model_validate(row) for row in rows], total, page
        )

    async def generate(self, actor: CurrentUser, payload: GenerateImageIn, provider) -> ImageJob:
        """TODO(provider): 调用 `provider.submit_image` → 落库 → 回填 cover_url。

        同步供应商可直接返回结果；异步供应商应改为建 job 记录 + 入队轮询。
        """
        raise NotImplementedFeature("生图接口待接入模型供应商")
