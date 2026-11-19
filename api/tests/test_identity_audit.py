"""授权面留痕：改权限 / 改角色 / 重置口令 / 停用，必须能查得到。

## 为什么单独一个文件

`tests/test_audit.py` 守的是**审计机制本身**（协议、写入端故障、生图那条端到端）。
这里守的是**身份模块有没有把机制用起来**：一个 RBAC 后台最该留痕的恰恰是
「谁给了谁钥匙」，而在这之前它只写 `logger.info` —— 日志一滚动就无从追责。

## 断言的形状

把写入端换成内存收集器（与 `test_audit.py` 同一套 `_restore_sink` 手法），
断言 **动作名 + 目标 + 差集**，不断言 JSON 字符串：
`{"added": ["task:read"]}` 这种结构才是审计能聚合的东西。
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from core import audit

API = "/api/v1/identity"
ADMIN = ("admin", "admin@12345")


@pytest.fixture(autouse=True)
def _restore_sink() -> Iterator[None]:
    """用例结束后把写入端还原成 app 启动时注册的数据库实现。"""
    from db.audit_sink import write

    yield
    audit.set_audit_sink(write)


@pytest.fixture
def captured() -> Iterator[list[audit.AuditEntry]]:
    entries: list[audit.AuditEntry] = []

    async def sink(entry: audit.AuditEntry) -> None:
        entries.append(entry)

    audit.set_audit_sink(sink)
    yield entries


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
    code = f"audit_role_{_suffix()}"
    resp = client.post(
        f"{API}/roles", json={"code": code, "name": code, "permissions": perms}, headers=admin
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["data"]


def _make_user(
    client: TestClient, admin: dict[str, str], perms: list[str]
) -> tuple[dict, dict[str, str]]:
    role = _make_role(client, admin, perms)
    username = f"audit_{_suffix()}"
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


def _only(entries: list[audit.AuditEntry], action: str) -> audit.AuditEntry:
    matched = [entry for entry in entries if entry.action == action]
    assert matched, f"没有 {action} 的留痕，实际收到：{[e.action for e in entries]}"
    return matched[-1]


def test_granting_permissions_leaves_a_diff(
    client: TestClient, captured: list[audit.AuditEntry]
) -> None:
    """给个人加权限 = 授出访问权，必须记下「加了哪几条」。

    注意「个人授权」与「角色权限」是两层：这里先落一层个人授权，
    再改它，`detail` 才是真正的前后差集（空 → 三条不是差集，是初始化）。
    """
    admin = _admin(client)
    user, _ = _make_user(client, admin, [])
    first = client.put(
        f"{API}/users/{user['id']}/permissions",
        json={"permissions": ["identity:read", "task:read"]},
        headers=admin,
    )
    assert first.status_code == 200, first.text
    captured.clear()

    resp = client.put(
        f"{API}/users/{user['id']}/permissions",
        json={"permissions": ["identity:read", "task:read", "library:read"]},
        headers=admin,
    )
    assert resp.status_code == 200, resp.text

    entry = _only(captured, "identity.user.permissions")
    assert entry.target_type == "user"
    assert entry.target_id == user["id"]
    assert entry.actor_id == "u_001"  # 种子超管
    assert entry.detail["added"] == ["library:read"]
    assert entry.detail["removed"] == []
    assert entry.detail["countFrom"] == 2
    assert entry.detail["countTo"] == 3


def test_role_permission_change_records_removal_too(
    client: TestClient, captured: list[audit.AuditEntry]
) -> None:
    """撤权方向也要留痕：只说「更新成功」等于什么都没说。"""
    admin = _admin(client)
    role = _make_role(client, admin, ["task:read", "library:read"])

    resp = client.put(
        f"{API}/roles/{role['id']}/permissions",
        json={"permissions": ["task:read"]},
        headers=admin,
    )
    assert resp.status_code == 200, resp.text

    entry = _only(captured, "identity.role.permissions")
    assert entry.target_id == role["id"]
    assert entry.detail["removed"] == ["library:read"]
    assert entry.detail["code"] == role["code"]


def test_role_assignment_records_from_and_to(
    client: TestClient, captured: list[audit.AuditEntry]
) -> None:
    admin = _admin(client)
    user, _ = _make_user(client, admin, ["task:read"])
    other = _make_role(client, admin, ["library:read"])

    resp = client.put(
        f"{API}/users/{user['id']}/roles",
        json={"roleIds": [other["id"]]},
        headers=admin,
    )
    assert resp.status_code == 200, resp.text

    entry = _only(captured, "identity.user.roles")
    assert entry.detail["to"] == [other["code"]]
    assert len(entry.detail["from"]) == 1  # 原来那个专属角色
    assert user["id"] == entry.target_id


def test_password_reset_is_audited_without_the_password(
    client: TestClient, captured: list[audit.AuditEntry]
) -> None:
    """重置口令是最强提权面：必须留痕，但**绝不能**把口令写进审计表。"""
    admin = _admin(client)
    user, _ = _make_user(client, admin, ["task:read"])

    resp = client.post(
        f"{API}/users/{user['id']}/reset-password",
        json={"newPassword": "brand-new-54321"},
        headers=admin,
    )
    assert resp.status_code == 200, resp.text

    entry = _only(captured, "identity.user.password_reset")
    assert entry.target_id == user["id"]
    assert "brand-new-54321" not in str(entry.detail)
    assert set(entry.detail) == {"username"}


def test_deactivate_via_patch_is_audited(
    client: TestClient, captured: list[audit.AuditEntry]
) -> None:
    """PATCH 的 `isActive: false` 与 DELETE 是同一条能力，留痕也要同口径。"""
    admin = _admin(client)
    user, _ = _make_user(client, admin, ["task:read"])

    resp = client.patch(
        f"{API}/users/{user['id']}", json={"isActive": False}, headers=admin
    )
    assert resp.status_code == 200, resp.text

    entry = _only(captured, "identity.user.deactivate")
    assert entry.detail["via"] == "patch"
    assert entry.detail["revokedSessions"] >= 0


def test_refresh_token_reuse_is_audited(
    client: TestClient, captured: list[audit.AuditEntry]
) -> None:
    """同一张 refresh 换第二次（已被轮换）要留痕，但仍然只 401、不吊销会话。

    处置为什么不改：两个标签页抢跑时后到的那个必然拿旧 jti，吊销会话等于
    因为「开了两个标签页」把用户踢下线。留痕是这件事唯一能两头兼顾的落点。
    """
    login = client.post(
        f"{API}/login", json={"username": ADMIN[0], "password": ADMIN[1]}
    )
    tokens = login.json()["data"]

    first = client.post(f"{API}/refresh", json={"refreshToken": tokens["refreshToken"]})
    assert first.status_code == 200, first.text

    replay = client.post(f"{API}/refresh", json={"refreshToken": tokens["refreshToken"]})
    assert replay.status_code == 401

    entry = _only(captured, "session.refresh_reuse")
    assert entry.target_type == "session"
    assert entry.target_id  # sid
    assert "userAgent" in entry.detail
