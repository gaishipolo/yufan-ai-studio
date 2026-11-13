"""B 直接拿资产库 v2 封装当参考（模板库 §23.5「缺的一跳」）。

用户 A 在资产库把同一产品的三视图 / 细节图封成一个包，用户 B 选它来跑替换。
这条链路与「模板封装（`template_packs`）」是**两个 id 空间**，靠显式字段
`libraryPackId` 分流，不做「先查一边再回退另一边」——两套 id 形状一样，
回退一旦生效就是静默换掉参考图。

四条判据都在「错了不会报错」的那一侧，所以每条都要有用例：

1. 成员**顺序**就是 `{{Mixed N}}` 的编号顺序（A 排的顺序被尊重，不被重排）；
2. **用途**决定每张图进提示词时的说明；v2 的 `role` 是自由字符串，
   认不出必须报错让人回封装页补，不能按位置猜主图；
3. **成员可读性**单独判：封装可读 ≠ 成员可读（打包时能把你共享给我的图收进包，
   之后我撤销共享，包还在但图不该再被用）；
4. 与模板封装**互斥**：两路都填直接 400，不做「谁优先」。
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi.testclient import TestClient
from library_support import LIB_RW, admin_headers, make_user, upload_image
from tests_template_helpers import upload, video_bytes

API = "/api/v1"


def _pack(
    client: TestClient, headers: dict[str, str], name: str, items: list[dict[str, Any]]
) -> dict[str, Any]:
    """建一个 v2 封装（走真实接口，`role` 就是模板侧要读的那一列）。"""
    response = client.post(
        f"{API}/library/packs", json={"name": name, "items": items}, headers=headers
    )
    assert response.status_code == 201, response.text
    return response.json()["data"]


def _prepare(client: TestClient, body: dict[str, Any], headers: dict[str, str]):
    return client.post(f"{API}/templates/replacement-runs", json=body, headers=headers)


def test_library_pack_supplies_images_in_member_order(client: TestClient) -> None:
    """整链路：封装 → 选它 prepare → 图按成员顺序冻进任务、字节落在归档里。"""
    admin = admin_headers(client)
    front = upload_image(client, API, admin, name="正面.png", color="orchid")
    multiview = upload_image(client, API, admin, name="三视图.png", color="olive")
    # 刻意「三视图在前、正面在后」：顺序必须照成员 position 走，不能按用途重排，
    # 否则 `{{Mixed 2}}` / `{{Mixed 3}}` 的含义会随封装页的排序漂移。
    pack = _pack(
        client,
        admin,
        "产品三视图",
        [
            {"assetId": multiview["id"], "role": "multiview", "description": "正侧背拼图"},
            {"assetId": front["id"], "role": "front", "description": "同款同色"},
        ],
    )
    source = upload(client, "library-pack.mp4", "source", video_bytes(2))

    response = _prepare(
        client,
        {
            "requestId": uuid.uuid4().hex,
            "kind": "product",
            "sourceId": source["id"],
            "libraryPackId": pack["id"],
            "replacementScope": "人物上身穿着",
        },
        admin,
    )
    assert response.status_code == 201, response.text
    run = response.json()["data"]
    # 目标名由封装决定：用户不填 `target` 也不能得到一条无名任务
    assert run["target"] == "产品三视图"

    assets = run["inputSummary"]["assets"]
    assert [item["id"] for item in assets] == [multiview["id"], front["id"], source["id"]]
    # 出参 role 是**编译器**认的角色（产品图 / 原片），不是封装里的用途
    assert [item["role"] for item in assets] == [
        "target_product",
        "target_product",
        "source_video",
    ]
    # 成员自己声明的用途要进说明文字：同是产品图，主图与三视图能决定的东西不同
    usages = [item.get("usage") or "" for item in assets[:2]]
    assert "多视拼图" in usages[0], usages
    assert "同款同色" in usages[1], usages

    # 字节真的复制进了本次任务：v2 的图在对象存储里，归档拿不到就等于裂图
    archived = client.get(assets[0]["url"], headers=admin)
    assert archived.status_code == 200, archived.text
    assert archived.content


def test_library_pack_member_without_role_is_rejected(client: TestClient) -> None:
    """用途为空 → 400 并指回封装页，**不按位置猜主图**。"""
    admin = admin_headers(client)
    image = upload_image(client, API, admin, name="没用途.png", color="orchid")
    pack = _pack(client, admin, "没声明用途的包", [{"assetId": image["id"]}])
    source = upload(client, "library-pack-role.mp4", "source", video_bytes(2))

    response = _prepare(
        client,
        {
            "requestId": uuid.uuid4().hex,
            "kind": "product",
            "sourceId": source["id"],
            "libraryPackId": pack["id"],
            "replacementScope": "人物上身穿着",
        },
        admin,
    )
    assert response.status_code == 400, response.text
    message = response.json()["message"]
    assert "没有声明可用途" in message, message
    # 指得到人：要说清是哪个包里的哪张图，不然用户只能一张张试
    assert "没声明用途的包" in message, message


def test_library_pack_with_duplicate_roles_is_rejected(client: TestClient) -> None:
    """两张都是「主图」→ 400。`{{Mixed N}}` 按用途取位，取哪张都算猜。"""
    admin = admin_headers(client)
    first = upload_image(client, API, admin, name="主图甲.png", color="orchid")
    second = upload_image(client, API, admin, name="主图乙.png", color="olive")
    pack = _pack(
        client,
        admin,
        "两张主图",
        [
            {"assetId": first["id"], "role": "front"},
            {"assetId": second["id"], "role": "front"},
        ],
    )
    source = upload(client, "library-pack-dup.mp4", "source", video_bytes(2))

    response = _prepare(
        client,
        {
            "requestId": uuid.uuid4().hex,
            "kind": "product",
            "sourceId": source["id"],
            "libraryPackId": pack["id"],
            "replacementScope": "人物上身穿着",
        },
        admin,
    )
    assert response.status_code == 400, response.text
    assert "不止一张" in response.json()["message"]


def test_library_pack_member_moved_to_trash_is_rejected(client: TestClient) -> None:
    """成员被删 / 取消共享 → 400。包还在，图不该再被拿去生成。"""
    admin = admin_headers(client)
    image = upload_image(client, API, admin, name="会被删.png", color="orchid")
    pack = _pack(client, admin, "会缺成员的包", [{"assetId": image["id"], "role": "front"}])
    source = upload(client, "library-pack-gone.mp4", "source", video_bytes(2))
    trashed = client.delete(f"{API}/library/assets/{image['id']}", headers=admin)
    assert trashed.status_code == 200, trashed.text

    response = _prepare(
        client,
        {
            "requestId": uuid.uuid4().hex,
            "kind": "product",
            "sourceId": source["id"],
            "libraryPackId": pack["id"],
            "replacementScope": "人物上身穿着",
        },
        admin,
    )
    assert response.status_code == 400, response.text
    message = response.json()["message"]
    assert "已删除或已停止共享" in message, message
    assert "会缺成员的包" in message, message


def test_library_pack_in_trash_is_rejected(client: TestClient) -> None:
    """封装本身进了回收站 → 400。与 v1「封装已停用」同一个口径。"""
    admin = admin_headers(client)
    image = upload_image(client, API, admin, name="包内图.png", color="orchid")
    pack = _pack(client, admin, "会进回收站的包", [{"assetId": image["id"], "role": "front"}])
    source = upload(client, "library-pack-trash.mp4", "source", video_bytes(2))
    trashed = client.delete(f"{API}/library/assets/{pack['id']}", headers=admin)
    assert trashed.status_code == 200, trashed.text

    response = _prepare(
        client,
        {
            "requestId": uuid.uuid4().hex,
            "kind": "product",
            "sourceId": source["id"],
            "libraryPackId": pack["id"],
            "replacementScope": "人物上身穿着",
        },
        admin,
    )
    assert response.status_code == 400, response.text
    assert "不存在或已停止共享" in response.json()["message"]


def test_library_pack_of_another_user_is_rejected(client: TestClient) -> None:
    """别人的**个人**封装：与「不存在」合并成同一句话，不能当探测别人资产的口子。"""
    admin = admin_headers(client)
    owner, owner_h = make_user(client, admin, LIB_RW)
    image = upload_image(client, API, owner_h, name="别人的图.png", color="orchid")
    pack = _pack(client, owner_h, "别人的包", [{"assetId": image["id"], "role": "front"}])
    source = upload(client, "library-pack-other.mp4", "source", video_bytes(2))

    response = _prepare(
        client,
        {
            "requestId": uuid.uuid4().hex,
            "kind": "product",
            "sourceId": source["id"],
            "libraryPackId": pack["id"],
            "replacementScope": "人物上身穿着",
        },
        admin,
    )
    assert response.status_code == 400, response.text
    assert "不存在或已停止共享" in response.json()["message"]


def test_library_pack_cannot_be_combined_with_template_pack(client: TestClient) -> None:
    """两路参考来源同时给 → 400。两套 id 形状一样，「谁优先」必须显式拒绝。"""
    admin = admin_headers(client)
    image = upload_image(client, API, admin, name="两路.png", color="orchid")
    pack = _pack(client, admin, "两路的包", [{"assetId": image["id"], "role": "front"}])
    source = upload(client, "library-pack-both.mp4", "source", video_bytes(2))

    response = _prepare(
        client,
        {
            "requestId": uuid.uuid4().hex,
            "kind": "product",
            "sourceId": source["id"],
            "libraryPackId": pack["id"],
            "packId": "0123456789abcdef0123456789abcdef",
            "packVersion": 1,
            "referenceIds": [image["id"]],
            "replacementScope": "人物上身穿着",
        },
        admin,
    )
    assert response.status_code == 400, response.text
    assert "只能有一个参考来源" in response.json()["message"]


def test_library_pack_must_be_a_pack_not_an_image(client: TestClient) -> None:
    """拿一张图片的 id 当 `libraryPackId` → 400，不扩成「空封装」。"""
    admin = admin_headers(client)
    image = upload_image(client, API, admin, name="其实是图.png", color="orchid")
    source = upload(client, "library-pack-image.mp4", "source", video_bytes(2))

    response = _prepare(
        client,
        {
            "requestId": uuid.uuid4().hex,
            "kind": "product",
            "sourceId": source["id"],
            "libraryPackId": image["id"],
            "replacementScope": "人物上身穿着",
        },
        admin,
    )
    assert response.status_code == 400, response.text
    assert "不存在或已停止共享" in response.json()["message"]
