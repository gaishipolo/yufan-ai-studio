"""封装（pack）：把一批已入库的素材打成一个可下载 / 可替换的整体。

## 封装是什么，不是什么

- **是**一行 `library_assets`（`kind=pack`，`origin=pack`，没有自己的字节）+
  `library_pack_items` 里的有序成员。所以它继承列表、可见性、标签、回收站、
  搜索的全部规则——不需要为「封装」再做一套入口。
- **不是**「复制一份素材」：成员行仍然只属于原来的 owner，封装只是引用。
  解散封装不会影响任何成员（设计 §2）。

## 三条约束

1. **成员必须是我看得见且在库的**：把看不见的 id 塞进封装，等于用封装当窥探
   别处资产的探针（下载时才发现拿不到字节，或者更糟——拿到）。
2. **成员不能是另一个封装**：嵌套会让「下载解包」变成递归，也让「这个封装多大」
   变成一个需要遍历的图。设计里没有这个需求，直接禁止。
3. **`position` 全量替换**：`(pack_id, position)` 有唯一索引，所以更新成员是
   「删干净 → flush → 重插」，不是逐条 update。反过来的写法在 SQLite 下会
   因为同一事务里的唯一性检查顺序而随机失败。
4. **标签自动继承成员**：建封装 / 换成员时把成员标签的并集并到封装上（见
   `tags.sync_pack_tags`）。成员被装进封装后从列表里隐身，不继承的话按
   「产品」筛只能筛到散件、筛不到装着产品图的封装，标签筛选像是漏了东西。
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.context import CurrentUser
from core.exceptions import Conflict, NotFound
from infra.storage.base import ObjectStorage
from modules.library import access
from modules.library.errors import PackEmpty, PackItemInvalid
from modules.library.models import LibraryAsset, LibraryPackItem
from modules.library.schema import (
    LibraryAssetDetailOut,
    PackIn,
    PackItemIn,
    PackPatchIn,
)
from modules.library.tags import PACK_TAG_NAME, ensure_tag, sync_pack_tags

__all__ = ["PackService", "pack_cover_key"]

#: 封装的 `origin`。写进资产行之后不再变——来源是审计信息，不该随编辑漂。
PACK_ORIGIN = "pack"


def pack_cover_key(members: Sequence[LibraryAsset]) -> str:
    """封面的取法：优先图片成员的缩略图，其次任意成员的缩略图，最后空。

    不引入「封面成员」这个概念（`coverAssetId` 只用来**指定**取谁），
    因为封面是展示细节，把它做成关系会让「解散后封面归谁」变成一个新问题。
    """
    for member in members:
        if member.kind == "image" and (member.cover_key or member.storage_key):
            return member.cover_key or member.storage_key
    for member in members:
        if member.cover_key:
            return member.cover_key
    return ""


class PackService:
    def __init__(self, session: AsyncSession, storage: ObjectStorage) -> None:
        self.session = session
        self.storage = storage

    # ------------------------------------------------------------------ 写

    async def create(self, payload: PackIn, actor: CurrentUser) -> LibraryAssetDetailOut:
        items = _ordered_items(payload.items, payload.asset_ids)
        if not items:
            raise PackEmpty()
        members = await self._validate(items, actor)
        cover = pack_cover_key(members)
        if payload.cover_asset_id:
            if payload.cover_asset_id not in {member.id for member in members}:
                raise PackItemInvalid(details={"items": [payload.cover_asset_id]})
            # 指定的成员没有可用图像（音频 / 抽帧失败）时退回自动封面，
            # 不报错：用户选的是「用这张当封面」，拿不到图时用别的比失败更合理。
            cover = _cover_of(payload.cover_asset_id, members) or cover

        pack = LibraryAsset(
            name=payload.name,
            kind="pack",
            origin=PACK_ORIGIN,
            # 封装没有自己的 mime / 字节：`storage_key` 空着，下载时解成员。
            mime_type="",
            size_bytes=sum(member.size_bytes for member in members),
            duration=sum(member.duration or 0.0 for member in members),
            has_audio=any(member.has_audio for member in members),
            cover_key=cover,
            owner_id=actor.id,
            visibility="personal",
            state="active",
            state_version=1,
        )
        self.session.add(pack)
        await self.session.flush()
        await self._write_items(pack.id, items)
        # 「封装」是固定标签（类型筛选里没有「封装」这一档，靠它找封装）；
        # 用户手选的标签 + 所有成员的标签一起并进来，`sync_pack_tags` 去重。
        pack_tag = await ensure_tag(self.session, PACK_TAG_NAME, actor.id)
        await sync_pack_tags(
            self.session,
            pack.id,
            [member.id for member in members],
            base=[pack_tag.id, *(payload.tag_ids or [])],
        )
        await self.session.commit()
        return await self._detail(pack.id, actor)

    async def update(
        self, pack_id: str, payload: PackPatchIn, actor: CurrentUser
    ) -> LibraryAssetDetailOut:
        pack = await self._load_pack(pack_id, actor)
        if payload.expected_version and pack.state_version != payload.expected_version:
            raise Conflict(
                "封装已被修改，请刷新后重试",
                details={"stateVersion": pack.state_version},
            )
        if payload.name is not None:
            pack.name = payload.name
        if payload.items is not None:
            items = _ordered_items(payload.items, [])
            if not items:
                raise PackEmpty()
            members = await self._validate(items, actor)
            await self.session.execute(
                delete(LibraryPackItem).where(LibraryPackItem.pack_id == pack.id)
            )
            # 先 flush 再插：唯一索引 (pack_id, position) 在同一事务里也要先看不到旧行
            await self.session.flush()
            await self._write_items(pack.id, items)
            pack.size_bytes = sum(member.size_bytes for member in members)
            pack.duration = sum(member.duration or 0.0 for member in members)
            pack.has_audio = any(member.has_audio for member in members)
            pack.cover_key = pack_cover_key(members)
        if payload.cover_asset_id is not None:
            current = await self.member_rows(pack.id)
            ids = [row.id for row in current]
            if payload.cover_asset_id and payload.cover_asset_id not in ids:
                raise PackItemInvalid(details={"items": [payload.cover_asset_id]})
            explicit = _cover_of(payload.cover_asset_id, current) if payload.cover_asset_id else ""
            pack.cover_key = explicit or pack_cover_key(current)
        if payload.tag_ids is not None or payload.items is not None:
            member_ids = (
                [member.id for member in members]
                if payload.items is not None
                else [row.id for row in await self.member_rows(pack.id)]
            )
            # 没显式传 tagIds 就在现有标签上「只增不减」：新装进来的素材，
            # 它的标签应该跟着出现在封装上，但用户手动去掉 / 加上的标签不该被抹掉。
            await sync_pack_tags(
                self.session,
                pack.id,
                member_ids,
                base=payload.tag_ids if payload.tag_ids is not None else None,
            )
        pack.state_version += 1
        await self.session.commit()
        return await self._detail(pack.id, actor)

    async def delete(self, pack_id: str, actor: CurrentUser) -> None:
        """解散 = 把封装行移进回收站。**成员一行不动**。

        不直接物理删：解散也要能反悔，而「反悔」在 v2 里已经有回收站这个统一答案。
        成员关系留在 `library_pack_items` 里，恢复后封装还是原来那个封装。
        """
        from modules.library.lifecycle import LifecycleService

        pack = await self._load_pack(pack_id, actor)
        await LifecycleService(self.session, self.storage).delete(pack.id, actor)

    # ------------------------------------------------------------------ 读

    async def member_rows(self, pack_id: str) -> list[LibraryAsset]:
        """按 `position` 返回成员资产行（不含已物理删除的）。"""
        ids = list(
            await self.session.scalars(
                select(LibraryPackItem.asset_id)
                .where(LibraryPackItem.pack_id == pack_id)
                .order_by(LibraryPackItem.position)
            )
        )
        if not ids:
            return []
        rows = {
            row.id: row
            for row in (
                await self.session.scalars(
                    select(LibraryAsset).where(LibraryAsset.id.in_(ids))
                )
            ).all()
        }
        return [rows[asset_id] for asset_id in ids if asset_id in rows]

    async def items_of(self, pack_id: str) -> list[LibraryPackItem]:
        return list(
            await self.session.scalars(
                select(LibraryPackItem)
                .where(LibraryPackItem.pack_id == pack_id)
                .order_by(LibraryPackItem.position)
            )
        )

    # ------------------------------------------------------------------ 内部

    async def _detail(self, pack_id: str, actor: CurrentUser) -> LibraryAssetDetailOut:
        from modules.library.service import LibraryService

        # 刚建 / 刚改完的封装必然可见（自己的），但共享出去的封装改完可能
        # 已经不在自己的「个人库」筛选里，所以这里不做二次可见性判定。
        return await LibraryService(self.session, self.storage).detail(
            pack_id, actor, require_visible=False
        )

    async def _load_pack(self, pack_id: str, actor: CurrentUser) -> LibraryAsset:
        row = await access.load_visible(self.session, pack_id, actor)
        if row.kind != "pack":
            # 拿普通资产的 id 调封装接口：对调用方来说就是「没这个东西」
            raise NotFound("封装不存在")
        if row.owner_id != actor.id and not actor.has(access.PERM_DELETE_ANY):
            raise NotFound("封装不存在")
        return row

    async def _validate(
        self, items: Sequence[PackItemIn], actor: CurrentUser
    ) -> list[LibraryAsset]:
        """逐条校验成员，**一次把所有不合法的 id 都列出来**。

        只报第一个会让用户改一次失败一次（10 个成员里 3 个不可用 = 3 轮往返）。
        """
        ids = [item.asset_id for item in items]
        rows = {
            row.id: row
            for row in (
                await self.session.scalars(
                    select(LibraryAsset).where(LibraryAsset.id.in_(ids))
                )
            ).all()
        }
        my_groups = await access.all_groups(self.session, actor)
        group_map = await access.asset_group_ids(self.session, ids)
        invalid: list[str] = []
        for asset_id in ids:
            row = rows.get(asset_id)
            if (
                row is None
                or row.state != "active"
                or row.kind == "pack"
                or not access.can_read(row, actor, my_groups, group_map.get(asset_id, []))
            ):
                invalid.append(asset_id)
        if invalid:
            raise PackItemInvalid(details={"items": list(dict.fromkeys(invalid))})
        return [rows[asset_id] for asset_id in dict.fromkeys(ids)]

    async def _write_items(self, pack_id: str, items: Sequence[PackItemIn]) -> None:
        for position, item in enumerate(items, start=1):
            self.session.add(
                LibraryPackItem(
                    pack_id=pack_id,
                    asset_id=item.asset_id,
                    position=position,
                    role=item.role,
                    description=item.description,
                )
            )


def _ordered_items(
    items: Sequence[PackItemIn], asset_ids: Sequence[str]
) -> list[PackItemIn]:
    """`items` 在前，`assetIds` 里没出现过的按给定顺序追加。

    **重复的成员只保留第一次出现的位置**：勾选列表在「先勾 A、取消、再勾 A」
    之后很容易带出重复项，而重复成员会让下载包里出现两份同样的文件。
    静默去重而不是报错，是因为调用方没法解释「为什么这个 id 不行」。
    """
    ordered: list[PackItemIn] = []
    seen: set[str] = set()
    for item in items:
        if item.asset_id in seen:
            continue
        seen.add(item.asset_id)
        ordered.append(item)
    for asset_id in asset_ids:
        if asset_id in seen:
            continue
        seen.add(asset_id)
        ordered.append(PackItemIn(asset_id=asset_id))
    return ordered


def _cover_of(cover_asset_id: str, members: Sequence[LibraryAsset]) -> str:
    """指定封面的取图。音频没有可展示的图，返回空让调用方退回自动封面。"""
    for member in members:
        if member.id != cover_asset_id:
            continue
        if member.kind == "audio":
            return ""
        return member.cover_key or (member.storage_key if member.kind == "image" else "")
    return ""
