"""`TemplatePackPort` 的实现：把模块内部的数据翻译成 core 层的数据类型。

**只有这一层做翻译**。模块内部一律用 HTTP 契约的 Pydantic 出参，
对外端口一律用 core 的 dataclass 快照。混用会出现「core 依赖上层类型」
或「上层类型泄漏到 core」，两个方向都不行。

`__init__.py` 里 import 本模块是**副作用导入**：注册是 import 期行为，
不 import 这行，注入口就永远没接上（而 `app modules` 不会报错——
「端口没接上」是启动期才发现的事）。
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from core.context import CurrentUser
from core.ports.template_pack import (
    TemplatePackCandidate,
    TemplatePackPort,
    TemplatePackSlot,
    TemplatePackSummary,
    register_template_pack,
)
from modules.template_pack.models import (
    STATUS_PUBLISHED,
    TemplatePackRow,
    TemplatePackSlotRow,
)
from modules.template_pack.repository import TemplatePackRepository

__all__ = ["SqlTemplatePackPort", "install"]


def _slot_summary(row: TemplatePackSlotRow) -> TemplatePackSlot:
    """槽位行 → core 快照。"""
    return TemplatePackSlot(
        position=row.position,
        token=row.token,
        slot_kind=row.slot_kind,
        label=row.label,
        asset_id=row.asset_id,
        asset_id_space=row.asset_id_space,
        media_type=row.media_type,
        filter_kind=row.filter_kind,
        filter_tag_ids=tuple(row.filter_tag_ids or []),
        filter_tag_match=row.filter_tag_match,
        required=bool(row.required),
    )


def _summary(
    row: TemplatePackRow, slots: Sequence[TemplatePackSlotRow]
) -> TemplatePackSummary:
    return TemplatePackSummary(
        id=row.id,
        template_key=row.template_key,
        name=row.name,
        description=row.description,
        prompt=row.prompt,
        prompt_sha256=row.prompt_sha256,
        owner_id=row.owner_id,
        source_video_asset_id=row.source_video_asset_id,
        source_video_id_space=row.source_video_id_space,
        source_duration_sec=float(row.source_duration_sec or 0),
        aspect_ratio=row.aspect_ratio,
        cover_url=row.cover_url,
        slots=tuple(_slot_summary(item) for item in slots),
        published_at=row.published_at,
    )


class SqlTemplatePackPort(TemplatePackPort):
    """基于数据库的实现。"""

    async def list_published(
        self, session: AsyncSession, *, owner_id: str = ""
    ) -> list[TemplatePackSummary]:
        repo = TemplatePackRepository(session)
        rows = list(await repo.list_all(owner_id=owner_id, status=STATUS_PUBLISHED))
        slots_map = await repo.slots_of([row.id for row in rows])
        return [_summary(row, slots_map.get(row.id, [])) for row in rows]

    async def get_published(
        self, session: AsyncSession, pack_id: str
    ) -> TemplatePackSummary | None:
        repo = TemplatePackRepository(session)
        row = await repo.get(pack_id)
        if row is None or row.status != STATUS_PUBLISHED:
            return None
        slots = list(await repo.slots(pack_id))
        return _summary(row, slots)

    async def candidates(
        self,
        session: AsyncSession,
        actor: CurrentUser,
        pack_id: str,
        token: str,
        *,
        limit: int = 100,
    ) -> list[TemplatePackCandidate]:
        from modules.template_pack.service import candidates as _candidates

        result = await _candidates(pack_id, token, session, actor=actor, limit=limit)
        return [
            TemplatePackCandidate(
                asset_id=item.asset_id,
                name=item.name,
                kind=item.kind,
                cover_url=item.cover_url,
            )
            for item in result.items
        ]

    async def verify_picks(
        self,
        session: AsyncSession,
        actor: CurrentUser,
        pack_id: str,
        picks: dict[str, str],
    ) -> dict[str, str]:
        from modules.template_pack.service import verify_picks as _verify

        return await _verify(pack_id, picks, session, actor=actor)


port = SqlTemplatePackPort()


def install() -> None:
    """import 期挂到注入口。幂等——每次传的是同一个实例。"""
    register_template_pack(port)
