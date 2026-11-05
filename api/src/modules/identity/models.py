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

from db.base import Base, TimestampMixin, UUIDMixin, utcnow


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
    # 最近一次改密时间。解析器据此拒绝「改密之前签发」的令牌：
    # 令牌是无状态的，不做这一步，账号被盗后改密码对攻击者没有影响（R4-13）
    password_changed_at: Mapped[datetime | None] = mapped_column(
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


class UserSession(Base, UUIDMixin, TimestampMixin):
    """一次登录产生的会话：access 令牌带 `sid`，refresh 令牌用 `refresh_jti` 轮换。

    为什么要有这张表，而不是在 `users` 上加一列 `token_version`：

    - `token_version` 只能「全部踢掉」，做不到「把这台设备下线」，
      也列不出「我在哪些设备上登录过」；
    - 而「登出后旧令牌立刻失效」这件事，只有**每请求校验一次会话状态**才兑现得了：
      令牌是无状态的，签名没坏就永远能解出一个合法主体。

    代价是每个带令牌的请求多一次主键查询（走 `pk_user_sessions`）。比起它解决的问题
    （离职员工在共用电脑上点了登出、令牌被复制走还能用满 2 小时）不值一提。

    刷新令牌不落库原文，只落 `jti`：JWT 本身有签名，校验时比对 jti 就够，
    落原文等于给自己加一个「数据库泄漏即令牌泄漏」的面。
    """

    __tablename__ = "user_sessions"

    user_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    # 当前有效的 refresh 令牌 id；轮换时替换，旧的自然失效（不需要黑名单表）
    refresh_jti: Mapped[str] = mapped_column(String(64), nullable=False)
    user_agent: Mapped[str] = mapped_column(String(256), default="", nullable=False)
    ip: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    last_seen_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # 刷新令牌的到期时间；access 令牌更短，由它自己的 exp 管
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # 为什么失效：logout / admin_revoke / password_reset / self_revoke
    revoked_reason: Mapped[str] = mapped_column(String(32), default="", nullable=False)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<UserSession {self.id} user={self.user_id} revoked={self.revoked_at}>"


class LoginEvent(Base, UUIDMixin):
    """一次登录尝试。**只追加**，不更新，也不挂在 `users` 上。

    单独一张表而不是给 `users` 加列：审计是时间序列（一次登录一行），
    塞进 `users` 就只能存「最后一次」，而安全事件恰恰要看「上周三凌晨那一次」。
    也刻意不带 `updated_at`——这类记录被改过一次就失去证据价值。
    """

    __tablename__ = "login_events"

    # 用户名不存在时为 NULL（此时 `username` 记下客户端提交的原值，供排查爆破）
    user_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    username: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    success: Mapped[bool] = mapped_column(Boolean, nullable=False)
    ip: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    user_agent: Mapped[str] = mapped_column(String(256), default="", nullable=False)
    # 失败原因：bad_password / disabled / rate_limited；成功为 None
    reason: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False, index=True
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<LoginEvent {self.username} ok={self.success} reason={self.reason}>"
