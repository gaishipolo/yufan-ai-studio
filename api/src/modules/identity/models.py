"""身份 ORM 实体：用户、角色、角色→权限、用户→角色。

两个刻意的设计选择：

1. **关联用 `role_id` 而不是 `role_code`**。角色改名不该级联改两张表，
   而且拼错 code 在 sqlite 里不会报错——只会静默地少一批权限，这类 bug 极难排查。
2. **不建外键**。与项目其他模块保持一致（`video_tasks.author_id` 也是字符串快照），
   删角色前由 service 显式检查引用；外键在 sqlite 下默认还不生效，写了也是假的。

`role_permissions.permission` 存权限码字符串而不是指向权限表：
权限点的权威来源是各模块的 `ModuleSpec`（`core/permissions.py` 的 catalog 汇总），
数据库只登记「哪些角色拥有它」。这样删掉一个模块的权限点不会被外键卡住，
只是变成孤儿数据，由角色详情接口标出来提醒清理。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base, TimestampMixin, UUIDMixin


class User(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "users"

    username: Mapped[str] = mapped_column(
        String(64), unique=True, index=True, nullable=False
    )
    password_hash: Mapped[str] = mapped_column(String(256), nullable=False)
    display_name: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    email: Mapped[str | None] = mapped_column(String(128), nullable=True)
    avatar_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    # 停用即不能登录；不物理删除，因为历史任务/素材的 author_id 还指着他
    is_active: Mapped[bool] = mapped_column(
        Boolean, default=True, nullable=False, index=True
    )
    last_login_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<User {self.id} {self.username!r} active={self.is_active}>"


class Role(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "roles"

    code: Mapped[str] = mapped_column(
        String(32), unique=True, index=True, nullable=False
    )
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    description: Mapped[str] = mapped_column(String(200), default="", nullable=False)
    # 持有该角色 = 展开为 `*`。用列而不是「角色名 == admin」，
    # 否则改个名就把超管降级了，这种 bug 不会报错、只会让所有权限悄悄失效。
    is_superuser: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    # 内置角色：不可删除、`code` 不可改（权限内容仍可调）
    is_builtin: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Role {self.code} super={self.is_superuser}>"


class RolePermission(Base, UUIDMixin, TimestampMixin):
    """角色 → 权限码。"""

    __tablename__ = "role_permissions"

    role_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    permission: Mapped[str] = mapped_column(String(64), nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "role_id", "permission", name="uq_role_permissions_role_permission"
        ),
    )


class UserRole(Base, UUIDMixin, TimestampMixin):
    """用户 → 角色。"""

    __tablename__ = "user_roles"

    user_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    role_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)

    __table_args__ = (
        UniqueConstraint("user_id", "role_id", name="uq_user_roles_user_role"),
        # 统计「某角色下有多少人」时走这条索引
        Index("ix_user_roles_role_user", "role_id", "user_id"),
    )
