"""生图模块出入参。"""

from __future__ import annotations

from pydantic import Field

from core.schema import CamelModel, UtcDateTime


class ImageJobOut(CamelModel):
    id: str
    name: str
    prompt: str
    size: str
    cover_url: str
    created_at: UtcDateTime


class ImageJobListOut(CamelModel):
    items: list[ImageJobOut] = Field(default_factory=list)
    total: int = 0


class GenerateImageIn(CamelModel):
    prompt: str = Field(min_length=1, max_length=2000)
    size: str = Field(default="1024x1536", max_length=32, description="宽x高")
    count: int = Field(default=1, ge=1, le=4)
