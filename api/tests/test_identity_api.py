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
    """停用可逆，但「启用」恢复的是**登录能力**，不是那几张旧票。

    R10-02 的原文：停用只写 `is_active`、不吊销会话，于是重新启用后同一张 access
    与 refresh 原地复活（连口令都不需要）。停用是切断，不是暂停。
    """
    admin = _admin(client)
    user, headers, _ = _make_user(client, admin, "comeback", ["task:read"])

    client.delete(f"{API}/users/{user['id']}", headers=admin)
    assert client.get(f"{API}/me", headers=headers).status_code == 401

    resp = client.patch(
        f"{API}/users/{user['id']}", json={"isActive": True}, headers=admin
    )
    assert resp.status_code == 200
    assert resp.json()["data"]["isActive"] is True

    # 旧令牌仍然 401：会话在停用那一刻就被吊销了，启用不会把它恢复
    assert client.get(f"{API}/me", headers=headers).status_code == 401

    # 能做的只有「重新登录」——这条路径走的是新的会话、新的票
    fresh = _login(client, "comeback", "pass-12345")
    assert client.get(f"{API}/me", headers=fresh).status_code == 200


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


def test_anonymous_identity_follows_demo_user_setting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """匿名回退的主体必须与 `DEMO_USER_ID` 同源。

    这个 id 曾经在 `api/deps.py` 里写死成 `"u_001"`，而 `app seed` 按
    `settings.demo_user_id` 种数据（`SeedContext.demo_user_id` 同源）。
    于是改了配置之后，匿名请求拿到的是一个库里不存在的身份：每个接口都是 200，
    只是列表全空、详情全 404——现象像「演示数据没种上」，其实是两个人。

    直接测构造函数而不是打 HTTP：`/me` 会走 `resolve_principal`，账号不存在时
    返回 401，断言就变成在测「查库失败」而不是在测「id 从哪来」。
    """
    from api.deps import demo_user
    from core.config import reload_settings

    monkeypatch.setenv("DEMO_USER_ID", "u_demo_from_env")
    monkeypatch.setenv("DEMO_USER_NAME", "环境里的演示账号")
    reload_settings()

    actor = demo_user()
    assert actor.id == "u_demo_from_env"
    assert actor.name == "环境里的演示账号"


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


# --------------------------------------------------------------------------- #
# 登录限流：不挡正确密码、表有界会回收（R5-01）
# --------------------------------------------------------------------------- #
def test_correct_password_survives_failures(client: TestClient) -> None:
    """连续猜错之后，正确密码仍然能登录（R5-01 ①）。

    原实现是「第 6 次请求无论密码对错都 429」，于是任何人用 5 个乱码
    就能把已知用户名锁 15 分钟——未认证即可拒绝服务，真实用户反而进不来。
    锁定应该只惩罚「猜错」这个动作。
    """
    from modules.identity import throttle

    for _ in range(throttle.MAX_FAILURES):
        resp = client.post(
            f"{API}/login", json={"username": "admin", "password": "wrong-pass-1"}
        )
        assert resp.status_code == 401

    ok = client.post(f"{API}/login", json={"username": "admin", "password": "admin@12345"})
    assert ok.status_code == 200
    # 成功即清零：紧接着再输错一次，应该回到 401 而不是 429
    after = client.post(f"{API}/login", json={"username": "admin", "password": "wrong-pass-1"})
    assert after.status_code == 401


def test_throttle_table_has_an_upper_bound(monkeypatch: pytest.MonkeyPatch) -> None:
    """计数表不能被匿名请求线性撑大（R5-01 ③）。

    原实现是裸 dict：换一个用户名就是一条新记录，永不回收，
    未认证的请求可以稳定地往进程内存里塞键。
    """
    from modules.identity import throttle

    monkeypatch.setattr(throttle, "MAX_KEYS", 8)
    for i in range(60):
        throttle.record_failure(f"u{i}|198.51.100.1")

    assert len(throttle._failures) <= 8


def test_throttle_entries_are_recycled(monkeypatch: pytest.MonkeyPatch) -> None:
    """窗口外的条目会在下一次写入时被回收，而不是永久留在表里。"""
    import time

    from modules.identity import throttle

    monkeypatch.setattr(throttle, "WINDOW_SEC", 0.02)
    throttle.record_failure("stale|198.51.100.1")
    time.sleep(0.05)
    throttle.record_failure("fresh|198.51.100.2")

    assert "stale|198.51.100.1" not in throttle._failures


# --------------------------------------------------------------------------- #
# 改口令三条路径共用同一个不变式（R5-02）
# --------------------------------------------------------------------------- #
def test_cli_password_rotation_invalidates_tokens(client: TestClient) -> None:
    """`create-admin` 轮换口令必须让旧令牌失效（R5-02）。

    这是「超管疑似泄漏、运维紧急轮换口令」唯一能用的命令：
    只让旧口令失效、旧令牌还能用满 2 小时，等于自救只做了一半。
    """
    import time

    from db.session import get_session_factory
    from modules.identity.service import IdentityService

    async def rotate(password: str) -> None:
        async with get_session_factory()() as session:
            await IdentityService(session).ensure_superuser("ops_rotate", password)

    asyncio.run(rotate("ops-rotate-1"))
    old = _login(client, "ops_rotate", "ops-rotate-1")
    assert client.get(f"{API}/me", headers=old).status_code == 200

    time.sleep(1.1)  # iat 是秒级：让写入时间严格晚于令牌签发时间
    asyncio.run(rotate("ops-rotate-2"))

    assert client.get(f"{API}/me", headers=old).status_code == 401
    fresh = _login(client, "ops_rotate", "ops-rotate-2")
    assert client.get(f"{API}/me", headers=fresh).status_code == 200


def test_create_admin_cannot_bypass_password_policy() -> None:
    """`create-admin` 与 API 共用同一份口令策略（R5-04）。

    生产环境的第一个超管是权限最大的账号，却曾是唯一只校验长度的入口。
    """
    from cli import main

    with pytest.raises(SystemExit) as excinfo:
        main(["create-admin", "--username", "ops_init", "--password", "1234567890"])
    assert excinfo.value.code == 1


# --------------------------------------------------------------------------- #
# 撤权方向：与授予方向对等（R5-03 / R5-06）
# --------------------------------------------------------------------------- #
def test_role_manager_cannot_strip_stronger_account(client: TestClient) -> None:
    """能管角色 ≠ 能清空比自己权限高的账号（R5-03）。

    修复前同一对（操作者, 目标）：给角色 403、清角色 200。
    """
    admin = _admin(client)
    editor, _, _ = _make_user(client, admin, "strong_editor", ["task:*"])
    _, headers, _ = _make_user(
        client, admin, "ops_strip", ["identity:read", "identity:role:manage"]
    )

    resp = client.put(
        f"{API}/users/{editor['id']}/roles", json={"roleIds": []}, headers=headers
    )
    assert resp.status_code == 403
    assert resp.json()["code"] == 40323
    # 角色没被改动
    kept = client.get(f"{API}/users/{editor['id']}", headers=admin).json()["data"]
    assert kept["roles"]


def test_role_manager_cannot_delete_or_gut_stronger_role(client: TestClient) -> None:
    """比自己强的角色既不能删，也不能"顺手改成只读"（R5-06 / R5-03 同族）。"""
    admin = _admin(client)
    strong = _make_role(client, admin, "strong_role", ["task:*"])
    _, headers, _ = _make_user(
        client, admin, "ops_del", ["identity:read", "identity:role:manage"]
    )

    deleted = client.delete(f"{API}/roles/{strong['id']}", headers=headers)
    assert deleted.status_code == 403
    assert deleted.json()["code"] == 40323

    gutted = client.put(
        f"{API}/roles/{strong['id']}/permissions",
        json={"permissions": []},
        headers=headers,
    )
    assert gutted.status_code == 403
    assert gutted.json()["code"] == 40323


# --------------------------------------------------------------------------- #
# `isActive` 的 null 语义与「开/关」双向守卫（R6-03 / R6-04）
# --------------------------------------------------------------------------- #
def test_is_active_null_is_rejected_instead_of_disabling(client: TestClient) -> None:
    """`{"isActive": null}` 必须 422，不能被 `bool(None)` 静默当成停用（R6-03）。

    `exclude_unset` 只挡「没传」；表单里「这个开关没填」发 null 很常见，
    而后果是把一个正常账号关掉，还带着「最后一个超管」的连带判定。
    """
    admin = _admin(client)
    user, _, _ = _make_user(client, admin, "null_active", ["task:read"])

    resp = client.patch(
        f"{API}/users/{user['id']}", json={"isActive": None}, headers=admin
    )
    assert resp.status_code == 422, resp.text

    kept = client.get(f"{API}/users/{user['id']}", headers=admin).json()["data"]
    assert kept["isActive"] is True


def test_restoring_a_superuser_requires_a_superuser(client: TestClient) -> None:
    """停用超管要超管，**恢复也要**（R6-04）。

    守卫原来只写在 `not active` 分支里：同一个账号、同一个接口，方向反过来就放行。
    安全事件后的流程通常是「先停用、排查、再启用」，而启用这一步恰好不需要超管——
    被停用的正是内鬼账号时，一个中级管理员就能把它放回来。
    """
    admin = _admin(client)
    target, _, _ = _make_user(client, admin, "su_target", ["*"])
    _, weak, _ = _make_user(
        client, admin, "ops_weak", ["identity:read", "identity:update"]
    )

    denied = client.patch(
        f"{API}/users/{target['id']}", json={"isActive": False}, headers=weak
    )
    assert denied.status_code == 403
    assert denied.json()["code"] == 40322

    assert (
        client.patch(
            f"{API}/users/{target['id']}", json={"isActive": False}, headers=admin
        ).status_code
        == 200
    )

    restored = client.patch(
        f"{API}/users/{target['id']}", json={"isActive": True}, headers=weak
    )
    assert restored.status_code == 403
    assert restored.json()["code"] == 40322  # 与停用同一个错误码
    still_off = client.get(f"{API}/users/{target['id']}", headers=admin).json()["data"]
    assert still_off["isActive"] is False


# --------------------------------------------------------------------------- #
# 演示数据的环境护栏（R6-06）
# --------------------------------------------------------------------------- #
def test_seed_refuses_to_run_in_production(monkeypatch: pytest.MonkeyPatch) -> None:
    """`app seed` 在 prod 下必须硬拒：它写的是口令固定的账号。

    注释里写「生产用 create-admin」不算护栏——部署脚本里「顺手 seed 初始化数据」
    很常见，到了生产就把 `admin/admin@12345` 这个超管写进了库。
    """
    from cli import main
    from core.config import get_settings

    monkeypatch.setattr(get_settings(), "app_env", "prod")
    with pytest.raises(SystemExit) as excinfo:
        main(["seed"])
    assert excinfo.value.code == 1


# --------------------------------------------------------------------------- #
# 改他人邮箱要过「目标不高于自己」的守卫（R7-03）
# --------------------------------------------------------------------------- #
def test_changing_another_users_email_requires_not_below_target(client: TestClient) -> None:
    """`identity:update` 能改昵称，但改**超管**的邮箱必须被挡（R7-03）。

    改昵称不是提权，所以资料字段一直不需要守卫；邮箱不一样——它是「账号找回」
    最可能接上的入口。等接上「邮箱找回密码」再补守卫，等于让这条路径先跑半年。
    """
    admin = _admin(client)
    target, _, _ = _make_user(client, admin, "mail_target", ["*"])
    _, ops, _ = _make_user(
        client, admin, "mail_ops", ["identity:read", "identity:update"]
    )

    denied = client.patch(
        f"{API}/users/{target['id']}",
        json={"email": "attacker@evil.test"},
        headers=ops,
    )
    assert denied.status_code == 403
    assert denied.json()["code"] == 40322

    # 昵称、头像也一起挡：R7-03 只守了 email，同一次请求里另外两个字段照旧 200（R8-02）。
    # 这里的「挡」来自另一档守卫——展示字段只判「目标是不是超管」（R9-02），
    # 而 `mail_target` 挂着 `*`，正是需要挡住的那种目标。
    for payload in ({"displayName": "改名没问题"}, {"avatarUrl": "http://evil/x.png"}):
        resp = client.patch(f"{API}/users/{target['id']}", json=payload, headers=ops)
        assert resp.status_code == 403, f"{payload} 没被守卫挡住"
        assert resp.json()["code"] == 40322

    kept = client.get(f"{API}/users/{target['id']}", headers=admin).json()["data"]
    assert kept["email"] is None
    assert kept["avatarUrl"] is None


def test_changing_own_email_via_user_endpoint_is_allowed(client: TestClient) -> None:
    """改自己的资料不走「目标不高于自己」那道路径，不该被自己的守卫挡住。"""
    admin = _admin(client)
    ops, ops_headers, _ = _make_user(
        client, admin, "mail_self", ["identity:read", "identity:update"]
    )

    resp = client.patch(
        f"{API}/users/{ops['id']}", json={"email": "me@demo.local"}, headers=ops_headers
    )
    assert resp.status_code == 200
    assert resp.json()["data"]["email"] == "me@demo.local"


def test_writing_a_field_back_to_its_current_value_still_needs_the_guard(
    client: TestClient,
) -> None:
    """把值写回原样也必须过守卫（R8-04）。

    `if active != user.is_active` 这种「值没变就免检」的写法是个免费的后门：
    请求照样落到 `_require_user`，于是「404 = 没有这个 id」「200 = 存在但我能写」
    就成了一次廉价的 id 探测；而 `avatarUrl` 传原值同样会走一遍 `<img src>` 的渲染路径。
    守卫应当按「要写什么」触发，不按「值变没变」触发。
    """
    admin = _admin(client)
    target, _, _ = _make_user(client, admin, "probe_target", ["*"])
    _, ops, _ = _make_user(
        client, admin, "probe_ops", ["identity:read", "identity:update"]
    )

    for payload in ({"isActive": True}, {"displayName": target["displayName"]}):
        resp = client.patch(f"{API}/users/{target['id']}", json=payload, headers=ops)
        assert resp.status_code == 403, f"{payload} 写原值绕过了守卫"
        assert resp.json()["code"] == 40322


def test_delegated_role_can_rename_a_normal_user_but_not_touch_email(
    client: TestClient,
) -> None:
    """委派角色能改普通账号的昵称——这是**有意选的**，不是漏掉的（R9-02 / R9-03）。

    「能不能改昵称」的判定是「目标是不是超管」，不是「目标的权限是不是我的子集」：
    后者要求一个人事管理员持有被管理者的**全部业务权限**，结果是这个权限点谁也改不
    了（实测连 viewer 的昵称都 40323），把「能用的功能」换成了「看起来安全但没人能用」。

    邮箱仍走严格判定：它是「找回密码」的入口，接上之后改它就是接管账号的第一步。
    两条规则的差别就在这里——同一个接口、同一次请求，字段不同、守卫不同。

    上一轮的用例用的是挂着 `*` 的目标（`["*"]` 是最省事的「比我高」），
    于是只走了「目标是超管」这一支，把另一支的回归整个绕开了（R9-03）。
    """
    admin = _admin(client)
    # 目标带 `task:read`：操作者没有这个权限码，严格判定会拒；轻判定才会放行
    target, _, _ = _make_user(client, admin, "delegated_target", ["task:read"])
    _, hr, _ = _make_user(
        client, admin, "delegated_hr", ["identity:read", "identity:update"]
    )

    renamed = client.patch(
        f"{API}/users/{target['id']}", json={"displayName": "新名字"}, headers=hr
    )
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["data"]["displayName"] == "新名字"

    avatared = client.patch(
        f"{API}/users/{target['id']}",
        json={"avatarUrl": "https://cdn.test/a.png"},
        headers=hr,
    )
    assert avatared.status_code == 200, avatared.text

    denied = client.patch(
        f"{API}/users/{target['id']}", json={"email": "hr@evil.test"}, headers=hr
    )
    assert denied.status_code == 403
    assert denied.json()["code"] == 40323

    # 超管目标两档都挡：轻判定只放行「不比自己更有权威」的目标
    superuser, _, _ = _make_user(client, admin, "delegated_admin", ["*"])
    blocked = client.patch(
        f"{API}/users/{superuser['id']}", json={"displayName": "冒充"}, headers=hr
    )
    assert blocked.status_code == 403
    assert blocked.json()["code"] == 40322
