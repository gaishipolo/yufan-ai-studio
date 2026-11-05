"""身份模块路由：登录、个人中心、用户与角色管理、权限目录。

权限分档：
- `/login` 匿名可访问（它就是要换令牌的地方，不能要求令牌）
- `/me`、`/me/**` 只要登录（不需要权限点——否则新用户连自己是谁都查不到）
- `/users/**`、`/roles/**`、`/permissions` 按 `identity:*` 判定
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query, status

from api.deps import ActorDep, PageDep
from core.exceptions import Unauthorized
from core.response import ApiResponse, Page
from modules.identity.dependencies import (
    IdentityCreateDep,
    IdentityDeleteDep,
    IdentityReadDep,
    IdentityRoleManageDep,
    IdentityServiceDep,
    IdentityUpdateDep,
)
from modules.identity.schema import (
    LoginIn,
    MeOut,
    PasswordChangeIn,
    PermissionCatalogOut,
    ResetPasswordIn,
    RoleCreateIn,
    RoleOut,
    RolePermissionsIn,
    RoleUpdateIn,
    TokenOut,
    UserCreateIn,
    UserOut,
    UserRolesIn,
    UserUpdateIn,
)

router = APIRouter(prefix="/identity", tags=["身份"])


# --------------------------------------------------------------------------- #
# 认证
# --------------------------------------------------------------------------- #
@router.post("/login", summary="登录换取令牌")
async def login(payload: LoginIn, service: IdentityServiceDep) -> ApiResponse[TokenOut]:
    user = await service.authenticate(payload.username, payload.password)
    identity = await service.resolve_identity(user.id)
    if identity is None:  # pragma: no cover - authenticate 已确认用户存在
        raise Unauthorized("账号状态异常，请重试")
    return ApiResponse.ok(await service.build_token(user, identity))


@router.get("/me", summary="当前用户（含角色与权限码）")
async def me(service: IdentityServiceDep, actor: ActorDep) -> ApiResponse[MeOut]:
    return ApiResponse.ok(await service.me(actor.id))


@router.patch("/me", summary="修改自己的资料")
async def update_me(
    payload: UserUpdateIn, service: IdentityServiceDep, actor: ActorDep
) -> ApiResponse[MeOut]:
    return ApiResponse.ok(await service.update_me(actor.id, payload))


@router.post("/me/password", summary="修改自己的密码")
async def change_password(
    payload: PasswordChangeIn, service: IdentityServiceDep, actor: ActorDep
) -> ApiResponse[None]:
    await service.change_password(actor.id, payload)
    return ApiResponse.ok(None, "密码已更新")


# --------------------------------------------------------------------------- #
# 用户
# --------------------------------------------------------------------------- #
@router.get("/users", summary="用户列表")
async def list_users(
    service: IdentityServiceDep,
    actor: IdentityReadDep,
    params: PageDep,
    keyword: Annotated[str, Query(max_length=64, description="匹配用户名/昵称/邮箱")] = "",
    is_active: Annotated[bool | None, Query(alias="isActive")] = None,
    role_id: Annotated[str | None, Query(alias="roleId")] = None,
) -> ApiResponse[Page[UserOut]]:
    page = await service.list_users(
        params, keyword=keyword, is_active=is_active, role_id=role_id
    )
    return ApiResponse.ok(page)


@router.post(
    "/users", summary="创建用户", status_code=status.HTTP_201_CREATED
)
async def create_user(
    payload: UserCreateIn, service: IdentityServiceDep, actor: IdentityCreateDep
) -> ApiResponse[UserOut]:
    return ApiResponse.ok(await service.create_user(payload))


@router.get("/users/{user_id}", summary="用户详情")
async def get_user(
    user_id: str, service: IdentityServiceDep, actor: IdentityReadDep
) -> ApiResponse[UserOut]:
    return ApiResponse.ok(await service.get_user(user_id))


@router.patch("/users/{user_id}", summary="编辑用户（含停用）")
async def update_user(
    user_id: str,
    payload: UserUpdateIn,
    service: IdentityServiceDep,
    actor: IdentityUpdateDep,
) -> ApiResponse[UserOut]:
    return ApiResponse.ok(await service.update_user(user_id, payload, actor))


@router.put("/users/{user_id}/roles", summary="设置用户角色（整组替换）")
async def set_user_roles(
    user_id: str,
    payload: UserRolesIn,
    service: IdentityServiceDep,
    actor: IdentityRoleManageDep,
) -> ApiResponse[UserOut]:
    return ApiResponse.ok(await service.set_user_roles(user_id, payload.role_ids, actor))


@router.post("/users/{user_id}/reset-password", summary="管理员重置密码")
async def reset_password(
    user_id: str,
    payload: ResetPasswordIn,
    service: IdentityServiceDep,
    actor: IdentityUpdateDep,
) -> ApiResponse[None]:
    await service.reset_password(user_id, payload.new_password)
    return ApiResponse.ok(None, "密码已重置")


@router.delete("/users/{user_id}", summary="停用用户（保留历史归属）")
async def deactivate_user(
    user_id: str, service: IdentityServiceDep, actor: IdentityDeleteDep
) -> ApiResponse[UserOut]:
    await service.deactivate_user(user_id, actor)
    return ApiResponse.ok(await service.get_user(user_id), "用户已停用")


# --------------------------------------------------------------------------- #
# 角色
# --------------------------------------------------------------------------- #
@router.get("/roles", summary="角色列表（含权限与人数）")
async def list_roles(
    service: IdentityServiceDep, actor: IdentityReadDep
) -> ApiResponse[list[RoleOut]]:
    return ApiResponse.ok(await service.list_roles())


@router.post("/roles", summary="创建角色", status_code=status.HTTP_201_CREATED)
async def create_role(
    payload: RoleCreateIn, service: IdentityServiceDep, actor: IdentityRoleManageDep
) -> ApiResponse[RoleOut]:
    return ApiResponse.ok(await service.create_role(payload, actor))


@router.patch("/roles/{role_id}", summary="编辑角色")
async def update_role(
    role_id: str,
    payload: RoleUpdateIn,
    service: IdentityServiceDep,
    actor: IdentityRoleManageDep,
) -> ApiResponse[RoleOut]:
    return ApiResponse.ok(await service.update_role(role_id, payload, actor))


@router.put("/roles/{role_id}/permissions", summary="设置角色权限（整组替换）")
async def set_role_permissions(
    role_id: str,
    payload: RolePermissionsIn,
    service: IdentityServiceDep,
    actor: IdentityRoleManageDep,
) -> ApiResponse[RoleOut]:
    return ApiResponse.ok(
        await service.set_role_permissions(role_id, payload.permissions)
    )


@router.delete("/roles/{role_id}", summary="删除角色")
async def delete_role(
    role_id: str, service: IdentityServiceDep, actor: IdentityRoleManageDep
) -> ApiResponse[None]:
    await service.delete_role(role_id)
    return ApiResponse.ok(None, "角色已删除")


# --------------------------------------------------------------------------- #
# 权限目录
# --------------------------------------------------------------------------- #
@router.get("/permissions", summary="全部权限点（按分组，供角色编辑界面）")
async def list_permissions(
    service: IdentityServiceDep, actor: IdentityReadDep
) -> ApiResponse[PermissionCatalogOut]:
    return ApiResponse.ok(service.permission_catalog())
