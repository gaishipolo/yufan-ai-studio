"""会话、刷新令牌、登出、登录审计、口令策略（前端需求 1/2/4/5/8）。

这一组用例守的是**「令牌有状态」这件事本身**：

- 登出以后旧令牌必须立刻失效（否则「退出登录」只是前端删了个本地副本）；
- 刷新令牌能换票、且换完旧的立刻作废（轮换）；
- 两张令牌不能互相冒用（refresh 不能访问业务接口，access 不能拿来续期）；
- 管理员重置口令 / 踢下线要真的把对方赶走；
- 每次登录尝试都留痕，失败原因可查。

这些都不是「实现细节」级别的东西：任何一条退化了，用户和运维都会先于测试发现，
只是发现的方式比较难看（「我明明退出了，别人还能用我的号」）。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

API = "/api/v1/identity"
ADMIN = ("admin", "admin@12345")


@pytest.fixture(autouse=True)
def _reset_login_throttle() -> None:
    """限流计数器是进程级状态，用例之间必须清零（同 `test_identity_api.py`）。"""
    from modules.identity import throttle

    throttle.clear()
    yield
    throttle.clear()


# --------------------------------------------------------------------------- #
# 夹具
# --------------------------------------------------------------------------- #
def _sign_in(
    client: TestClient,
    username: str = ADMIN[0],
    password: str = ADMIN[1],
    *,
    user_agent: str = "pytest-agent",
) -> dict:
    resp = client.post(
        f"{API}/login",
        json={"username": username, "password": password},
        headers={"User-Agent": user_agent},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


def _auth(token_data: dict) -> dict[str, str]:
    return {"Authorization": f"Bearer {token_data['accessToken']}"}


def _provision(client: TestClient, admin: dict, code: str, perms: list[str]) -> dict:
    """建「角色 + 账号」并登录，返回登录结果（含 user / accessToken / refreshToken）。"""
    role = client.post(
        f"{API}/roles",
        json={"code": code, "name": code, "permissions": perms},
        headers=_auth(admin),
    )
    assert role.status_code == 201, role.text
    created = client.post(
        f"{API}/users",
        json={
            "username": code,
            "password": "pass-12345",
            "displayName": code,
            "roleIds": [role.json()["data"]["id"]],
        },
        headers=_auth(admin),
    )
    assert created.status_code == 201, created.text
    return _sign_in(client, code, "pass-12345")


# --------------------------------------------------------------------------- #
# 1. 续期
# --------------------------------------------------------------------------- #
def test_login_returns_refresh_token_and_records_a_session(client: TestClient) -> None:
    data = _sign_in(client, user_agent="Mozilla/5.0 (Macintosh)")

    assert data["refreshToken"]
    assert data["refreshExpiresIn"] > data["expiresIn"]  # 刷新票比访问票长命
    # 两张票不能一样：一样就意味着「把长命的票发给浏览器当访问票用」
    assert data["refreshToken"] != data["accessToken"]

    # 用例库里 admin 可能已经有别的会话（别的测试文件登录过），只看这一台
    sessions = [
        s
        for s in client.get(f"{API}/me/sessions", headers=_auth(data)).json()["data"]
        if "Macintosh" in s["userAgent"]
    ]
    assert len(sessions) == 1
    assert sessions[0]["current"] is True


def test_refresh_rotates_the_token_and_kills_the_old_one(client: TestClient) -> None:
    """续期必须轮换：同一张 refresh 不能用第二次。"""
    first = _sign_in(client)
    refreshed = client.post(
        f"{API}/refresh", json={"refreshToken": first["refreshToken"]}
    )
    assert refreshed.status_code == 200, refreshed.text
    second = refreshed.json()["data"]

    # 新访问票可用
    assert client.get(f"{API}/me", headers=_auth(second)).status_code == 200

    reused = client.post(f"{API}/refresh", json={"refreshToken": first["refreshToken"]})
    assert reused.status_code == 401
    assert reused.json()["code"] == 40122

    # 轮换后的票还能继续续期（不是一次性用品）
    again = client.post(f"{API}/refresh", json={"refreshToken": second["refreshToken"]})
    assert again.status_code == 200


def test_the_two_tokens_cannot_be_swapped(client: TestClient) -> None:
    """refresh 不能访问业务接口，access 也不能拿来续期——`typ` 就是干这个的。"""
    data = _sign_in(client)

    as_access = client.get(
        f"{API}/me", headers={"Authorization": f"Bearer {data['refreshToken']}"}
    )
    assert as_access.status_code == 401

    as_refresh = client.post(
        f"{API}/refresh", json={"refreshToken": data["accessToken"]}
    )
    assert as_refresh.status_code == 401
    assert as_refresh.json()["code"] == 40122


def test_refresh_dies_after_the_password_changes(client: TestClient) -> None:
    """改密之后，**刷新令牌也得作废**。

    只让 access 失效的话，攻击者手里那张 refresh 还能换出新票，
    「改密码自救」就只做了一半。
    """
    admin = _sign_in(client)
    victim = _provision(client, admin, "refresh_victim", ["task:read"])

    reset = client.post(
        f"{API}/users/{victim['user']['id']}/reset-password",
        json={"newPassword": "after-reset-123"},
        headers=_auth(admin),
    )
    assert reset.status_code == 200, reset.text

    stale = client.post(
        f"{API}/refresh", json={"refreshToken": victim["refreshToken"]}
    )
    assert stale.status_code == 401
    assert stale.json()["code"] == 40122


# --------------------------------------------------------------------------- #
# 2. 登出 / 吊销
# --------------------------------------------------------------------------- #
def test_logout_revokes_the_token_immediately_and_is_idempotent(
    client: TestClient,
) -> None:
    data = _sign_in(client)
    assert client.get(f"{API}/me", headers=_auth(data)).status_code == 200

    assert client.post(f"{API}/logout", headers=_auth(data)).status_code == 200
    # 这就是这张表存在的全部理由：签名还是好的，但这张票已经不许用了
    assert client.get(f"{API}/me", headers=_auth(data)).status_code == 401

    # 幂等：前端第二次点「退出」不该看到一条报错
    assert client.post(f"{API}/logout", headers=_auth(data)).status_code == 200
    assert client.post(f"{API}/logout").status_code == 200  # 连令牌都没有也算成功


def test_logout_also_kills_the_refresh_token(client: TestClient) -> None:
    data = _sign_in(client)
    client.post(f"{API}/logout", headers=_auth(data))
    assert (
        client.post(
            f"{API}/refresh", json={"refreshToken": data["refreshToken"]}
        ).status_code
        == 401
    )


def test_reset_password_kicks_every_device_of_that_user(client: TestClient) -> None:
    admin = _sign_in(client)
    victim = _provision(client, admin, "reset_victim", ["task:read"])
    assert client.get(f"{API}/me", headers=_auth(victim)).status_code == 200

    client.post(
        f"{API}/users/{victim['user']['id']}/reset-password",
        json={"newPassword": "new-pass-12345"},
        headers=_auth(admin),
    )

    assert client.get(f"{API}/me", headers=_auth(victim)).status_code == 401
    sessions = client.get(
        f"{API}/me/sessions", headers=_auth(_sign_in(client, "reset_victim", "new-pass-12345"))
    ).json()["data"]
    # 旧会话被吊销，列表里只剩刚登录的这一条
    assert [s["current"] for s in sessions] == [True]


def test_admin_can_kick_a_user_but_not_a_superuser(client: TestClient) -> None:
    admin = _sign_in(client)
    victim = _provision(client, admin, "kick_victim", ["task:read"])
    ops = _provision(client, admin, "kick_ops", ["identity:read", "identity:update"])

    kicked = client.post(
        f"{API}/users/{victim['user']['id']}/revoke-sessions", headers=_auth(admin)
    )
    assert kicked.status_code == 200, kicked.text
    assert kicked.json()["data"]["revoked"] == 1
    assert client.get(f"{API}/me", headers=_auth(victim)).status_code == 401

    # 踢下线是「拿走访问权」，目标高于自己时和重置口令同级地拒绝
    denied = client.post(
        f"{API}/users/{admin['user']['id']}/revoke-sessions", headers=_auth(ops)
    )
    assert denied.status_code == 403
    assert denied.json()["code"] == 40322


def test_my_sessions_lists_devices_and_can_revoke_one(client: TestClient) -> None:
    phone = _sign_in(client, user_agent="iPhone")
    laptop = _sign_in(client, user_agent="MacBook")

    # 同一个用例库里 admin 已经登录过很多次，只看这两台设备
    sessions = [
        s
        for s in client.get(f"{API}/me/sessions", headers=_auth(laptop)).json()["data"]
        if s["userAgent"] in {"iPhone", "MacBook"}
    ]
    assert len(sessions) == 2
    by_agent = {s["userAgent"]: s for s in sessions}
    assert by_agent["MacBook"]["current"] is True
    assert by_agent["iPhone"]["current"] is False

    # 踢掉手机，当前这台不受影响
    revoked = client.delete(
        f"{API}/me/sessions/{by_agent['iPhone']['id']}", headers=_auth(laptop)
    )
    assert revoked.status_code == 200
    assert client.get(f"{API}/me", headers=_auth(phone)).status_code == 401
    assert client.get(f"{API}/me", headers=_auth(laptop)).status_code == 200


def test_revoking_other_sessions_keeps_the_current_one(client: TestClient) -> None:
    other = _sign_in(client, user_agent="old-tablet")
    current = _sign_in(client, user_agent="current-laptop")

    resp = client.delete(f"{API}/me/sessions", headers=_auth(current))
    assert resp.status_code == 200
    assert resp.json()["data"]["revoked"] >= 1

    assert client.get(f"{API}/me", headers=_auth(other)).status_code == 401
    assert client.get(f"{API}/me", headers=_auth(current)).status_code == 200


def test_cannot_revoke_someone_elses_session(client: TestClient) -> None:
    """`DELETE /me/sessions/{id}` 只能动自己的：别人的 sid 一律 404，不区分「存在但不属于你」。"""
    admin = _sign_in(client)
    victim = _provision(client, admin, "session_owner", ["task:read"])
    other = _provision(client, admin, "session_other", ["task:read"])

    victim_session = client.get(
        f"{API}/me/sessions", headers=_auth(victim)
    ).json()["data"][0]

    resp = client.delete(
        f"{API}/me/sessions/{victim_session['id']}", headers=_auth(other)
    )
    assert resp.status_code == 404
    assert resp.json()["code"] == 40423  # 404 + 模块号段 2 + 序号 3
    # 对方仍然在线
    assert client.get(f"{API}/me", headers=_auth(victim)).status_code == 200


# --------------------------------------------------------------------------- #
# 4. 登录审计
# --------------------------------------------------------------------------- #
def test_login_events_keep_failures_and_success_reasons(client: TestClient) -> None:
    admin = _sign_in(client)
    victim = _provision(client, admin, "audit_target", ["task:read"])

    for _ in range(3):
        bad = client.post(
            f"{API}/login",
            json={"username": "audit_target", "password": "wrong-pass"},
            headers={"User-Agent": "audit-agent"},
        )
        assert bad.status_code == 401
    good = _sign_in(client, "audit_target", "pass-12345", user_agent="audit-agent")

    items = client.get(f"{API}/me/logins?limit=10", headers=_auth(good)).json()["data"][
        "items"
    ]
    # 最近一次是这次成功登录
    assert items[0]["success"] is True
    assert items[0]["reason"] is None
    # 连续 3 次错口令在同一个折叠窗口内只留一行（R10-06）：第 2、3 次没有新增信息，
    # 而匿名方正是靠「每次都写一行」把审计表线性填满的
    assert [item["reason"] for item in items[1:]] == ["bad_password", None]
    assert items[2]["success"] is True  # 建号后 `_provision` 的那次登录
    assert all(item["userAgent"] == "audit-agent" for item in items[:2])
    assert all(item["ip"] for item in items)
    # 账号 id 是 victim 的，说明失败记录也挂到了正确的账号上
    assert victim["user"]["id"]


def test_rate_limited_attempts_do_not_write_a_row_each(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """被 429 拦下的尝试不再各写一行审计（R10-06）。

    限流只减慢了请求，没有减少写入：`rate_limited` 那几次照样 insert + commit，
    于是一个匿名方以约 7 次/秒的速度线性填充这张表。现在同一对来源在一个窗口内
    只留一条——审计要说明的是「有人在试」，第 N 次不增加任何信息。
    """
    from modules.identity import throttle

    # 只测写入次数，不测等待：渐进延迟在别的用例里保证
    monkeypatch.setattr(throttle, "penalty", lambda key: 0.0)

    admin = _sign_in(client)
    victim = _provision(client, admin, "audit_flood", ["task:read"])

    statuses = [
        client.post(
            f"{API}/login",
            json={"username": "audit_flood", "password": "wrong-pass"},
            headers={"User-Agent": "flood-agent"},
        ).status_code
        for _ in range(12)
    ]
    # 前 5 次仍然 401（用户要知道是口令错了），之后才是 429
    assert statuses[:5] == [401] * 5
    assert set(statuses[5:]) == {429}

    items = client.get(
        f"{API}/users/{victim['user']['id']}/logins?limit=100", headers=_auth(admin)
    ).json()["data"]["items"]
    reasons = [item["reason"] for item in items]
    assert reasons.count("bad_password") == 1
    assert reasons.count("rate_limited") == 1
    assert len(items) < 12


def test_disabled_account_does_not_confirm_the_password(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """停用账号的登录尝试不再透露「口令对不对」（R10-04）。

    修复前：错口令 401、对口令 403 —— 403 就是「你猜对了」，而这条路径每次都
    先把计数清零，所以永远走不到 429，等于一个免费的慢速口令验证器。
    """
    from modules.identity import throttle

    monkeypatch.setattr(throttle, "penalty", lambda key: 0.0)

    admin = _sign_in(client)
    victim = _provision(client, admin, "pwd_probe", ["task:read"])
    client.delete(f"{API}/users/{victim['user']['id']}", headers=_auth(admin))

    wrong = client.post(
        f"{API}/login", json={"username": "pwd_probe", "password": "definitely-wrong"}
    )
    right = client.post(
        f"{API}/login", json={"username": "pwd_probe", "password": "pass-12345"}
    )
    assert wrong.status_code == right.status_code == 403
    assert right.json()["code"] == 40321
    # 反复用对口令也拿不到 429：状态码与次数都不再提供额外信息
    repeats = [
        client.post(
            f"{API}/login", json={"username": "pwd_probe", "password": "pass-12345"}
        ).status_code
        for _ in range(8)
    ]
    assert set(repeats) == {403}


def test_deactivation_kills_the_tokens_for_good(client: TestClient) -> None:
    """停用是**切断**，不是暂停（R10-02）。

    修复前停用只写 `is_active`，会话行原封不动；管理员重新启用后，同一张 access
    与 refresh 原地复活。refresh 有 7 天寿命且滑动续期，所以「先停用再启用」
    在真实运维动作里是一条能走通的路径。
    """
    admin = _sign_in(client)
    victim = _provision(client, admin, "deact_victim", ["task:read"])
    assert client.get(f"{API}/me", headers=_auth(victim)).status_code == 200

    client.delete(f"{API}/users/{victim['user']['id']}", headers=_auth(admin))
    assert client.get(f"{API}/me", headers=_auth(victim)).status_code == 401
    assert (
        client.post(
            f"{API}/refresh", json={"refreshToken": victim["refreshToken"]}
        ).status_code
        == 401
    )

    client.patch(
        f"{API}/users/{victim['user']['id']}",
        json={"isActive": True},
        headers=_auth(admin),
    )
    # 启用恢复的是「能重新登录」，不是「那两张旧票」——它们已经在停用那一刻被吊销
    assert client.get(f"{API}/me", headers=_auth(victim)).status_code == 401
    stale = client.post(f"{API}/refresh", json={"refreshToken": victim["refreshToken"]})
    assert stale.status_code == 401
    assert stale.json()["code"] == 40122

    fresh = _sign_in(client, "deact_victim", "pass-12345")
    assert client.get(f"{API}/me", headers=_auth(fresh)).status_code == 200


def test_change_password_signs_every_device_out(client: TestClient) -> None:
    """自助改密后会话列表不能说谎（R10-03）。

    修复前 `change_password` 只更新 `password_changed_at`，会话行的 `revoked_at`
    仍是 None、`expires_at` 还在 7 天后，于是「我的设备」把几台已经换不出票的
    设备列成在线。
    """
    admin = _sign_in(client)
    victim = _provision(client, admin, "pwd_change", ["task:read"])
    second = _sign_in(client, "pwd_change", "pass-12345", user_agent="second-device")

    changed = client.post(
        f"{API}/me/password",
        json={"oldPassword": "pass-12345", "newPassword": "brand-new-12345"},
        headers=_auth(second),
    )
    assert changed.status_code == 200, changed.text

    # 两台都下线：当前设备的 refresh 同样过不了 `iat < password_changed_at`
    assert client.get(f"{API}/me", headers=_auth(second)).status_code == 401
    assert client.get(f"{API}/me", headers=_auth(victim)).status_code == 401
    assert (
        client.post(
            f"{API}/refresh", json={"refreshToken": second["refreshToken"]}
        ).status_code
        == 401
    )

    fresh = _sign_in(client, "pwd_change", "brand-new-12345")
    sessions = client.get(f"{API}/me/sessions", headers=_auth(fresh)).json()["data"]
    assert len(sessions) == 1  # 旧的两个会话真的没了，而不是还挂在列表里
    assert sessions[0]["current"] is True


def test_disabled_account_attempt_is_audited_as_disabled(client: TestClient) -> None:
    admin = _sign_in(client)
    victim = _provision(client, admin, "audit_disabled", ["task:read"])
    client.delete(
        f"{API}/users/{victim['user']['id']}", headers=_auth(admin)
    )  # 停用

    blocked = client.post(
        f"{API}/login",
        json={"username": "audit_disabled", "password": "pass-12345"},
        headers={"User-Agent": "audit-agent"},
    )
    assert blocked.status_code == 403

    items = client.get(
        f"{API}/users/{victim['user']['id']}/logins?limit=10", headers=_auth(admin)
    ).json()["data"]["items"]
    assert items[0]["success"] is False
    assert items[0]["reason"] == "disabled"


def test_admin_can_read_another_users_login_history_but_needs_the_permission(
    client: TestClient,
) -> None:
    admin = _sign_in(client)
    victim = _provision(client, admin, "audit_read_target", ["task:read"])
    reader = _provision(client, admin, "audit_reader", ["identity:read"])

    ok = client.get(f"{API}/users/{victim['user']['id']}/logins", headers=_auth(reader))
    assert ok.status_code == 200

    denied = client.get(
        f"{API}/users/{victim['user']['id']}/logins", headers=_auth(victim)
    )
    assert denied.status_code == 403  # 普通成员只有自己的 /me/logins

    missing = client.get(f"{API}/users/not-a-real-id/logins", headers=_auth(admin))
    assert missing.status_code == 404  # id 写错要能看出来，而不是返回空列表


def test_login_history_of_a_superuser_is_not_readable_from_below(
    client: TestClient,
) -> None:
    """读审计也要看层级（R10-05）：`identity:read` 不该顺带能读超管的登录记录。

    返回的是 IP + UA + 失败原因，够推断一个人的常用网络、设备与作息，
    而「凌晨三点有一次失败登录」本身就是安全事件素材。
    """
    admin = _sign_in(client)
    auditor = _provision(client, admin, "auditor01", ["identity:read"])
    plain = _provision(client, admin, "plain_target", ["task:read"])

    # 读普通账号可以：审计员的职责就是这个
    assert (
        client.get(
            f"{API}/users/{plain['user']['id']}/logins", headers=_auth(auditor)
        ).status_code
        == 200
    )

    denied = client.get(
        f"{API}/users/{admin['user']['id']}/logins", headers=_auth(auditor)
    )
    assert denied.status_code == 403
    assert denied.json()["code"] == 40322


# --------------------------------------------------------------------------- #
# 5. 口令策略下发
# --------------------------------------------------------------------------- #
def test_password_policy_is_public_and_matches_the_backend(client: TestClient) -> None:
    resp = client.get(f"{API}/password-policy")
    assert resp.status_code == 200
    data = resp.json()["data"]

    from modules.identity.policy import PASSWORD_MAX_LENGTH, PASSWORD_MIN_LENGTH

    assert data["minLength"] == PASSWORD_MIN_LENGTH
    assert data["maxLength"] == PASSWORD_MAX_LENGTH
    assert data["forbidAllNumeric"] is True
    # 没实现就得报 false：报 true 会让前端拦下一个后端并不拦的输入
    assert data["forbidSameAsUsername"] is False
    assert str(PASSWORD_MIN_LENGTH) in data["message"]

    # 前端照这个策略构造的密码必须真的能过
    accepted = client.post(
        f"{API}/users",
        json={
            "username": "policy_probe",
            "password": "policy-pass-123",
            "roleIds": [],
        },
        headers=_auth(_sign_in(client)),
    )
    assert accepted.status_code == 201, accepted.text
