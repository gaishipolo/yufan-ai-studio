"""素材资产 ORM 实体。"""

from __future__ import annotations

from sqlalchemy import BigInteger, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base, TimestampMixin, UUIDMixin


class Asset(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "assets"

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    type: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    mime_type: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)

    cover_url: Mapped[str] = mapped_column(String(512), default="", nullable=False)
    storage_key: Mapped[str] = mapped_column(String(256), default="", nullable=False)

    width: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    height: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    ref_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    owner_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)

    __table_args__ = (Index("ix_assets_owner_type", "owner_id", "type"),)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Asset {self.id} {self.type} {self.name[:16]!r}>"
