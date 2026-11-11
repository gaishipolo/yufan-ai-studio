"""生命周期：草稿 / 在库 / 回收站，以及删除权限矩阵（设计 §3.3–§3.5）。

## 两个「删除」不是一件事

- **移入回收站**（`delete`）：可恢复，只改状态；
- **彻底删除**（`purge`）：不可逆，删字节前必须过 `library-references`。

把这两件事塞进同一个接口，前端就没法在按钮上写清「这一下还能不能后悔」。

## 不变式

- A：`state ∈ {draft, trash} ⇒ visibility = personal`；
- B：`state = trash ⇒ library_asset_groups` 里没有该资产的行。

两条都在 `_to_trash()` 里**强制**成立（清组行 + 置 personal），而不是靠调用方
记得先调某个方法。原因是这两条一旦破，回收站里的东西会继续出现在小组库里，
而「删除」这个动作对共享出来的人意味着「同事不再能看到」——破了就不算删。
"""

from __future__ import annotations

import contextlib
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, cast

from sqlalchemy import delete, func, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from core.audit import record as audit
from core.config import settings
from core.context import CurrentUser
from core.exceptions import Conflict, NotFound
from core.logging import get_logger
from core.ports.library_references import get_library_references
from infra.storage.base import ObjectStorage
from modules.library import access
from modules.library.errors import (
    AssetNotInTrash,
    DraftNotReviewable,
    DraftReviewForbidden,
    RevokeReasonRequired,
    SharedAssetNotDeletable,
    ShareRevokePending,
    TeamDeleteForbidden,
    TrashEntryReferenced,
)
from modules.library.models import (
    LibraryAsset,
    LibraryAssetGeneration,
    LibraryAssetGroup,
    LibraryAssetTag,
    LibraryDraftReview,
    LibraryPackItem,
    LibraryShareRequest,
    LibraryTextVersion,
)
from modules.library.schema import (
    DraftDecisionIn,
    LibraryAssetDetailOut,
)

__all__ = ["LifecycleService"]

logger = get_logger(__name__)

#: 自审窄口写进申请单的标记，运营查账时按它筛出「合并动作」的记录。
SELF_LEADER_NOTE = "self_leader_combined"


class LifecycleService:
    def __init__(self, session: AsyncSession, storage: ObjectStorage) -> None:
        self.session = session
        self.storage = storage

    # ------------------------------------------------------------------ 删除

    async def delete(
        self,
        asset_id: str,
        actor: CurrentUser,
        *,
        revoke_own_group_share: bool = False,
        reason: str = "",
    ) -> None:
        """移入回收站。权限矩阵见设计 §3.4。

        顺序很重要：先判「在不在回收站」，再判「是不是别人的」，最后才判共享。
        反过来会让「别人回收站里的条目」返回 409 而不是 404——那就等于确认了
        「这个 id 存在」。
        """
        row = await access.load_asset(self.session, asset_id)
        is_admin = actor.has("library:delete:any")
        if not is_admin and row.owner_id != actor.id:
            raise NotFound("资产不存在")
        if row.state == "trash":
            raise Conflict("该资产已在回收站")
        if row.state == "draft":
            # 草稿直接删：`trashed_from_state=draft`，恢复时回到草稿箱而不是库内
            await self._to_trash(row, actor, reason="deleted", commit=True)
            return

        group_ids = (await access.asset_group_ids(self.session, [row.id])).get(row.id, [])
        pending = await self._pending_revoke(row.id)

        if not is_admin:
            if row.visibility == "team":
                raise TeamDeleteForbidden()
            if row.visibility == "group":
                if pending is not None:
                    raise ShareRevokePending()
                eligible = await self.can_revoke_and_delete(row, actor, group_ids)
                if revoke_own_group_share and eligible:
                    if not reason.strip():
                        # 一步不等于免记录：申请单里的 reason 是撤销的凭据
                        raise RevokeReasonRequired()
                    await self.revoke_and_delete(row, actor, group_ids[0], reason)
                    return
                raise SharedAssetNotDeletable(
                    details={"canRevokeAndDelete": eligible, "groupIds": group_ids}
                )
            await self._to_trash(row, actor, reason="deleted", commit=True)
            return

        # 管理员兜底：删团队库 / 删别人的 / 删已共享的，都要留一条自审撤销记录
        shared = row.visibility != "personal" or bool(group_ids)
        if shared or row.owner_id != actor.id:
            if not reason.strip():
                raise RevokeReasonRequired()
            await self._admin_delete(row, actor, group_ids, reason)
            return
        await self._to_trash(row, actor, reason="deleted", commit=True)

    async def can_revoke_and_delete(
        self,
        row: LibraryAsset,
        actor: CurrentUser,
        group_ids: list[str] | None = None,
    ) -> bool:
        """设计 §3.4.1 的窄口：四个条件全满足才算命中。

        「只共享给本组」靠 `visibility=group` + 恰好一行组行来表达：共享到团队时
        `_apply_share` 会清空组行并把 visibility 置 team，所以「有 team 又有组」
        这种状态不存在。仍然再查一次组行数，是因为这条规则要挡住不可逆动作，
        宁可多重算一次，也不要依赖另一处的写入正确性。
        """
        if row.state != "active" or row.visibility != "group":
            return False
        if row.owner_id != actor.id:
            return False
        groups = group_ids or []
        if len(groups) != 1:
            return False
        my_groups = await access.all_groups(self.session, actor)
        if groups[0] not in my_groups:
            return False
        return await access.is_group_leader(self.session, actor.id, groups[0])

    async def revoke_and_delete(
        self, row: LibraryAsset, actor: CurrentUser, group_id: str, reason: str
    ) -> None:
        """窄口动作：一次事务里「自审撤销 + 移入回收站」。

        申请单必须真的落一行：审计与申请单一一对应，运营才能回答「这条素材是
        什么时候、被谁撤了共享」。所以这里的「一步」只是省掉了中间那两次点击，
        不是省掉了记录。
        """
        now = datetime.now(UTC)
        request = LibraryShareRequest(
            asset_id=row.id,
            applicant_id=actor.id,
            action="revoke",
            target="group",
            group_id=group_id,
            status="approved",
            reviewer_id=actor.id,
            note=SELF_LEADER_NOTE,
            reason=reason.strip() or "组长撤销本组共享并删除",
            decided_at=now,
            expected_version=row.state_version,
        )
        self.session.add(request)
        await self.session.flush()
        await self.session.execute(
            delete(LibraryAssetGroup).where(
                LibraryAssetGroup.asset_id == row.id,
                LibraryAssetGroup.group_id == group_id,
            )
        )
        row.visibility = "personal"
        await self._to_trash(row, actor, reason="deleted", commit=False)
        await self.session.commit()
        await audit(
            "library.asset.revoke_and_delete",
            actor_id=actor.id,
            target_type="library_asset",
            target_id=row.id,
            detail={"requestId": request.id, "groupId": group_id},
        )

    async def _admin_delete(
        self, row: LibraryAsset, actor: CurrentUser, group_ids: list[str], reason: str
    ) -> None:
        now = datetime.now(UTC)
        # 团队共享登记成一条 target=team 的撤销单；小组共享逐组登记。
        # 哪怕管理员只是「直接删掉」，也要留下「撤销存在过」的证据。
        targets: list[dict[str, str]] = (
            [{"target": "team", "group_id": ""}]
            if row.visibility == "team"
            else [{"target": "group", "group_id": group_id} for group_id in group_ids]
        )
        request_ids: list[str] = []
        for item in targets:
            request = LibraryShareRequest(
                asset_id=row.id,
                applicant_id=actor.id,
                action="revoke",
                target=item["target"],
                group_id=item["group_id"],
                status="approved",
                reviewer_id=actor.id,
                note="admin_forced",
                reason=reason.strip(),
                decided_at=now,
                expected_version=row.state_version,
            )
            self.session.add(request)
            await self.session.flush()
            request_ids.append(request.id)
        await self._to_trash(row, actor, reason="deleted", commit=False)
        await self.session.commit()
        await audit(
            "library.asset.admin_delete",
            actor_id=actor.id,
            target_type="library_asset",
            target_id=row.id,
            detail={"requestIds": request_ids, "ownerId": row.owner_id, "reason": reason},
        )

    # ------------------------------------------------------------------ 回收站

    async def restore(self, asset_id: str, actor: CurrentUser) -> LibraryAssetDetailOut:
        row = await access.load_asset(self.session, asset_id)
        is_admin = actor.has("library:delete:any")
        if not is_admin and row.owner_id != actor.id:
            raise NotFound("资产不存在")
        if row.state != "trash":
            raise AssetNotInTrash()

        row.state = row.trashed_from_state or "active"
        row.visibility = "personal"  # 绝不恢复共享：要重新走一次申请
        row.trashed_from_state = None
        row.trash_reason = None
        row.trash_expires_at = None
        row.deleted_at = None
        row.deleted_by = None
        row.state_version += 1
        await self.session.commit()
        await audit(
            "library.asset.restore",
            actor_id=actor.id,
            target_type="library_asset",
            target_id=row.id,
            detail={"state": row.state},
        )
        from modules.library.service import LibraryService

        return await LibraryService(self.session, self.storage).detail(
            asset_id, actor, require_visible=False
        )

    async def purge(self, asset_id: str, actor: CurrentUser) -> None:
        """彻底删除。先过引用检查，被引用就 409 并说明「谁在用」。"""
        row = await access.load_asset(self.session, asset_id)
        is_admin = actor.has("library:delete:any")
        if not is_admin and row.owner_id != actor.id:
            raise NotFound("资产不存在")
        if row.state != "trash":
            raise AssetNotInTrash()

        # fail-closed：注入口没接上时抛 503，绝不把「查不到」当「没人引用」
        references = await get_library_references().references(self.session, [row.id])
        blocked = references.get(row.id)
        if blocked:
            raise TrashEntryReferenced(details={"references": list(blocked)})

        await self._purge_row(row, actor)

    async def _other_holders(
        self, key: str, asset_id: str, *, column: Literal["storage", "cover"]
    ) -> int:
        """除了自己，还有几个行指着这个**对象**。

        按 `storage_key` / `cover_key` 数，不按 `sha256`：内容寻址的 key 带扩展名
        （`<digest><ext>`），同一份字节可以落到多个对象上，「字节相同」证明不了
        「我这个对象还有别人在用」。反过来数错的方向是永久孤儿：行删了、字节还在。

        不带 `deleted_at` 条件：回收站里的行恢复后仍要用这份字节（见 `_purge_row`）。
        """
        if not key:
            return 0
        field = LibraryAsset.storage_key if column == "storage" else LibraryAsset.cover_key
        return int(
            await self.session.scalar(
                select(func.count())
                .select_from(LibraryAsset)
                .where(field == key, LibraryAsset.id != asset_id)
            )
            or 0
        )

    async def purge_expired(self, asset_id: str) -> bool:
        """GC 专用：到期的回收站条目，被引用则跳过并返回 False。"""
        row = await self.session.get(LibraryAsset, asset_id)
        if row is None or row.state != "trash":
            return False
        references = await get_library_references().references(self.session, [row.id])
        if references.get(row.id):
            return False
        await self._purge_row(row, None)
        return True

    async def _purge_row(self, row: LibraryAsset, actor: CurrentUser | None) -> None:
        storage_key, cover_key = row.storage_key, row.cover_key
        # 内容寻址：同一份字节可能还被别人的行登记着，只有最后一个引用者
        # 才删物理文件。判断放在删除本行之前，删除之后我们自己就查不到了。
        #
        # **引用要按对象数，不能按 sha256 数**。key 是 `<digest><扩展名>`，同一张图
        # 以 `.jpg` / `.jpeg` 上传会落到两个**不同对象**、却共享同一个 `sha256`。
        # 按 sha256 数出来的「还有人用」是假的：我们自己那个对象没有任何其他行
        # 指着，却因为别人有同样字节而永远不删 —— 行删了、回收站清了、字节还在，
        # 只有扫孤儿文件能发现。
        #
        # **不能带 `deleted_at IS NULL`**：回收站里的行 deleted_at 恰好非空，
        # 它们恢复之后还要用这份字节。带上这个条件的后果是「两个用户的同内容
        # 资产都在回收站时，先彻底删除的那个把文件删了，后恢复的那个 404」——
        # 上游去重（`_find_duplicate`）要的是「未删除」，这里要的是「还有人引用」，
        # 两个问题不同，条件也就不能抄。
        payload_owners = await self._other_holders(storage_key, row.id, column="storage")
        preview_holders = (
            await self._other_holders(cover_key, row.id, column="cover")
            if cover_key and cover_key != storage_key
            else 0
        )
        # 真正会被删掉的对象：原始字节与缩略图是**两个独立对象**，要分开判。
        # 缩略图按 digest 归一，通常被同一份字节的多个行共用；原始字节带扩展名，
        # 通常只有一行指着。合成一个判断，必然是一边多删一边少删。
        doomed = []
        if storage_key and payload_owners == 0:
            doomed.append(storage_key)
        if cover_key and cover_key != storage_key and preview_holders == 0:
            doomed.append(cover_key)

        # **闸门必须是第一条语句，而且要看 rowcount。**
        # `WHERE state='trash'` 写在最后等于没写：子表已经删掉、commit 也照做，
        # 只有那条 DELETE 静默命中 0 行。并发 restore 把行改回 active 时，
        # 用户看到「恢复成功」，拿回来的却是没有标签、没有封装关系、原文件 404
        # 的空壳——不可逆。所以：删不到就整段放弃，什么都不动。
        # 先取出 id：下面的 rollback 会让 ORM 实例过期，之后再访问 `row.id`
        # 会触发一次懒加载（行已经不在了），日志里就会抛异常把整个操作带崩
        asset_id = row.id
        purged = await self.session.execute(
            delete(LibraryAsset).where(
                LibraryAsset.id == asset_id, LibraryAsset.state == "trash"
            )
        )
        # DML 经 `session.execute` 返回的实际是 `CursorResult`（带 rowcount），
        # 而 SQLAlchemy 的注解按查询结果集写成 `Result`（没有 rowcount）。这里
        # 显式 cast，而不是 `getattr(purged, "rowcount", 0)`——后者一旦注解或
        # 返回类型漂移就会静默读成 0，把这道闸门自己关掉，且没有任何报错。
        deleted_rows = cast("CursorResult[Any]", purged).rowcount
        if deleted_rows != 1:
            await self.session.rollback()
            logger.info("彻底删除跳过：记录已恢复或不存在 id=%s", asset_id)
            return

        await self.session.execute(
            delete(LibraryAssetTag).where(LibraryAssetTag.asset_id == row.id)
        )
        # 封装的封面借用成员的 `cover_key`（拿不到封面图时还借用 `storage_key`）。
        # 字节真被删掉时，这些封面会变成 404 的裂图——而封装本身可能只是被解散、
        # 之后还要恢复。置空让前端回退到占位图：丢一张封面是可恢复的，裂图不是。
        #
        # 只清「指向即将删掉的对象」的那些：还活着的对象不能清，否则等于把
        # 别人的封面一起清掉（`borrowed` 只可能是 doomed，封装自己的
        # `storage_key` 为空、`sha256` 为空，所以删封装永远落不进这里）。
        if doomed:
            await self.session.execute(
                update(LibraryAsset)
                .where(
                    LibraryAsset.kind == "pack",
                    LibraryAsset.id != row.id,
                    LibraryAsset.cover_key.in_(doomed),
                )
                .values(cover_key="")
            )
        await self.session.execute(
            delete(LibraryAssetGroup).where(LibraryAssetGroup.asset_id == row.id)
        )
        await self.session.execute(
            delete(LibraryPackItem).where(LibraryPackItem.asset_id == row.id)
        )
        # 被删的本身可能是一个封装：成员关系挂在 `pack_id` 而不是 `asset_id` 上。
        # 漏了这一段，彻底删除封装会留下一批指向不存在 pack 的孤儿行，而引用检查
        # 只按 `asset_id` 查——于是谁也不会发现。
        await self.session.execute(
            delete(LibraryPackItem).where(LibraryPackItem.pack_id == row.id)
        )
        await self.session.execute(
            delete(LibraryAssetGeneration).where(LibraryAssetGeneration.asset_id == row.id)
        )
        # 文本版本快照是行内正文，purge 后没有资产行可挂。版本行留着只会让
        # 注入口按 asset_id 查到一段「已经彻底删掉」的正文（可读性判据查的是
        # `library_assets`，这里不删就绕过删除语义了）。
        await self.session.execute(
            delete(LibraryTextVersion).where(LibraryTextVersion.asset_id == row.id)
        )
        # 资产没了，挂在它上面的 pending 申请单必须一起关掉：否则审批人点
        # 「通过 / 驳回」只会拿到 404「资产不存在」，申请单永远留在待办列表里
        # （待我审核、健康检查的陈旧告警都清不掉）。
        await self.session.execute(
            update(LibraryShareRequest)
            .where(
                LibraryShareRequest.asset_id == row.id,
                LibraryShareRequest.status == "pending",
            )
            .values(status="cancelled", reviewer_id=None, decided_at=datetime.now(tz=UTC))
        )
        await self.session.commit()

        if doomed:
            for key in doomed:
                with contextlib.suppress(Exception):
                    # 元数据已删，重试一次也没法把行找回来；留下日志由
                    # `library.gc`（M3）扫孤儿文件，不在这里卡住用户操作。
                    await self.storage.delete(key)
        await audit(
            "library.asset.purge",
            actor_id=actor.id if actor else None,
            target_type="library_asset",
            target_id=row.id,
            # 只有真删了原始字节才算「释放了空间」：封装没有自己的字节，
            # `size_bytes` 是成员之和，按它记会把没释放的量记成已释放。
            detail={
                "ownerId": row.owner_id,
                "bytesFreed": row.size_bytes if storage_key in doomed else 0,
            },
        )

    # ------------------------------------------------------------------ 草稿

    async def approve_draft(
        self, asset_id: str, payload: DraftDecisionIn, actor: CurrentUser
    ) -> LibraryAssetDetailOut:
        row = await self._draft_for_review(asset_id, actor, payload.expected_version)
        now = datetime.now(UTC)
        snapshot = self._snapshot(row)
        row.state = "active"
        row.visibility = "personal"
        row.reviewed_by = actor.id
        row.reviewed_at = now
        row.reject_reason = None
        row.state_version += 1
        self.session.add(
            LibraryDraftReview(
                asset_id=row.id,
                reviewer_id=actor.id,
                decision="approved",
                reason=payload.reason,
                snapshot=snapshot,
            )
        )
        await self.session.commit()
        await audit(
            "library.draft.approve",
            actor_id=actor.id,
            target_type="library_asset",
            target_id=row.id,
            detail={"selfReviewed": row.owner_id == actor.id},
        )
        from modules.library.service import LibraryService

        return await LibraryService(self.session, self.storage).detail(
            asset_id, actor, require_visible=False
        )

    async def reject_draft(
        self, asset_id: str, payload: DraftDecisionIn, actor: CurrentUser
    ) -> LibraryAssetDetailOut:
        row = await self._draft_for_review(asset_id, actor, payload.expected_version)
        snapshot = self._snapshot(row)
        row.reviewed_by = actor.id
        row.reviewed_at = datetime.now(UTC)
        row.reject_reason = payload.reason or None
        self.session.add(
            LibraryDraftReview(
                asset_id=row.id,
                reviewer_id=actor.id,
                decision="rejected",
                reason=payload.reason,
                snapshot=snapshot,
            )
        )
        # 驳回 = 进回收站（trash_reason=rejected），恢复时回到草稿而不是库内
        await self._to_trash(row, actor, reason="rejected", commit=False)
        await self.session.commit()
        await audit(
            "library.draft.reject",
            actor_id=actor.id,
            target_type="library_asset",
            target_id=row.id,
            detail={"reason": payload.reason, "selfReviewed": row.owner_id == actor.id},
        )
        from modules.library.service import LibraryService

        return await LibraryService(self.session, self.storage).detail(
            asset_id, actor, require_visible=False
        )

    async def _draft_for_review(
        self, asset_id: str, actor: CurrentUser, expected_version: int
    ) -> LibraryAsset:
        row = await access.load_asset(self.session, asset_id)
        if row.owner_id != actor.id and not actor.has("library:draft:review"):
            # 草稿本身对审核人是可见的（列表里能看到），缺的是动作权限 → 403
            raise DraftReviewForbidden()
        if row.state != "draft":
            raise DraftNotReviewable()
        if expected_version and row.state_version != expected_version:
            raise Conflict(
                "草稿已被处理，请刷新后重试",
                details={"stateVersion": row.state_version},
            )
        return row

    def _snapshot(self, row: LibraryAsset) -> dict[str, Any]:
        return {
            "name": row.name,
            "kind": row.kind,
            "origin": row.origin,
            "sizeBytes": row.size_bytes,
            "ownerId": row.owner_id,
            "sourceKey": row.source_key,
            "stateVersion": row.state_version,
        }

    # ------------------------------------------------------------------ 内部

    async def _pending_revoke(self, asset_id: str) -> LibraryShareRequest | None:
        return await self.session.scalar(
            select(LibraryShareRequest).where(
                LibraryShareRequest.asset_id == asset_id,
                LibraryShareRequest.action == "revoke",
                LibraryShareRequest.status == "pending",
            )
        )

    async def _to_trash(
        self,
        row: LibraryAsset,
        actor: CurrentUser,
        *,
        reason: str,
        commit: bool,
    ) -> None:
        """移入回收站。**在这里**强制不变式 A 与 B。

        不要求调用方先撤销共享：共享行留着而状态变成 trash 时，小组库会继续
        列出这条「已删除」的资产（列表只按 visibility 过滤），删除就失去了语义。
        """
        if row.visibility != "personal":
            await self.session.execute(
                delete(LibraryAssetGroup).where(LibraryAssetGroup.asset_id == row.id)
            )
            row.visibility = "personal"
        # 恢复的去向由「进来之前在哪儿」决定：草稿被驳回的应回到草稿箱，
        # 而不是一恢复就直接进库（那等于跳过审核）。
        from_state = row.state if row.state in ("draft", "active") else "active"
        row.state = "trash"
        row.trashed_from_state = from_state
        row.trash_reason = reason
        row.trash_expires_at = datetime.now(UTC) + timedelta(
            days=settings.library_trash_retention_days
        )
        row.deleted_at = datetime.now(UTC)
        row.deleted_by = actor.id
        row.state_version += 1
        if commit:
            await self.session.commit()
            await audit(
                "library.asset.delete",
                actor_id=actor.id,
                target_type="library_asset",
                target_id=row.id,
                detail={"reason": reason},
            )
