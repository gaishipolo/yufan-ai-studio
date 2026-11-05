"""分镜模板 ORM 实体。"""

from __future__ import annotations

from sqlalchemy import Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from core.enums import AspectRatio
from db.base import Base, TimestampMixin, UUIDMixin


class Storyboard(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "storyboards"

    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    shot_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    duration_sec: Mapped[int] = mapped_column(Integer, default=15, nullable=False)
    aspect_ratio: Mapped[str] = mapped_column(
        String(8), default=AspectRatio.PORTRAIT.value, nullable=False
    )
    cover_url: Mapped[str] = mapped_column(String(512), default="", nullable=False)
