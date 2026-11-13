"""封装模板的表访问层：只认 SQLAlchemy，不含业务规则。

与 `modules/template` 的 repository 分开：本模块的表是「模板 + 槽位」
两张，槽位要按 `template_id` 批量取、按 `position` 排序，且**槽位的
唯一性约束在数据库层**（`uq_template_pack_slots_position`）。
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.exceptions import NotFound
from modules.template_pack.models import TemplatePackRow, TemplatePackSlotRow

__all__ = ["TemplatePackRepository"]


class TemplatePackRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, pack_id: str) -> TemplatePackRow | None:
        return await self.session.get(TemplatePackRow, pack_id)

    async def get_or_404(self, pack_id: str) -> TemplatePackRow:
        row = await self.get(pack_id)
        if row is None:
            raise NotFound("封装模板不存在")
        return row

    async def get_by_key(self, template_key: str) -> TemplatePackRow | None:
        """稳定标识的唯一命中行。空键不查：`save` 会给它自动生成一个。"""
        if not template_key:
            return None
        stmt = select(TemplatePackRow).where(
            TemplatePackRow.template_key == template_key
        )
        return await self.session.scalar(stmt)

    async def list_all(
        self, *, owner_id: str = "", status: str = ""
    ) -> Sequence[TemplatePackRow]:
        """列表。`owner_id` 非空时只看本人的，`status` 非空时按状态收窄。

        排序键以主键收尾（`EXTENDING.md` §1.1）：`created_at` 在同一瞬间插入
        的两行上可能相同，不收尾会让列表顺序在两次请求之间抖动。
        """
        stmt = select(TemplatePackRow)
        if owner_id:
            stmt = stmt.where(TemplatePackRow.owner_id == owner_id)
        if status:
            stmt = stmt.where(TemplatePackRow.status == status)
        stmt = stmt.order_by(
            TemplatePackRow.created_at.desc(), TemplatePackRow.id
        )
        return (await self.session.scalars(stmt)).all()

    async def slots(self, template_id: str) -> Sequence[TemplatePackSlotRow]:
        """某模板的全部槽位，按 `position` 升序。

        顺序是**契约**：`position` 决定 `{{Mixed N}}` 指向谁，调用方不得重排。
        """
        stmt = (
            select(TemplatePackSlotRow)
            .where(TemplatePackSlotRow.template_id == template_id)
            .order_by(TemplatePackSlotRow.position)
        )
        return (await self.session.scalars(stmt)).all()

    async def slots_of(
        self, template_ids: Sequence[str]
    ) -> dict[str, list[TemplatePackSlotRow]]:
        """批量取多个模板的槽位。

        列表页要一次拿到多条模板的槽位，逐条查就是 N 次往返。
        """
        wanted = [item for item in dict.fromkeys(template_ids) if item]
        if not wanted:
            return {}
        stmt = (
            select(TemplatePackSlotRow)
            .where(TemplatePackSlotRow.template_id.in_(wanted))
            .order_by(TemplatePackSlotRow.template_id, TemplatePackSlotRow.position)
        )
        grouped: dict[str, list[TemplatePackSlotRow]] = {item: [] for item in wanted}
        for row in (await self.session.scalars(stmt)).all():
            grouped.setdefault(row.template_id, []).append(row)
        return grouped

    async def add(self, row: TemplatePackRow) -> TemplatePackRow:
        self.session.add(row)
        await self.session.flush()
        return row

    async def replace_slots(
        self, template_id: str, slots: Sequence[TemplatePackSlotRow]
    ) -> None:
        """整体替换某模板的槽位。

        先删后插是刻意的：槽位是「模板的完整定义」，不是增量补丁。
        留着没被新定义提到的旧槽位，会让 `{{Mixed N}}` 指向一个作者已经
        删掉的东西 —— 而且前端看不到它，因为它不在新的槽位清单里。
        """
        existing = list(await self.slots(template_id))
        for row in existing:
            await self.session.delete(row)
        await self.session.flush()
        for row in slots:
            self.session.add(row)
        await self.session.flush()

    async def save(self) -> None:
        await self.session.flush()
