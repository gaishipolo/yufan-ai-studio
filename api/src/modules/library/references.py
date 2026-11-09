"""`library-references` 注入口的 v2 实现：彻底删除前问「还有谁在用」。

## 为什么必须 fail-closed

`get_library_references()` 在实现未注册时抛 503，调用方（`purge`）必须拒绝删除。
这里如果退化成「查不到 = 没人引用」，一次装配故障就会让「不可逆删除」静默放行，
事后只能靠备份找回——而回收站的 30 天保留期恰恰是为了避免走到这一步。

## 查什么

- `library_pack_items`：资产被封装收录（封装是用户可见的作品，删成员等于毁封面）；
- `library_download_jobs`：有人已经把它打进下载包（打包任务还没过期）。

不查的：`library_asset_groups`（共享关系，删除矩阵已经先拦）、
`library_asset_tags`（标签不是引用）、`library_asset_generation.reference_asset_ids`
（那里指的是**旧 `assets.id`**，由 `modules/asset` 的静态来源守卫负责，两套 id 不能混）。

跨模块来源（任务中心、模板库将来指向 v2 资产）由各自的 `ModuleSpec` 声明，
不在这个类里：这里只回答「v2 自己的表里谁在引用」。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from sqlalchemy import String, cast, select
from sqlalchemy.ext.asyncio import AsyncSession

from modules.library.models import LibraryAsset, LibraryDownloadJob, LibraryPackItem

__all__ = ["SqlLibraryReferences"]

_MAX_IDS = 200


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


class SqlLibraryReferences:
    async def references(
        self, session: AsyncSession, asset_ids: Sequence[str]
    ) -> Mapping[str, Sequence[str]]:
        wanted = [asset_id for asset_id in dict.fromkeys(asset_ids) if asset_id][:_MAX_IDS]
        if not wanted:
            return {}
        found: dict[str, list[str]] = {}

        # 只把**已解散（在回收站）**的封装排除掉。解散后 `library_pack_items`
        # 的行仍然留着（恢复时要原样回来），但它已经不是「用户还在用这个封装」——
        # 把解散的也算成引用，成员就永远删不掉：提示「封装 X 的成员」，而 X 正是
        # 用户刚解散的那一个，界面上已经找不到它。恢复封装后 state 回到 active，
        # 引用随之重新生效。
        #
        # 判「不在回收站」而不是「在库」：指向不存在封装的孤儿行也要算引用。
        # 这是不可逆删除的最后一道闸，查不清楚的一律当「有人在用」。
        trashed_packs = select(LibraryAsset.id).where(
            LibraryAsset.kind == "pack",
            LibraryAsset.state == "trash",
        )
        pack_rows = await session.execute(
            select(LibraryPackItem.pack_id, LibraryPackItem.asset_id).where(
                LibraryPackItem.asset_id.in_(wanted),
                LibraryPackItem.pack_id.not_in(trashed_packs),
            )
        )
        for pack_id, asset_id in pack_rows.all():
            found.setdefault(asset_id, []).append(f"封装 {pack_id} 的成员")

        # JSON 列没法跨方言按元素查：先按子串粗筛（只会多捞），再精确比对
        for asset_id in wanted:
            job_rows = await session.execute(
                select(LibraryDownloadJob.id, LibraryDownloadJob.asset_ids).where(
                    LibraryDownloadJob.status.in_(("queued", "running", "succeeded")),
                    cast(LibraryDownloadJob.asset_ids, String).like(
                        f"%{_escape_like(asset_id)}%", escape="\\"
                    ),
                )
            )
            for job_id, asset_ids_in_job in job_rows.all():
                if asset_id in (asset_ids_in_job or []):
                    found.setdefault(asset_id, []).append(f"下载任务 {job_id}")
        return found
