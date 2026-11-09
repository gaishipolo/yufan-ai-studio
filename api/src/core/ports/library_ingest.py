"""生成产物入库注入口。

image / template / task 三个模块在生成完成后要把结果写进资产库 v2，但它们
不能 import `modules.library`（业务模块之间不许互相 import）。所以由资产库
模块实现这个协议，生成侧只认 `ingest_generated()`。

## 语义边界（写在协议上，不是注释）

- **落点固定**：`state=draft`、`visibility=personal`。生成结果先进草稿箱，
  由本人（或持 `library:draft:review` 的人）确认后才进个人库。协议不给调用方
  传 `state` / `visibility` 的机会——想直接入库的调用方应该走用户操作，不是这里。
- **`source_key` 是幂等键**：生成侧的轮询可能重复回调，同一个 `source_key`
  第二次调用必须返回同一个 `asset_id` 且 `created=False`，不能产生第二行。
- **字节必须物化**：接受 `storage_key`（已在对象存储里）或 `source_url`
  （服务端下载后再存），不接受「只存一个外部 URL」——外部链接会过期，
  详情页与下载都会裂。

## 未注册时的行为

fail-closed：抛 `Unavailable`(503)。生成结果悄悄不落库是最难发现的一类问题
（用户看到「生成成功」，去资产库找不到），宁可让生成侧显式失败一次。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from sqlalchemy.ext.asyncio import AsyncSession

from core.exceptions import Unavailable
from core.ports.host import Port
from core.ports.library_lookup import LibraryAssetKind

__all__ = [
    "IngestRequest",
    "IngestResult",
    "LibraryIngest",
    "get_library_ingest",
    "has_library_ingest",
    "register_library_ingest",
    "reset_library_ingest",
]


@dataclass(frozen=True, slots=True)
class IngestRequest:
    """一次生成结果入库请求。

    `generation` 是**冻结快照**，不是联表查询：生成参数散落在
    `image_jobs` / `video_tasks` / `replacement_runs` 三张表里，且历史数据
    没有完整记录。入库时能拿到什么就存什么，详情页对缺失字段显示「未记录」。
    """

    source_module: str
    """`image` / `template` / `task`，用于追溯与幂等键前缀。"""

    source_task_id: str
    """业务侧任务 / 作业 / 运行 id。"""

    source_key: str
    """幂等键 = `sourceModule:sourceTaskId[:index]`，全局唯一。"""

    kind: LibraryAssetKind
    """生成结果的类型，只允许 `video` / `image`（由实现方校验）。"""

    #: 归属人。生成任务的 owner 在业务表里（`image_jobs.owner_id` 等），
    #: 而 worker 里没有请求上下文，所以调用方**应该显式传**；留空时退回
    #: `get_current_user()`（请求内调用），两者都没有就拒绝入库——
    #: 归属是这个模块的权限基础，不能猜一个默认账号。
    owner_id: str = ""

    name: str = ""
    storage_key: str = ""
    source_url: str = ""
    mime_type: str = ""
    size_bytes: int = 0
    generation: Mapping[str, object] = field(default_factory=dict)
    tag_ids: Sequence[str] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class IngestResult:
    """入库结果。`created=False` 表示命中了 `source_key` 幂等，没有新建行。"""

    asset_id: str
    created: bool


@runtime_checkable
class LibraryIngest(Protocol):
    """把一次生成结果写进资产库 v2 的草稿箱。"""

    async def ingest_generated(
        self, session: AsyncSession, request: IngestRequest
    ) -> IngestResult:
        """幂等写入；实现方自己 commit（与调用方同一次事务）。"""
        ...


_ingest: Port[LibraryIngest] = Port("library-ingest", multi=False)


def register_library_ingest(port: LibraryIngest) -> LibraryIngest:
    return _ingest.provide(port, owner="modules.library")


def has_library_ingest() -> bool:
    return _ingest.has()


def get_library_ingest() -> LibraryIngest:
    impl = _ingest.single()
    if impl is None:
        raise Unavailable(
            "资产库入库不可用：没有任何模块注册 LibraryIngest。"
            "生成结果会丢失，先确认 `discover_modules()` 已执行。"
        )
    return impl


def reset_library_ingest() -> None:
    """仅测试用。"""
    _ingest.reset()
