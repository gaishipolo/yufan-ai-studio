"""身份模块接口契约测试。

重点覆盖三类「接上真实账号后才会暴露」的问题：
1. 登录/停用的判定顺序与错误码（不能把「账号存在」送出去）
2. 权限**实时生效**（令牌不再是权限快照）——这是 `api/authn.py` 存在的理由
3. 锁死红线（最后一个超管、内置角色、停用自己）

种子账号：admin/admin@12345、editor/editor@12345、viewer/viewer@12345（见 `src/seed.py`）。
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

API = "/api/v1/identity"
ADMIN = ("admin", "admin@12345")


@pytest.fixture(autouse=True)
def _reset_login_throttle() -> Iterator[None]:
    """限流计数器是进程级状态：不清零会让用例之间互相影响（顺序一变就红）。"""
    from modules.identity import throttle

    throttle.clear()
    yield
    throttle.clear()


def _login(client: TestClient, username: str, password: str) -> dict[str, str]:
    resp = client.post(f"{API}/login", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['data']['accessToken']}"}


def _admin(client: TestClient) -> dict[str, str]:
    return _login(client, *ADMIN)


def _make_role(client: TestClient, admin: dict[str, str], code: str, perms: list[str]) -> dict:
    resp = client.post(
        f"{API}/roles",
        json={"code": code, "name": code, "permissions": perms},
        headers=admin,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["data"]


def _make_user(
    client: TestClient, admin: dict[str, str], code: str, perms: list[str]
) -> tuple[dict, dict[str, str], dict]:
    """建「专属角色 + 用户」并登录，返回 (用户, 请求头, 角色)。"""
    role = _make_role(client, admin, code, perms)
    resp = client.post(
        f"{API}/users",
        json={
            "username": code,
            "password": "pass-12345",
            "displayName": code,
            "roleIds": [role["id"]],
        },
        headers=admin,
    )
    assert resp.status_code == 201, resp.text
    user = resp.json()["data"]
    return user, _login(client, code, "pass-12345"), role


# --------------------------------------------------------------------------- #
# 登录
# --------------------------------------------------------------------------- #
def test_login_returns_token_and_permissions(client: TestClient) -> None:
    resp = client.post(f"{API}/login", json={"username": "admin", "password": "admin@12345"})
    data = resp.json()["data"]

    assert resp.status_code == 200
    assert data["tokenType"] == "Bearer"
    assert data["expiresIn"] > 0
    assert data["user"]["username"] == "admin"
    assert data["user"]["displayName"] == "老大"
    # 超管是 `*` 而不是逐条列出：新模块的权限点自动覆盖
    assert "*" in data["user"]["permissions"]
    assert "passwordHash" not in data["user"]


def test_login_error_does_not_leak_account_existence(client: TestClient) -> None:
    wrong = client.post(f"{API}/login", json={"username": "admin", "password": "bad-pass"})
    unknown = client.post(
        f"{API}/login", json={"username": "no-such-user", "password": "bad-pass"}
    )

    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json()["code"] == unknown.json()["code"] == 40121
    # 文案必须一致，否则「响应消息」就是免费的账号枚举接口
    assert wrong.json()["message"] == unknown.json()["message"]


def test_login_username_is_case_insensitive(client: TestClient) -> None:
    resp = client.post(f"{API}/login", json={"username": "ADMIN ", "password": "admin@12345"})
    assert resp.status_code == 200


def test_disabled_account_cannot_login(client: TestClient) -> None:
    admin = _admin(client)
    user, _, _ = _make_user(client, admin, "idle_user", ["task:read"])

    client.delete(f"{API}/users/{user['id']}", headers=admin)
    resp = client.post(
        f"{API}/login", json={"username": "idle_user", "password": "pass-12345"}
    )
    assert resp.status_code == 403
    assert resp.json()["code"] == 40321


def test_short_password_rejected_at_creation(client: TestClient) -> None:
    admin = _admin(client)
    resp = client.post(
        f"{API}/users",
        json={"username": "short_pwd", "password": "1234567"},
        headers=admin,
    )
    assert resp.status_code == 422


def test_deactivated_user_can_be_reactivated(client: TestClient) -> None:
    """停用是可逆的：界面上「启用」开关必须真的能把人放回来。"""
    admin = _admin(client)
    user, headers, _ = _make_user(client, admin, "comeback", ["task:read"])

    client.delete(f"{API}/users/{user['id']}", headers=admin)
    assert client.get(f"{API}/me", headers=headers).status_code == 401

    resp = client.patch(
        f"{API}/users/{user['id']}", json={"isActive": True}, headers=admin
    )
    assert resp.status_code == 200
    assert resp.json()["data"]["isActive"] is True
    # 恢复后旧令牌直接又能用（解析器每次请求都查库）
    assert client.get(f"{API}/me", headers=headers).status_code == 200


# --------------------------------------------------------------------------- #
# 权限实时生效（主体解析器）
# --------------------------------------------------------------------------- #
def test_deactivation_takes_effect_on_existing_token(client: TestClient, api: str) -> None:
    """停用后旧令牌必须立刻失效。

    改造前权限来自令牌快照，被停用的人还能用满 TTL 继续调接口、继续产生费用。
    这条用例就是 `api/authn.py` 的存在理由。
    """
    admin = _admin(client)
    user, headers, _ = _make_user(client, admin, "victim", ["task:read"])
    assert client.get(f"{api}/tasks", headers=headers).status_code == 200

    client.delete(f"{API}/users/{user['id']}", headers=admin)

    after = client.get(f"{api}/tasks", headers=headers)
    assert after.status_code == 401
    assert after.json()["code"] == 40100


def test_role_change_applies_without_relogin(client: TestClient) -> None:
    """改角色权限后，同一个令牌的下一个请求就要用新权限。"""
    admin = _admin(client)
    _, headers, role = _make_user(client, admin, "promo", ["task:read"])

    before = client.get(f"{API}/me", headers=headers).json()["data"]
    assert "task:create" not in before["permissions"]

    client.put(
        f"{API}/roles/{role['id']}/permissions",
        json={"permissions": ["task:read", "task:create"]},
        headers=admin,
    )

    after = client.get(f"{API}/me", headers=headers).json()["data"]
    assert "task:create" in after["permissions"]


def test_anonymous_demo_account_still_works_in_test_env(client: TestClient) -> None:
    """非生产环境的匿名回退是本地联调的入口，不能被账号体系误伤。"""
    assert client.get(f"{API}/me").status_code == 200


# --------------------------------------------------------------------------- #
# 权限边界
# --------------------------------------------------------------------------- #
def test_user_management_denied_without_permission(client: TestClient) -> None:
    admin = _admin(client)
    _, headers, _ = _make_user(client, admin, "no_identity", ["task:read"])

    resp = client.get(f"{API}/users", headers=headers)
    assert resp.status_code == 403
    assert resp.json()["code"] == 40300


def test_role_management_needs_its_own_permission(client: TestClient) -> None:
    """能改用户资料 ≠ 能改角色：`identity:update` 不能创建角色。"""
    admin = _admin(client)
    _, headers, _ = _make_user(
        client, admin, "user_editor", ["identity:read", "identity:update"]
    )

    resp = client.post(
        f"{API}/roles", json={"code": "sneaky", "name": "sneaky"}, headers=headers
    )
    assert resp.status_code == 403


def test_only_superuser_can_grant_superuser(client: TestClient) -> None:
    """有 `identity:role:manage` 但不是超管的人，不能给自己发超管。"""
    admin = _admin(client)
    _, headers, role = _make_user(client, admin, "role_admin", ["identity:role:manage"])

    resp = client.patch(
        f"{API}/roles/{role['id']}", json={"isSuperuser": True}, headers=headers
    )
    assert resp.status_code == 403
    assert resp.json()["code"] == 40322


# --------------------------------------------------------------------------- #
# 锁死红线
# --------------------------------------------------------------------------- #
def test_cannot_deactivate_self(client: TestClient) -> None:
    admin = _admin(client)
    me = client.get(f"{API}/me", headers=admin).json()["data"]

    resp = client.delete(f"{API}/users/{me['id']}", headers=admin)
    assert resp.status_code == 409
    assert resp.json()["code"] == 40925


def test_cannot_remove_the_last_superuser(client: TestClient) -> None:
    """把 admin 角色的超管标记清掉 = 全系统没有超管，必须拒绝。"""
    admin = _admin(client)
    roles = client.get(f"{API}/roles", headers=admin).json()["data"]
    admin_role = next(role for role in roles if role["code"] == "admin")

    resp = client.patch(
        f"{API}/roles/{admin_role['id']}", json={"isSuperuser": False}, headers=admin
    )
    assert resp.status_code == 409
    assert resp.json()["code"] == 40926
    # 失败后必须还能正常用（事务已回滚）
    assert client.get(f"{API}/me", headers=admin).status_code == 200


def test_builtin_role_cannot_be_deleted(client: TestClient) -> None:
    admin = _admin(client)
    roles = client.get(f"{API}/roles", headers=admin).json()["data"]
    builtin = next(role for role in roles if role["code"] == "editor")

    resp = client.delete(f"{API}/roles/{builtin['id']}", headers=admin)
    assert resp.status_code == 409
    assert resp.json()["code"] == 40923


def test_role_in_use_cannot_be_deleted(client: TestClient) -> None:
    admin = _admin(client)
    _, _, role = _make_user(client, admin, "occupied_role", ["task:read"])

    resp = client.delete(f"{API}/roles/{role['id']}", headers=admin)
    assert resp.status_code == 409
    assert resp.json()["code"] == 40924


def test_cannot_demote_the_only_superuser_user(client: TestClient) -> None:
    """绕过角色、直接改人：把唯一超管换成普通角色也不行。"""
    admin = _admin(client)
    me = client.get(f"{API}/me", headers=admin).json()["data"]
    roles = client.get(f"{API}/roles", headers=admin).json()["data"]
    viewer = next(role for role in roles if role["code"] == "viewer")

    resp = client.put(
        f"{API}/users/{me['id']}/roles",
        json={"roleIds": [viewer["id"]]},
        headers=admin,
    )
    assert resp.status_code == 409
    assert resp.json()["code"] == 40926


# --------------------------------------------------------------------------- #
# 权限点校验
# --------------------------------------------------------------------------- #
def test_unknown_permission_is_rejected(client: TestClient) -> None:
    admin = _admin(client)
    resp = client.post(
        f"{API}/roles",
        json={"code": "typo_role", "name": "typo", "permissions": ["task:nope"]},
        headers=admin,
    )
    assert resp.status_code == 400
    assert resp.json()["code"] == 40022
    assert resp.json()["details"]["invalid"] == ["task:nope"]


def test_wildcard_permission_is_accepted_and_not_orphaned(client: TestClient) -> None:
    """`task:*` 是「该资源未来新增的动作也授予」的合法意图，不能当成拼错的垃圾。"""
    admin = _admin(client)
    role = _make_role(client, admin, "wildcard_role", ["task:*"])

    assert role["permissions"] == ["task:*"]
    assert role["orphanedPermissions"] == []


def test_orphaned_permission_is_reported(client: TestClient) -> None:
    """模块下线后库里残留的权限码要让管理员看得见，而不是静默失效。"""
    from db.session import get_session_factory
    from modules.identity.models import RolePermission

    admin = _admin(client)
    role = _make_role(client, admin, "stale_role", ["task:read"])

    async def inject() -> None:
        async with get_session_factory()() as session:
            session.add(RolePermission(role_id=role["id"], permission="ghost:read"))
            await session.commit()

    asyncio.run(inject())

    roles = client.get(f"{API}/roles", headers=admin).json()["data"]
    reloaded = next(item for item in roles if item["code"] == "stale_role")
    assert reloaded["orphanedPermissions"] == ["ghost:read"]
    assert sorted(reloaded["permissions"]) == ["ghost:read", "task:read"]


def test_permission_catalog_is_grouped(client: TestClient) -> None:
    admin = _admin(client)
    data = client.get(f"{API}/permissions", headers=admin).json()["data"]

    groups = {group["group"] for group in data["groups"]}
    assert {"组织管理", "内容生产"} <= groups
    codes = {
        item["code"] for group in data["groups"] for item in group["items"]
    }
    assert {"identity:role:manage", "task:create", "catalog:read"} <= codes
    assert data["total"] == len(codes)


# --------------------------------------------------------------------------- #
# 账号自助
# --------------------------------------------------------------------------- #
def test_change_own_password(client: TestClient) -> None:
    admin = _admin(client)
    _, headers, _ = _make_user(client, admin, "pwd_user", ["task:read"])

    bad = client.post(
        f"{API}/me/password",
        json={"oldPassword": "wrong-old", "newPassword": "brand-new-1"},
        headers=headers,
    )
    assert bad.status_code == 400
    assert bad.json()["code"] == 40021

    ok = client.post(
        f"{API}/me/password",
        json={"oldPassword": "pass-12345", "newPassword": "brand-new-1"},
        headers=headers,
    )
    assert ok.status_code == 200

    assert (
        client.post(
            f"{API}/login", json={"username": "pwd_user", "password": "pass-12345"}
        ).status_code
        == 401
    )
    _login(client, "pwd_user", "brand-new-1")


def test_admin_resets_password(client: TestClient) -> None:
    admin = _admin(client)
    user, _, _ = _make_user(client, admin, "reset_me", ["task:read"])

    resp = client.post(
        f"{API}/users/{user['id']}/reset-password",
        json={"newPassword": "reset-by-admin"},
        headers=admin,
    )
    assert resp.status_code == 200
    _login(client, "reset_me", "reset-by-admin")


def test_update_own_profile(client: TestClient) -> None:
    admin = _admin(client)
    _, headers, _ = _make_user(client, admin, "profile_user", ["task:read"])

    patched = client.patch(
        f"{API}/me", json={"displayName": "新昵称"}, headers=headers
    ).json()["data"]
    assert patched["displayName"] == "新昵称"
    # 身份解析器会用库里的昵称覆盖令牌里的旧值
    assert client.get(f"{API}/me", headers=headers).json()["data"]["displayName"] == "新昵称"


# --------------------------------------------------------------------------- #
# 列表与筛选
# --------------------------------------------------------------------------- #
def test_user_list_shape_and_filters(client: TestClient) -> None:
    admin = _admin(client)
    data = client.get(f"{API}/users", params={"pageSize": 5}, headers=admin).json()["data"]
    assert data["pageSize"] == 5
    assert data["total"] >= 3  # 三个种子账号
    assert {"id", "username", "displayName", "isActive", "roles", "createdAt"} <= set(
        data["items"][0]
    )

    found = client.get(
        f"{API}/users", params={"keyword": "editor"}, headers=admin
    ).json()["data"]
    assert "editor" in {item["username"] for item in found["items"]}
    assert all("editor" in item["username"] for item in found["items"])

    inactive = client.get(
        f"{API}/users", params={"isActive": False}, headers=admin
    ).json()["data"]
    assert all(item["isActive"] is False for item in inactive["items"])


def test_duplicate_username_rejected(client: TestClient) -> None:
    admin = _admin(client)
    resp = client.post(
        f"{API}/users",
        json={"username": "editor", "password": "pass-12345"},
        headers=admin,
    )
    assert resp.status_code == 409
    assert resp.json()["code"] == 40921


def test_missing_user_returns_404(client: TestClient) -> None:
    admin = _admin(client)
    resp = client.get(f"{API}/users/does-not-exist", headers=admin)
    assert resp.status_code == 404
    assert resp.json()["code"] == 40421


@pytest.mark.parametrize("path", [f"{API}/users", f"{API}/roles"])
def test_bad_token_is_rejected_with_401(client: TestClient, path: str) -> None:
    resp = client.get(path, headers={"Authorization": "Bearer not-a-token"})
    assert resp.status_code == 401


# --------------------------------------------------------------------------- #
# 生产首启：create-admin 走的同一条路径
# --------------------------------------------------------------------------- #
def test_create_admin_bootstrap_is_idempotent(client: TestClient) -> None:
    """生产第一个超管只能从命令行建，重复执行必须「修复」而不是「再建一个」。"""
    from db.session import get_session_factory
    from modules.identity.service import IdentityService

    async def run() -> tuple[bool, bool]:
        async with get_session_factory()() as session:
            service = IdentityService(session)
            _, created = await service.ensure_superuser("ops", "ops-pass-123", "运维")
            _, again = await service.ensure_superuser("ops", "ops-pass-456")
            return created, again

    created, again = asyncio.run(run())
    assert created is True
    assert again is False
    # 第二次跑把密码改成了新值，说明它复用了同一个账号
    _login(client, "ops", "ops-pass-456")


def test_without_resolver_permissions_come_from_token() -> None:
    """兼容性承诺：没有解析器时行为与改造前一致（纯令牌子系统仍然可用）。"""
    from api.authn import (
        register_principal_resolver,
        reset_principal_resolver,
        resolve_principal,
    )
    from core.context import CurrentUser
    from modules.identity.principal import IdentityPrincipalResolver

    claimed = CurrentUser(id="x", name="x", permissions=frozenset({"task:read"}))
    reset_principal_resolver()
    try:
        resolved = asyncio.run(resolve_principal(claimed, None))  # type: ignore[arg-type]
        assert resolved is claimed
    finally:
        register_principal_resolver(IdentityPrincipalResolver())


# --------------------------------------------------------------------------- #
# 提权面：能管角色 ≠ 能给自己提权（R4-11）
# --------------------------------------------------------------------------- #
def _ids(client: TestClient, headers: dict[str, str]) -> tuple[str, dict[str, str]]:
    """返回 (admin 用户 id, {角色 code: 角色})——两条断言都要用到。"""
    me = client.get(f"{API}/me", headers=headers).json()["data"]
    roles = {r["code"]: r for r in client.get(f"{API}/roles", headers=headers).json()["data"]}
    return me["id"], roles


def test_role_manage_cannot_write_wildcard(client: TestClient) -> None:
    """`identity:role:manage` 写 `*` 等于一次调用把自己变成超管（R4-11 路径 A）。"""
    admin = _admin(client)
    _, headers, own_role = _make_user(
        client, admin, "ops_mgr", ["identity:read", "identity:role:manage", "task:read"]
    )

    promote = client.put(
        f"{API}/roles/{own_role['id']}/permissions",
        json={"permissions": ["*"]},
        headers=headers,
    )
    assert promote.status_code == 403
    assert promote.json()["code"] == 40323
    # 权限没有真的写进去
    assert client.get(f"{API}/me", headers=headers).json()["data"]["permissions"] != ["*"]

    # 换个入口一样拦：新建角色时塞 `*`
    sneaky = client.post(
        f"{API}/roles",
        json={"code": "sneaky", "name": "sneaky", "permissions": ["*"]},
        headers=headers,
    )
    assert sneaky.status_code == 403

    # 也不允许「造一个比自己大的普通角色」再挂给自己
    bigger = client.post(
        f"{API}/roles",
        json={"code": "bigger", "name": "bigger", "permissions": ["task:*"]},
        headers=headers,
    )
    assert bigger.status_code == 403


def test_update_permission_cannot_reset_superuser_password(client: TestClient) -> None:
    """重置超管口令 = 直接接管超管身份，`identity:update` 不该够用（R4-11 路径 B）。"""
    admin = _admin(client)
    admin_id, _ = _ids(client, admin)
    _, headers, _ = _make_user(
        client, admin, "ops_update", ["identity:read", "identity:update", "task:read"]
    )

    resp = client.post(
        f"{API}/users/{admin_id}/reset-password",
        json={"newPassword": "hijacked-pass-1"},
        headers=headers,
    )
    assert resp.status_code == 403
    assert resp.json()["code"] == 40322
    # 原口令仍然有效，说明密码没被改掉
    _login(client, "admin", "admin@12345")


def test_create_user_cannot_attach_superuser_or_stronger_role(
    client: TestClient,
) -> None:
    """建号时直接挂超管 / 挂一个自己都没有的角色（R4-11 路径 C）。"""
    admin = _admin(client)
    _, roles = _ids(client, admin)
    _, headers, _ = _make_user(
        client, admin, "ops_create", ["identity:read", "identity:create"]
    )

    as_superuser = client.post(
        f"{API}/users",
        json={
            "username": "backdoor",
            "password": "backdoor-pass-1",
            "roleIds": [roles["admin"]["id"]],
        },
        headers=headers,
    )
    assert as_superuser.status_code == 403

    as_editor = client.post(
        f"{API}/users",
        json={
            "username": "lender",
            "password": "lender-pass-1",
            "roleIds": [roles["editor"]["id"]],
        },
        headers=headers,
    )
    assert as_editor.status_code == 403


def test_delete_permission_cannot_disable_superuser(client: TestClient) -> None:
    """停用超管和重置口令同级：先把人踢出去，再慢慢接管（R4-11 同类路径）。"""
    admin = _admin(client)
    admin_id, _ = _ids(client, admin)
    _, headers, _ = _make_user(
        client, admin, "ops_delete", ["identity:read", "identity:delete"]
    )

    resp = client.delete(f"{API}/users/{admin_id}", headers=headers)
    assert resp.status_code == 403
    assert resp.json()["code"] == 40322


# --------------------------------------------------------------------------- #
# 自助路径与口令策略（R4-12 / R4-13 / R4-14 / R4-15）
# --------------------------------------------------------------------------- #
def test_self_service_cannot_disable_account(client: TestClient) -> None:
    """`PATCH /me` 只接受展示字段：`isActive` 被忽略而不是把自己锁死（R4-12）。"""
    admin = _admin(client)
    _, headers, _ = _make_user(client, admin, "self_off", ["task:read"])

    resp = client.patch(f"{API}/me", json={"isActive": False}, headers=headers)
    assert resp.status_code == 200
    assert resp.json()["data"]["isActive"] is True
    # 令牌仍然可用——旧实现这里会直接 401，且本人无法自救
    assert client.get(f"{API}/me", headers=headers).status_code == 200


def test_password_change_invalidates_old_tokens(client: TestClient) -> None:
    """改密后旧令牌立即失效（R4-13）：账号被盗时「改密码」必须真的有用。

    令牌 `iat` 只有秒级精度，同一秒内签发的令牌与改密时间相等；
    这个 1 秒的窗口是有意保留的（否则改密当秒连新登录都会立刻失效）。
    """
    admin = _admin(client)
    _, headers, _ = _make_user(client, admin, "rotate_pwd", ["task:read"])

    old = headers
    # 令牌 `iat` 只有秒级精度：先跨过一秒再改密，才能构造出「旧令牌严格更早」的场景
    import time

    time.sleep(1.1)
    assert (
        client.post(
            f"{API}/me/password",
            json={"oldPassword": "pass-12345", "newPassword": "rotated-pass-1"},
            headers=old,
        ).status_code
        == 200
    )

    assert client.get(f"{API}/me", headers=old).status_code == 401
    fresh = _login(client, "rotate_pwd", "rotated-pass-1")
    assert client.get(f"{API}/me", headers=fresh).status_code == 200


def test_weak_password_is_rejected(client: TestClient) -> None:
    """口令策略三条：不得与旧密码相同、不得纯数字、不得重复字符（R4-14）。"""
    admin = _admin(client)
    _, headers, _ = _make_user(client, admin, "weak_pwd", ["task:read"])

    for weak in ("pass-12345", "1234567890", "aaaaaaaaaa"):
        resp = client.post(
            f"{API}/me/password",
            json={"oldPassword": "pass-12345", "newPassword": weak},
            headers=headers,
        )
        assert resp.status_code == 422, f"{weak} 应被拒绝"

    assert (
        client.post(
            f"{API}/me/password",
            json={"oldPassword": "pass-12345", "newPassword": "strong-pass-1"},
            headers=headers,
        ).status_code
        == 200
    )


def test_login_is_rate_limited_after_repeated_failures(client: TestClient) -> None:
    """连续失败到阈值后返回 429，而不是无限次 401（R4-15）。"""
    from modules.identity import throttle

    for _ in range(throttle.MAX_FAILURES):
        resp = client.post(
            f"{API}/login",
            json={"username": "brute_target", "password": "wrong-pass-1"},
        )
        assert resp.status_code == 401

    blocked = client.post(
        f"{API}/login", json={"username": "brute_target", "password": "wrong-pass-1"}
    )
    assert blocked.status_code == 429
    assert blocked.json()["code"] == 42900
