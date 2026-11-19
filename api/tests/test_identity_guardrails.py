"""三条「权限点与路由不一致」的守卫（2026-11-19 审查的 P2）。

每条都对应一个**能直接打接口复现**的行为，而不是风格问题：

1. `identity:delete` 不是停用的闸门 —— `PATCH {"isActive": false}` 只要 `identity:update`；
2. 团队基础权限的「地板」可以被委派管理员移除 —— 全站非超管当场 403；
3. 头像地址的 scheme 校验只在前端 —— 后端允许写入任意 scheme。
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from fastapi.testclient import TestClient

API = "/api/v1/identity"
ADMIN = ("admin", "admin@12345")

#: `errors.code(403, 4)`。与 `PermissionEscalation`(40323) / `SuperuserRequired`(40322)
#: 分开：前两者是「授出了自己没有的东西」，这个只是「少了 identity:delete」。
CODE_ACCESS_SWITCH_FORBIDDEN = 40324
CODE_SUPERUSER_REQUIRED = 40322


def _login(client: TestClient, username: str, password: str) -> dict[str, str]:
    resp = client.post(f"{API}/login", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['data']['accessToken']}"}


def _admin(client: TestClient) -> dict[str, str]:
    return _login(client, *ADMIN)


def _suffix() -> str:
    return uuid.uuid4().hex[:6]


def _make_role(client: TestClient, admin: dict[str, str], perms: list[str]) -> dict:
    # 页面门控（core.pages）：identity 的接口只有在「用户与权限」这一页被授予时
    # 才有效。测试里造的是「只授接口、不授页面」的账号，直接加权限会被门控关掉，
    # 于是所有断言都会变成「一上来就 403」——那不是这些用例要测的东西。
    # 门控本身由 tests/test_pages.py 专门测。
    perms = list(perms)
    if "*" not in perms and any(code.startswith("identity:") for code in perms):
        perms.append("page:user-access")
    code = f"guard_role_{_suffix()}"
    resp = client.post(
        f"{API}/roles", json={"code": code, "name": code, "permissions": perms}, headers=admin
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["data"]


def _make_user(
    client: TestClient, admin: dict[str, str], perms: list[str]
) -> tuple[dict, dict[str, str]]:
    role = _make_role(client, admin, perms)
    username = f"guard_{_suffix()}"
    resp = client.post(
        f"{API}/users",
        json={
            "username": username,
            "password": "pass-12345",
            "displayName": username,
            "roleIds": [role["id"]],
        },
        headers=admin,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["data"], _login(client, username, "pass-12345")


# --------------------------------------------------------------------------- #
# 1. 停用 / 启用：identity:delete 才是闸门
# --------------------------------------------------------------------------- #
def test_patch_is_active_requires_the_delete_permission(client: TestClient) -> None:
    """「能编辑资料」不该顺带等于「能停用账号」。

    停用与启用是同一个能力的两端，两端都必须挂 `identity:delete`；
    否则管理员在界面上勾了 `identity:update` 却没勾 `identity:delete` 时，
    他以为设的是「只能改资料」，实际拿到的是「可以关掉任何人」。
    """
    admin = _admin(client)
    target, _ = _make_user(client, admin, [])  # 权限集为空：先过「不高于自己」那道闸
    _, updater = _make_user(client, admin, ["identity:read", "identity:update"])

    denied = client.patch(
        f"{API}/users/{target['id']}", json={"isActive": False}, headers=updater
    )
    assert denied.status_code == 403
    assert denied.json()["code"] == CODE_ACCESS_SWITCH_FORBIDDEN

    # 同一个请求里不带 isActive 就照旧能改——证明被挡的是那一个动作，不是这个人
    still_allowed = client.patch(
        f"{API}/users/{target['id']}",
        json={"displayName": "改个昵称"},
        headers=updater,
    )
    assert still_allowed.status_code == 200, still_allowed.text

    # 启用方向同样要挡：安全事件后「先停用、再启用」，反向放行等于前功尽弃
    assert (
        client.patch(
            f"{API}/users/{target['id']}", json={"isActive": False}, headers=admin
        ).status_code
        == 200
    )
    restore_denied = client.patch(
        f"{API}/users/{target['id']}", json={"isActive": True}, headers=updater
    )
    assert restore_denied.status_code == 403
    assert restore_denied.json()["code"] == CODE_ACCESS_SWITCH_FORBIDDEN


# --------------------------------------------------------------------------- #
# 2. 团队基础权限的地板
# --------------------------------------------------------------------------- #
def test_team_floor_cannot_be_removed_by_a_delegate(client: TestClient) -> None:
    """委派管理员可以调团队权限，但不能把「登录后还看得见东西」的底线拿掉。

    背景：`_guard_layer_not_above` 在团队层上恒真（每个人的有效权限本来就把团队层
    并进去了），所以它拦不住「整组清空」。真发生时的后果不是某个人 403，
    而是**全站非超管**一起 403，且（在补审计之前）查不出是谁干的。
    """
    admin = _admin(client)
    base = client.get(f"{API}/team/permissions", headers=admin).json()["data"]
    assert {"catalog:read", "task:read", "library:read"} <= set(base)

    _, delegate = _make_user(
        client, admin, ["identity:read", "identity:team:manage"]
    )
    try:
        floor_removed = client.put(
            f"{API}/team/permissions",
            json={"permissions": [c for c in base if c != "task:read"]},
            headers=delegate,
        )
        assert floor_removed.status_code == 403, floor_removed.text
        assert floor_removed.json()["code"] == CODE_SUPERUSER_REQUIRED

        emptied = client.put(
            f"{API}/team/permissions", json={"permissions": []}, headers=delegate
        )
        assert emptied.status_code == 403

        # 非地板项依然可以委派：守卫必须窄，「一律不许改」等于把功能关掉
        non_floor = [c for c in base if c != "image:read"]
        allowed = client.put(
            f"{API}/team/permissions", json={"permissions": non_floor}, headers=delegate
        )
        assert allowed.status_code == 200, allowed.text
        assert "image:read" not in allowed.json()["data"]
    finally:
        restored = client.put(
            f"{API}/team/permissions", json={"permissions": base}, headers=admin
        )
        assert restored.status_code == 200


# --------------------------------------------------------------------------- #
# 3. 头像地址：scheme 校验在服务层
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "bad",
    ["javascript:alert(1)", "data:text/html;base64,PHNjcmlwdD4=", "file:///C:/x.png", "vbscript:x"],
)
def test_avatar_url_rejects_non_http_schemes(client: TestClient, bad: str) -> None:
    """前端那份 `AVATAR_URL_PATTERN` 是即时反馈，不是安全边界。

    今天渲染走 `<img src>`，`javascript:` 不会执行；但导出、审批、邮件模板
    任何一处把它当链接用，就是一个存储型 XSS。写入口堵掉最便宜。
    """
    headers = _admin(client)
    resp = client.patch(f"{API}/me", json={"avatarUrl": bad}, headers=headers)
    assert resp.status_code == 422, f"{bad} 不该被接受"


def test_avatar_url_accepts_http_and_site_relative(client: TestClient) -> None:
    headers = _admin(client)
    for good in ("https://example.com/a.png", "/static/avatars/a.png"):
        resp = client.patch(f"{API}/me", json={"avatarUrl": good}, headers=headers)
        assert resp.status_code == 200, resp.text
        assert resp.json()["data"]["avatarUrl"] == good
    cleared = client.patch(f"{API}/me", json={"avatarUrl": None}, headers=headers)
    assert cleared.status_code == 200
    assert cleared.json()["data"]["avatarUrl"] is None


# --------------------------------------------------------------------------- #
# 4. 「超管」只有一套口径
# --------------------------------------------------------------------------- #
def test_superuser_count_covers_star_grants(client: TestClient) -> None:
    """`count_active_superusers` 必须与 `resolve_identity` 同源。

    只数 `is_superuser` 角色的话，「给某人单独授 `*`」这条合法操作会让计数漏人：
    少算时「最后一个超管」判定会误判成 0，把一个正常的改角色请求拒成 `LastSuperuser`。
    """
    admin = _admin(client)
    user, _ = _make_user(client, admin, [])
    granted = client.put(
        f"{API}/users/{user['id']}/permissions", json={"permissions": ["*"]}, headers=admin
    )
    assert granted.status_code == 200, granted.text

    async def count() -> set[str]:
        from db.session import get_session_factory
        from modules.identity.repository import UserRepository

        async with get_session_factory()() as session:
            return await UserRepository(session).active_superuser_ids()

    ids = asyncio.run(count())
    assert user["id"] in ids, "个人授权 `*` 的账号就是超管等价，计数不能漏"


# --------------------------------------------------------------------------- #
# 5. 下线码把「重新保存」锁死：启动时要清掉
# --------------------------------------------------------------------------- #
def test_stale_permission_codes_block_saving_until_pruned(client: TestClient) -> None:
    """真实库实测出来的死锁：库里躺着一个已下线的码，那一层就永远存不回去。

    `set_grants` 是整组替换、写入口拒绝未知码，而前端会把「当前码」原样提交 ——
    于是 `asset:read`（v1 素材库下线）或 `template:read`（改名前）这类残留
    让团队层 400，而 doctor 的提示恰恰是「重新保存一次即可清理」。
    """

    async def setup() -> None:
        from db.session import get_session_factory
        from modules.identity.models import PermissionGrant
        from modules.identity.permissions import SCOPE_TEAM, TEAM_SCOPE_ID

        async with get_session_factory()() as session:
            session.add(
                PermissionGrant(
                    scope=SCOPE_TEAM, scope_id=TEAM_SCOPE_ID, permission="asset:read"
                )
            )
            await session.commit()

    async def prune() -> dict[str, list[str]]:
        from db.session import get_session_factory
        from modules.identity.service import IdentityService

        async with get_session_factory()() as session:
            return await IdentityService(session).prune_stale_permissions()

    admin = _admin(client)
    base = client.get(f"{API}/team/permissions", headers=admin).json()["data"]
    asyncio.run(setup())
    try:
        polluted = client.get(f"{API}/team/permissions", headers=admin).json()["data"]
        assert "asset:read" in polluted

        deadlock = client.put(
            f"{API}/team/permissions", json={"permissions": polluted}, headers=admin
        )
        assert deadlock.status_code == 400, "残留码会把整组保存挡成 400"
        assert deadlock.json()["code"] == 40022

        removed = asyncio.run(prune())
        assert removed["layers"] == ["asset:read"]

        clean = client.get(f"{API}/team/permissions", headers=admin).json()["data"]
        assert "asset:read" not in clean
        saved = client.put(
            f"{API}/team/permissions", json={"permissions": clean}, headers=admin
        )
        assert saved.status_code == 200, saved.text
        assert asyncio.run(prune()) == {"layers": [], "roles": []}  # 幂等
    finally:
        client.put(f"{API}/team/permissions", json={"permissions": base}, headers=admin)
