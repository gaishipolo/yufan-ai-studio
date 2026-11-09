"""资产库 v2 出入参（字段用 snake_case 写，序列化自动转 camelCase）。

M0 只定「列表 + 分面」这一组，因为它是五个一级入口共用的壳。详情、封装、
共享申请、下载任务等出入参在 M2/M3 建表时随实现一起加——**先定壳、后定肉**，
避免 M0 凭空写一批没实现支撑的字段，M1 建表时又全改一遍。
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field

from core.ports.library_lookup import (
    LibraryAssetKind,
    LibraryAssetState,
    LibraryVisibility,
)
from core.response import Page, PageParams
from core.schema import CamelModel, UtcDateTime

__all__ = [
    "AssetSort",
    "LibraryAssetOut",
    "LibraryAssetPage",
    "LibraryFacets",
    "LibraryTab",
]


class LibraryTab(StrEnum):
    """列表的可见范围（对应侧栏一级入口的个人/小组/团队/全部）。

    草稿箱与回收站不在这里：它们是 `state` 的两个值，与可见范围正交。
    把「草稿」做成 `library=draft` 会让「草稿属于谁」变成一个可见范围问题，
    而实际不变式是「草稿必须是 personal」。
    """

    ALL = "all"
    PERSONAL = "personal"
    GROUP = "group"
    TEAM = "team"


class AssetSort(StrEnum):
    """排序键。默认按创建时间倒序。"""

    CREATED_DESC = "-createdAt"
    CREATED_ASC = "createdAt"
    NAME = "name"
    SIZE_DESC = "-sizeBytes"


class LibraryFacets(CamelModel):
    """分面统计：五个一级入口的条数，前端直接渲染成侧栏角标。

    与 `items` 同一次查询算出，不额外发起请求——否则切换 tab 时角标会跳。
    """

    all: int = 0
    personal: int = 0
    group: int = 0
    team: int = 0
    drafts: int = 0
    trash: int = 0


class LibraryAssetOut(CamelModel):
    """列表项。字段对齐 `library_assets` 表，M1 建表后不再改形状。"""

    id: str
    name: str
    kind: LibraryAssetKind
    origin: str = Field(description="upload / generated / template_source / imported")
    mime_type: str | None = None
    size_bytes: int = 0
    duration_ms: int | None = None
    has_audio: bool = False
    owner_id: str
    owner_name: str | None = None
    visibility: LibraryVisibility
    state: LibraryAssetState
    state_version: int = 1
    cover_url: str | None = None
    created_at: UtcDateTime
    updated_at: UtcDateTime | None = None


class LibraryAssetPage(Page[LibraryAssetOut]):
    """列表响应 = 标准分页 + 分面统计。

    继承 `Page` 而不是复制 `items/total/page/pageSize`：前端的通用分页组件
    认的是那四个字段，复制一遍迟早出现「这个页面 pageSize 拼错成 page_size」。
    """

    facets: LibraryFacets = Field(default_factory=LibraryFacets)

    @classmethod
    def empty(
        cls, params: PageParams, facets: LibraryFacets | None = None
    ) -> LibraryAssetPage:
        """空页。父类的 `create()` 返回 `Page[T]`，泛型子类的额外字段会被丢掉，
        所以这里显式构造——M0 骨架与真实空库都用这条路径。"""
        return cls(
            items=[],
            total=0,
            page=params.page,
            page_size=params.page_size,
            facets=facets or LibraryFacets(),
        )
