"""资产库 v2 的只读注入口：按 id 批量取资产元数据。

与 `core/ports/media.py`（旧素材库）是**两套独立的对象**，不要合并：

- `core.ports.media` 回答的是 `assets` 表里的旧素材（模板库 prepare 用它）；
- 这个文件回答的是 `library_assets` 表里的 v2 资产（草稿 / 小组 / 团队 / 回收站）。

并存期两个库同时在线，调用方必须显式选择问哪一个。合并成一个「素材查询」
会让「模板库拿到的到底是旧素材还是 v2 资产」变成隐式行为，切换那天才发现
某一侧悄悄读到了另一半的数据。

## 为什么 fail-closed

与 `media` 的理由相同：未注册时抛 `Unavailable`(503)，不返回空映射。调用方
拿到空映射会报「资产不存在」，把装配故障说成用户数据问题，排查方向被带偏。
这是 `api/ports.py` 里「未注册必须返回中性值」的刻意例外，已在 `PORT_CATALOG`
的 `when_unregistered` 里写明。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, runtime_checkable

from sqlalchemy.ext.asyncio import AsyncSession

from core.exceptions import Unavailable
from core.ports.host import Port

__all__ = [
    "LibraryAssetRef",
    "LibraryAssetState",
    "LibraryAssetKind",
    "LibraryVisibility",
    "LibraryLookup",
    "get_library_lookup",
    "has_library_lookup",
    "register_library_lookup",
    "reset_library_lookup",
]


class LibraryAssetKind(StrEnum):
    """资产类型。`pack` 是封装，成员关系在 `library_pack_items`。"""

    VIDEO = "video"
    AUDIO = "audio"
    IMAGE = "image"
    PACK = "pack"


class LibraryAssetState(StrEnum):
    """生命周期状态。与 `visibility` 正交，别把草稿/回收站塞进可见性。"""

    DRAFT = "draft"
    ACTIVE = "active"
    TRASH = "trash"


class LibraryVisibility(StrEnum):
    """可见范围。`personal` 是唯一允许草稿与回收站存在的范围。"""

    PERSONAL = "personal"
    GROUP = "group"
    TEAM = "team"


@dataclass(frozen=True, slots=True)
class LibraryAssetRef:
    """跨模块只读的资产快照。

    刻意是**扁平的、与 ORM 解耦的**：消费方（模板、任务、选择器）不该拿到
    一个带 lazy 关系的 ORM 对象，否则它会在别人的 session 上触发查询。
    """

    id: str
    owner_id: str
    kind: LibraryAssetKind
    name: str
    state: LibraryAssetState
    visibility: LibraryVisibility
    mime_type: str = ""
    storage_key: str = ""
    size_bytes: int = 0
    duration_ms: int | None = None
    has_audio: bool = False


@runtime_checkable
class LibraryLookup(Protocol):
    """按 id 批量取 v2 资产元数据。"""

    async def get_many(
        self, session: AsyncSession, asset_ids: Sequence[str]
    ) -> Mapping[str, LibraryAssetRef]:
        """返回 `{asset_id: LibraryAssetRef}`，不存在的 id 不出现在结果里。

        不要为缺失 id 造占位对象：调用方靠「键是否存在」区分「已删除」与
        「还没上传完」。`session` 由调用方传入，保证与业务读取同一个快照。
        """
        ...


_lookup: Port[LibraryLookup] = Port("library-lookup", multi=False)


def register_library_lookup(port: LibraryLookup) -> LibraryLookup:
    """资产库模块 import 期调用；重复注册（不同实例）直接报错。"""
    return _lookup.provide(port, owner="modules.library")


def has_library_lookup() -> bool:
    return _lookup.has()


def get_library_lookup() -> LibraryLookup:
    impl = _lookup.single()
    if impl is None:
        raise Unavailable(
            "资产库查询不可用：没有任何模块注册 LibraryLookup。"
            "通常是启动时漏了 `discover_modules()`。"
        )
    return impl


def reset_library_lookup() -> None:
    """仅测试用。"""
    _lookup.reset()
