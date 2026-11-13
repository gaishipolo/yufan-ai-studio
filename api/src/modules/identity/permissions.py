"""身份模块的权限点与内置角色模板。

权限码前缀必须是 `identity:`（`tests/test_architecture.py` 会强制），
其中 `identity:role:manage` 单独拆出来：**能改昵称不等于能给自己提权**，
角色与权限的改动必须是一个独立开关。

内置角色模板里出现的 `task:*` / `library:*` 是**字符串常量**，不是 import——
权限点的权威仍然在各自模块的 `ModuleSpec` 里，这里只是默认角色的初始值。
用通配而不是逐个列举，是为了让别的模块新增权限点后，内置角色自动覆盖到它。

2026-11-13：v1 素材库（`assets` 表 + `modules/asset`）下线，内置角色里的
`asset:read/create/update/delete/share` 一一换成 `library:*` 的同名动作。
`asset:reference:manage`（维护**他人**的参考素材）没有对应项——v2 里最接近的
`library:delete:any` 是跨人删除，授给「素材专员」属于越权，所以直接不补。
已经建好的角色行不会被这段模板改写（那需要显式迁移），本仓也没有那样的迁移：
开发阶段按需重建角色即可。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Final

from core.permissions import PermissionSpec

# ---------- 权限码 ----------
PERM_READ: Final = "identity:read"
PERM_CREATE: Final = "identity:create"
PERM_UPDATE: Final = "identity:update"
PERM_DELETE: Final = "identity:delete"
PERM_ROLE_MANAGE: Final = "identity:role:manage"
# 三层授权：团队 / 小组 / 个人。这些点**刻意不进 BUILTIN_ROLES**——
# 内置角色模板会被 `sync_new_permissions` 在启动时自动补齐，写进去等于
# 给 editor / viewer 也发一张「管理组织」的票。
PERM_TEAM_MANAGE: Final = "identity:team:manage"
PERM_GROUP_READ: Final = "identity:group:read"
PERM_GROUP_CREATE: Final = "identity:group:create"
PERM_GROUP_UPDATE: Final = "identity:group:update"
PERM_GROUP_DELETE: Final = "identity:group:delete"
PERM_GROUP_MEMBER_MANAGE: Final = "identity:group:member:manage"
PERM_GROUP_PERMISSION_MANAGE: Final = "identity:group:permission:manage"
PERM_USER_PERMISSION_MANAGE: Final = "identity:user:permission:manage"

SPECS: tuple[PermissionSpec, ...] = (
    PermissionSpec(PERM_READ, "查看用户与角色", group="组织管理"),
    PermissionSpec(PERM_CREATE, "创建用户", group="组织管理"),
    PermissionSpec(
        PERM_UPDATE,
        "编辑用户（含停用、重置密码）",
        group="组织管理",
        description=(
            "不含分配角色。改他人昵称/头像：目标不能是超管；"
            "重置密码、改他人邮箱、启停账号：目标权限不得高于自己（超管仍需超管）"
        ),
    ),
    PermissionSpec(PERM_DELETE, "停用用户", group="组织管理"),
    PermissionSpec(
        PERM_ROLE_MANAGE,
        "管理角色与权限点",
        group="组织管理",
        description="新建/删除角色、调整角色权限、给用户分配角色",
    ),
    PermissionSpec(
        PERM_TEAM_MANAGE,
        "设置团队基础权限",
        group="组织管理",
        description="团队 = 所有启用账号；禁止把 identity:* 或 * 放进这一层",
    ),
    PermissionSpec(PERM_GROUP_READ, "查看小组与成员", group="组织管理"),
    PermissionSpec(PERM_GROUP_CREATE, "新建小组", group="组织管理"),
    PermissionSpec(PERM_GROUP_UPDATE, "编辑小组资料", group="组织管理"),
    PermissionSpec(PERM_GROUP_DELETE, "删除小组", group="组织管理"),
    PermissionSpec(
        PERM_GROUP_MEMBER_MANAGE,
        "分配小组成员",
        group="组织管理",
        description="整组替换成员；一人可属多组",
    ),
    PermissionSpec(
        PERM_GROUP_PERMISSION_MANAGE,
        "设置小组整体权限",
        group="组织管理",
        description="该组所有成员自动获得；个人权限只增不减",
    ),
    PermissionSpec(
        PERM_USER_PERMISSION_MANAGE,
        "设置个人权限",
        group="组织管理",
        description="单独授予，与团队/小组/角色取并集；不能授出自己没有的权限",
    ),
)

# ---------- 内置角色 ----------
# 首次启动时写入；之后**不会**覆盖已有角色的权限配置
# （管理员手工调过的权限不该被重启抹掉；新增权限点请在角色界面显式勾选）。
SUPERUSER_ROLE: Final = "admin"


@dataclass(frozen=True, slots=True)
class BuiltinRole:
    code: str
    name: str
    description: str
    is_superuser: bool = False
    sort_order: int = 0
    permissions: tuple[str, ...] = field(default_factory=tuple)


BUILTIN_ROLES: tuple[BuiltinRole, ...] = (
    BuiltinRole(
        code=SUPERUSER_ROLE,
        name="超级管理员",
        description="拥有全部权限（含未上线模块），不可删除",
        is_superuser=True,
        sort_order=0,
    ),
    BuiltinRole(
        code="editor",
        name="内容编辑",
        description="内容生产全量 + 查看他人任务；不能管理用户与角色",
        sort_order=10,
        permissions=(
            "catalog:read",
            "task:*",
            # 资产库 v2 的码（v1 的 `asset:*` 随 `assets` 表于 2026-11-13 下线）。
            # 一一对应：能看、能传、能改、能删、能发起共享。
            "library:read",
            "library:create",
            "library:update",
            "library:delete",
            "library:share",
            "image:*",
            "template:read",
            # 模板替换的「提交生成」：editor 本来就持有 task:* / image:*，
            # 替换只是把同一件事（产内容）换个入口。少了这一条，非超管点提交
            # 会拿到 403——功能上线即不可用，而超管自测发现不了。
            "template:use",
            "template:review",
            "storyboard:read",
            "shot:read",
        ),
    ),
    BuiltinRole(
        code="viewer",
        name="只读成员",
        description="只读账号：能看自己的任务与全部目录，不能创建与修改",
        sort_order=20,
        permissions=(
            "catalog:read",
            "task:read",
            "library:read",
            "image:read",
            "template:read",
            "storyboard:read",
            "shot:read",
        ),
    ),
    BuiltinRole(
        code="asset_specialist",
        name="素材专员",
        description="维护本人参考素材、封装和发布公共对象；执行任务需另授内容编辑角色",
        sort_order=15,
        permissions=(
            "library:read",
            "library:create",
            "library:update",
            "library:delete",
            "library:share",
            "template:read",
            "template:pack:manage",
            "template:pack:publish",
        ),
    ),
)

# ---------- 三层权限授予（团队 / 小组 / 个人） ----------
# `permission_grants.scope` 的取值。团队是「所有启用账号」，没有成员表，
# 所以它的 scope_id 是一个固定常量，而不是某个实体 id。
SCOPE_TEAM: Final = "team"
SCOPE_GROUP: Final = "group"
SCOPE_USER: Final = "user"
TEAM_SCOPE_ID: Final = "__all__"
SCOPES: Final = (SCOPE_TEAM, SCOPE_GROUP, SCOPE_USER)

# 团队基础权限的初始模板。**直接取 viewer 的默认权限**，不另抄一份：
# 两边各写一遍，迟早会出现「只读成员能看的东西，团队基础权限却没给」。
# 只在首次启动时播种（见 `IdentityService.ensure_team_permissions`），
# 之后管理员在界面上改过的配置不会被重启覆盖。
TEAM_BASE_PERMISSIONS: Final[tuple[str, ...]] = tuple(
    next(role for role in BUILTIN_ROLES if role.code == "viewer").permissions
)

# 团队层是「所有人」：放进管理类权限等于给全站发管理权，放进 `*` 等于
# 把所有人都变成超管。写接口要挡，常量单独列出来是为了让守卫与文档同源。
TEAM_FORBIDDEN_CODES: Final = ("*", "identity:*")
TEAM_FORBIDDEN_PREFIXES: Final = ("identity:",)

# 口令策略（长度 / 纯数字 / 重复字符）不在本文件：它不属于「权限点声明」，
# 而且 CLI 与 API 要共用同一份规则，统一放在 `policy.py`。

__all__ = [
    "BUILTIN_ROLES",
    "BuiltinRole",
    "PERM_CREATE",
    "PERM_DELETE",
    "PERM_GROUP_CREATE",
    "PERM_GROUP_DELETE",
    "PERM_GROUP_MEMBER_MANAGE",
    "PERM_GROUP_PERMISSION_MANAGE",
    "PERM_GROUP_READ",
    "PERM_GROUP_UPDATE",
    "PERM_READ",
    "PERM_ROLE_MANAGE",
    "PERM_TEAM_MANAGE",
    "PERM_UPDATE",
    "PERM_USER_PERMISSION_MANAGE",
    "SCOPES",
    "SCOPE_GROUP",
    "SCOPE_TEAM",
    "SCOPE_USER",
    "SPECS",
    "SUPERUSER_ROLE",
    "TEAM_BASE_PERMISSIONS",
    "TEAM_FORBIDDEN_CODES",
    "TEAM_FORBIDDEN_PREFIXES",
    "TEAM_SCOPE_ID",
]
