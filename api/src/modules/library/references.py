"""`library-references` 注入口的 v2 实现：彻底删除前问「还有谁在用」。

## 为什么必须 fail-closed

`ask_library_references()` 在来源不齐时抛 503，调用方（`purge`）必须拒绝删除。
这里如果退化成「查不到 = 没人引用」，一次装配故障就会让「不可逆删除」静默放行，
事后只能靠备份找回——而回收站的 30 天保留期恰恰是为了避免走到这一步。

## 一个来源一个类，不是一个类查两张表

`LibraryReferenceSource` 是「模块把 `library_assets.id` 写进了哪个持久化位置」的
声明，`location` 指向**一个**位置。所以下面按表拆 checker：`library_download_jobs`
是独立的写入点，合成一个类会让「新增第二张表」
只能改现有类，静态守卫看不出扩展发生过。聚合由
`core/ports/library_references.py::ask_library_references()` 负责。

## 查什么

- `library_download_jobs`：有人已经把它打进下载包（打包任务还没过期）。

不查的：`library_asset_groups`（共享关系，删除矩阵已经先拦）、
`library_asset_tags`（标签不是引用）、`library_asset_generation.reference_asset_ids`
（那里指的是**旧 `assets.id`**，由 `modules/asset` 的静态来源守卫负责，两套 id 不能混）。

跨模块来源（生图参考图）由各自的 `ModuleSpec`
声明，不在这个文件里：这里只回答「v2 自己的表里谁在引用」。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from sqlalchemy import String, cast, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.exceptions import Unavailable
from modules.library.models import LibraryDownloadJob

__all__ = ["DownloadJobReferences"]

_MAX_IDS = 200


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _wanted(asset_ids: Sequence[str]) -> list[str]:
    """去重后的 id 列表。**超上限就报错，不静默截断。**

    静默截断在这里是危险的：这个函数的返回值直接决定「这条资产能不能
    彻底删除」。丢掉一部分 id = 少查一批引用 = 「查不到 = 没人引用」
    ——而本文件开头整段都在说这个等式不成立。不可逆删除一旦因此放行，
    只能靠备份找回。

    抛 `Unavailable`（503）而不是 `BadRequest`（400）：这是**服务端调用方式**
    的问题，不是用户的输入问题；而且删除路径必须 fail-closed——宁可这次
    不删，也不要在可能漏查的前提下删掉。
    """
    wanted = [asset_id for asset_id in dict.fromkeys(asset_ids) if asset_id]
    if len(wanted) > _MAX_IDS:
        raise Unavailable(
            f"一次最多检查 {_MAX_IDS} 个资产引用，收到 {len(wanted)} 个："
            "拒绝在可能漏查的前提下做删除判断，请分批处理"
        )
    return wanted


class DownloadJobReferences:
    """`library_download_jobs.asset_ids`：已经打进下载包的资产。"""

    async def references(
        self, session: AsyncSession, asset_ids: Sequence[str]
    ) -> Mapping[str, Sequence[str]]:
        wanted = _wanted(asset_ids)
        if not wanted:
            return {}
        found: dict[str, list[str]] = {}
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
