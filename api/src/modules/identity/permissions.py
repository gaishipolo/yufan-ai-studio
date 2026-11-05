"""身份模块的权限点与内置角色模板。

权限码前缀必须是 `identity:`（`tests/test_architecture.py` 会强制），
其中 `identity:role:manage` 单独拆出来：**能改昵称不等于能给自己提权**，
角色与权限的改动必须是一个独立开关。

内置角色模板里出现的 `task:*` / `asset:*` 是**字符串常量**，不是 import——
权限点的权威仍然在各自模块的 `ModuleSpec` 里，这里只是默认角色的初始值。
用通配而不是逐个列举，是为了让别的模块新增权限点后，内置角色自动覆盖到它。
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

SPECS: tuple[PermissionSpec, ...] = (
    PermissionSpec(PERM_READ, "查看用户与角色", group="组织管理"),
    PermissionSpec(PERM_CREATE, "创建用户", group="组织管理"),
    PermissionSpec(
        PERM_UPDATE,
        "编辑用户（含停用、重置密码）",
        group="组织管理",
        description="不含分配角色；重置密码只能重置「权限不高于自己」的账号，超管仍需超管",
    ),
    PermissionSpec(PERM_DELETE, "停用用户", group="组织管理"),
    PermissionSpec(
        PERM_ROLE_MANAGE,
        "管理角色与权限点",
        group="组织管理",
        description="新建/删除角色、调整角色权限、给用户分配角色",
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
            "asset:*",
            "image:*",
            "template:read",
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
            "asset:read",
            "image:read",
            "template:read",
            "storyboard:read",
            "shot:read",
        ),
    ),
)

# 口令策略（长度 / 纯数字 / 重复字符）不在本文件：它不属于「权限点声明」，
# 而且 CLI 与 API 要共用同一份规则，统一放在 `policy.py`。

__all__ = [
    "BUILTIN_ROLES",
    "BuiltinRole",
    "PERM_CREATE",
    "PERM_DELETE",
    "PERM_READ",
    "PERM_ROLE_MANAGE",
    "PERM_UPDATE",
    "SPECS",
    "SUPERUSER_ROLE",
]
