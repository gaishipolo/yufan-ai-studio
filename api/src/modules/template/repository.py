"""替换运行的数据访问层：调用档案、本次输入、评价。

素材包/视频模板的仓储已经搬到 `modules/template_pack`（2026-11-14 C2）。
本模块只读写「一次替换进行到哪一步」，**没有一处 `select(Model)` 全表读**：
列表查询都带 owner 或 status 前缀，分页走 `limit/offset`，排序键以主键收尾
（见 `docs/EXTENDING.md` §1.1）。
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import NamedTuple

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.exceptions import NotFound
from modules.template.models import (
    ReplacementReview,
    ReplacementRun,
    ReplacementRunInput,
)


class RunInputRow(NamedTuple):
    """一条「这次实际用到的素材」（`library_assets.id`）。"""

    asset_id: str
    role: str
    position: int
    usage: str = ""
    storage_key: str = ""


class RunRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, run_id: str) -> ReplacementRun | None:
        return await self.session.get(ReplacementRun, run_id)

    async def get_or_404(self, run_id: str, *, owner: str | None = None) -> ReplacementRun:
        run = await self.get(run_id)
        if run is None:
            raise NotFound("调用档案不存在")
        if owner is not None and run.owner_id != owner:
            raise NotFound("调用档案不存在")
        return run

    async def list_owned(
        self, owner: str, *, include_deleted: bool = False
    ) -> Sequence[ReplacementRun]:
        stmt = select(ReplacementRun).where(ReplacementRun.owner_id == owner)
        if not include_deleted:
            stmt = stmt.where(ReplacementRun.deleted_at.is_(None))
        stmt = stmt.order_by(ReplacementRun.created_at.desc(), ReplacementRun.id)
        return (await self.session.scalars(stmt)).all()

    async def list_unfinished(self, statuses: Sequence[str]) -> Sequence[ReplacementRun]:
        """启动恢复要扫的行：只取非终态，避免把十年历史全读一遍。"""
        stmt = (
            select(ReplacementRun)
            .where(
                ReplacementRun.status.in_(list(statuses)),
                ReplacementRun.deleted_at.is_(None),
            )
            .order_by(ReplacementRun.created_at)
        )
        return (await self.session.scalars(stmt)).all()

    async def add(self, run: ReplacementRun) -> ReplacementRun:
        self.session.add(run)
        await self.session.flush()
        return run

    async def save(self, run: ReplacementRun) -> None:
        await self.session.flush()

    # ---------- 输入 ----------
    async def inputs(self, run_id: str) -> Sequence[ReplacementRunInput]:
        stmt = (
            select(ReplacementRunInput)
            .where(ReplacementRunInput.run_id == run_id)
            .order_by(ReplacementRunInput.position)
        )
        return (await self.session.scalars(stmt)).all()

    async def replace_inputs(self, run_id: str, rows: Sequence[RunInputRow]) -> None:
        await self.session.execute(
            delete(ReplacementRunInput).where(ReplacementRunInput.run_id == run_id)
        )
        for row in rows:
            self.session.add(
                ReplacementRunInput(
                    run_id=run_id,
                    asset_id=row.asset_id,
                    role=row.role,
                    position=row.position,
                    usage=row.usage,
                    storage_key=row.storage_key,
                )
            )

    # ---------- 评价 ----------
    async def review(self, run_id: str) -> ReplacementReview | None:
        stmt = select(ReplacementReview).where(ReplacementReview.run_id == run_id)
        return await self.session.scalar(stmt)

    async def upsert_review(self, review: ReplacementReview) -> ReplacementReview:
        self.session.add(review)
        await self.session.flush()
        return review


