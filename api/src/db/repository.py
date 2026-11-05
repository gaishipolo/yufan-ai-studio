"""通用仓储基类。

把「增删改查 + 分页」这类与实体无关的操作收敛于此，
子类只需声明 `model` 并扩展专属查询，避免每个模块重写一遍。
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Generic, TypeVar

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.exceptions import NotFound
from core.response import PageParams
from db.base import Base

ModelT = TypeVar("ModelT", bound=Base)


class BaseRepository(Generic[ModelT]):
    """通用仓储。

    - 复杂实体：子类声明 `model` 并扩展专属查询
    - 简单实体：直接 `BaseRepository(session, Asset)` 实例化即可，无需写子类
    """

    model: type[ModelT]

    def __init__(self, session: AsyncSession, model: type[ModelT] | None = None) -> None:
        self.session = session
        if model is not None:
            self.model = model

    # ---------- 读 ----------
    async def get(self, entity_id: str) -> ModelT | None:
        return await self.session.get(self.model, entity_id)

    async def get_or_404(self, entity_id: str, message: str = "资源不存在") -> ModelT:
        obj = await self.get(entity_id)
        if obj is None:
            raise NotFound(message)
        return obj

    async def exists(self, entity_id: str) -> bool:
        return await self.get(entity_id) is not None

    async def list(self, stmt: Select[Any]) -> Sequence[ModelT]:
        return (await self.session.scalars(stmt)).all()

    async def count(self, stmt: Select[Any] | None = None) -> int:
        base = stmt if stmt is not None else select(self.model)
        total = await self.session.scalar(select(func.count()).select_from(base.subquery()))
        return int(total or 0)

    async def paginate(
        self, stmt: Select[Any], params: PageParams
    ) -> tuple[Sequence[ModelT], int]:
        """分页查询：返回 `(本页行, 总数)`。

        **传进来的 `stmt` 必须自己带 `order_by`，并且以主键收尾。**
        sqlite 会按 rowid 兜底，看起来「总是有顺序」；但这个顺序不是查询的一部分，
        换 PG 或并发写入之后就不成立了——同一时间戳的行在翻页边界上可能重复出现、
        也可能一行都没有。`total` 走 `count(*)`，不要用 `len(rows)`（R8-06）。
        """
        total = await self.session.scalar(select(func.count()).select_from(stmt.subquery()))
        rows = (
            await self.session.scalars(stmt.offset(params.offset).limit(params.limit))
        ).all()
        return rows, int(total or 0)

    # ---------- 写 ----------
    async def add(self, obj: ModelT) -> ModelT:
        self.session.add(obj)
        await self.session.flush()
        return obj

    async def add_all(self, objs: Sequence[ModelT]) -> Sequence[ModelT]:
        self.session.add_all(list(objs))
        await self.session.flush()
        return objs

    async def update(self, obj: ModelT, **fields: Any) -> ModelT:
        for key, value in fields.items():
            setattr(obj, key, value)
        await self.session.flush()
        return obj

    async def delete(self, obj: ModelT) -> None:
        await self.session.delete(obj)
        await self.session.flush()

    async def delete_by_ids(self, ids: Sequence[str]) -> int:
        objs = [obj for _id in ids if (obj := await self.get(_id)) is not None]
        for obj in objs:
            await self.session.delete(obj)
        await self.session.flush()
        return len(objs)
