"""视频模板的表访问层：只认 SQLAlchemy，不含业务规则。

与 `repository.py`（封装 / 替换运行）分开：那张表是「运营配置」节奏（低频、整块
读写），这张是「封装卡片」节奏——列表一次全读、按原片反查、抢蓝本。放一起会
让两边共享同一个事务边界，而它们本来就该各自提交。

**为什么列表不设分页**：模板是「封装配置」不是业务流水，一个原片通常只有几条。
分页要额外维护排序稳定性，换来的只是省几 KB 序列化；真到几百条时的正确做法是
按原片分页再来，而不是在这里加 `limit/offset`。
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from core.exceptions import NotFound
from modules.template_pack.models import VideoTemplate

__all__ = ["VideoTemplateRepository"]


class VideoTemplateRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, template_id: str) -> VideoTemplate | None:
        return await self.session.get(VideoTemplate, template_id)

    async def get_or_404(self, template_id: str) -> VideoTemplate:
        row = await self.get(template_id)
        if row is None:
            raise NotFound("视频模板不存在")
        return row

    async def get_by_key(self, template_key: str) -> VideoTemplate | None:
        """稳定标识的唯一命中行。空键不查：`create` 会给它自动生成一个。"""
        if not template_key:
            return None
        stmt = select(VideoTemplate).where(VideoTemplate.template_key == template_key)
        return await self.session.scalar(stmt)

    async def list_all(self) -> Sequence[VideoTemplate]:
        """固定蓝本永远最前，其余按创建时间倒序。

        排序键以主键收尾（`EXTENDING.md` §1.1）：`created_at` 在同一瞬间插入的
        两行上可能相同，不收尾会让列表顺序在两次请求之间抖动。
        """
        stmt = select(VideoTemplate).order_by(
            VideoTemplate.pinned.desc(),
            VideoTemplate.created_at.desc(),
            VideoTemplate.id,
        )
        return (await self.session.scalars(stmt)).all()

    async def bound_for_source(
        self, source_id: str, kind: str | None
    ) -> Sequence[VideoTemplate]:
        """同一原片（可再按处理类型收窄）的全部模板，最早的在前。"""
        stmt = select(VideoTemplate).where(
            VideoTemplate.source_video_asset_id == source_id
        )
        if kind is not None:
            stmt = stmt.where(VideoTemplate.default_kind == kind)
        stmt = stmt.order_by(VideoTemplate.created_at, VideoTemplate.id)
        return (await self.session.scalars(stmt)).all()

    async def names(self, template_ids: Sequence[str]) -> dict[str, str]:
        """批量反查名字，给老档案补 `videoTemplateName` 用。"""
        wanted = [item for item in dict.fromkeys(template_ids) if item]
        if not wanted:
            return {}
        stmt = select(VideoTemplate.id, VideoTemplate.name).where(
            VideoTemplate.id.in_(wanted)
        )
        return {row[0]: row[1] for row in await self.session.execute(stmt)}

    async def add(self, row: VideoTemplate) -> VideoTemplate:
        self.session.add(row)
        await self.session.flush()
        return row

    async def unpin_others(self, keep_id: str | None) -> None:
        """把别人的蓝本标记清掉。

        **必须先于「把这一条设成 pin」执行**：`uq_video_templates_pinned` 是
        非延迟唯一索引，同一事务里先设后清会当场撞约束。顺序反了的表现不是
        「抢蓝本失败」，而是一条莫名其妙的 IntegrityError。
        """
        stmt = update(VideoTemplate).where(VideoTemplate.pinned.is_(True))
        if keep_id is not None:
            stmt = stmt.where(VideoTemplate.id != keep_id)
        await self.session.execute(stmt.values(pinned=False))
        await self.session.flush()

    async def save(self) -> None:
        await self.session.flush()
