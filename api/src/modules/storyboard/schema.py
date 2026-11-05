"""分镜模板出入参。"""

from __future__ import annotations

from pydantic import Field

from core.enums import AspectRatio
from core.schema import CamelModel, UtcDateTime


class StoryboardOut(CamelModel):
    id: str
    name: str
    description: str
    shot_count: int
    duration_sec: int
    aspect_ratio: AspectRatio
    cover_url: str
    created_at: UtcDateTime


class StoryboardListOut(CamelModel):
    items: list[StoryboardOut] = Field(default_factory=list)
    total: int = 0
