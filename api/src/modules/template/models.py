"""模板 ORM 实体：可复用的分镜结构。"""

from __future__ import annotations

from sqlalchemy import Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base, TimestampMixin, UUIDMixin


class VideoTemplate(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "video_templates"

    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    industry: Mapped[str] = mapped_column(String(64), default="", nullable=False, index=True)
    shot_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    usage_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    cover_url: Mapped[str] = mapped_column(String(512), default="", nullable=False)
    # 标签用逗号分隔存储；若需检索可改为关联表 + GIN 索引
    tags: Mapped[str] = mapped_column(String(256), default="", nullable=False)

    @property
    def tag_list(self) -> list[str]:
        return [t for t in self.tags.split(",") if t]
