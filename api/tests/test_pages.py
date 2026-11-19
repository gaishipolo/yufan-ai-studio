"""页面权限：把「能看见哪一页」与「能调哪个接口」分成两层。

判据来自 `core/pages.py` 的那条规则：

> 接口权限只有在「至少一个声明它的页面被授予」时才有效；
> 不被任何页面声明的接口不参与门控。

这里用**真实接口**验，而不是只测 `gate()`：门控挂在 `resolve_identity()` 上，
只有走完整条请求链路（令牌 → 解析 → require）才能证明「前端关掉页面，
后端真的 401/403」，而不是「函数返回值对了但没接上」。
"""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient

from core.pages import page_catalog

API = "/api/v1"
IDENTITY = f"{API}/identity"


def _login(client: TestClient, username: str, password: str) -> dict[str, str]:
    resp = client.post(f"{IDENTITY}/login", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['data']['accessToken']}"}


def _admin(client: TestClient) -> dict[str, str]:
    return _login(client, "admin", "admin@12345")


def _suffix() -> str:
    return uuid.uuid4().hex[:6]


def _provision(
    client: TestClient, admin: dict[str, str], perms: list[str]
) -> tuple[dict, dict[str, str], dict]:
    """建「专属角色 + 账号」并登录，返回 (用户, 请求头, 角色)。"""
    code = f"page_{_suffix()}"
    role = client.post(
        f"{IDENTITY}/roles",
        json={"code": code, "name": code, "permissions": perms},
        headers=admin,
    )
    assert role.status_code == 201, role.text
    role_data = role.json()["data"]
    created = client.post(
        f"{IDENTITY}/users",
        json={
            "username": code,
            "password": "pass-12345",
            "displayName": code,
            "roleIds": [role_data["id"]],
        },
        headers=admin,
    )
    assert created.status_code == 201, created.text
    return created.json()["data"], _login(client, code, "pass-12345"), role_data


def _set_role_perms(
    client: TestClient, admin: dict[str, str], role_id: str, perms: list[str]
) -> None:
    resp = client.put(
        f"{IDENTITY}/roles/{role_id}/permissions",
        json={"permissions": perms},
        headers=admin,
    )
    assert resp.status_code == 200, resp.text


# --------------------------------------------------------------------------- #
# 1. 关掉页面 ⇒ 它的接口自动失效
# --------------------------------------------------------------------------- #
def test_disabling_a_page_disables_its_apis(client: TestClient) -> None:
    admin = _admin(client)
    _, headers, role = _provision(client, admin, ["task:read", "page:task-center"])

    allowed = client.get(f"{API}/tasks", headers=headers)
    assert allowed.status_code == 200, allowed.text

    # 只撤页面、保留接口码：这就是「前端把页面关了，后端跟着关」
    _set_role_perms(client, admin, role["id"], ["task:read"])

    denied = client.get(f"{API}/tasks", headers=headers)
    assert denied.status_code == 403, denied.text
    assert "task:read" in denied.json()["message"]


def test_shared_api_survives_while_any_page_is_granted(client: TestClient) -> None:
    """`catalog:read` 同时被「生成工作台」和「生图」声明。

    关掉其中一页不该把另一页打残 —— 否则错误会出现在一个看起来无关的接口上
    （「我明明开了生图，为什么生成工作台 403」）。
    """
    admin = _admin(client)
    _, headers, role = _provision(
        client, admin, ["catalog:read", "page:studio"]
    )

    assert client.get(f"{API}/models", headers=headers).status_code == 200

    # 换成「只开生图页」：catalog:read 仍然有效
    _set_role_perms(client, admin, role["id"], ["catalog:read", "page:image"])
    assert client.get(f"{API}/models", headers=headers).status_code == 200

    # 两页都不要了，catalog:read 才失效
    _set_role_perms(client, admin, role["id"], ["catalog:read"])
    assert client.get(f"{API}/models", headers=headers).status_code == 403


def test_api_without_any_page_declaration_is_not_gated(client: TestClient) -> None:
    """没被任何页面声明的接口（基础设施 / 后台能力）不参与门控。

    `export:read` 目前没有任何前端页面，若它也被门控，就等于「必须先开一页
    不相干的页面才能用导出」——那是把门控当成了全站开关。
    """
    admin = _admin(client)
    _, headers, _ = _provision(client, admin, ["export:read"])

    assert page_catalog.pages_for("export:read") == ()
    assert client.get(f"{API}/exports", headers=headers).status_code == 200


def test_superuser_bypasses_the_page_gate(client: TestClient) -> None:
    """超管不看页面：新页面还没配给任何角色时，他必须进得去把它配好。"""
    admin = _admin(client)
    assert client.get(f"{API}/tasks", headers=admin).status_code == 200
    assert client.get(f"{API}/models", headers=admin).status_code == 200


def test_page_wildcard_opens_every_page(client: TestClient) -> None:
    """`page:*` 等价于所有页面都开了；通配判定必须与鉴权主路径同源。"""
    admin = _admin(client)
    _, headers, _ = _provision(client, admin, ["task:read", "page:*"])

    assert client.get(f"{API}/tasks", headers=headers).status_code == 200

    # `/me` 原样下发 `page:*`，由前端的 `grantMatches` 展开（与后端同一条通配规则）。
    # 这里断言的是「通配确实留在权限集里且未被门控误删」，展开逻辑由
    # `shared/access/evaluate.test.ts` 覆盖。
    me = client.get(f"{IDENTITY}/me", headers=headers).json()["data"]
    assert "page:*" in me["permissions"]


# --------------------------------------------------------------------------- #
# 2. 界面要能解释「为什么我有权限却还是 403」
# --------------------------------------------------------------------------- #
def test_effective_permissions_reports_pages_and_gated_off(
    client: TestClient,
) -> None:
    admin = _admin(client)
    user, _, _ = _provision(client, admin, ["task:read", "catalog:read"])

    payload = client.get(
        f"{IDENTITY}/users/{user['id']}/effective-permissions", headers=admin
    ).json()["data"]

    assert payload["pages"] == []
    assert "task:read" in payload["gatedOff"]
    assert "catalog:read" in payload["gatedOff"]
    assert "task:read" not in payload["effective"]

    # 补上页面之后，接口权限回到 effective 里
    me_role = client.get(f"{IDENTITY}/users/{user['id']}", headers=admin).json()["data"]
    role_id = me_role["roles"][0]["id"]
    _set_role_perms(
        client, admin, role_id, ["task:read", "catalog:read", "page:task-center"]
    )
    payload = client.get(
        f"{IDENTITY}/users/{user['id']}/effective-permissions", headers=admin
    ).json()["data"]
    assert payload["pages"] == ["page:task-center"]
    assert "task:read" in payload["effective"]
    # catalog:read 仍然被门控：只开了任务中心，没有开声明它的那两页
    assert "catalog:read" in payload["gatedOff"]


# --------------------------------------------------------------------------- #
# 3. 目录本身的完整性（不依赖接口，纯声明层）
# --------------------------------------------------------------------------- #
def test_page_catalog_is_coherent() -> None:
    """页面码前缀、路由绝对路径与唯一性、requires 必须是已声明的接口码。"""
    from core.permissions import catalog

    known = catalog.codes()
    routes: dict[str, str] = {}
    problems: list[str] = []

    for page in page_catalog.all():
        if not page.code.startswith("page:"):
            problems.append(f"{page.code}: 前缀不是 page:")
        if page.code not in known:
            problems.append(f"{page.code}: 不在权限目录里（管理员将无处勾选）")
        if not page.route.startswith("/"):
            problems.append(f"{page.route}: 路由必须是绝对路径")
        if page.route in routes:
            problems.append(f"{page.route}: 被 {routes[page.route]} 与 {page.code} 重复占用")
        routes[page.route] = page.code
        for code in page.requires:
            if code not in known:
                problems.append(f"{page.code}: requires 里的 {code} 没有任何模块声明")

    assert not problems, "页面声明不自洽 -> " + "；".join(problems)


# --------------------------------------------------------------------------- #
# 4. 一次性迁移：老库的自定义角色不能因为升级就全线失效
# --------------------------------------------------------------------------- #
def test_one_time_migration_gives_old_roles_their_pages(client: TestClient) -> None:
    """升级时自动给「只有接口码」的角色补上页面码。

    不做这一步的话，老库里每个自定义角色的人都会「打开页面一片 403」，
    而配置界面看上去完全正常 —— 这是最坏的一种升级事故。
    """
    import asyncio

    admin = _admin(client)
    code = f"legacy_{uuid.uuid4().hex[:6]}"
    created = client.post(
        f"{IDENTITY}/roles",
        json={"code": code, "name": code, "permissions": ["task:read"]},
        headers=admin,
    )
    assert created.status_code == 201, created.text

    async def run(reset_marker: bool = False) -> dict[str, list[str]]:
        from sqlalchemy import delete

        from db.app_meta import AppMeta
        from db.session import get_session_factory
        from modules.identity.service import PAGES_MIGRATION_KEY, IdentityService

        async with get_session_factory()() as session:
            if reset_marker:
                # 启动引导已经跑过一次迁移（标记已写）；这里把标记抹掉，
                # 好让这条用例能单独验「迁移做了什么」
                await session.execute(
                    delete(AppMeta).where(AppMeta.key == PAGES_MIGRATION_KEY)
                )
                await session.commit()
            return await IdentityService(session).migrate_roles_to_pages()

    added = asyncio.run(run(reset_marker=True))
    assert code in added, f"老角色没有补上页面码：{added}"
    assert "page:task-center" in added[code]

    # 补完之后，这个角色的人能真的调接口
    user = client.post(
        f"{IDENTITY}/users",
        json={
            "username": f"legacy_u_{uuid.uuid4().hex[:6]}",
            "password": "pass-12345",
            "displayName": "老角色用户",
            "roleIds": [created.json()["data"]["id"]],
        },
        headers=admin,
    )
    assert user.status_code == 201, user.text
    headers = _login(
        client, user.json()["data"]["username"], "pass-12345"
    )
    assert client.get(f"{API}/tasks", headers=headers).status_code == 200

    # 幂等：没有新增受门控的码时不动任何授权
    assert asyncio.run(run()) == {}


def test_new_page_declaration_backfills_existing_roles(client: TestClient) -> None:
    """这一版新声明的页面关系，必须补到已存在的授权上。

    场景：某模块这一版才把 `model_driver:read` 关联到「任务中心」页。
    老库里持有这个码的角色**升级前能用、升级后 403** —— 而配置界面完全正常，
    没人知道该改哪里。

    这就是「一次性迁移」的漏洞：它只在第一次跑，之后新增的页面关系没人管。
    """
    import asyncio

    admin = _admin(client)
    code = f"legacy2_{uuid.uuid4().hex[:6]}"
    created = client.post(
        f"{IDENTITY}/roles",
        json={"code": code, "name": code, "permissions": ["model_driver:read"]},
        headers=admin,
    )
    assert created.status_code == 201, created.text

    async def run(reset: bool = False) -> dict[str, list[str]]:
        from sqlalchemy import delete

        from db.app_meta import AppMeta
        from db.session import get_session_factory
        from modules.identity.service import PAGES_MIGRATION_KEY, IdentityService

        async with get_session_factory()() as session:
            if reset:
                await session.execute(
                    delete(AppMeta).where(AppMeta.key == PAGES_MIGRATION_KEY)
                )
                await session.commit()
            return await IdentityService(session).migrate_roles_to_pages()

    # 首次运行（无存档）→ 全部受门控的码都算新增，这个角色该被补上页面码
    added = asyncio.run(run(reset=True))
    assert code in added, f"老角色没有补上页面码：{added}"
    assert "page:task-center" in added[code], (
        f"model_driver:read 归任务中心页，补的应该是它：{added[code]}"
    )

    # 补完之后这个人真的能用那个接口
    user = client.post(
        f"{IDENTITY}/users",
        json={
            "username": f"legacy2_u_{uuid.uuid4().hex[:6]}",
            "password": "pass-12345",
            "displayName": "老角色用户2",
            "roleIds": [created.json()["data"]["id"]],
        },
        headers=admin,
    )
    assert user.status_code == 201, user.text
    headers = _login(client, user.json()["data"]["username"], "pass-12345")
    assert client.get(f"{API}/tasks", headers=headers).status_code == 200


def test_migration_does_not_reopen_pages_admin_closed(client: TestClient) -> None:
    """管理员后来特意关掉的页面，不该被增量迁移重新打开。

    增量迁移只补「这一版才开始受门控的码」。已经门控过的码不在差集里，
    所以管理员的决定被保留。
    """
    import asyncio

    admin = _admin(client)

    async def run() -> dict[str, list[str]]:
        from db.session import get_session_factory
        from modules.identity.service import IdentityService

        async with get_session_factory()() as session:
            return await IdentityService(session).migrate_roles_to_pages()

    # 跑一次把当前映射存档下来
    asyncio.run(run())

    # 造一个「有接口码、但没有对应页面码」的授权，模拟管理员手工关掉页面
    code = f"closed_{uuid.uuid4().hex[:6]}"
    created = client.post(
        f"{IDENTITY}/roles",
        json={"code": code, "name": code, "permissions": ["task:read"]},
        headers=admin,
    )
    assert created.status_code == 201, created.text
    role_id = created.json()["data"]["id"]

    # 手动把它改成「有接口码、没有页面码」——这正是管理员关掉页面后的状态
    resp = client.put(
        f"{IDENTITY}/roles/{role_id}/permissions",
        json={"permissions": ["task:read"]},
        headers=admin,
    )
    assert resp.status_code == 200, resp.text

    # 再跑一次迁移：这个码早就受门控了，不该被补回来
    added = asyncio.run(run())
    assert code not in added, (
        f"增量迁移把管理员关掉的页面重新打开了：{added}"
    )
