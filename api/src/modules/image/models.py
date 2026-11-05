"""生图任务记录 ORM 实体。"""

from __future__ import annotations

from sqlalchemy import String, Text
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base, TimestampMixin, UUIDMixin


class ImageJob(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "image_jobs"

    name: Mapped[str] = mapped_column(String(120), nullable=False)
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    size: Mapped[str] = mapped_column(String(32), default="1024x1536", nullable=False)
    cover_url: Mapped[str] = mapped_column(String(512), default="", nullable=False)
    owner_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)

    # 生图是同步供应商时可直接落结果；异步供应商复用 external_id 轮询
    external_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
