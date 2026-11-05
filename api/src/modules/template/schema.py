"""模板模块出入参。"""

from __future__ import annotations

from pydantic import Field, field_validator

from core.schema import CamelModel, UtcDateTime


class TemplateOut(CamelModel):
    id: str
    name: str
    description: str
    industry: str
    shot_count: int
    usage_count: int
    cover_url: str
    tags: list[str] = Field(default_factory=list)
    created_at: UtcDateTime

    @field_validator("tags", mode="before")
    @classmethod
    def _split_tags(cls, v: object) -> object:
        """ORM 侧是逗号分隔字符串，转成数组再输出给前端。"""
        if isinstance(v, str):
            return [item for item in v.split(",") if item]
        return v


class TemplateListOut(CamelModel):
    items: list[TemplateOut] = Field(default_factory=list)
    total: int = 0
