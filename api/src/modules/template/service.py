"""模板服务。

模板是运营侧配置内容，读多写少；后续接 CMS 时替换 repository 即可。
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.repository import BaseRepository
from modules.template.models import VideoTemplate
from modules.template.schema import TemplateListOut, TemplateOut


class TemplateService:
    def __init__(self, session: AsyncSession) -> None:
        self.repo = BaseRepository(session, VideoTemplate)

    async def list_templates(self, *, industry: str | None = None) -> TemplateListOut:
        stmt = select(VideoTemplate)
        if industry:
            stmt = stmt.where(VideoTemplate.industry == industry)
        stmt = stmt.order_by(VideoTemplate.usage_count.desc(), VideoTemplate.created_at.desc())

        rows = list(await self.repo.list(stmt))
        return TemplateListOut(
            items=[TemplateOut.model_validate(row) for row in rows],
            total=len(rows),
        )

    async def get_template(self, template_id: str) -> VideoTemplate:
        return await self.repo.get_or_404(template_id, "模板不存在")
