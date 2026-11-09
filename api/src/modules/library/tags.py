"""标签：CRUD + 资产打标。

## 两个刻意的取舍

1. **新建同名标签不报错，返回已有的那条**。用户在筛选栏里输入「圣诞节」时，
   他想的是「打上这个标签」，不是「创建一条叫圣诞节的记录」。返回 409 会让前端
   每次都得先查一遍再决定调哪个接口，而重名本身没有危害——标签是全局词表，
   同名就是同一个意思。
2. **改名撞到已有名字时报 400，不做隐式合并**。改名与合并是两个不可逆程度不同的
   动作：改名只换显示，合并会把两条标签的资产并到一起、之后拆不开。想合并就
   明确走「合并」接口（M3），不在改名里顺手做掉。

标签本身不做权限隔离（全局词表），`library:tag:manage` 管的是「谁能改词表」。
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.context import CurrentUser
from core.exceptions import BadRequest, Conflict, NotFound
from modules.library.models import LibraryAssetTag, LibraryTag
from modules.library.schema import LibraryTagOut, TagIn

__all__ = [
    "PACK_TAG_NAME",
    "TagService",
    "add_asset_tags",
    "ensure_tag",
    "remove_asset_tags",
    "set_asset_tags",
    "tag_ids_by_asset",
]

#: 封装创建时自动打上的标签名。
#:
#: 为什么用标签而不是留一个 `kind=pack` 的筛选项：用户要的是「按封装找」这件事，
#: 标签本来就可筛选、可改名、可组合，而 `kind` 是类型维度——把封装塞进类型筛选，
#: 它会和「成片 / 图片 / 音频」并列，看起来像第四种素材，而它其实是个容器。
PACK_TAG_NAME = "封装"


async def ensure_tag(session: AsyncSession, name: str, created_by: str) -> LibraryTag:
    """按名字取标签，没有就建。与 `TagService.create` 同一个幂等口径。"""
    row = await session.scalar(select(LibraryTag).where(LibraryTag.name == name))
    if row is not None:
        return row
    row = LibraryTag(name=name, created_by=created_by)
    session.add(row)
    await session.flush()
    return row


async def _ensure_tags_exist(session: AsyncSession, tag_ids: Sequence[str]) -> None:
    wanted = [tag_id for tag_id in dict.fromkeys(tag_ids) if tag_id]
    if not wanted:
        return
    found = set(
        await session.scalars(select(LibraryTag.id).where(LibraryTag.id.in_(wanted)))
    )
    missing = [tag_id for tag_id in wanted if tag_id not in found]
    if missing:
        # 400 而不是 404：一次请求里可能同时含存在与不存在的 id，
        # 404 会让人以为「整个打标失败了」，而实际只是其中几个不合法。
        raise BadRequest(f"标签不存在：{', '.join(missing[:5])}")


async def tag_ids_by_asset(
    session: AsyncSession, asset_ids: Sequence[str]
) -> dict[str, list[str]]:
    wanted = [asset_id for asset_id in dict.fromkeys(asset_ids) if asset_id]
    if not wanted:
        return {}
    rows = await session.execute(
        select(LibraryAssetTag.asset_id, LibraryAssetTag.tag_id).where(
            LibraryAssetTag.asset_id.in_(wanted)
        )
    )
    result: dict[str, list[str]] = {}
    for asset_id, tag_id in rows.all():
        result.setdefault(asset_id, []).append(tag_id)
    return result


async def add_asset_tags(
    session: AsyncSession, asset_id: str, tag_ids: Sequence[str]
) -> None:
    """幂等追加。已存在的绑定跳过——批量打标时同一批里重复点一次很常见。"""
    await _ensure_tags_exist(session, tag_ids)
    current = set(
        await session.scalars(
            select(LibraryAssetTag.tag_id).where(LibraryAssetTag.asset_id == asset_id)
        )
    )
    for tag_id in dict.fromkeys(tag_ids):
        if tag_id and tag_id not in current:
            session.add(LibraryAssetTag(asset_id=asset_id, tag_id=tag_id))


async def remove_asset_tags(
    session: AsyncSession, asset_id: str, tag_ids: Sequence[str]
) -> None:
    wanted = [tag_id for tag_id in dict.fromkeys(tag_ids) if tag_id]
    if not wanted:
        return
    await session.execute(
        delete(LibraryAssetTag).where(
            LibraryAssetTag.asset_id == asset_id,
            LibraryAssetTag.tag_id.in_(wanted),
        )
    )


async def set_asset_tags(
    session: AsyncSession, asset_id: str, tag_ids: Sequence[str]
) -> None:
    """整体替换（PATCH 的语义）。空数组 = 清空标签。"""
    await _ensure_tags_exist(session, tag_ids)
    kept = [tag_id for tag_id in dict.fromkeys(tag_ids) if tag_id]
    if kept:
        await session.execute(
            delete(LibraryAssetTag).where(
                LibraryAssetTag.asset_id == asset_id,
                LibraryAssetTag.tag_id.not_in(kept),
            )
        )
    else:
        await session.execute(
            delete(LibraryAssetTag).where(LibraryAssetTag.asset_id == asset_id)
        )
    current = set(
        await session.scalars(
            select(LibraryAssetTag.tag_id).where(LibraryAssetTag.asset_id == asset_id)
        )
    )
    for tag_id in kept:
        if tag_id not in current:
            session.add(LibraryAssetTag(asset_id=asset_id, tag_id=tag_id))


class TagService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_tags(self) -> list[LibraryTagOut]:
        rows = await self.session.scalars(select(LibraryTag).order_by(LibraryTag.name))
        return [LibraryTagOut(id=row.id, name=row.name) for row in rows.all()]

    async def create(self, payload: TagIn, actor: CurrentUser) -> LibraryTagOut:
        existing = await self.session.scalar(
            select(LibraryTag).where(LibraryTag.name == payload.name.strip())
        )
        if existing is not None:
            # 见模块文档第 1 条：同名即同义，返回已有那条
            return LibraryTagOut(id=existing.id, name=existing.name)
        row = LibraryTag(name=payload.name.strip(), created_by=actor.id)
        self.session.add(row)
        await self.session.commit()
        return LibraryTagOut(id=row.id, name=row.name)

    async def rename(self, tag_id: str, payload: TagIn) -> LibraryTagOut:
        row = await self.session.get(LibraryTag, tag_id)
        if row is None:
            raise NotFound("标签不存在")
        name = payload.name.strip()
        if name == row.name:
            return LibraryTagOut(id=row.id, name=row.name)
        other = await self.session.scalar(select(LibraryTag).where(LibraryTag.name == name))
        if other is not None:
            raise Conflict("标签名已被占用")
        row.name = name
        await self.session.commit()
        return LibraryTagOut(id=row.id, name=row.name)

    async def delete(self, tag_id: str) -> None:
        """删标签同时清绑定。

        不做「还有资产在用就拒绝」：标签是组织手段，不是资产的一部分，
        删一个词表项不该被 200 条资产卡住。资产本身完好无损。
        """
        row = await self.session.get(LibraryTag, tag_id)
        if row is None:
            raise NotFound("标签不存在")
        await self.session.execute(
            delete(LibraryAssetTag).where(LibraryAssetTag.tag_id == tag_id)
        )
        await self.session.delete(row)
        await self.session.commit()
