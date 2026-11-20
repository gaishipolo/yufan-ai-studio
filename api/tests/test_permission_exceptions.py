"""个人例外：只对一个人关掉一项，不动共享单元。

判据来自 `core.authz` 的模块文档：

    effective = 页面门控(摊开通配(四层并集) − 个人例外)

以前减法只能做到**共享单元**：撤角色会连累同角色的人，把人移出角色会丢掉
该角色的全部权限。所以「撤掉张三的 task:delete」实际要动两三处、还要给别人
补回去 —— 例外把粒度降到「这个人 + 这一项」。

全部用**真实接口**验：例外挂在 `resolve_identity()` 上，只有走完整条请求
链路（令牌 → 解析 → require）才能证明「他 403、同角色的另一个人 200」，
而不是「仓库层删对了行」。
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.test_pages import API, IDENTITY, _admin, _login, _suffix

DELETE_TASKS = f"{API}/tasks"


def _role(client: TestClient, admin: dict[str, str], perms: list[str]) -> dict:
    code = f"exc_{_suffix()}"
    resp = client.post(
        f"{IDENTITY}/roles",
        json={"code": code, "name": code, "permissions": perms},
        headers=admin,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["data"]


def _user(
    client: TestClient, admin: dict[str, str], role_id: str
) -> tuple[dict, dict[str, str]]:
    name = f"exc_{_suffix()}"
    resp = client.post(
        f"{IDENTITY}/users",
        json={
            "username": name,
            "password": "pass-12345",
            "displayName": name,
            "roleIds": [role_id],
        },
        headers=admin,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["data"], _login(client, name, "pass-12345")


def _put_exceptions(
    client: TestClient, admin: dict[str, str], uid: str, codes: list[str]
):
    return client.put(
        f"{IDENTITY}/users/{uid}/permission-exceptions",
        json={"exceptions": codes},
        headers=admin,
    )


def _detail(client: TestClient, admin: dict[str, str], uid: str) -> dict:
    resp = client.get(f"{IDENTITY}/users/{uid}/effective-permissions", headers=admin)
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


def _delete_tasks(client: TestClient, headers: dict[str, str]) -> int:
    """`task:delete` 的真实判定点。用一个不存在的 id：只验权限，不真删东西。"""
    return client.request(
        "DELETE", DELETE_TASKS, json={"ids": ["task_does_not_exist"]}, headers=headers
    ).status_code


# --------------------------------------------------------------------------- #
# 1. 关掉他一个人，同角色的其他人不受影响
# --------------------------------------------------------------------------- #
def test_exception_revokes_one_person_and_leaves_the_role_alone(
    client: TestClient,
) -> None:
    admin = _admin(client)
    role = _role(client, admin, ["task:*", "page:task-center"])
    victim, victim_headers = _user(client, admin, role["id"])
    peer, peer_headers = _user(client, admin, role["id"])

    assert _delete_tasks(client, victim_headers) == 200
    assert _delete_tasks(client, peer_headers) == 200

    resp = _put_exceptions(client, admin, victim["id"], ["task:delete"])
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"] == ["task:delete"]

    # 他 403，同角色的另一个人 200 —— 角色一行没动
    denied = client.request(
        "DELETE",
        DELETE_TASKS,
        json={"ids": ["task_does_not_exist"]},
        headers=victim_headers,
    )
    assert denied.status_code == 403, denied.text
    assert "task:delete" in denied.json()["message"]
    assert _delete_tasks(client, peer_headers) == 200

    detail = _detail(client, admin, victim["id"])
    assert "task:delete" in detail["denied"]
    assert "task:delete" in detail["deniedOff"]
    assert "task:delete" not in detail["effective"]
    assert "task:delete" not in detail["gatedOff"]


def test_clearing_the_exception_restores_access(client: TestClient) -> None:
    admin = _admin(client)
    role = _role(client, admin, ["task:*", "page:task-center"])
    victim, headers = _user(client, admin, role["id"])

    _put_exceptions(client, admin, victim["id"], ["task:delete"])
    assert _delete_tasks(client, headers) == 403

    resp = _put_exceptions(client, admin, victim["id"], [])
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"] == []
    assert _delete_tasks(client, headers) == 200
    assert _detail(client, admin, victim["id"])["denied"] == []


# --------------------------------------------------------------------------- #
# 2. 例外可以作用在页面上：那一页的接口一并失效
# --------------------------------------------------------------------------- #
def test_exception_on_a_page_gates_its_interfaces(client: TestClient) -> None:
    admin = _admin(client)
    role = _role(
        client, admin, ["task:*", "library:read", "page:task-center", "page:library"]
    )
    victim, headers = _user(client, admin, role["id"])
    assert _delete_tasks(client, headers) == 200

    resp = _put_exceptions(client, admin, victim["id"], ["page:task-center"])
    assert resp.status_code == 200, resp.text

    assert _delete_tasks(client, headers) == 403
    detail = _detail(client, admin, victim["id"])
    # 原因归到「你自己关的」，不归「页面没开」—— 界面对这两种给的动动作不同
    assert "task:read" in detail["deniedOff"]
    assert "task:read" not in detail["gatedOff"]
    # 别的页面不受牵连
    assert "page:library" in detail["pages"]


# --------------------------------------------------------------------------- #
# 3. 回归：改允许项不能把例外洗掉
# --------------------------------------------------------------------------- #
def test_editing_personal_permissions_keeps_the_exception(client: TestClient) -> None:
    """`set_grants` 是整组替换；`delete` 不带 effect 条件就会把例外一起删。

    这个 bug 在界面上只表现为「权限多了一项」，没人会怀疑是保存动作弄丢的。
    """
    admin = _admin(client)
    role = _role(client, admin, ["task:*", "page:task-center"])
    victim, _ = _user(client, admin, role["id"])

    client.put(
        f"{IDENTITY}/users/{victim['id']}/permissions",
        json={"permissions": ["template_pack:create"]},
        headers=admin,
    )
    _put_exceptions(client, admin, victim["id"], ["task:delete"])

    resp = client.put(
        f"{IDENTITY}/users/{victim['id']}/permissions",
        json={"permissions": ["template_pack:create", "template_pack:publish"]},
        headers=admin,
    )
    assert resp.status_code == 200, resp.text

    detail = _detail(client, admin, victim["id"])
    assert detail["denied"] == ["task:delete"], "改允许项把例外洗掉了"
    assert detail["personal"] == ["template_pack:create", "template_pack:publish"]


# --------------------------------------------------------------------------- #
# 4. 例外只接受「具体的、可管理的」码
# --------------------------------------------------------------------------- #
def test_exception_rejects_wildcards_identity_and_unknown(client: TestClient) -> None:
    admin = _admin(client)
    role = _role(client, admin, ["task:*", "page:task-center"])
    victim, _ = _user(client, admin, role["id"])

    wildcard = _put_exceptions(client, admin, victim["id"], ["task:*"])
    assert wildcard.status_code == 400, wildcard.text
    assert wildcard.json()["code"] == 40024

    locked = _put_exceptions(client, admin, victim["id"], ["identity:read"])
    assert locked.status_code == 400, locked.text
    assert locked.json()["code"] == 40025

    unknown = _put_exceptions(client, admin, victim["id"], ["nope:nothing"])
    assert unknown.status_code == 400, unknown.text
    assert unknown.json()["code"] == 40022

    # 全都被拒之后库里仍然是空的（没有半截写入）
    assert _detail(client, admin, victim["id"])["denied"] == []


def test_exception_refused_for_a_full_privilege_account(client: TestClient) -> None:
    """持有 `*` 的账号：求值在 `*` 处短路，例外不会生效，所以不许写。"""
    admin = _admin(client)
    role = _role(client, admin, ["*"])
    target, _ = _user(client, admin, role["id"])

    resp = _put_exceptions(client, admin, target["id"], ["task:delete"])
    assert resp.status_code == 409, resp.text
    assert resp.json()["code"] == 40928

    # 必须收尾：持有 `*` 的**启用**账号会被算作「超管等价」
    # （`active_superuser_ids` 与鉴权侧同源），留着它会让后面
    # 「不能撤掉最后一个超管」那类用例数出 2 个人 —— 实测过一次。
    client.delete(f"{IDENTITY}/users/{target['id']}", headers=admin)
    client.delete(f"{IDENTITY}/roles/{role['id']}", headers=admin)


# --------------------------------------------------------------------------- #
# 5. 被否决的码不能授给别人（否则例外就是提权绕过）
# --------------------------------------------------------------------------- #
def test_denied_code_cannot_be_granted_away(client: TestClient) -> None:
    admin = _admin(client)
    delegator_role = _role(
        client,
        admin,
        [
            "task:*",
            "page:task-center",
            # 管理类权限归「用户与权限」这一页，没有它会被门控砍掉
            "identity:user:permission:manage",
            "page:user-access",
        ],
    )
    delegator, delegator_headers = _user(client, admin, delegator_role["id"])
    target, _ = _user(client, admin, _role(client, admin, [])["id"])

    _put_exceptions(client, admin, delegator["id"], ["task:delete"])

    resp = client.put(
        f"{IDENTITY}/users/{target['id']}/permissions",
        json={"permissions": ["task:delete"]},
        headers=delegator_headers,
    )
    assert resp.status_code == 403, resp.text
    assert resp.json()["code"] == 40323
    assert "task:delete" in resp.json()["details"]["missing"]
