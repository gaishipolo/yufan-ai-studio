"""镜头词条 ORM 实体：运镜指令片段。"""

from __future__ import annotations

from sqlalchemy import Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base, TimestampMixin, UUIDMixin


class ShotPreset(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "shot_presets"

    name: Mapped[str] = mapped_column(String(120), nullable=False)
    prompt_fragment: Mapped[str] = mapped_column(Text, default="", nullable=False)
    duration_sec: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    cover_url: Mapped[str] = mapped_column(String(512), default="", nullable=False)
    popularity: Mapped[int] = mapped_column(Integer, default=0, nullable=False, index=True)
