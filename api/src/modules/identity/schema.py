"""身份模块出入参。

契约与前端 `entities/user/model/types.ts` 一一对应（camelCase 自动转换）。

三条约定：
- 出参**永不包含** `password_hash`：`UserOut` 是白名单式声明，不是 `model_dump()` 直出。
- PATCH 用 `exclude_unset`，才能区分「没传这个字段」和「显式传 null 清空」。
- 字段的 `description` 会写进 `docs/API.md` 的字段表，新增字段请一并补上。
"""

from __future__ import annotations

from pydantic import Field, field_validator, model_validator

from core.schema import CamelModel, CamelORMModel, UtcDateTime
from modules.identity.policy import PASSWORD_MIN_LENGTH, check_password_strength

# 用户名只允许字母数字与 . _ -：避免大小写、空格、emoji 带来的「看起来一样但登录不上」
USERNAME_PATTERN = r"^[a-zA-Z0-9_.-]{3,64}$"

# 邮箱只做「能发出去」的最低限度校验：不追求 RFC 5322 全集（那件事没有正确答案），
# 只拦住空格、缺 @、缺域名后缀这类必然发不出去的输入。
EMAIL_PATTERN = r"^[^@\s]+@[^@\s]+\.[^@\s]{2,}$"


class LoginIn(CamelModel):
    username: str = Field(min_length=1, max_length=64, description="用户名")
    password: str = Field(min_length=1, max_length=128, description="密码")

    @field_validator("username")
    @classmethod
    def _normalize(cls, v: str) -> str:
        return v.strip().lower()


class RoleBriefOut(CamelModel):
    """用户出参里内嵌的角色摘要。"""

    id: str = Field(description="角色 ID")
    code: str = Field(description="角色标识，如 `editor`")
    name: str = Field(description="角色名称")
    is_superuser: bool = Field(default=False, description="是否超级管理员角色（权限展开为 `*`）")


class UserOut(CamelORMModel):
    id: str = Field(description="用户 ID")
    username: str = Field(description="登录名，入库前已转小写")
    display_name: str = Field(default="", description="昵称（顶栏展示用）")
    email: str | None = Field(default=None, description="邮箱，未填为 null")
    avatar_url: str | None = Field(default=None, description="头像 URL，未设置为 null")
    is_active: bool = Field(default=True, description="是否启用；停用后无法登录且令牌立即失效")
    last_login_at: UtcDateTime | None = Field(
        default=None, description="最后登录时间，从未登录为 null"
    )
    created_at: UtcDateTime = Field(description="创建时间（UTC）")
    roles: list[RoleBriefOut] = Field(default_factory=list, description="拥有的角色")


class MeOut(UserOut):
    """`GET /identity/me`：前端靠 `permissions` 渲染菜单与按钮。"""

    permissions: list[str] = Field(
        default_factory=list, description="展开后的权限码；含 `*` 表示超级管理员"
    )


class TokenOut(CamelModel):
    access_token: str = Field(description="访问令牌，放进 `Authorization: Bearer <token>`")
    token_type: str = Field(default="Bearer", description="令牌类型，固定 Bearer")
    expires_in: int = Field(default=0, description="有效期（秒），过期后请求返回 401")
    refresh_token: str = Field(
        default="", description="刷新令牌，只用来换新的 accessToken，不能访问业务接口"
    )
    refresh_expires_in: int = Field(
        default=0, description="刷新令牌有效期（秒）；续期时滑动延长"
    )
    user: MeOut = Field(description="登录用户，可省一次 `/me` 请求")


class RefreshIn(CamelModel):
    refresh_token: str = Field(min_length=1, max_length=2048, description="登录时拿到的刷新令牌")


class UserCreateIn(CamelModel):
    username: str = Field(
        pattern=USERNAME_PATTERN,
        description="登录名，3-64 位字母数字或 `. _ -`，会被转成小写",
    )
    password: str = Field(
        min_length=PASSWORD_MIN_LENGTH,
        max_length=128,
        description=f"初始密码，管理员指定；至少 {PASSWORD_MIN_LENGTH} 位，不能是纯数字",
    )
    display_name: str = Field(default="", max_length=64, description="昵称，留空则与用户名相同")
    email: str | None = Field(
        default=None, max_length=128, pattern=EMAIL_PATTERN, description="邮箱"
    )
    avatar_url: str | None = Field(default=None, max_length=512, description="头像 URL")
    role_ids: list[str] = Field(default_factory=list, description="角色 id 列表，可留空")

    @field_validator("username")
    @classmethod
    def _normalize(cls, v: str) -> str:
        return v.strip().lower()

    @field_validator("password")
    @classmethod
    def _strength(cls, v: str) -> str:
        return check_password_strength(v)


class UserUpdateIn(CamelModel):
    display_name: str | None = Field(default=None, max_length=64, description="昵称")
    email: str | None = Field(
        default=None, max_length=128, pattern=EMAIL_PATTERN, description="邮箱；传 null 清空"
    )
    avatar_url: str | None = Field(
        default=None, max_length=512, description="头像 URL；传 null 清空"
    )
    is_active: bool | None = Field(
        default=None,
        description="启用/停用；不能停用自己，且必须保留一个启用的超管。"
        "不接受 null：停用请传 false，不想改就不要传这个字段",
    )

    @field_validator("is_active", mode="before")
    @classmethod
    def _reject_null_active(cls, v: object) -> object:
        """`isActive: null` 必须 422，不能静默当成 false（R6-03）。

        `exclude_unset` 只挡「没传」，挡不住「显式传了 null」，而
        `bool(None)` 会把一次误传变成**停用账号**——表单里「这个开关没填」
        发 null 很常见，后果却是把一个正常账号关掉，还带着「最后一个超管」的
        连带判定，排查时会一路往权限方向查。
        """
        if v is None:
            raise ValueError("isActive 不能为 null：停用请传 false，保持不动请不要传该字段")
        return v


class ProfileUpdateIn(CamelModel):
    """`PATCH /identity/me` 的自助可写字段。

    刻意**不含** `is_active`：停用是管理动作，用户误点一下就把自己锁在门外，
    而恢复只有管理员一个入口。自助路径能改的只有「展示信息」。
    """

    display_name: str | None = Field(default=None, max_length=64, description="昵称")
    email: str | None = Field(
        default=None, max_length=128, pattern=EMAIL_PATTERN, description="邮箱；传 null 清空"
    )
    avatar_url: str | None = Field(
        default=None, max_length=512, description="头像 URL；传 null 清空"
    )



class UserRolesIn(CamelModel):
    role_ids: list[str] = Field(default_factory=list, description="整组替换，传空数组即清空")


class PasswordChangeIn(CamelModel):
    old_password: str = Field(min_length=1, max_length=128, description="当前密码")
    new_password: str = Field(
        min_length=PASSWORD_MIN_LENGTH,
        max_length=128,
        description=f"新密码；至少 {PASSWORD_MIN_LENGTH} 位，不能是纯数字，且不能与当前密码相同",
    )

    @field_validator("new_password")
    @classmethod
    def _strength(cls, v: str) -> str:
        return check_password_strength(v)

    @model_validator(mode="after")
    def _not_same_as_old(self) -> PasswordChangeIn:
        if self.new_password == self.old_password:
            raise ValueError("新密码不能与当前密码相同")
        return self


class ResetPasswordIn(CamelModel):
    new_password: str = Field(
        min_length=PASSWORD_MIN_LENGTH,
        max_length=128,
        description=f"管理员设置的新密码；至少 {PASSWORD_MIN_LENGTH} 位，不能是纯数字",
    )

    @field_validator("new_password")
    @classmethod
    def _strength(cls, v: str) -> str:
        return check_password_strength(v)


class RoleOut(CamelORMModel):
    id: str = Field(description="角色 ID")
    code: str = Field(description="角色标识，唯一；内置角色不可改")
    name: str = Field(description="角色名称")
    description: str = Field(default="", description="角色说明")
    is_superuser: bool = Field(default=False, description="持有即展开为 `*`，拥有全部权限")
    is_builtin: bool = Field(default=False, description="内置角色：不可删除")
    sort_order: int = Field(default=0, description="排序值，小的在前")
    created_at: UtcDateTime = Field(description="创建时间（UTC）")
    permissions: list[str] = Field(default_factory=list, description="角色拥有的权限码")
    user_count: int = Field(default=0, description="使用该角色的用户数")
    # 库里存了、但 catalog 里已经不存在（模块被删/权限点改名）——提示管理员清理
    orphaned_permissions: list[str] = Field(
        default_factory=list, description="已失效的权限码（权限点被删或改名后残留）"
    )


class RoleCreateIn(CamelModel):
    code: str = Field(pattern=r"^[a-z][a-z0-9_-]{1,31}$", description="角色标识，唯一且不可改")
    name: str = Field(min_length=1, max_length=64, description="角色名称")
    description: str = Field(default="", max_length=200, description="角色说明")
    permissions: list[str] = Field(default_factory=list, description="权限码列表")
    is_superuser: bool = Field(default=False, description="仅超级管理员可设置")


class RoleUpdateIn(CamelModel):
    name: str | None = Field(default=None, min_length=1, max_length=64, description="角色名称")
    description: str | None = Field(default=None, max_length=200, description="角色说明")
    is_superuser: bool | None = Field(default=None, description="仅超级管理员可设置")


class RolePermissionsIn(CamelModel):
    permissions: list[str] = Field(
        default_factory=list, description="整组替换；出现未登记的权限码会被拒绝"
    )


class PermissionOut(CamelModel):
    code: str = Field(description="权限码，如 `task:create`")
    name: str = Field(description="权限名称")
    description: str = Field(default="", description="权限说明")


class PermissionGroupOut(CamelModel):
    group: str = Field(description="分组名，如「任务」「身份」")
    items: list[PermissionOut] = Field(default_factory=list, description="该组下的权限点")


class PermissionCatalogOut(CamelModel):
    groups: list[PermissionGroupOut] = Field(
        default_factory=list, description="按分组组织的全部权限点，角色编辑界面直接渲染"
    )
    total: int = Field(default=0, description="权限点总数")


class SessionOut(CamelORMModel):
    """一条登录会话。前端「我的登录设备」列表用它。"""

    id: str = Field(description="会话 ID，踢下线时用它")
    user_agent: str = Field(default="", description="登录时的 UA，未记录为空串")
    ip: str = Field(default="", description="登录时的来源 IP")
    last_seen_at: UtcDateTime | None = Field(
        default=None, description="最近一次续期时间，从未续期为 null"
    )
    expires_at: UtcDateTime = Field(description="刷新令牌到期时间（UTC）")
    created_at: UtcDateTime = Field(description="登录时间（UTC）")
    current: bool = Field(
        default=False, description="是否是发起本次请求的会话；前端据此标「本机」"
    )


# 「吊销了 N 个会话」的结果。之前这两个接口直接返回 `dict[str, int]`，于是 TS 生成器
# 把 Pydantic 的自动名 `ApiResponse_dict_str__int__` 切成 `dict_str__int` 当类型名写进
# `schema.ts`，而那个名字在 `components.schemas` 里并不存在——契约生成物编不过，
# 前端只能手改生成物，后端字节比对又红（R10-01）。
# 更根本的是：`{"revoked": n}` 本来就该有名字，字段含义不该只写在注释里。
class RevokedSessionsOut(CamelModel):
    """吊销会话的结果。两个「踢下线」类接口都返回这个形状。"""

    revoked: int = Field(description="本次吊销的会话数；已经失效的会话不计入")


class LoginEventOut(CamelModel):
    """一次登录尝试。审计用，只读。

    字段名 `at` 与库里的 `created_at` 不同：契约里这一列叫 `at`（前端需求文档的写法），
    转换放在 service 里显式做一次，比给 `created_at` 起两个别名好读。
    """

    at: UtcDateTime = Field(description="发生时间（UTC）")
    success: bool = Field(description="是否成功")
    ip: str = Field(default="", description="来源 IP")
    user_agent: str = Field(default="", description="客户端 UA")
    reason: str | None = Field(
        default=None,
        description="失败原因：`bad_password` / `disabled` / `rate_limited`；成功为 null",
    )


class LoginEventListOut(CamelModel):
    items: list[LoginEventOut] = Field(default_factory=list, description="按时间倒序")


class PasswordPolicyOut(CamelModel):
    """口令策略下发。**文案也由后端给**：前端四处表单不该各拼一份。"""

    min_length: int = Field(description="最短长度")
    max_length: int = Field(description="最长长度")
    forbid_all_numeric: bool = Field(description="是否禁止纯数字")
    forbid_same_as_username: bool = Field(
        description="是否禁止与用户名相同；当前未启用，前端按 false 处理"
    )
    message: str = Field(description="可直接展示的一句话，前端不要自己拼")


__all__ = [
    "LoginIn",
    "MeOut",
    "PasswordChangeIn",
    "PermissionCatalogOut",
    "PermissionGroupOut",
    "LoginEventListOut",
    "LoginEventOut",
    "PasswordPolicyOut",
    "PermissionOut",
    "ProfileUpdateIn",
    "RefreshIn",
    "ResetPasswordIn",
    "RevokedSessionsOut",
    "RoleBriefOut",
    "RoleCreateIn",
    "RoleOut",
    "RolePermissionsIn",
    "RoleUpdateIn",
    "SessionOut",
    "TokenOut",
    "UserCreateIn",
    "UserOut",
    "UserRolesIn",
    "UserUpdateIn",
]
