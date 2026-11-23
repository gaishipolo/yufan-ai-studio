"""模板案例的接口契约。

命名前缀统一 `templateCase` —— 前端有「案例池（素材）」也有「模板案例（配置快照）」，
两个字段名混在一起会分不清。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from core.schema import CamelModel, UtcDateTime
from modules.template_pack.spec_schema import StrictIn

__all__ = [
    "TemplateCaseOut",
    "TemplateCaseListOut",
    "TemplateCaseCreateIn",
    "TemplateCaseUpdateIn",
]


class TemplateCaseOut(CamelModel):
    id: str
    name: str
    description: str
    category: str
    #: `published`（可复用）| `offline`（已下架：不可复用，但管理界面看得到）
    status: Literal["published", "offline"]
    #: 来源模板。`specKey` 是身份 —— 模板改名后仍然指向同一个模板。
    spec_id: str
    spec_key: str
    spec_name: str
    #: 发布案例时来源模板的版本号；0 = 模板从未发版过。
    spec_version: int
    #: 来源模板已被软删 → True。案例自带快照，**照样能复用**，
    #: 只是界面上要告诉用户"它的母版没了"。
    source_missing: bool = False
    #: 模板配置快照（camelCase，与 `TemplateSpecIn` 同形）。
    #: 复用时由前端挑字段灌进新草稿 —— 后端不替用户决定复用哪几项。
    config: dict[str, Any] = Field(default_factory=dict)
    owner_id: str
    owner_name: str
    published_at: UtcDateTime | None = None
    #: 最近一次下架时间；重新上架时清空。
    offline_at: UtcDateTime | None = None
    created_at: UtcDateTime
    updated_at: UtcDateTime


class TemplateCaseListOut(CamelModel):
    items: list[TemplateCaseOut] = Field(default_factory=list)
    total: int = 0
    #: 已用到的分类（给筛选器用；空串表示「未分类」）。
    categories: list[str] = Field(default_factory=list)


class TemplateCaseCreateIn(StrictIn):
    #: 来源模板的 key。
    spec_key: str = Field(min_length=1, max_length=80)
    #: 案例名。独立于模板名 —— 同一份模板可以发布成多个案例。
    name: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=2000)
    #: 分类。空串 = 未分类。
    category: str = Field(default="", max_length=64)


class TemplateCaseUpdateIn(StrictIn):
    """编辑案例。只改案例自己的字段，不碰模板。"""

    name: str | None = Field(default=None, min_length=1, max_length=160)
    description: str | None = Field(default=None, max_length=2000)
    category: str | None = Field(default=None, max_length=64)
