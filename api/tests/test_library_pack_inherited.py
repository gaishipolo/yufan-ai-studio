"""封装共享后，成员素材「继承可见」的读取权。

共享是额外开一扇门，不改成员的归属：包对谁可见，谁就能在模板库挑到并用它的
成员；但资产库列表不会因此多出散图，写路径也不因此放开（能用 ≠ 能改）。
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient
from library_support import LIB_RW, admin_headers, make_user, upload_image
from sqlalchemy import delete

from core.context import CurrentUser
from core.ports.library_lookup import get_library_lookup
from db.session import get_session_factory
from modules.library.models import LibraryAsset, LibraryPackItem, LibraryShareRequest


@pytest.fixture(autouse=True)
def _pack_cleanup():
    """本文件造的包共享给团队后，所有账号都看得到 —— 不清理会污染同轮其它用例的
    「全量包列表」断言（`test_pack_list_filters_by_slot_candidate_tags` 就吃过）。
    """
    created: list[str] = []
    yield created

    async def purge() -> None:
        if not created:
            return
        async with get_session_factory()() as session:
            await session.execute(
                delete(LibraryShareRequest).where(LibraryShareRequest.asset_id.in_(created))
            )
            await session.execute(
                delete(LibraryPackItem).where(LibraryPackItem.pack_id.in_(created))
            )
            await session.execute(delete(LibraryAsset).where(LibraryAsset.id.in_(created)))
            await session.commit()

    asyncio.run(purge())


def _pack(client: TestClient, api: str, headers: dict[str, str], ids: list[str]) -> str:
    tag = client.post(f"{api}/library/tags", json={"name": "共同产品"}, headers=headers)
    assert tag.status_code in (200, 201), tag.text
    tag_id = tag.json()["data"]["id"]
    for asset_id in ids:
        resp = client.patch(
            f"{api}/library/assets/{asset_id}", json={"tagIds": [tag_id]}, headers=headers
        )
        assert resp.status_code == 200, resp.text
    body = client.post(
        f"{api}/library/packs", json={"name": "团队共用包", "assetIds": ids}, headers=headers
    )
    assert body.status_code == 201, body.text
    return body.json()["data"]["id"]


def _setup(client: TestClient, api: str, cleanup: list[str]):
    admin = admin_headers(client)
    perms = LIB_RW + [
        "library:share:direct",
        "library:share:approve:team",
        "library:share:approve:group",
    ]
    owner, owner_h = make_user(client, admin, perms)
    other, other_h = make_user(client, admin, LIB_RW)
    first = upload_image(client, api, owner_h, name="成员一.png", color="lightcoral")
    second = upload_image(client, api, owner_h, name="成员二.png", color="lightblue")
    pack_id = _pack(client, api, owner_h, [first["id"], second["id"]])
    share = client.post(
        f"{api}/library/share-requests",
        json={"assetId": pack_id, "target": "team", "groupId": ""},
        headers=owner_h,
    )
    assert share.status_code == 201, share.text
    assert share.json()["data"]["status"] == "approved"
    cleanup.append(pack_id)
    return owner, owner_h, other, other_h, pack_id, [first["id"], second["id"]]


def test_team_pack_members_are_selectable_and_readable(
    client: TestClient, api: str, _pack_cleanup: list[str]
) -> None:
    _, _, other, other_h, pack_id, member_ids = _setup(client, api, _pack_cleanup)

    selection = client.get(
        f"{api}/library/selection", params={"kind": "image", "pageSize": 100}, headers=other_h
    )
    assert selection.status_code == 200, selection.text
    listed = {item["id"] for item in selection.json()["data"]["items"]}
    assert set(member_ids) <= listed

    # 详情 / 原文件这两条只读链路也要认继承来的读取权，否则「挑得到、用不了」
    for member_id in member_ids:
        detail = client.get(f"{api}/library/assets/{member_id}", headers=other_h)
        assert detail.status_code == 200, detail.text
        file_resp = client.get(f"{api}/library/assets/{member_id}/file", headers=other_h)
        assert file_resp.status_code == 200, file_resp.text

    # 编译 / 生成走的注入口也必须认同一口径
    async def check_port() -> set[str]:
        async with get_session_factory()() as session:
            actor = CurrentUser(id=other["id"], name=other["username"])
            return await get_library_lookup().readable_ids(session, actor, member_ids)

    assert set(member_ids) <= asyncio.run(check_port())


def test_inherited_pack_members_are_counted_in_tag_list(
    client: TestClient, api: str, _pack_cleanup: list[str]
) -> None:
    """模板库标签词表要看见「包可见 ⇒ 成员可读」的成员。

    选择器已经认继承可见；标签计数若只按 tab_clause 算，普通用户会看到
    素材列表 47 张、标签却只剩少数几个（成员身上的标签计数为 0 被前端隐藏）。
    """
    _, _, _, other_h, _, member_ids = _setup(client, api, _pack_cleanup)

    tags = client.get(
        f"{api}/library/tags",
        params={"kind": "image", "includePackMembers": "true"},
        headers=other_h,
    )
    assert tags.status_code == 200, tags.text
    shared = [row for row in tags.json()["data"] if row["name"] == "共同产品"]
    assert shared, "共享包上的标签没有出现在普通用户的词表里"
    assert any(row["assetCount"] >= len(member_ids) for row in shared), shared


def test_inherited_members_stay_out_of_library_list_and_write_paths(
    client: TestClient, api: str, _pack_cleanup: list[str]
) -> None:
    _, _, _, other_h, _, member_ids = _setup(client, api, _pack_cleanup)

    listing = client.get(f"{api}/library/assets", params={"pageSize": 100}, headers=other_h)
    assert listing.status_code == 200, listing.text
    listed = {item["id"] for item in listing.json()["data"]["items"]}
    assert not (set(member_ids) & listed), "资产库列表不该因为包可见就多出散图"

    for member_id in member_ids:
        patch = client.patch(
            f"{api}/library/assets/{member_id}", json={"name": "冒名改名"}, headers=other_h
        )
        assert patch.status_code == 404, patch.text


def test_revoking_pack_share_closes_the_door(
    client: TestClient, api: str, _pack_cleanup: list[str]
) -> None:
    _, owner_h, _, other_h, pack_id, member_ids = _setup(client, api, _pack_cleanup)

    req = client.post(
        f"{api}/library/share-requests",
        json={
            "assetId": pack_id,
            "action": "revoke",
            "target": "team",
            "groupId": "",
            "reason": "不再共享",
        },
        headers=owner_h,
    )
    assert req.status_code == 201, req.text
    record = req.json()["data"]
    # 自己就是审批人时后端当场通过；再点「审批」会 409
    if record["status"] == "pending":
        decision = client.post(
            f"{api}/library/share-requests/{record['id']}/approve",
            json={},
            headers=owner_h,
        )
        assert decision.status_code == 200, decision.text

    selection = client.get(
        f"{api}/library/selection", params={"kind": "image", "pageSize": 100}, headers=other_h
    )
    listed = {item["id"] for item in selection.json()["data"]["items"]}
    assert not (set(member_ids) & listed)
    detail = client.get(f"{api}/library/assets/{member_ids[0]}", headers=other_h)
    assert detail.status_code == 404, detail.text
