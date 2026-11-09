"""分类条的账要对得上。

这一组只有一条核心断言：**分类角标之和 == 同一个库的列表总数**。
它失败的症状极轻——「产品图 149」点进去只有 12 条——所以很少有人去查，
通常是让用户"刷新一下试试"。而它一旦不对，整个库的信任就没了：
用户不会相信任何一个数字。

其余三条覆盖"库参数翻译"与"团队可见性"：分类树是第②段的数据源，
它要是能跨团队读到，跨团队不可见这条规则就在界面上被绕开了。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from tests_template_helpers import API, login


def _headers(client: TestClient, username: str = "admin", password: str = "admin@12345"):
    return login(client, username, password)


def _categories(client: TestClient, library: str, headers: dict[str, str]) -> dict:
    response = client.get(f"{API}/asset-categories", params={"library": library}, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()["data"]


@pytest.mark.parametrize(
    ("library", "scope", "extra"),
    [
        ("personal", "mine", {}),
        ("brand", "public", {}),
        ("team:t_content", "team", {"teamId": "t_content"}),
        ("team:t_ops", "team", {"teamId": "t_ops"}),
    ],
)
def test_category_counts_add_up_to_the_list_total(
    client: TestClient, library: str, scope: str, extra: dict[str, str]
) -> None:
    """角标之和 == 列表总数。

    用 `pageSize=1` 拿总数：这条只关心 `total`，把整页拉下来会让用例
    依赖演示数据的条数，素材一变就要改测试。
    """
    admin = _headers(client)
    body = _categories(client, library, admin)
    listed = client.get(
        f"{API}/assets",
        params={"scope": scope, "pageSize": 1, **extra},
        headers=admin,
    )
    assert listed.status_code == 200, listed.text
    total = listed.json()["data"]["total"]
    counted = sum(item["count"] for item in body["items"])
    assert counted == total, f"{library}：角标合计 {counted}，列表总数 {total}"
    assert body["library"] == library


def test_every_library_has_an_inbox_with_a_badge(client: TestClient) -> None:
    """收件箱必须存在且带 `isInbox`。

    它是"我上传了但列表里没有"这个问题的唯一答案，所以角标为 0 时也要显示，
    前端靠 `isInbox` 判断"这一项不参与排序、不能删"。
    """
    body = _categories(client, "personal", _headers(client))
    inbox = [item for item in body["items"] if item["isInbox"]]
    assert len(inbox) == 1, f"个人库的收件箱不是恰好一个：{inbox}"
    assert body["inboxCount"] == inbox[0]["count"]


def test_categories_of_a_foreign_team_are_forbidden(client: TestClient) -> None:
    """不是成员读分类树是 403。

    分类树会泄露「这个团队按什么维度组织内容」，比列表更敏感——列表至少
    还是"有什么"，分类树是"怎么想"。所以它不能退化成空列表。
    """
    lead = _headers(client, "tm.content.lead", "tm@content2026")
    assert _categories(client, "team:t_content", lead)["items"]

    response = client.get(
        f"{API}/asset-categories", params={"library": "team:t_ops"}, headers=lead
    )
    assert response.status_code == 403, response.text


def test_unknown_library_is_rejected(client: TestClient) -> None:
    """库参数不认识时 400，不是回落到个人库。

    回落会让前端拼错参数时"看起来正常"——用户切到内容团队，看到的是自己的
    个人分类，而顶栏写的是内容团队。这种错误只能靠人眼发现。
    """
    response = client.get(
        f"{API}/asset-categories", params={"library": "t_content"}, headers=_headers(client)
    )
    assert response.status_code == 400, response.text
