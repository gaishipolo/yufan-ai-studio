"""共享与撤销审批。

## 为什么撤销也要走审批

用户的原话是「取消共享需要经过审批才能取消，取消后才能删除」。风险方向决定了
这条规则不能因为「他有 `library:share:direct`」而放宽：**共享**是把自己的东西
给别人看（风险在自己可控范围），**撤销**是让别人突然拿不到正在用的文件
（风险落在别人身上）。所以：直接共享可以，直接收回不行。

## 唯一的一步例外

`owner=me ∧ 只共享给本组 ∧ 我是组长` 时，撤销与删除合并成一个动作，但仍然
先落一条 `status=approved` 的自审申请单再删共享行——「撤共享必须留一条审批记录」
这条不变式不破，只是审批人是他自己。实现在 `lifecycle.py`，因为它是删除动作的
一个分支（`DELETE /assets/{id}?revokeOwnGroupShare=true`），不是一个独立的申请入口。

## 并发

审批用 `UPDATE ... WHERE status='pending'` 抢占，`rowcount != 1` 就是别人先处理了。
不先查再改：两个人同时点「通过」时，先查再改的两条语句都会读到 pending，
于是「审批通过」被写两次、审计出现两条、资产版本被推两次。
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any, cast

from sqlalchemy import CursorResult, delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from core.audit import record as audit
from core.context import CurrentUser
from core.exceptions import BadRequest, Conflict, Forbidden, NotFound
from core.ports.org_directory import group_names
from modules.library import access
from modules.library.errors import (
    RequestNotPending,
    RevokeReasonRequired,
    ShareTargetInvalid,
)
from modules.library.models import (
    LibraryAsset,
    LibraryAssetGroup,
    LibraryGroupLeader,
    LibraryShareRequest,
)
from modules.library.schema import (
    LibraryGroupOut,
    ShareAction,
    ShareDecisionIn,
    ShareRequestIn,
    ShareRequestOut,
    ShareTarget,
)

__all__ = ["ShareService"]

#: 申请单列表一次最多返回的条数。审批是「处理完就走」的界面，不做无限滚动。
PAGE_LIMIT = 100


class ShareService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ------------------------------------------------------------------ 发起

    async def request(
        self, payload: ShareRequestIn, actor: CurrentUser
    ) -> ShareRequestOut:
        asset = await access.load_asset(self.session, payload.asset_id)
        if asset.owner_id != actor.id:
            # 只有所有者能处置自己的资产。管理员想替别人共享，走的是
            # `library:delete:any` 的删除通道，不是这里——「替别人共享」在语义上
            # 等于替别人承担可见性后果，没有这个需求就不开这个口子。
            raise NotFound("资产不存在")
        if asset.state != "active":
            raise BadRequest("只有库内的资产可以共享或撤销共享")
        if payload.action is ShareAction.REVOKE:
            return await self._request_revoke(asset, payload, actor)
        return await self._request_share(asset, payload, actor)

    async def _request_share(
        self, asset: LibraryAsset, payload: ShareRequestIn, actor: CurrentUser
    ) -> ShareRequestOut:
        target, group_id = await self._validate_share_target(asset, payload, actor)
        pending = await self._pending(asset.id, ShareAction.SHARE, target, group_id)
        if pending is not None:
            return await self._to_out(pending)

        direct = actor.has("library:share:direct")
        row = LibraryShareRequest(
            asset_id=asset.id,
            applicant_id=actor.id,
            action=ShareAction.SHARE.value,
            target=target.value,
            group_id=group_id,
            status="approved" if direct else "pending",
            reviewer_id=actor.id if direct else None,
            decided_at=datetime.now(UTC) if direct else None,
            expected_version=payload.expected_version,
        )
        self.session.add(row)
        if direct:
            await self._apply_share(asset, target, group_id)
            asset.state_version += 1
        await self.session.commit()
        await audit(
            "library.share.direct" if direct else "library.share.request",
            actor_id=actor.id,
            target_type="library_asset",
            target_id=asset.id,
            detail={"target": target.value, "groupId": group_id, "requestId": row.id},
        )
        return await self._to_out(row)

    async def _request_revoke(
        self, asset: LibraryAsset, payload: ShareRequestIn, actor: CurrentUser
    ) -> ShareRequestOut:
        reason = payload.reason.strip()
        if not reason:
            raise RevokeReasonRequired()
        target, group_id = await self._validate_revoke_target(asset, payload)
        pending = await self._pending(asset.id, ShareAction.REVOKE, target, group_id)
        if pending is not None:
            return await self._to_out(pending)

        # 自己就是审核人时自审：否则会出现「自己申请、等自己批」的死锁。
        self_review = actor.has("library:share:approve")
        row = LibraryShareRequest(
            asset_id=asset.id,
            applicant_id=actor.id,
            action=ShareAction.REVOKE.value,
            target=target.value,
            group_id=group_id,
            status="approved" if self_review else "pending",
            reviewer_id=actor.id if self_review else None,
            note="self_approved" if self_review else "",
            reason=reason,
            decided_at=datetime.now(UTC) if self_review else None,
            expected_version=payload.expected_version,
        )
        self.session.add(row)
        if self_review:
            await self._apply_revoke(asset, target, group_id)
            asset.state_version += 1
        await self.session.commit()
        await audit(
            "library.share.revoke.self" if self_review else "library.share.revoke.request",
            actor_id=actor.id,
            target_type="library_asset",
            target_id=asset.id,
            detail={"target": target.value, "groupId": group_id, "reason": reason},
        )
        return await self._to_out(row)

    # ------------------------------------------------------------------ 审批

    async def decide(
        self, request_id: str, payload: ShareDecisionIn, actor: CurrentUser, *, approve: bool
    ) -> ShareRequestOut:
        row = await self.session.get(LibraryShareRequest, request_id)
        if row is None:
            raise NotFound("申请不存在")
        if row.status != "pending":
            raise RequestNotPending()
        if not await self._can_decide(row, actor):
            raise Forbidden("没有审批该申请的权限")

        asset = await access.load_asset(self.session, row.asset_id)
        if payload.expected_version and asset.state_version != payload.expected_version:
            raise Conflict(
                "资产已被修改，请刷新后重试",
                details={"stateVersion": asset.state_version},
            )

        # 抢占：只有真正的第一个请求能把 status 从 pending 改掉
        claimed = cast(
            "CursorResult[Any]",
            await self.session.execute(
                update(LibraryShareRequest)
                .where(
                    LibraryShareRequest.id == request_id,
                    LibraryShareRequest.status == "pending",
                )
                .values(
                    status="approved" if approve else "rejected",
                    reviewer_id=actor.id,
                    note=payload.note,
                    decided_at=datetime.now(UTC),
                )
                .execution_options(synchronize_session=False)
            ),
        )
        if claimed.rowcount != 1:
            raise RequestNotPending()

        action = ShareAction(row.action)
        target = ShareTarget(row.target)
        if approve:
            if action is ShareAction.SHARE:
                await self._apply_share(asset, target, row.group_id)
            else:
                await self._apply_revoke(asset, target, row.group_id)
            asset.state_version += 1
        await self.session.commit()
        await audit(
            f"library.share.{'approve' if approve else 'reject'}",
            actor_id=actor.id,
            target_type="library_share_request",
            target_id=request_id,
            detail={
                "assetId": asset.id,
                "action": action.value,
                "target": target.value,
                "groupId": row.group_id,
                "note": payload.note,
            },
        )
        refreshed = await self.session.get(LibraryShareRequest, request_id)
        assert refreshed is not None  # 刚用 UPDATE 命中过，不会消失
        # `synchronize_session=False` + `expire_on_commit=False`：内存里那个对象
        # 还停在 pending，不 refresh 会把「刚批完的单」原样返回给前端。
        await self.session.refresh(refreshed)
        return await self._to_out(refreshed)

    async def cancel(self, request_id: str, actor: CurrentUser) -> ShareRequestOut:
        row = await self.session.get(LibraryShareRequest, request_id)
        if row is None:
            raise NotFound("申请不存在")
        if row.applicant_id != actor.id:
            # 404 而不是 403：别人的申请单对我不存在，避免「这个 id 有效」外泄
            raise NotFound("申请不存在")
        if row.status != "pending":
            raise RequestNotPending()
        row.status = "cancelled"
        row.decided_at = datetime.now(UTC)
        await self.session.commit()
        await audit(
            "library.share.cancel",
            actor_id=actor.id,
            target_type="library_share_request",
            target_id=request_id,
            detail={"assetId": row.asset_id},
        )
        return await self._to_out(row)

    async def list_requests(
        self, actor: CurrentUser, *, scope: str = "mine"
    ) -> list[ShareRequestOut]:
        """`scope=mine` 我提交的；`scope=pending` 待我审核。

        「待我审核」按权限点与组长身份收窄，**不是**全站 pending：否则每个登录
        用户都能看到别人在共享什么，那本身就是一次泄露。
        """
        query = select(LibraryShareRequest)
        if scope == "pending":
            query = query.where(LibraryShareRequest.status == "pending")
            if not actor.has("library:share:approve"):
                leaders = await access.leader_group_ids(self.session, actor.id)
                if not leaders:
                    return []
                query = query.where(
                    LibraryShareRequest.target == ShareTarget.GROUP.value,
                    LibraryShareRequest.group_id.in_(leaders),
                )
        else:
            query = query.where(LibraryShareRequest.applicant_id == actor.id)
        rows = (
            await self.session.scalars(
                query.order_by(LibraryShareRequest.created_at.desc()).limit(PAGE_LIMIT)
            )
        ).all()
        return await self._to_out_many(rows)

    # ------------------------------------------------------------------ 小组

    async def my_groups(self, actor: CurrentUser) -> list[LibraryGroupOut]:
        ids = await access.all_groups(self.session, actor)
        names = await group_names(self.session, ids)
        leaders = set(await access.leader_group_ids(self.session, actor.id))
        return [
            LibraryGroupOut(
                id=group_id,
                name=names.get(group_id, ""),
                is_leader=group_id in leaders,
            )
            for group_id in ids
        ]

    async def set_leaders(
        self, group_id: str, user_ids: Sequence[str], actor: CurrentUser
    ) -> list[str]:
        """整体替换某组的负责人（组长）。

        组长是「谁能审批本组共享」的唯一来源，存在 v2 自己的表里：身份模块的组
        模型刚上线、正在被真实用户使用，为一件资产库的事去改它，会把两个变更的
        风险绑在一起。代价是这里要自己维护一份映射——所以只留一个入口。
        """
        names = await group_names(self.session, [group_id])
        if not names:
            raise NotFound("小组不存在")
        await self.session.execute(
            delete(LibraryGroupLeader).where(LibraryGroupLeader.group_id == group_id)
        )
        kept = [user_id for user_id in dict.fromkeys(user_ids) if user_id]
        for user_id in kept:
            self.session.add(LibraryGroupLeader(group_id=group_id, user_id=user_id))
        await self.session.commit()
        await audit(
            "library.group.leaders.set",
            actor_id=actor.id,
            target_type="group",
            target_id=group_id,
            detail={"userIds": kept},
        )
        return kept

    # ------------------------------------------------------------------ 内部

    async def _validate_share_target(
        self, asset: LibraryAsset, payload: ShareRequestIn, actor: CurrentUser
    ) -> tuple[ShareTarget, str]:
        if payload.target is ShareTarget.TEAM:
            if asset.visibility == "team":
                raise ShareTargetInvalid("该资产已共享给团队")
            return ShareTarget.TEAM, ""
        if not payload.group_id:
            raise BadRequest("共享到小组时 groupId 必填")
        my_groups = await access.all_groups(self.session, actor)
        if payload.group_id not in my_groups:
            # 允许共享到自己不在的组，等于让一个人把素材塞进别人的库
            raise ShareTargetInvalid("只能共享给自己所在的小组")
        if asset.visibility == "team":
            raise ShareTargetInvalid("资产已共享给团队，无需再共享到小组")
        rows = await access.asset_group_ids(self.session, [asset.id])
        if payload.group_id in rows.get(asset.id, []):
            raise ShareTargetInvalid("该资产已经共享给这个小组")
        return ShareTarget.GROUP, payload.group_id

    async def _validate_revoke_target(
        self, asset: LibraryAsset, payload: ShareRequestIn
    ) -> tuple[ShareTarget, str]:
        if payload.target is ShareTarget.TEAM:
            if asset.visibility != "team":
                raise ShareTargetInvalid("该资产不在团队库")
            return ShareTarget.TEAM, ""
        if not payload.group_id:
            raise BadRequest("撤销小组共享时 groupId 必填")
        rows = await access.asset_group_ids(self.session, [asset.id])
        if payload.group_id not in rows.get(asset.id, []):
            raise ShareTargetInvalid("该资产没有共享给这个小组")
        return ShareTarget.GROUP, payload.group_id

    async def _pending(
        self,
        asset_id: str,
        action: ShareAction,
        target: ShareTarget,
        group_id: str,
    ) -> LibraryShareRequest | None:
        return await self.session.scalar(
            select(LibraryShareRequest).where(
                LibraryShareRequest.asset_id == asset_id,
                LibraryShareRequest.action == action.value,
                LibraryShareRequest.target == target.value,
                LibraryShareRequest.group_id == group_id,
                LibraryShareRequest.status == "pending",
            )
        )

    async def _apply_share(
        self, asset: LibraryAsset, target: ShareTarget, group_id: str
    ) -> None:
        if target is ShareTarget.TEAM:
            # 团队 ⊇ 小组：共享到团队时清掉组行，否则「撤销某组」会在
            # 团队共享上留下说不清的状态（设计 §3.1 的口径 2）
            await self.session.execute(
                delete(LibraryAssetGroup).where(LibraryAssetGroup.asset_id == asset.id)
            )
            asset.visibility = "team"
            return
        exists = await self.session.scalar(
            select(LibraryAssetGroup.id).where(
                LibraryAssetGroup.asset_id == asset.id,
                LibraryAssetGroup.group_id == group_id,
            )
        )
        if exists is None:
            self.session.add(LibraryAssetGroup(asset_id=asset.id, group_id=group_id))
        asset.visibility = "group"

    async def _apply_revoke(
        self, asset: LibraryAsset, target: ShareTarget, group_id: str
    ) -> None:
        if target is ShareTarget.TEAM:
            await self.session.execute(
                delete(LibraryAssetGroup).where(LibraryAssetGroup.asset_id == asset.id)
            )
            asset.visibility = "personal"
            return
        await self.session.execute(
            delete(LibraryAssetGroup).where(
                LibraryAssetGroup.asset_id == asset.id,
                LibraryAssetGroup.group_id == group_id,
            )
        )
        remaining = await self.session.scalar(
            select(LibraryAssetGroup.id).where(LibraryAssetGroup.asset_id == asset.id)
        )
        # 撤到最后一个组才回 personal；还有别的组时保持 group（部分撤销）
        asset.visibility = "group" if remaining is not None else "personal"

    async def _can_decide(self, row: LibraryShareRequest, actor: CurrentUser) -> bool:
        if actor.has("library:share:approve"):
            return True
        if row.target != ShareTarget.GROUP.value or not row.group_id:
            # 团队共享没有「团队负责人」这个概念，只能由持权限点的人审
            return False
        return await access.is_group_leader(self.session, actor.id, row.group_id)

    async def _to_out(self, row: LibraryShareRequest) -> ShareRequestOut:
        return (await self._to_out_many([row]))[0]

    async def _to_out_many(
        self, rows: Sequence[LibraryShareRequest]
    ) -> list[ShareRequestOut]:
        if not rows:
            return []
        asset_ids = [row.asset_id for row in rows]
        assets = {
            asset.id: asset
            for asset in (
                await self.session.scalars(
                    select(LibraryAsset).where(LibraryAsset.id.in_(asset_ids))
                )
            ).all()
        }
        applicant_ids = [row.applicant_id for row in rows]
        names = await access.owner_names(self.session, applicant_ids)
        group_ids = [row.group_id for row in rows if row.group_id]
        groups = await group_names(self.session, group_ids)
        return [
            ShareRequestOut(
                id=row.id,
                asset_id=row.asset_id,
                asset_name=assets[row.asset_id].name if row.asset_id in assets else "",
                applicant_id=row.applicant_id,
                applicant_name=names.get(row.applicant_id),
                action=ShareAction(row.action),
                target=ShareTarget(row.target),
                group_id=row.group_id,
                group_name=groups.get(row.group_id) if row.group_id else None,
                status=row.status,
                reviewer_id=row.reviewer_id,
                note=row.note,
                reason=row.reason,
                decided_at=row.decided_at,
                created_at=row.created_at,
            )
            for row in rows
        ]
