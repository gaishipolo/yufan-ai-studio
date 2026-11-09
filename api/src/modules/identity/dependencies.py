"""身份模块的依赖装配。

权限码只写在 `permissions.py` 一处，这里全部引常量——
避免出现「常量改了、路由还挂着旧字符串」这种查半天的分叉。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends

from api.deps import SessionDep, require
from core.context import CurrentUser
from modules.identity.permissions import (
    PERM_CREATE,
    PERM_DELETE,
    PERM_GROUP_CREATE,
    PERM_GROUP_DELETE,
    PERM_GROUP_MEMBER_MANAGE,
    PERM_GROUP_PERMISSION_MANAGE,
    PERM_GROUP_READ,
    PERM_GROUP_UPDATE,
    PERM_READ,
    PERM_ROLE_MANAGE,
    PERM_TEAM_MANAGE,
    PERM_UPDATE,
    PERM_USER_PERMISSION_MANAGE,
)
from modules.identity.service import IdentityService


def get_identity_service(session: SessionDep) -> IdentityService:
    return IdentityService(session)


IdentityServiceDep = Annotated[IdentityService, Depends(get_identity_service)]

# ---------- 权限依赖 ----------
IdentityReadDep = Annotated[CurrentUser, Depends(require(PERM_READ))]
IdentityCreateDep = Annotated[CurrentUser, Depends(require(PERM_CREATE))]
IdentityUpdateDep = Annotated[CurrentUser, Depends(require(PERM_UPDATE))]
IdentityDeleteDep = Annotated[CurrentUser, Depends(require(PERM_DELETE))]
# 分配角色 / 增删角色 / 改角色权限：提权面，独立开关
IdentityRoleManageDep = Annotated[CurrentUser, Depends(require(PERM_ROLE_MANAGE))]

# 三层授权：每个动作一个独立开关。新建小组 ≠ 能给小组授权限，
# 能分配成员 ≠ 能改小组权限——和 `identity:role:manage` 拆出来是同一个理由。
IdentityTeamManageDep = Annotated[CurrentUser, Depends(require(PERM_TEAM_MANAGE))]
IdentityGroupReadDep = Annotated[CurrentUser, Depends(require(PERM_GROUP_READ))]
IdentityGroupCreateDep = Annotated[CurrentUser, Depends(require(PERM_GROUP_CREATE))]
IdentityGroupUpdateDep = Annotated[CurrentUser, Depends(require(PERM_GROUP_UPDATE))]
IdentityGroupDeleteDep = Annotated[CurrentUser, Depends(require(PERM_GROUP_DELETE))]
IdentityGroupMemberManageDep = Annotated[
    CurrentUser, Depends(require(PERM_GROUP_MEMBER_MANAGE))
]
IdentityGroupPermissionManageDep = Annotated[
    CurrentUser, Depends(require(PERM_GROUP_PERMISSION_MANAGE))
]
IdentityUserPermissionManageDep = Annotated[
    CurrentUser, Depends(require(PERM_USER_PERMISSION_MANAGE))
]
