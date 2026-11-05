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
    PERM_READ,
    PERM_ROLE_MANAGE,
    PERM_UPDATE,
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
