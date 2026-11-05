"""镜头词条出入参。"""

from __future__ import annotations

from pydantic import Field

from core.schema import CamelModel


class ShotPresetOut(CamelModel):
    id: str
    name: str
    prompt_fragment: str
    duration_sec: int
    cover_url: str
    popularity: int


class ShotListOut(CamelModel):
    items: list[ShotPresetOut] = Field(default_factory=list)
    total: int = 0
