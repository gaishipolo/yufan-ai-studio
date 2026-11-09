"""库边界：团队实体、成员关系与分类树。

三件事各有一组用例，因为它们失败的症状完全不同：

- **团队可见性**错了，顶栏会多出或少了库，用户点进去要么 403 要么看到别人的东西；
- **成员关系**错了，成员数与审批人列表会偏，而「人数多了一个」几乎不会被发现；
- **分类树**错了，收件箱计数会分裂成两个，用户不知道东西在哪个「未分类」里。

最后一条是 M1 里最隐蔽的：它不报错，只是数字对不上。
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient
from tests_template_helpers import ADMIN, API, login

from core.teams import TEAM_ACCOUNTS
from db.session import get_session_factory
from modules.asset.library_models import Category, Team, TeamMember

TEAM_LEAD = "tm.content.lead"
TEAM_LEAD_PASSWORD = "tm@content2026"
OPS_LEAD = "tm.ops.lead"
OPS_LEAD_PASSWORD = "tm@ops2026"


def _headers(client: TestClient, username: str = "admin", password: str = "admin@12345"):
    return login(client, username, password)


def test_seed_creates_three_teams_with_two_admins_each() -> None:
    """seed 之后三个团队各 2 名管理员。

    「各 2 名」不是随手定的：单人即单点故障，他休假时待审区会开始堆积，
    而「临时给谁开一下权限」是一次不可审计的例外。
    """

    async def read() -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
        async with get_session_factory()() as session:
            from sqlalchemy import select

            teams = list((await session.execute(select(Team.id, Team.status))).all())
            admins = list(
                (
                    await session.execute(
                        select(TeamMember.team_id, TeamMember.user_id).where(
                            TeamMember.role == "admin"
                        )
                    )
                ).all()
            )
            return teams, admins

    teams, admins = asyncio.run(read())
    assert len(teams) == 3
    assert all(status == "active" for _, status in teams)
    assert len(admins) == len(TEAM_ACCOUNTS) == 6
    per_team = {team_id for team_id, _ in admins}
    assert per_team == {team_id for team_id, _ in teams}


def test_admin_sees_all_teams_others_see_only_their_own(client: TestClient) -> None:
    """超管看全部，团队管理员只看自己那个，普通账号看不到任何团队。

    第三条容易被当成 bug，但它是 A1 的结论：跨团队不可见。要让两个团队互相
    看得见，走「提升到公共库」，那条路有审核。
    """
    admin = _headers(client)
    response = client.get(f"{API}/teams", headers=admin)
    assert response.status_code == 200, response.text
    body = response.json()["data"]
    assert len(body["items"]) == 3
    assert body["canManage"] is True

    lead = _headers(client, TEAM_LEAD, TEAM_LEAD_PASSWORD)
    response = client.get(f"{API}/teams", headers=lead)
    assert response.status_code == 200, response.text
    body = response.json()["data"]
    assert [item["id"] for item in body["items"]] == ["t_content"]
    assert body["items"][0]["role"] == "admin"
    # 能看自己的团队，但不能建/任命——`asset:team:manage` 只在平台管理员手里
    assert body["canManage"] is False

    editor = _headers(client, "editor", "editor@12345")
    response = client.get(f"{API}/teams", headers=editor)
    assert response.status_code == 200, response.text
    assert response.json()["data"]["items"] == []


def test_team_member_list_does_not_leak_names(client: TestClient) -> None:
    """团队列表不带成员明细。

    能看见团队 ≠ 能看见成员名单。后者是人事信息，要 `asset:team:member:manage`；
    把 `members` 塞进这个响应里，等于让目录页变成了通讯录。
    """
    body = client.get(f"{API}/teams", headers=_headers(client)).json()["data"]
    for item in body["items"]:
        assert set(item) == {"id", "name", "kind", "role", "memberCount"}
        assert item["memberCount"] >= 1


def test_one_person_cannot_join_the_same_team_twice() -> None:
    """`(team_id, user_id)` 唯一。

    重复关系会让成员数、审批人列表、权限合并三处口径全部错一遍，
    而症状只是「人数多了一个」——这类错误能存半年不被发现。
    """

    async def attempt() -> None:
        from sqlalchemy.exc import IntegrityError

        # 用 seed 已经被加进去的那一对，否则插的是一条全新关系——它当然不会冲突，
        # 用例会变成「什么都没验证」。（演示账号 u_001 不属于任何团队。）
        account = TEAM_ACCOUNTS[0]
        async with get_session_factory()() as session:
            session.add(
                TeamMember(team_id=account.team_id, user_id=account.user_id, role="member")
            )
            with pytest.raises(IntegrityError):
                await session.commit()
            await session.rollback()

    asyncio.run(attempt())


def test_every_library_has_exactly_one_inbox(client: TestClient) -> None:
    """每个库恰好一个收件箱。

    两棵收件箱会让「未分类 12」变成「6 + 6」，而用户不知道为什么两边各一半。
    唯一索引兜住写入，这条用例兜住「seed 建了几棵」。
    """

    async def read() -> list[tuple[str, str]]:
        from sqlalchemy import select

        async with get_session_factory()() as session:
            return list(
                (
                    await session.execute(
                        select(Category.scope, Category.owner_key).where(
                            Category.is_inbox.is_(True)
                        )
                    )
                ).all()
            )

    keys = asyncio.run(read())
    assert len(keys) == len(set(keys)), f"同一个库出现了多个收件箱：{keys}"
    # 公共库 + 三个团队 + 演示账号的个人库
    assert ("brand", "") in keys
    assert {("team", account.team_id) for account in TEAM_ACCOUNTS} <= set(keys)
    assert ("personal", ADMIN) in keys


def test_team_library_needs_an_explicit_team(client: TestClient) -> None:
    """`scope=team` 不带 `teamId` 是 400，不是「返回全部团队」。

    后者看着更"宽容"，实际是把跨团队不可见这条规则关掉了：前端漏传一个参数，
    用户就会在内容团队的库名底下看到运营团队的素材，而界面上没有任何提示。
    """
    response = client.get(
        f"{API}/assets", params={"scope": "team"}, headers=_headers(client)
    )
    assert response.status_code == 400, response.text


def test_team_library_is_scoped_by_team_not_by_owner(client: TestClient) -> None:
    """团队库按 `visibility + team_id` 取，**不按 owner**。

    这条正是「团队库退化成个人库」的探针：内容管理员自己的个人库里没有这些
    素材（owner 是演示账号），但团队库里必须有——写成 `owner == me` 的话
    第一段过、第二段红，症状在界面上只是「同事传的东西我看不到」。
    """
    lead = _headers(client, TEAM_LEAD, TEAM_LEAD_PASSWORD)

    team = client.get(
        f"{API}/assets",
        params={"scope": "team", "teamId": "t_content"},
        headers=lead,
    )
    assert team.status_code == 200, team.text
    items = team.json()["data"]["items"]
    assert items, "内容团队的库里一条素材都没有，seed 的团队归属没生效"
    assert all(item["ownerId"] != TEAM_LEAD for item in items), "团队库里全是自己传的，探针失效"

    mine = client.get(f"{API}/assets", params={"scope": "mine"}, headers=lead)
    assert mine.status_code == 200, mine.text
    mine_ids = {item["id"] for item in mine.json()["data"]["items"]}
    assert mine_ids.isdisjoint({item["id"] for item in items})


def test_cross_team_library_is_forbidden(client: TestClient) -> None:
    """不是成员就是 403，而不是「200 + 空列表」。

    空列表会被读成「这个团队还没传东西」，于是用户去问管理员要权限；
    403 说的是「你不属于这里」。两者的下一步动作完全不同。
    """
    response = client.get(
        f"{API}/assets",
        params={"scope": "team", "teamId": "t_ops"},
        headers=_headers(client, TEAM_LEAD, TEAM_LEAD_PASSWORD),
    )
    assert response.status_code == 403, response.text

    # 自己是 t_ops 的成员，同一个请求就该放行——否则上面的 403 可能只是
    # 「这条路径全挂」，而不是「跨团队被拦」
    ok = client.get(
        f"{API}/assets",
        params={"scope": "team", "teamId": "t_ops"},
        headers=_headers(client, OPS_LEAD, OPS_LEAD_PASSWORD),
    )
    assert ok.status_code == 200, ok.text


def test_platform_admin_can_open_every_team_library(client: TestClient) -> None:
    """有 `asset:team:manage` 的人能进任何团队库。

    否则「管理团队」只能看到自己所在的团队，而平台管理员本来就不属于任何
    团队——他会看到一个空的管理界面，只能改数据库。
    """
    admin = _headers(client)
    for team_id in ("t_content", "t_ops", "t_tech"):
        response = client.get(
            f"{API}/assets", params={"scope": "team", "teamId": team_id}, headers=admin
        )
        assert response.status_code == 200, f"{team_id}: {response.text}"


def test_an_asset_has_exactly_one_home_library(client: TestClient) -> None:
    """一条素材只有一个「家」：可见域是单一值。

    把团队素材也放进「我的素材」看着更贴心，但它会**破坏计数**：团队素材的
    `category_id` 在团队那棵树上，个人树的角标统计不到它，于是个人库出现
    「共 9 项、分类加起来 5」——这种差几条的对不上账只能靠人眼发现。
    要拿回自己库里走「取用」复制（§2.5 的往下拿）。
    """
    admin = _headers(client)
    mine = {
        item["id"]
        for item in client.get(f"{API}/assets", params={"scope": "mine"}, headers=admin)
        .json()["data"]["items"]
    }
    team = {
        item["id"]
        for item in client.get(
            f"{API}/assets", params={"scope": "team", "teamId": "t_content"}, headers=admin
        ).json()["data"]["items"]
    }
    assert team, "内容团队库里没有素材，seed 的团队归属没生效"
    assert mine and team
    assert mine.isdisjoint(team), f"两个库出现了同一条素材：{mine & team}"
