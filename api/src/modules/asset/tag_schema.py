"""标签与保存视图的 HTTP 契约。

两个刻意的取舍：

1. **标签出参带 `useCount` 与 `pinned`，但不带词表的"准入三问"。** 三问是
   `POST` 表单上的文案，不是接口字段——把它做成字段会让人以为填了就能建。
2. **保存视图的 `query` 是原样的筛选参数对象**，不是 SQL、也不是结果 id 列表。
   它是"把当时的条件再放回去"，所以字段名必须与 `GET /assets` 的查询参数同名：
   前端拿到它就能直接展开成一次请求，不需要翻译层。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import Field

from core.schema import CamelModel


class TagOut(CamelModel):
    id: str
    # style / scene / composition / shot / placement / content
    dimension: str
    name: str
    status: str = "active"
    # manual / inferred。推断出来的词在卡片上要有区分标记
    source: str = "manual"
    # 在用的素材数（不含回收站）。**算出来的，不是存的**
    use_count: int = 0
    # 是否进筛选条常显区（`useCount >= 5`）。前端也可以自己算，
    # 但让后端给一个布尔值能保证两个客户端判据一致
    pinned: bool = False


class TagGroup(CamelModel):
    dimension: str
    label: str
    items: list[TagOut] = Field(default_factory=list)


class TagCatalog(CamelModel):
    groups: list[TagGroup] = Field(default_factory=list)
    total: int = 0
    # 常显门槛，前端在「＋更多」里说明"用满 N 次会常显"时用它，别硬编码
    pinned_threshold: int = 0


class TagCreate(CamelModel):
    dimension: str = Field(min_length=1, max_length=16)
    name: str = Field(min_length=1, max_length=32)


class AssetTagSet(CamelModel):
    """整体替换一条素材的标签集合。

    空列表是合法输入，含义是「清空标签」——不要用「不传该字段」表达清空，
    那样和「没改」无法区分。
    """

    tag_ids: list[str] = Field(default_factory=list, alias="tagIds")


class AssetTagList(CamelModel):
    asset_id: str
    items: list[TagOut] = Field(default_factory=list)


class AssetViewOut(CamelModel):
    id: str
    name: str
    # 原始筛选参数（与 `GET /assets` 的查询参数同名）
    query: dict[str, Any] = Field(default_factory=dict)
    sort: str = ""
    created_at: datetime


class AssetViewList(CamelModel):
    items: list[AssetViewOut] = Field(default_factory=list)


class AssetViewCreate(CamelModel):
    name: str = Field(min_length=1, max_length=40)
    query: dict[str, Any] = Field(default_factory=dict)
    sort: str = Field(default="", max_length=24)
