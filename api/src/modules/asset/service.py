"""素材资产服务。

已实现：列表查询（含分面计数）。
待实现：上传凭证、删除、引用计数维护 —— 方法内已标注 TODO 锚点。
"""

from __future__ import annotations

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.context import CurrentUser
from core.exceptions import NotImplementedFeature
from db.repository import BaseRepository
from modules.asset.models import Asset
from modules.asset.schema import (
    AssetCounts,
    AssetListOut,
    AssetListQuery,
    AssetOut,
    UploadAssetIn,
    UploadTicketOut,
)


class AssetService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repo = BaseRepository(session, Asset)

    # ------------------------------------------------------------------ 已实现
    async def list_assets(self, actor: CurrentUser, query: AssetListQuery) -> AssetListOut:
        counts = await self._counts(actor, query)

        stmt = self._scoped_query(actor, query, with_type=True)
        stmt = stmt.order_by(Asset.created_at.desc(), Asset.id.desc())
        rows = list(await self.repo.list(stmt))

        return AssetListOut(
            items=[AssetOut.model_validate(row) for row in rows],
            total=counts.all if query.type == "all" else counts.model_dump()[query.type],
            counts=counts,
        )

    # ------------------------------------------------------------------ 待实现
    async def create_upload_ticket(
        self, actor: CurrentUser, payload: UploadAssetIn, storage
    ) -> UploadTicketOut:
        """TODO(storage): 生成对象 key → 签发预签名 URL → 落 asset 草稿记录。"""
        raise NotImplementedFeature("素材上传接口待接入对象存储")

    async def delete_asset(self, actor: CurrentUser, asset_id: str) -> None:
        """TODO: 校验归属 → 引用计数 > 0 时拒绝 → 删除对象与记录。"""
        raise NotImplementedFeature("素材删除接口待实现")

    # ------------------------------------------------------------------ 内部
    def _scoped_query(
        self, actor: CurrentUser, query: AssetListQuery, *, with_type: bool = False
    ) -> Select:
        stmt = select(Asset).where(Asset.owner_id == actor.id)
        keyword = query.keyword.strip()
        if keyword:
            stmt = stmt.where(Asset.name.ilike(f"%{keyword}%"))
        if with_type and query.type != "all":
            stmt = stmt.where(Asset.type == query.type)
        return stmt

    async def _counts(self, actor: CurrentUser, query: AssetListQuery) -> AssetCounts:
        stmt = self._scoped_query(actor, query)
        sub = stmt.subquery()
        rows = (
            await self.session.execute(select(sub.c.type, func.count()).group_by(sub.c.type))
        ).all()
        buckets = {str(kind): int(total) for kind, total in rows}
        return AssetCounts(
            all=sum(buckets.values()),
            image=buckets.get("image", 0),
            video=buckets.get("video", 0),
            audio=buckets.get("audio", 0),
        )


__all__ = ["AssetService"]
