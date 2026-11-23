"""版本语义（乐观锁）：`0` 不是合法版本，「不校验」只能显式写 `None`。

背景：旧契约里 `expected_version = 0` 是「不校验」的哨兵值，于是「漏传版本」
与「显式不校验」成了同一件事——漏传会被**静默降级**为不校验：接口看着有
并发保护，实际上一条基于旧快照的写照样生效（REVIEW_2026-11-23 严重#1）。

P2-2 之后，版本只有两种合法表达：

- **一个真实版本（≥1）** → 做乐观锁校验，比不上就 409；
- **字段不出现 / `null`** → 明确不校验。

`0` 会在入参层被直接拒（422）——它既不是合法版本，也不再能冒充「不校验」。

这些用例守的是**契约层**的语义：任何新增的写接口，只要用同一个版本字段，
就必须继承同一条规则。
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi.testclient import TestClient
from library_support import (
    LIB_RW,
    admin_headers,
    fetch_asset,
    make_group,
    make_session_asset,
    make_user,
    upload_image,
)


def _assert_rejected_as_invalid_version(response: Any) -> None:
    """断言是**入参校验**拒的（422），且指出了版本这一项。

    项目的 422 响应体是 `{code, message, data, details}`，`details` 里每一项是
    `{field, message}`——`field` 是入参字段名（camelCase 的 alias）。
    """
    assert response.status_code == 422, response.text
    body = response.json()
    fields = [str(item.get("field", "")) for item in body.get("details", [])]
    assert any("expectedVersion" in field for field in fields), (
        f"422 指的不是版本字段：{body}"
    )


def _share_request_row(asset_id: str) -> dict[str, Any]:
    """直接读申请单行：快照版本在出参里看不到（也不该给前端看）。"""
    from sqlalchemy import select

    from db.session import get_session_factory
    from modules.library.models import LibraryShareRequest

    async def run() -> dict[str, Any]:
        async with get_session_factory()() as session:
            row = await session.scalar(
                select(LibraryShareRequest)
                .where(LibraryShareRequest.asset_id == asset_id)
                .order_by(LibraryShareRequest.created_at.desc())
            )
            assert row is not None, "没有建出申请单"
            return {
                "id": row.id,
                "status": row.status,
                "expected_version": row.expected_version,
            }

    return asyncio.run(run())


# --------------------------------------------------------------------------- #
# 0 不再是合法版本
# --------------------------------------------------------------------------- #
def test_asset_patch_with_zero_version_is_rejected(client: TestClient, api: str) -> None:
    admin = admin_headers(client)
    owner, owner_h = make_user(client, admin, LIB_RW)
    asset = upload_image(client, api, owner_h)

    response = client.patch(
        f"{api}/library/assets/{asset['id']}",
        json={"name": "新名字.png", "expectedVersion": 0},
        headers=owner_h,
    )
    _assert_rejected_as_invalid_version(response)


def test_set_purpose_with_zero_version_is_rejected(client: TestClient, api: str) -> None:
    admin = admin_headers(client)
    owner, owner_h = make_user(client, admin, LIB_RW)
    asset = upload_image(client, api, owner_h)

    response = client.post(
        f"{api}/library/assets/{asset['id']}/purpose",
        json={"purpose": "", "expectedVersion": 0},
        headers=owner_h,
    )
    _assert_rejected_as_invalid_version(response)


def test_share_request_with_zero_version_is_rejected(client: TestClient, api: str) -> None:
    admin = admin_headers(client)
    owner, owner_h = make_user(client, admin, LIB_RW)
    group = make_group(client, admin, [owner["id"]])
    asset = upload_image(client, api, owner_h)

    response = client.post(
        f"{api}/library/share-requests",
        json={
            "assetId": asset["id"],
            "action": "share",
            "target": "group",
            "groupId": group["id"],
            "expectedVersion": 0,
        },
        headers=owner_h,
    )
    _assert_rejected_as_invalid_version(response)


def test_share_decision_with_zero_version_is_rejected(client: TestClient, api: str) -> None:
    admin = admin_headers(client)
    owner, owner_h = make_user(client, admin, LIB_RW)
    group = make_group(client, admin, [owner["id"]])
    asset = upload_image(client, api, owner_h)

    created = client.post(
        f"{api}/library/share-requests",
        json={
            "assetId": asset["id"],
            "action": "share",
            "target": "group",
            "groupId": group["id"],
        },
        headers=owner_h,
    )
    assert created.status_code == 201, created.text
    request_id = created.json()["data"]["id"]

    response = client.post(
        f"{api}/library/share-requests/{request_id}/approve",
        json={"note": "同意", "expectedVersion": 0},
        headers=admin,
    )
    _assert_rejected_as_invalid_version(response)


def test_draft_decision_with_zero_version_is_rejected(client: TestClient, api: str) -> None:
    admin = admin_headers(client)
    owner, owner_h = make_user(client, admin, LIB_RW + ["library:draft:review"])
    asset_id = make_session_asset(state="draft", owner_id=owner["id"])

    response = client.post(
        f"{api}/library/drafts/{asset_id}/approve",
        json={"reason": "收下", "expectedVersion": 0},
        headers=owner_h,
    )
    _assert_rejected_as_invalid_version(response)


def test_text_update_with_zero_version_is_rejected(client: TestClient, api: str) -> None:
    admin = admin_headers(client)
    owner, owner_h = make_user(client, admin, LIB_RW)
    asset = upload_image(client, api, owner_h)

    response = client.put(
        f"{api}/library/assets/{asset['id']}/text",
        json={"content": "正文", "expectedVersion": 0},
        headers=owner_h,
    )
    _assert_rejected_as_invalid_version(response)


def test_bulk_with_zero_version_is_rejected(client: TestClient, api: str) -> None:
    admin = admin_headers(client)
    owner, owner_h = make_user(client, admin, LIB_RW)
    asset = upload_image(client, api, owner_h)

    response = client.post(
        f"{api}/library/assets/bulk",
        json={
            "ids": [asset["id"]],
            "action": "rename",
            "payload": {"name": "批量改名.png"},
            "expectedVersions": {asset["id"]: 0},
        },
        headers=owner_h,
    )
    _assert_rejected_as_invalid_version(response)


# --------------------------------------------------------------------------- #
# 不校验必须显式表达
# --------------------------------------------------------------------------- #
def test_omitting_the_version_means_no_check(client: TestClient, api: str) -> None:
    """不传版本 = 明确不校验：请求成立，且资产被改到了。

    「不传」是合法的显式表达——单页操作没必要为了一个数字失败一次。
    关键在于它**不再与「漏传」混为一谈**：0 会被拒，漏传的调用方拿到的
    永远是「不校验」这一个确定语义，而不是「猜 0 是不是不校验」。
    """
    admin = admin_headers(client)
    owner, owner_h = make_user(client, admin, LIB_RW)
    asset = upload_image(client, api, owner_h)

    response = client.patch(
        f"{api}/library/assets/{asset['id']}",
        json={"name": "不带版本.png"},
        headers=owner_h,
    )
    assert response.status_code == 200, response.text
    assert response.json()["data"]["name"] == "不带版本.png"


def test_explicit_null_version_means_no_check(client: TestClient, api: str) -> None:
    """显式写 `null` 与不传是同一件事：明确不校验。"""
    admin = admin_headers(client)
    owner, owner_h = make_user(client, admin, LIB_RW)
    asset = upload_image(client, api, owner_h)

    response = client.patch(
        f"{api}/library/assets/{asset['id']}",
        json={"name": "显式 null.png", "expectedVersion": None},
        headers=owner_h,
    )
    assert response.status_code == 200, response.text


# --------------------------------------------------------------------------- #
# 带真实版本时，保护照常生效
# --------------------------------------------------------------------------- #
def test_a_stale_real_version_is_still_rejected(client: TestClient, api: str) -> None:
    """带真实版本 → 该拦的照样拦（409）。版本语义改了，保护没跟着松。"""
    admin = admin_headers(client)
    owner, owner_h = make_user(client, admin, LIB_RW)
    asset = upload_image(client, api, owner_h)

    # 先改一次名字：资产的 state_version 往前走
    first = client.patch(
        f"{api}/library/assets/{asset['id']}",
        json={"name": "第一次.png", "expectedVersion": asset["stateVersion"]},
        headers=owner_h,
    )
    assert first.status_code == 200, first.text

    # 再拿**旧版本**改：应当 409
    stale = client.patch(
        f"{api}/library/assets/{asset['id']}",
        json={"name": "第二次.png", "expectedVersion": asset["stateVersion"]},
        headers=owner_h,
    )
    assert stale.status_code == 409, stale.text


def test_bulk_still_requires_a_version_for_versioned_actions(
    client: TestClient, api: str
) -> None:
    """批量改名缺版本 → 400（缺版本不是「不校验」，是调用方没给）。"""
    admin = admin_headers(client)
    owner, owner_h = make_user(client, admin, LIB_RW)
    asset = upload_image(client, api, owner_h)

    response = client.post(
        f"{api}/library/assets/bulk",
        json={
            "ids": [asset["id"]],
            "action": "rename",
            "payload": {"name": "批量改名.png"},
        },
        headers=owner_h,
    )
    assert response.status_code == 400, response.text


# --------------------------------------------------------------------------- #
# 快照回落：前端没带版本时，申请单记的是服务端的真实版本
# --------------------------------------------------------------------------- #
def test_share_request_snapshot_falls_back_to_the_server_side_version(
    client: TestClient, api: str
) -> None:
    """前端没带版本 → 申请单快照取服务端当时的版本（不是 0）。

    这是「快照回落」路径：它保证审批端拿得到一个**真实**的比对基准，
    也就是这一列永远不会出现 0。改动版本语义之后这条路径依然有效。
    """
    admin = admin_headers(client)
    owner, owner_h = make_user(client, admin, LIB_RW)
    group = make_group(client, admin, [owner["id"]])
    asset = upload_image(client, api, owner_h)

    created = client.post(
        f"{api}/library/share-requests",
        json={
            "assetId": asset["id"],
            "action": "share",
            "target": "group",
            "groupId": group["id"],
        },
        headers=owner_h,
    )
    assert created.status_code == 201, created.text

    row = _share_request_row(asset["id"])
    assert row["expected_version"] == asset["stateVersion"], (
        f"快照版本应为服务端当时的 {asset['stateVersion']}，实际 {row['expected_version']}"
    )
    assert row["expected_version"] > 0, "快照版本不能是 0"


def test_share_request_with_a_real_version_records_that_version(
    client: TestClient, api: str
) -> None:
    """前端带了真实版本 → 快照就记那个版本。"""
    admin = admin_headers(client)
    owner, owner_h = make_user(client, admin, LIB_RW)
    group = make_group(client, admin, [owner["id"]])
    asset = upload_image(client, api, owner_h)

    created = client.post(
        f"{api}/library/share-requests",
        json={
            "assetId": asset["id"],
            "action": "share",
            "target": "group",
            "groupId": group["id"],
            "expectedVersion": asset["stateVersion"],
        },
        headers=owner_h,
    )
    assert created.status_code == 201, created.text

    row = _share_request_row(asset["id"])
    assert row["expected_version"] == asset["stateVersion"]


def test_stale_version_on_share_request_is_rejected(client: TestClient, api: str) -> None:
    """带了真实版本但已过期 → 409：保护在共享申请这条路上也生效。"""
    admin = admin_headers(client)
    owner, owner_h = make_user(client, admin, LIB_RW)
    group = make_group(client, admin, [owner["id"]])
    asset = upload_image(client, api, owner_h)

    # 先把资产改掉，版本往前走
    renamed = client.patch(
        f"{api}/library/assets/{asset['id']}",
        json={"name": "先改一次.png", "expectedVersion": asset["stateVersion"]},
        headers=owner_h,
    )
    assert renamed.status_code == 200, renamed.text

    response = client.post(
        f"{api}/library/share-requests",
        json={
            "assetId": asset["id"],
            "action": "share",
            "target": "group",
            "groupId": group["id"],
            "expectedVersion": asset["stateVersion"],
        },
        headers=owner_h,
    )
    assert response.status_code == 409, response.text


def test_asset_state_version_is_not_zero(client: TestClient, api: str) -> None:
    """资产的 `state_version` 从 1 起——0 从来就不是合法版本。

    这条是整套语义的地基：如果哪天某个新建资产的版本是 0，那么
    「带 0 = 不校验」的老写法就会重新混进来。
    """
    admin = admin_headers(client)
    owner, owner_h = make_user(client, admin, LIB_RW)
    asset = upload_image(client, api, owner_h)

    row = fetch_asset(asset["id"])
    assert row is not None
    assert row["state_version"] >= 1, f"state_version 竟是 {row['state_version']}"


# --------------------------------------------------------------------------- #
# 409 的两种语义：版本过期 vs 业务冲突
#
# 409 在前端有两种完全不同的处置，所以后端必须把它们区分开：
# - **版本过期**（你读到的那份被别人先改了）：重读权威状态，让用户确认后重做；
# - **业务冲突**（重名、已结束、不是你的）：重试没用，得换个做法。
#
# 区分的标记是 `details.reason = 'state_changed'`（`core/exceptions.py::StateChanged`）。
# 少了它，前端只能把两类 409 一视同仁地显示成一句「请刷新后重试」——
# 而业务冲突刷新一百次也一样失败。
# --------------------------------------------------------------------------- #
def _conflict_details(response: Any) -> dict[str, Any]:
    """取出 409 的 details（并断言确实是 409）。"""
    assert response.status_code == 409, response.text
    details = response.json().get("details") or {}
    assert isinstance(details, dict), response.text
    return details


def test_a_stale_asset_patch_conflict_is_marked_as_state_changed(
    client: TestClient, api: str
) -> None:
    admin = admin_headers(client)
    owner, owner_h = make_user(client, admin, LIB_RW)
    asset = upload_image(client, api, owner_h)

    moved = client.patch(
        f"{api}/library/assets/{asset['id']}",
        json={"name": "先改一次.png", "expectedVersion": asset["stateVersion"]},
        headers=owner_h,
    )
    assert moved.status_code == 200, moved.text

    stale = client.patch(
        f"{api}/library/assets/{asset['id']}",
        json={"name": "再改一次.png", "expectedVersion": asset["stateVersion"]},
        headers=owner_h,
    )
    details = _conflict_details(stale)
    assert details.get("reason") == "state_changed", details
    # 版本信息要留着：前端要告诉用户「现在是第几版」，而不只是「冲突了」
    assert details.get("stateVersion") is not None, details


def test_a_share_decision_conflict_is_marked_as_state_changed(
    client: TestClient, api: str
) -> None:
    admin = admin_headers(client)
    owner, owner_h = make_user(client, admin, LIB_RW)
    group = make_group(client, admin, [owner["id"]])
    asset = upload_image(client, api, owner_h)

    created = client.post(
        f"{api}/library/share-requests",
        json={
            "assetId": asset["id"],
            "action": "share",
            "target": "group",
            "groupId": group["id"],
        },
        headers=owner_h,
    )
    assert created.status_code == 201, created.text
    request_id = created.json()["data"]["id"]

    # 审批人看到的那份状态过期了：先把资产改掉，版本往前走
    renamed = client.patch(
        f"{api}/library/assets/{asset['id']}",
        json={"name": "审批前被改了.png", "expectedVersion": asset["stateVersion"]},
        headers=owner_h,
    )
    assert renamed.status_code == 200, renamed.text

    response = client.post(
        f"{api}/library/share-requests/{request_id}/approve",
        json={"note": "同意", "expectedVersion": asset["stateVersion"]},
        headers=admin,
    )
    details = _conflict_details(response)
    assert details.get("reason") == "state_changed", details


def test_a_draft_decision_conflict_is_marked_as_state_changed(
    client: TestClient, api: str
) -> None:
    admin = admin_headers(client)
    owner, owner_h = make_user(client, admin, LIB_RW + ["library:draft:review"])
    asset_id = make_session_asset(state="draft", owner_id=owner["id"])

    response = client.post(
        f"{api}/library/drafts/{asset_id}/approve",
        json={"reason": "收下", "expectedVersion": 999},
        headers=owner_h,
    )
    details = _conflict_details(response)
    assert details.get("reason") == "state_changed", details


def test_business_conflicts_are_not_marked_as_state_changed() -> None:
    """只有「版本过期」才带 `state_changed`——业务冲突标了就是误导。

    业务冲突（重名、已结束、不是你的）刷新多少次都一样失败。标成
    `state_changed` 会让前端说「已为你刷新，请重试」，用户刷新后照样失败，
    只是多绕一圈。
    """
    from core.exceptions import Conflict, StateChanged

    plain = Conflict("同一分组下已有同名标签").to_payload()
    assert "reason" not in (plain.get("details") or {}), plain

    changed = StateChanged(
        "资产已被修改，请刷新后重试", details={"stateVersion": 3}
    ).to_payload()
    assert changed["details"]["reason"] == "state_changed", changed
    assert changed["details"]["stateVersion"] == 3, changed
