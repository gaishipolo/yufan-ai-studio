"""ORM 基座：声明基类 + 公共字段混入。

所有实体继承 `Base`，并复用 `UUIDMixin` / `TimestampMixin`，
保证主键策略与审计字段全项目一致。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import DateTime, MetaData, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# 统一命名约定，保证 Alembic 自动生成的约束名可预测
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)

    def to_dict(self) -> dict[str, Any]:
        return {c.name: getattr(self, c.name) for c in self.__table__.columns}


def _uuid_str() -> str:
    return uuid.uuid4().hex


def utcnow() -> datetime:
    return datetime.now(tz=UTC)


class UUIDMixin:
    """32 位十六进制字符串主键，跨数据库行为一致（sqlite / postgres 通用）。"""

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid_str)


class TimestampMixin:
    """审计字段。

    时间统一由 Python 生成（UTC），而非数据库 `now()`：
    sqlite 与 postgres 的时区行为不一致，交给应用层可保证跨库格式统一。
    """

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )
