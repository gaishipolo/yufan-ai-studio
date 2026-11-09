"""批量写：**逐条判定、逐条提交，不整体回滚**。

设计 §3.4 第 3 条要求「一批 10 条里 3 条已共享 → 返回 7 成功、3 条明细」。
整体回滚会让人以为「一条都没删」，然后重试一遍又全失败。

做法是把每条丢进单条接口的同一个服务方法，捕获 `AppError` 记成明细：

- 权限按 **action** 判一次（不是每条判一次）：同一批里不会有「这条我能删、
  那条不能」，差异来自资产状态，不来自身份；
- 每条成功后立刻提交。失败时 `rollback()` 清掉该条的半截写入，**不影响**
  已经提交的条目——这是「逐条提交」的全部意义；
- `purge`（彻底删除）不在批量动作里：不可逆操作一次点错十条就没救了。
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from core.context import CurrentUser
from core.exceptions import AppError, BadRequest, Forbidden
from infra.storage.base import ObjectStorage
from modules.library.lifecycle import LifecycleService
from modules.library.schema import (
    AssetPatchIn,
    BulkAction,
    BulkIn,
    BulkItemResult,
    BulkOut,
    DraftDecisionIn,
    ShareRequestIn,
)
from modules.library.service import LibraryService
from modules.library.sharing import ShareService
from modules.library.tags import add_asset_tags, remove_asset_tags

__all__ = ["BulkService"]

#: 每个动作需要的权限点。批量入口本身只要求「至少有一个」，具体动作在这里判。
PERMISSION_BY_ACTION = {
    BulkAction.ADD_TAG: "library:update",
    BulkAction.REMOVE_TAG: "library:update",
    BulkAction.RENAME: "library:update",
    BulkAction.DELETE: "library:delete",
    BulkAction.RESTORE: "library:delete",
    BulkAction.APPROVE_DRAFT: "library:create",
    BulkAction.REJECT_DRAFT: "library:create",
    BulkAction.REQUEST_REVOKE: "library:share",
    #: 封装是「新建一个东西」而不是改这批，所以是 `create`
    BulkAction.PACK: "library:create",
}


class BulkService:
    def __init__(self, session: AsyncSession, storage: ObjectStorage) -> None:
        self.session = session
        self.storage = storage

    async def run(self, payload: BulkIn, actor: CurrentUser) -> BulkOut:
        permission = PERMISSION_BY_ACTION.get(payload.action)
        if permission is None:  # pragma: no cover - 枚举穷尽，防御新增动作漏登记
            raise BadRequest(f"不支持的批量动作：{payload.action}")
        if not actor.has(permission):
            raise Forbidden(f"没有执行 {payload.action} 的权限")

        if payload.action is BulkAction.PACK:
            return await self._pack(payload, actor)

        results: list[BulkItemResult] = []
        for asset_id in dict.fromkeys(payload.ids):
            try:
                await self._apply(asset_id, payload, actor)
                results.append(BulkItemResult(id=asset_id, ok=True))
            except AppError as exc:
                # 该条可能已经写了半截（比如状态改完、提交前撞版本），清掉再记明细
                await self.session.rollback()
                results.append(
                    BulkItemResult(
                        id=asset_id,
                        ok=False,
                        error_code=exc.code,
                        message=exc.message,
                        details=exc.details if isinstance(exc.details, dict) else None,
                    )
                )
        succeeded = sum(1 for item in results if item.ok)
        return BulkOut(
            succeeded=succeeded, failed=len(results) - succeeded, results=results
        )

    async def _pack(self, payload: BulkIn, actor: CurrentUser) -> BulkOut:
        """把勾选的这批直接封装。

        **不走逐条循环**：`pack` 是「用这批建一个东西」，逐条跑会建出 N 个
        单成员封装。所以它在 `run()` 里就分流，返回一条结果，`id` 是新封装的 id；
        成员里只要有一个不可用，整次封装失败（`PackItemInvalid` 带明细）。
        """
        from modules.library.packs import PackService
        from modules.library.schema import PackIn

        data = payload.payload or {}
        name = str(data.get("name") or "").strip()
        if not name:
            raise BadRequest("pack 需要 payload.name")
        pack = await PackService(self.session, self.storage).create(
            PackIn(
                name=name[:200],
                asset_ids=list(dict.fromkeys(payload.ids)),
                cover_asset_id=str(data.get("coverAssetId") or ""),
                tag_ids=self._str_list(data, "tagIds"),
            ),
            actor,
        )
        return BulkOut(
            succeeded=1,
            failed=0,
            results=[
                BulkItemResult(
                    id=pack.id,
                    ok=True,
                    message=f"已封装 {len(pack.pack_items)} 项",
                )
            ],
        )

    async def _apply(self, asset_id: str, payload: BulkIn, actor: CurrentUser) -> None:
        version = payload.expected_versions.get(asset_id, 0)
        data = payload.payload or {}
        action = payload.action

        if action is BulkAction.ADD_TAG:
            tag_ids = self._str_list(data, "tagIds")
            if not tag_ids:
                raise BadRequest("addTag 需要 payload.tagIds")
            await add_asset_tags(self.session, asset_id, tag_ids)
            await self.session.commit()
            return
        if action is BulkAction.REMOVE_TAG:
            tag_ids = self._str_list(data, "tagIds")
            if not tag_ids:
                raise BadRequest("removeTag 需要 payload.tagIds")
            await remove_asset_tags(self.session, asset_id, tag_ids)
            await self.session.commit()
            return
        if action is BulkAction.RENAME:
            name = str(data.get("name") or "").strip()
            if not name:
                raise BadRequest("rename 需要 payload.name")
            await LibraryService(self.session, self.storage).patch(
                asset_id,
                AssetPatchIn(name=name[:200], expected_version=version),
                actor,
            )
            return
        if action is BulkAction.DELETE:
            await LifecycleService(self.session, self.storage).delete(
                asset_id,
                actor,
                revoke_own_group_share=bool(data.get("revokeOwnGroupShare")),
                reason=str(data.get("reason") or ""),
            )
            return
        if action is BulkAction.RESTORE:
            await LifecycleService(self.session, self.storage).restore(asset_id, actor)
            return
        if action is BulkAction.APPROVE_DRAFT:
            await LifecycleService(self.session, self.storage).approve_draft(
                asset_id,
                DraftDecisionIn(reason=str(data.get("reason") or ""), expected_version=version),
                actor,
            )
            return
        if action is BulkAction.REJECT_DRAFT:
            await LifecycleService(self.session, self.storage).reject_draft(
                asset_id,
                DraftDecisionIn(reason=str(data.get("reason") or ""), expected_version=version),
                actor,
            )
            return
        if action is BulkAction.REQUEST_REVOKE:
            await ShareService(self.session).request(
                ShareRequestIn(
                    asset_id=asset_id,
                    action="revoke",
                    target=str(data.get("target") or "group"),
                    group_id=str(data.get("groupId") or ""),
                    reason=str(data.get("reason") or ""),
                    expected_version=version,
                ),
                actor,
            )
            return
        raise BadRequest(f"不支持的批量动作：{action}")

    def _str_list(self, data: dict, key: str) -> list[str]:
        raw = data.get(key) or []
        if not isinstance(raw, list):
            raise BadRequest(f"payload.{key} 必须是数组")
        return [str(item) for item in raw if item]
