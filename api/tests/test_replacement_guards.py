"""替换任务的两道后台闸门。

这两条都不是「代码写得对不对」，而是「出事的时候钱会不会继续花」：
发起人被停用后队列还在替他跑、上游一直忙就无限重试。测试钉的就是这两个边界。
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from modules.template.replacement_repository import (
    ReplacementManifest,
    ReplacementRepository,
)
from modules.template.replacement_worker import (
    MAX_RETRIES,
    _bump_retry,
    backoff_delay,
    owner_disabled,
)

API = "/api/v1"
ADMIN = ("admin", "admin@12345")


def _login(
    client: TestClient, username: str = ADMIN[0], password: str = ADMIN[1]
) -> dict[str, str]:
    resp = client.post(f"{API}/identity/login", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text
    return {"Authorization": "Bearer " + resp.json()["data"]["accessToken"]}


def _manifest(owner_id: str) -> ReplacementManifest:
    return ReplacementManifest(
        id="a" * 32,
        kind="character",
        target="hero",
        owner_id=owner_id,
        fingerprint="f" * 64,
    )


def test_backoff_starts_small_and_caps() -> None:
    """退避必须两端都成立：起步别太急（打上游），上限别太大（用户等到以为卡死）。"""
    assert [backoff_delay(n) for n in (1, 2, 3, 4)] == [30.0, 60.0, 120.0, 240.0]
    assert backoff_delay(9) == 600.0  # 封顶
    assert backoff_delay(0) == 30.0  # 计数异常时按第一次算，不返回 0


def test_retry_is_bounded_and_lands_in_service_error() -> None:
    repo = ReplacementRepository()
    path = repo.storage.root() / "runs/guard-check"
    meta = _manifest("u_guard")

    delays = []
    for _ in range(MAX_RETRIES):
        delay = _bump_retry(repo, path, meta, reason="执行器持续被占用")
        assert delay is not None
        delays.append(delay)
    assert delays == [30.0, 60.0, 120.0, 240.0, 480.0]

    # 第 6 次不再排队：返回 None 即为「别再 enqueue」，run 转人工
    assert _bump_retry(repo, path, meta, reason="执行器持续被占用") is None

    saved = repo.load(path)
    assert saved.status == "service_error"
    assert saved.error_code == "RETRY_EXHAUSTED"
    assert saved.retry_count == MAX_RETRIES + 1
    assert "转人工" in (saved.last_error or "")


async def test_disabled_owner_blocks_and_unknown_owner_passes(client: TestClient) -> None:
    admin = _login(client)
    role = client.post(
        f"{API}/identity/roles",
        json={"code": "guard_none", "name": "guard", "permissions": []},
        headers=admin,
    )
    assert role.status_code == 201, role.text
    created = client.post(
        f"{API}/identity/users",
        json={
            "username": "guard_owner",
            "password": "pass-12345",
            "displayName": "guard",
            "roleIds": [role.json()["data"]["id"]],
        },
        headers=admin,
    )
    assert created.status_code == 201, created.text
    user_id = created.json()["data"]["id"]

    assert await owner_disabled(user_id) is False

    assert client.delete(f"{API}/identity/users/{user_id}", headers=admin).status_code == 200
    assert await owner_disabled(user_id) is True

    # 历史档案里的合成 owner / 已删除的 id：不拦（否则老数据全部卡死）
    assert await owner_disabled("u_that_never_existed") is False


async def _set_role_permissions(role_id: str, codes: list[str]) -> None:
    from db.session import get_session_factory
    from modules.identity.repository import RoleRepository

    async with get_session_factory()() as session:
        await RoleRepository(session).set_permissions(role_id, codes)
        await session.commit()


async def test_sync_roles_backfills_only_missing_permission(client: TestClient) -> None:
    """上线带新权限点的模块时，老库里的 editor 不会自动长出新权限。

    这条测的就是那个缺口：症状是「超管测都正常，非超管点提交 403」，
    而 `sync-roles` 只补差集、不删任何手工配置。
    """
    from db.session import get_session_factory
    from modules.identity.service import IdentityService

    admin = _login(client)
    roles = client.get(f"{API}/identity/roles", headers=admin).json()["data"]
    editor = next(r for r in roles if r["code"] == "editor")
    assert "template:use" in editor["permissions"]  # 新库：声明即生效

    # 模拟「模块上线前就在的老库」：把这条权限拿掉
    await _set_role_permissions(
        editor["id"], [p for p in editor["permissions"] if p != "template:use"]
    )
    resp = client.post(
        f"{API}/identity/users",
        json={
            "username": "guard_editor",
            "password": "pass-12345",
            "displayName": "editor",
            "roleIds": [editor["id"]],
        },
        headers=admin,
    )
    assert resp.status_code == 201, resp.text
    headers = _login(client, "guard_editor", "pass-12345")

    # 缺权限时：提交替换被 403 挡在门外（不是 422 校验失败）
    submit = f"{API}/templates/replacement-runs"
    assert client.post(submit, json={}, headers=headers).status_code == 403

    async with get_session_factory()() as session:
        added = await IdentityService(session).sync_builtin_role_permissions()
    assert added.get("editor") == ["template:use"]

    # 补齐后同一张令牌就能过权限检查，落到参数校验（422）
    assert client.post(submit, json={}, headers=headers).status_code == 422

    # 幂等：再跑一次什么都不做
    async with get_session_factory()() as session:
        assert await IdentityService(session).sync_builtin_role_permissions() == {}

