"""身份模块路由：登录、个人中心、用户与角色管理、权限目录。

权限分档：
- `/login` 匿名可访问（它就是要换令牌的地方，不能要求令牌）
- `/me`、`/me/**` 只要登录（不需要权限点——否则新用户连自己是谁都查不到）
- `/users/**`、`/roles/**`、`/permissions` 按 `identity:*` 判定
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Header, Query, Request, status

from api.deps import ActorDep, PageDep
from core.exceptions import Unauthorized
from core.net import client_ip
from core.response import ApiResponse, Page
from modules.identity.dependencies import (
    IdentityCreateDep,
    IdentityDeleteDep,
    IdentityGroupCreateDep,
    IdentityGroupDeleteDep,
    IdentityGroupMemberManageDep,
    IdentityGroupPermissionManageDep,
    IdentityGroupReadDep,
    IdentityGroupUpdateDep,
    IdentityReadDep,
    IdentityRoleManageDep,
    IdentityServiceDep,
    IdentityTeamManageDep,
    IdentityUpdateDep,
    IdentityUserPermissionManageDep,
)
from modules.identity.schema import (
    EffectivePermissionsOut,
    GroupCreateIn,
    GroupMembersIn,
    GroupOut,
    GroupUpdateIn,
    LoginEventListOut,
    LoginIn,
    MeOut,
    PasswordChangeIn,
    PasswordPolicyOut,
    PermissionCatalogOut,
    PermissionsIn,
    ProfileUpdateIn,
    RefreshIn,
    ResetPasswordIn,
    RevokedSessionsOut,
    RoleCreateIn,
    RoleOut,
    RolePermissionsIn,
    RoleUpdateIn,
    SessionOut,
    TokenOut,
    UserCreateIn,
    UserOut,
    UserRolesIn,
    UserUpdateIn,
)

router = APIRouter(prefix="/identity", tags=["身份"])

# 两个「查登录记录」端点是同一语义，limit 的口径必须一致（R10-12）：
# 一处 le=100/default=10、另一处 le=200/default=20，前端迟早会按错的那份写。
LOGIN_LIMIT_DEFAULT = 10
LOGIN_LIMIT_MAX = 100


# --------------------------------------------------------------------------- #
# 认证
# --------------------------------------------------------------------------- #
@router.post("/login", summary="登录换取令牌")
async def login(
    payload: LoginIn, service: IdentityServiceDep, request: Request
) -> ApiResponse[TokenOut]:
    # 客户端 IP 只用于限流计数。取法见 core/net：只有直连对端是可信代理时才读
    # X-Forwarded-For，否则一律用 request.client（伪造这个头不能换掉限流键）。
    user = await service.authenticate(
        payload.username,
        payload.password,
        client_ip=client_ip(request),
        user_agent=request.headers.get("user-agent", ""),
    )
    identity = await service.resolve_identity(user.id)
    if identity is None:  # pragma: no cover - authenticate 已确认用户存在
        raise Unauthorized("账号状态异常，请重试")
    return ApiResponse.ok(
        await service.build_token(
            user,
            identity,
            # UA / IP 落进会话表：前端「我的登录设备」靠它区分，安全排查也靠它
            user_agent=request.headers.get("user-agent", ""),
            ip=client_ip(request),
        )
    )


@router.get("/me", summary="当前用户（含角色与权限码）")
async def me(service: IdentityServiceDep, actor: ActorDep) -> ApiResponse[MeOut]:
    return ApiResponse.ok(await service.me(actor))


@router.patch("/me", summary="修改自己的资料")
async def update_me(
    payload: ProfileUpdateIn, service: IdentityServiceDep, actor: ActorDep
) -> ApiResponse[MeOut]:
    return ApiResponse.ok(await service.update_me(actor, payload))


@router.post("/me/password", summary="修改自己的密码")
async def change_password(
    payload: PasswordChangeIn, service: IdentityServiceDep, actor: ActorDep
) -> ApiResponse[None]:
    await service.change_password(actor.id, payload)
    return ApiResponse.ok(None, "密码已更新")


@router.post("/refresh", summary="用刷新令牌换取新令牌")
async def refresh(
    payload: RefreshIn, service: IdentityServiceDep, request: Request
) -> ApiResponse[TokenOut]:
    return ApiResponse.ok(
        await service.refresh(
            payload.refresh_token,
            user_agent=request.headers.get("user-agent", ""),
            ip=client_ip(request),
        )
    )


@router.post("/logout", summary="退出登录（作废当前会话，幂等）")
async def logout(
    service: IdentityServiceDep,
    authorization: Annotated[str | None, Header()] = None,
) -> ApiResponse[None]:
    # 刻意**不依赖 get_actor**：令牌已经失效时（重复点退出、令牌过期）
    # 也要正常返回 200，否则用户第二次点退出会看到一条报错
    await service.logout_by_token(authorization)
    return ApiResponse.ok(None, "已退出登录")


@router.get("/password-policy", summary="口令策略（匿名）")
async def password_policy(service: IdentityServiceDep) -> ApiResponse[PasswordPolicyOut]:
    """登录页与「重置密码」表单要在未登录状态下拿到策略，所以不挂鉴权依赖。

    只暴露规则，不暴露任何账号信息。
    """
    return ApiResponse.ok(service.password_policy())


# --------------------------------------------------------------------------- #
# 我的会话 / 我的登录记录
# --------------------------------------------------------------------------- #
@router.get("/me/sessions", summary="我的登录会话")
async def list_sessions(
    service: IdentityServiceDep, actor: ActorDep
) -> ApiResponse[list[SessionOut]]:
    return ApiResponse.ok(await service.list_sessions(actor))


@router.delete("/me/sessions/{session_id}", summary="踢掉我的一条会话")
async def revoke_session(
    session_id: str, service: IdentityServiceDep, actor: ActorDep
) -> ApiResponse[None]:
    await service.revoke_session(actor, session_id)
    return ApiResponse.ok(None, "该设备已下线")


@router.delete("/me/sessions", summary="退出所有其他设备（保留当前）")
async def revoke_other_sessions(
    service: IdentityServiceDep, actor: ActorDep
) -> ApiResponse[RevokedSessionsOut]:
    count = await service.revoke_other_sessions(actor)
    return ApiResponse.ok(
        RevokedSessionsOut(revoked=count), f"已下线 {count} 个其他会话"
    )


@router.get("/me/logins", summary="我的登录记录")
async def my_logins(
    service: IdentityServiceDep,
    actor: ActorDep,
    limit: Annotated[
        int, Query(ge=1, le=LOGIN_LIMIT_MAX, description="返回条数")
    ] = LOGIN_LIMIT_DEFAULT,
) -> ApiResponse[LoginEventListOut]:
    return ApiResponse.ok(await service.my_logins(actor, limit))


# --------------------------------------------------------------------------- #
# 用户
# --------------------------------------------------------------------------- #
@router.get("/users", summary="用户列表")
async def list_users(
    service: IdentityServiceDep,
    actor: IdentityReadDep,
    params: PageDep,
    keyword: Annotated[
        str, Query(max_length=64, description="匹配用户名/昵称/邮箱/手机号/职务")
    ] = "",
    name: Annotated[str, Query(max_length=64, description="按姓名（昵称）模糊筛选")] = "",
    username: Annotated[str, Query(max_length=64, description="按账号模糊筛选")] = "",
    phone: Annotated[str, Query(max_length=32, description="按手机号模糊筛选")] = "",
    is_active: Annotated[bool | None, Query(alias="isActive")] = None,
    role_id: Annotated[str | None, Query(alias="roleId")] = None,
    group_id: Annotated[str | None, Query(alias="groupId", description="只看该小组成员")] = None,
) -> ApiResponse[Page[UserOut]]:
    page = await service.list_users(
        params,
        keyword=keyword,
        name=name,
        username=username,
        phone=phone,
        is_active=is_active,
        role_id=role_id,
        group_id=group_id,
    )
    return ApiResponse.ok(page)


@router.post(
    "/users", summary="创建用户", status_code=status.HTTP_201_CREATED
)
async def create_user(
    payload: UserCreateIn, service: IdentityServiceDep, actor: IdentityCreateDep
) -> ApiResponse[UserOut]:
    return ApiResponse.ok(await service.create_user(payload, actor))


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
    await service.reset_password(user_id, payload.new_password, actor)
    return ApiResponse.ok(None, "密码已重置")


@router.post("/users/{user_id}/revoke-sessions", summary="踢下线（吊销该用户全部会话）")
async def revoke_user_sessions(
    user_id: str, service: IdentityServiceDep, actor: IdentityUpdateDep
) -> ApiResponse[RevokedSessionsOut]:
    count = await service.revoke_user_sessions(user_id, actor)
    return ApiResponse.ok(RevokedSessionsOut(revoked=count), f"已吊销 {count} 个会话")


@router.get("/users/{user_id}/logins", summary="某账号的登录记录")
async def user_logins(
    user_id: str,
    service: IdentityServiceDep,
    actor: IdentityReadDep,
    limit: Annotated[
        int, Query(ge=1, le=LOGIN_LIMIT_MAX, description="返回条数")
    ] = LOGIN_LIMIT_DEFAULT,
) -> ApiResponse[LoginEventListOut]:
    # actor 要传进 service：读审计也要过「目标不高于自己」的守卫（R10-05）
    return ApiResponse.ok(await service.logins_of(user_id, limit, actor))


@router.delete("/users/{user_id}", summary="停用用户（保留历史归属）")
async def deactivate_user(
    user_id: str, service: IdentityServiceDep, actor: IdentityDeleteDep
) -> ApiResponse[UserOut]:
    await service.deactivate_user(user_id, actor)
    return ApiResponse.ok(await service.get_user(user_id), "用户已停用")


# --------------------------------------------------------------------------- #
# 三层授权：团队 / 小组 / 个人
# --------------------------------------------------------------------------- #
@router.get("/team/permissions", summary="团队基础权限（全体启用账号）")
async def team_permissions(
    service: IdentityServiceDep, actor: IdentityReadDep
) -> ApiResponse[list[str]]:
    return ApiResponse.ok(await service.team_permissions())


@router.put("/team/permissions", summary="设置团队基础权限（整组替换）")
async def set_team_permissions(
    payload: PermissionsIn,
    service: IdentityServiceDep,
    actor: IdentityTeamManageDep,
) -> ApiResponse[list[str]]:
    return ApiResponse.ok(await service.set_team_permissions(payload.permissions, actor))


@router.get("/groups", summary="小组列表（含成员与权限）")
async def list_groups(
    service: IdentityServiceDep, actor: IdentityGroupReadDep
) -> ApiResponse[list[GroupOut]]:
    return ApiResponse.ok(await service.list_groups())


@router.post(
    "/groups", summary="新建小组", status_code=status.HTTP_201_CREATED
)
async def create_group(
    payload: GroupCreateIn,
    service: IdentityServiceDep,
    actor: IdentityGroupCreateDep,
) -> ApiResponse[GroupOut]:
    return ApiResponse.ok(await service.create_group(payload, actor))


@router.get("/groups/{group_id}", summary="小组详情")
async def get_group(
    group_id: str, service: IdentityServiceDep, actor: IdentityGroupReadDep
) -> ApiResponse[GroupOut]:
    return ApiResponse.ok(await service.get_group(group_id))


@router.patch("/groups/{group_id}", summary="编辑小组资料")
async def update_group(
    group_id: str,
    payload: GroupUpdateIn,
    service: IdentityServiceDep,
    actor: IdentityGroupUpdateDep,
) -> ApiResponse[GroupOut]:
    return ApiResponse.ok(await service.update_group(group_id, payload, actor))


@router.delete("/groups/{group_id}", summary="删除小组（同时清成员与授权）")
async def delete_group(
    group_id: str, service: IdentityServiceDep, actor: IdentityGroupDeleteDep
) -> ApiResponse[None]:
    await service.delete_group(group_id, actor)
    return ApiResponse.ok(None, "小组已删除")


@router.get("/groups/{group_id}/members", summary="小组成员列表")
async def group_members(
    group_id: str, service: IdentityServiceDep, actor: IdentityGroupReadDep
) -> ApiResponse[list[UserOut]]:
    return ApiResponse.ok(await service.group_members(group_id))


@router.put("/groups/{group_id}/members", summary="设置小组成员（整组替换）")
async def set_group_members(
    group_id: str,
    payload: GroupMembersIn,
    service: IdentityServiceDep,
    actor: IdentityGroupMemberManageDep,
) -> ApiResponse[GroupOut]:
    return ApiResponse.ok(
        await service.set_group_members(group_id, payload.user_ids, actor)
    )


@router.put("/groups/{group_id}/permissions", summary="设置小组整体权限（整组替换）")
async def set_group_permissions(
    group_id: str,
    payload: PermissionsIn,
    service: IdentityServiceDep,
    actor: IdentityGroupPermissionManageDep,
) -> ApiResponse[GroupOut]:
    return ApiResponse.ok(
        await service.set_group_permissions(group_id, payload.permissions, actor)
    )


@router.get("/users/{user_id}/permissions", summary="用户单独授予的权限")
async def user_permissions(
    user_id: str, service: IdentityServiceDep, actor: IdentityReadDep
) -> ApiResponse[list[str]]:
    return ApiResponse.ok(await service.user_permissions(user_id))


@router.put("/users/{user_id}/permissions", summary="设置个人权限（整组替换）")
async def set_user_permissions(
    user_id: str,
    payload: PermissionsIn,
    service: IdentityServiceDep,
    actor: IdentityUserPermissionManageDep,
) -> ApiResponse[list[str]]:
    return ApiResponse.ok(
        await service.set_user_permissions(user_id, payload.permissions, actor)
    )


@router.get(
    "/users/{user_id}/effective-permissions",
    summary="用户有效权限与四层来源",
)
async def effective_permissions(
    user_id: str, service: IdentityServiceDep, actor: IdentityReadDep
) -> ApiResponse[EffectivePermissionsOut]:
    return ApiResponse.ok(await service.effective_permissions(user_id))


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
        await service.set_role_permissions(role_id, payload.permissions, actor)
    )


@router.delete("/roles/{role_id}", summary="删除角色")
async def delete_role(
    role_id: str, service: IdentityServiceDep, actor: IdentityRoleManageDep
) -> ApiResponse[None]:
    await service.delete_role(role_id, actor)
    return ApiResponse.ok(None, "角色已删除")


# --------------------------------------------------------------------------- #
# 权限目录
# --------------------------------------------------------------------------- #
@router.get("/permissions", summary="全部权限点（按分组，供角色编辑界面）")
async def list_permissions(
    service: IdentityServiceDep, actor: IdentityReadDep
) -> ApiResponse[PermissionCatalogOut]:
    return ApiResponse.ok(service.permission_catalog())
