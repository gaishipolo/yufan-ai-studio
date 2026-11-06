"""素材新角色的显式迁移；不在启动时改写既有角色。"""

from __future__ import annotations

from typing import Any, TypedDict

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.exceptions import Conflict
from modules.identity.models import Role, User, UserRole
from modules.identity.permissions import BUILTIN_ROLES
from modules.identity.repository import RoleRepository, UserRepository


class RoleChange(TypedDict):
    roleId: str
    code: str
    before: list[str]
    after: list[str]
    users: int


class WildcardRole(TypedDict):
    code: str
    permissions: list[str]
    users: int


class MigrationReport(TypedDict, total=False):
    """迁移快照的固定结构；`affectedUsers` 由 CLI 在预览后追加。"""

    version: int
    changes: list[RoleChange]
    wildcardRoles: list[WildcardRole]
    affectedUsers: list[dict[str, Any]]


async def preview(session: AsyncSession) -> MigrationReport:
    repo = RoleRepository(session)
    roles = list(await repo.list_all())
    permissions = await repo.permissions_for([r.id for r in roles])
    counts = await repo.user_counts([r.id for r in roles])
    changes: list[RoleChange] = []
    wildcards: list[WildcardRole] = []
    for role in roles:
        before = sorted(permissions.get(role.id, []))
        if role.code == "editor":
            after = (set(before) - {"asset:*"}) | {
                "asset:read",
                "asset:create",
                "asset:update",
                "asset:delete",
                "asset:share",
                "template:read",
                "template:use",
                "template:review",
            }
            changes.append(
                {
                    "roleId": role.id,
                    "code": role.code,
                    "before": before,
                    "after": sorted(after),
                    "users": counts.get(role.id, 0),
                }
            )
        elif not role.is_superuser and set(before) & {"asset:*", "template:*", "*"}:
            wildcards.append(
                {"code": role.code, "permissions": before, "users": counts.get(role.id, 0)}
            )
    return {"version": 1, "changes": changes, "wildcardRoles": wildcards}


async def apply(
    session: AsyncSession, report: MigrationReport, *, rollback: bool = False
) -> None:
    repo = RoleRepository(session)
    for change in report["changes"]:
        actual = sorted((await repo.permissions_for([change["roleId"]])).get(change["roleId"], []))
        expected = change["after"] if rollback else change["before"]
        if actual != expected:
            raise Conflict("角色配置已变化，拒绝覆盖；请重新预览")
    for change in report["changes"]:
        await repo.set_permissions(
            change["roleId"], change["before"] if rollback else change["after"]
        )
    if not rollback:
        # 只创建附加能力角色，不给任何账号自动分配。
        spec = next(r for r in BUILTIN_ROLES if r.code == "asset_specialist")
        if await repo.get_by_code(spec.code) is None:
            role = Role(
                code=spec.code,
                name=spec.name,
                description=spec.description,
                is_builtin=True,
                sort_order=spec.sort_order,
            )
            session.add(role)
            await session.flush()
            await repo.set_permissions(role.id, spec.permissions)
    await session.commit()


async def affected_users(
    session: AsyncSession, report: MigrationReport
) -> list[dict[str, Any]]:
    users = UserRepository(session)
    # 只返回角色绑定信息，不读取口令或会话。
    role_ids = [row["roleId"] for row in report["changes"]]
    rows = (
        (
            await session.execute(
                select(User)
                .join(UserRole, UserRole.user_id == User.id)
                .where(UserRole.role_id.in_(role_ids))
            )
        )
        .scalars()
        .all()
    )
    bindings = await users.roles_for([u.id for u in rows])
    return [
        {"username": u.username, "roles": [r.code for r in bindings.get(u.id, [])]} for u in rows
    ]
