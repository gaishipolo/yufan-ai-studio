"""共享与撤销审批。

## 为什么撤销也要走审批

用户的原话是「取消共享需要经过审批才能取消，取消后才能删除」。风险方向决定了
这条规则不能因为「他有 `library:share:direct`」而放宽：**共享**是把自己的东西
给别人看（风险在自己可控范围），**撤销**是让别人突然拿不到正在用的文件
（风险落在别人身上）。所以：直接共享可以，直接收回不行。

## 谁能发起

- **共享**：读得到这条资产的人。组员看到一份该全员复用的素材、上传者又不在，
  如果只能等所有者来点，审批流就只服务「自己的东西」——那它把关的从来不是
  「范围该不该扩大」。范围由审批人把关，申请人只需要够得着这条资产。
- **撤销**：仍然只认所有者。见 `ShareNotOwned`。
- **直接生效**（`library:share:direct`）只对**自己的**资产成立，替别人扩大
  范围一律留 pending：审批记录是「谁把谁的私货推上了团队库」的唯一载体。

## 谁能审批（2026-11-10 拆分）

按**申请的目标范围**拆成三个码，共享与撤销共用同一份口径（风险方向不同，但
「谁来把关范围」是同一个问题）：

- `library:share:approve`：**全站审批**，旧码，等同同时持有下面两项。保留是为了
  不打断已经按旧码配好的角色；
- `library:share:approve:team`：`target=team` 的申请（团队没有「负责人」概念）；
- `library:share:approve:group`：`target=group` 且**该组是我所在小组**的申请。
  范围来自组织目录，不是 `library_group_leaders`——组长表按产品口径退回去只服务
  删除窄口，不再单独发一张审批票。

三处判定必须同口径：`decide()`、`list_requests(scope=pending)`、撤销申请的
「自己就是审批人时自审」。`_can_approve_target()` 是唯一入口，改口径只改这里。
路由层仍然只要求 `library:read`：审批资格要按申请单的目标与当前组成员关系算，
`require()` 表达不了。

## 唯一的一步例外

`owner=me ∧ 只共享给本组 ∧ 我是组长` 时，撤销与删除合并成一个动作，但仍然
先落一条 `status=approved` 的自审申请单再删共享行——「撤共享必须留一条审批记录」
这条不变式不破，只是审批人是他自己。实现在 `lifecycle.py`，因为它是删除动作的
一个分支（`DELETE /assets/{id}?revokeOwnGroupShare=true`），不是一个独立的申请入口。
**这条窄口刻意不跟着审批权限改**：组长身份在这里是「我正在收回自己给本组的
东西」的凭据，不是审批别人申请的资格。

## 并发

审批用 `UPDATE ... WHERE status='pending'` 抢占，`rowcount != 1` 就是别人先处理了。
不先查再改：两个人同时点「通过」时，先查再改的两条语句都会读到 pending，
于是「审批通过」被写两次、审计出现两条、资产版本被推两次。
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any, cast

from sqlalchemy import CursorResult, and_, delete, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from core.audit import record as audit
from core.context import CurrentUser
from core.exceptions import BadRequest, Forbidden, NotFound, StateChanged
from core.ports.org_directory import group_names
from modules.library import access
from modules.library.errors import (
    RequestNotPending,
    RevokeReasonRequired,
    ShareNotOwned,
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

#: 审批权限码。旧码是「全站」，两个新码按申请目标范围细分；三个码在
#: `ModuleSpec.permissions` 里声明，`test_permission_coverage` 会核对它们确实
#: 出现在下面的判定点里。
PERM_APPROVE_ALL = "library:share:approve"
PERM_APPROVE_TEAM = "library:share:approve:team"
PERM_APPROVE_GROUP = "library:share:approve:group"


class ShareService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ------------------------------------------------------------------ 发起

    async def request(
        self, payload: ShareRequestIn, actor: CurrentUser
    ) -> ShareRequestOut:
        asset = await access.load_visible(self.session, payload.asset_id, actor)
        if asset.state != "active":
            raise BadRequest("只有库内的资产可以共享或撤销共享")
        # 乐观锁：与 `decide()` 同一口径（比的是 `state_version`）。
        # 之前这条链上没人消费 `expected_version`：批量撤销共享把它一路传下来，
        # 只存进申请单就没人看了——基于旧页面发出的撤销申请会作用在用户没见过的
        # 资产状态上，而撤销恰恰是风险方向最重的一侧。
        if (
            payload.expected_version is not None
            and asset.state_version != payload.expected_version
        ):
            raise StateChanged(
                "资产已被修改，请刷新后重试",
                details={"stateVersion": asset.state_version},
            )
        if asset.owner_id != actor.id:
            await self._check_non_owner(asset, payload, actor)
        if payload.action is ShareAction.REVOKE:
            return await self._request_revoke(asset, payload, actor)
        return await self._request_share(asset, payload, actor)

    async def _check_non_owner(
        self, asset: LibraryAsset, payload: ShareRequestIn, actor: CurrentUser
    ) -> None:
        """别人（非所有者）发起的申请：只放行「共享」。

        走到这里说明 `load_visible` 已经通过（看不见的 id 一律 404，别把申请
        入口变成探库的探针），剩下的只有动作资格。
        """
        if payload.action is ShareAction.REVOKE:
            raise ShareNotOwned()

    async def _request_share(
        self, asset: LibraryAsset, payload: ShareRequestIn, actor: CurrentUser
    ) -> ShareRequestOut:
        target, group_id = await self._validate_share_target(asset, payload, actor)
        pending = await self._pending(asset.id, ShareAction.SHARE, target, group_id)
        if pending is not None:
            return await self._to_out(pending)

        # `direct` 只对自己的东西成立：替别人扩大可见范围必须留一条审批记录，
        # 否则管理员能悄悄把任何人的私货推上团队库（`_check_non_owner` 已拦住
        # 撤销那一半，这里拦「直接生效」那一半）。
        direct = actor.has("library:share:direct") and asset.owner_id == actor.id
        row = LibraryShareRequest(
            asset_id=asset.id,
            applicant_id=actor.id,
            action=ShareAction.SHARE.value,
            target=target.value,
            group_id=group_id,
            status="approved" if direct else "pending",
            reviewer_id=actor.id if direct else None,
            decided_at=datetime.now(UTC) if direct else None,
            # 快照：申请人看到的资产版本。前端没带时以服务端当时的版本为准，
            # 保证这一列永远是一个真实版本而不是 0（否则审批端拿不到比对基准）。
            # 显式判 `None` 而不是用 `or`：版本号没有「假值」语义，
            # `or` 会把 0 当成「没带」（P2-2）。
            expected_version=(
                payload.expected_version
                if payload.expected_version is not None
                else asset.state_version
            ),
        )
        self.session.add(row)
        if direct:
            await self._apply_share(asset, target, group_id)
            asset.state_version += 1
        try:
            await self.session.commit()
        except IntegrityError:
            # 并发提交同一张申请单：`uq_library_share_requests_pending` 兜底。
            # 直接冒出去会变成一条泛泛的 409（「冲突」），用户不知道是「已经
            # 提过了」还是「真出错了」。回滚后按**同一口径**再查一次，把那张
            # 已经存在的单子返回去 —— 与 `pending is not None` 那条早退路径
            # 落在同一个结果上。
            await self.session.rollback()
            raced = await self._pending(asset.id, ShareAction.SHARE, target, group_id)
            if raced is None:
                raise
            return await self._to_out(raced)
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

        # 自己就是「这个目标」的审核人时自审：否则会出现「自己申请、等自己批」
        # 的死锁。拆分后必须按目标判定——只有小组审批权的人撤销自己共享到团队
        # 的资产，不能因为「他有点审批权」就把整条审批流跳过去。
        self_review = await self._can_approve_target(actor, target.value, group_id)
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
            # 快照：同 `_request_share`，撤销申请也记下当时的资产版本。
            expected_version=(
                payload.expected_version
                if payload.expected_version is not None
                else asset.state_version
            ),
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
        if approve:
            # 申请单是**快照**：申请时成立，不代表审批时还成立。这里按申请的目标
            # 重跑一次目标有效性校验，不通过就 409 让审批人重新看一眼。
            #
            # **只在「通过」时校验**：驳回一条已经过时的申请单是正当操作（正是
            # 把它关掉的时候），被 409 拦住的话，待办列表里就会留下一条谁也
            # 处理不了的单子。
            await self._recheck_target(
                asset,
                ShareAction(row.action),
                ShareTarget(row.target),
                row.group_id or "",
            )
            # 乐观锁：审批人看到的资产版本必须仍然是当前版本。
            #
            # 判据是**客户端回传的版本**（审批人界面上那一版的 stateVersion），
            # 与 `LibraryService._check_version` 的口径一致：**不带（`None`）
            # 表示不校验**，0 不是合法版本（P2-2）。前端回传真实版本，保护才生效
            # （见 REVIEW_2026-11-23 严重#1）。
            #
            # **刻意不拿申请单快照兜底**：快照是「申请时」的版本，而通过共享
            # 本身就会推进 state_version——同一资产挂两条待审申请时（先小组、
            # 后团队），第二条的快照必然落后；用它兜底会把这条合法的升级路径
            # 变成 409，而不跟踪版本的客户端连「刷新后重试」都做不到（永远 409），
            # 待办列表里就会留下一条谁也处理不掉的单子。
            # 快照的用途是**提示**而不是拦截：出参里的 `requestStateVersion`
            # 让界面标出「资产已变更」，由审批人自己决定要不要通过。
            if (
                payload.expected_version is not None
                and asset.state_version != payload.expected_version
            ):
                raise StateChanged(
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
            # 锁住资产行：批准要写组行 + 写 visibility，两步之间不能被另一个
            # 审批插进来（否则会留下「visibility=group 但组行已空」的资产，
            # 既删不掉也撤销不了）。详见 `access.load_asset_for_update`。
            asset = await access.load_asset_for_update(self.session, row.asset_id)
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
        # 抢占：与 `decide` 同一条路。**读改写会输给并发审批** —— 审批已经把
        # 单子批了、而且共享**已经生效**（组行写进去了），撤回再把状态改成
        # cancelled，结果是「单子显示已撤回，共享却还在」，用户以为收回去了。
        claimed = cast(
            "CursorResult[Any]",
            await self.session.execute(
                update(LibraryShareRequest)
                .where(
                    LibraryShareRequest.id == request_id,
                    LibraryShareRequest.status == "pending",
                )
                .values(status="cancelled", decided_at=datetime.now(UTC))
                .execution_options(synchronize_session=False)
            ),
        )
        if claimed.rowcount != 1:
            raise RequestNotPending()
        await self.session.commit()
        await self.session.refresh(row)
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

        「待我审核」按审批权限点收窄，**不是**全站 pending：否则每个登录用户
        都能看到别人在共享什么，那本身就是一次泄露。这里的分支必须和
        `_can_approve_target()` 同口径，不然会出现「看得到却点不动」或反过来。
        """
        query = select(LibraryShareRequest)
        if scope == "pending":
            query = query.where(LibraryShareRequest.status == "pending")
            if not actor.has(PERM_APPROVE_ALL):
                clauses = []
                if actor.has(PERM_APPROVE_TEAM):
                    clauses.append(LibraryShareRequest.target == ShareTarget.TEAM.value)
                if actor.has(PERM_APPROVE_GROUP):
                    groups = await access.all_groups(self.session, actor)
                    if groups:
                        clauses.append(
                            and_(
                                LibraryShareRequest.target == ShareTarget.GROUP.value,
                                LibraryShareRequest.group_id.in_(groups),
                            )
                        )
                if not clauses:
                    return []
                query = query.where(or_(*clauses))
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

        2026-11-10 起这张表**不再发审批票**：审批资格按权限点与目标范围判定
        （见 `_can_approve_target`）。负责人现在只服务删除窄口（§3.4.1：
        收回自己给本组的共享并删除），以及 `/groups` 里的 `isLeader` 展示。
        仍然放在 v2 自己的表里，是为了不改身份模块刚上线、正在使用的组模型；
        只留这一个写入口，避免映射被多处写坏。
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

    async def _recheck_target(
        self,
        asset: LibraryAsset,
        action: ShareAction,
        target: ShareTarget,
        group_id: str,
    ) -> None:
        """审批通过前，按申请的目标重跑一次「这个目标现在还成不成立」。

        申请单是**快照**：申请时成立，不代表审批时还成立——资产被移进回收站、
        已经共享给团队、或者那条小组共享行已经没了。这时点「通过」会把回收站里
        的条目改成 team 可见（破坏不变式 A/B），或者让一条旧的组级申请把团队
        共享降级 / 抹掉，而那次降级**没有任何撤销申请单**。

        不满足就 409：审批人看到的是「这个目标已经变了」，而不是一条被写坏的
        可见性。前端固定传 `expectedVersion: 0`，版本闸门在这里不生效，所以
        这道复核是审批链上唯一的闸门。
        """
        if asset.state != "active":
            raise ShareTargetInvalid(
                "资产已不在库内，这次申请的目标不再成立",
                details={"state": asset.state},
            )
        groups = (await access.asset_group_ids(self.session, [asset.id])).get(asset.id, [])
        if action is ShareAction.SHARE:
            if target is ShareTarget.TEAM:
                if asset.visibility == "team":
                    raise ShareTargetInvalid(
                        "该资产已共享给团队，这次申请的目标不再成立",
                        details={"visibility": asset.visibility},
                    )
                return
            # 共享到小组：团队共享优先（团队 ⊇ 小组），且不能重复共享给同一个组
            if asset.visibility == "team":
                raise ShareTargetInvalid(
                    "资产已共享给团队，无需再共享到小组",
                    details={"visibility": asset.visibility},
                )
            if group_id and group_id in groups:
                raise ShareTargetInvalid(
                    "该资产已经共享给这个小组，这次申请的目标不再成立",
                    details={"groupId": group_id},
                )
            return
        # 撤销：目标必须还在，否则这次撤销没有对象——更不能顺手把「别的共享」
        # 抹掉（组行已经不在还去撤销，_apply_revoke 会把 visibility 写成
        # personal，团队共享就此无声消失）。
        if target is ShareTarget.TEAM:
            if asset.visibility != "team":
                raise ShareTargetInvalid(
                    "该资产不在团队库，这次撤销的目标不再成立",
                    details={"visibility": asset.visibility},
                )
            return
        if group_id not in groups:
            raise ShareTargetInvalid(
                "该资产没有共享给这个小组，这次撤销的目标不再成立",
                details={"groupId": group_id},
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

    async def _can_approve_target(
        self, actor: CurrentUser, target: str, group_id: str
    ) -> bool:
        """审批资格的**唯一**判据，按申请的目标范围分三支。

        `library_group_leaders` 刻意不在这里出现：负责人表按产品口径退回去只服务
        删除窄口（`lifecycle.py`），不再单独发一张审批票。改审批口径只改这个函数，
        `decide` / 待我审核 / 撤销自审三处都会跟着变。
        """
        if actor.has(PERM_APPROVE_ALL):
            return True
        if target == ShareTarget.TEAM.value:
            # 团队没有「负责人」这个概念，只能由持团队审批码的人把关
            return actor.has(PERM_APPROVE_TEAM)
        if not actor.has(PERM_APPROVE_GROUP) or not group_id:
            return False
        return group_id in await access.all_groups(self.session, actor)

    async def _can_decide(self, row: LibraryShareRequest, actor: CurrentUser) -> bool:
        return await self._can_approve_target(actor, row.target, row.group_id or "")

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
                # 资产当前版本：审批人回传它做乐观锁。
                state_version=assets[row.asset_id].state_version if row.asset_id in assets else 0,
                # 申请单入库时的快照版本：用来提示「该申请基于旧版本」。
                request_state_version=row.expected_version,
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
