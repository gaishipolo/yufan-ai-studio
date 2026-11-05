"""生图模块出入参。"""

from __future__ import annotations

from pydantic import Field

from core.schema import CamelModel, UtcDateTime


class ImageJobOut(CamelModel):
    id: str = Field(description="生图记录 ID")
    name: str = Field(description="名称")
    prompt: str = Field(description="画面描述")
    size: str = Field(description="尺寸，如 `1024x1536`")
    cover_url: str = Field(default="", description="封面图 URL")
    created_at: UtcDateTime = Field(description="创建时间（UTC）")


class GenerateImageIn(CamelModel):
    prompt: str = Field(min_length=1, max_length=2000)
    size: str = Field(default="1024x1536", max_length=32, description="宽x高")
    count: int = Field(default=1, ge=1, le=4)
