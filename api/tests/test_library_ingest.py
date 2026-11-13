"""生成入库：落草稿箱、幂等、权限、来源校验。

五个断言方向：

1. **落点固定**：`state=draft` / `visibility=personal` / `origin=generated`，
   入参给不了这些字段；
2. **快照冻结**：`generation` 原样存进 `library_asset_generation`，详情页读回来
   不需要联表；
3. **幂等两种命中**：同一个 `sourceKey`、或同一份字节已在该 owner 的库里，
   都返回 `created=false` 且不产生第二行；
4. **权限单列**：只有 `library:generation:ingest` 能调，`library:create` 不够；
5. **拒绝只存外部链接**：没有 `storageKey` 也没有 `sourceUrl` 一律 400。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from library_support import (
    LIB_RW,
    admin_headers,
    make_user,
    png_bytes,
    put_blob,
    upload_image,
)

from core.exceptions import BadRequest
from modules.library.ingest import _snapshot_values


def _ingest(client: TestClient, api: str, headers: dict[str, str], body: dict):
    return client.post(f"{api}/library/assets/from-generation", json=body, headers=headers)


def _fresh_blob(name: str, color: str) -> str:
    """一份「还没被谁登记过」的字节。入库的新建路径只能用它测。"""
    return put_blob(f"library/blobs/gen/{name}", png_bytes(color))


def _storage_key(client: TestClient, api: str, headers: dict[str, str], asset_id: str) -> str:
    detail = client.get(f"{api}/library/assets/{asset_id}", headers=headers).json()["data"]
    return detail["archive"]["storageKey"]


def test_from_generation_lands_in_draft_with_snapshot(
    client: TestClient, api: str
) -> None:
    admin = admin_headers(client)
    owner, owner_h = make_user(client, admin, LIB_RW + ["library:generation:ingest"])
    uploaded = upload_image(client, api, owner_h, name="ref.png", color="mediumseagreen")
    key = _fresh_blob("land.png", "seagreen")

    response = _ingest(
        client,
        api,
        owner_h,
        {
            "sourceModule": "image",
            "sourceTaskId": "job-42",
            "sourceKey": "image:job-42:0",
            "kind": "image",
            "name": "生成结果.png",
            "storageKey": key,
            "generation": {
                "model": "seedream",
                "modelId": "seedream-3",
                "prompt": "一只戴帽子的猫，工作室白底",
                "size": "1024x1024",
                "count": 4,
                "seed": "12345",
                "referenceLibraryIds": [uploaded["id"]],
                "configuration": {"steps": 28},
                "credits": 3.5,
            },
        },
    )
    assert response.status_code == 201, response.text
    created = response.json()["data"]
    assert created["created"] is True

    detail = client.get(
        f"{api}/library/assets/{created['assetId']}", headers=owner_h
    ).json()["data"]
    assert detail["state"] == "draft"
    assert detail["visibility"] == "personal"
    assert detail["origin"] == "generated"
    assert detail["name"] == "生成结果.png"
    generation = detail["generation"]
    assert generation["prompt"] == "一只戴帽子的猫，工作室白底"
    assert generation["model"] == "seedream"
    assert generation["seed"] == "12345"
    assert generation["configuration"] == {"steps": 28}
    assert generation["sourceModule"] == "image"
    assert generation["sourceTaskId"] == "job-42"
    # v1 下线后只剩一列：`referenceLibraryIds` 就是它，不再有「另一列是不是空的」。
    assert generation["referenceLibraryIds"] == [uploaded["id"]]

    # 草稿只出现在草稿箱，不进个人库也不进选择器
    personal = client.get(
        f"{api}/library/assets", params={"library": "personal"}, headers=owner_h
    ).json()["data"]
    assert created["assetId"] not in [item["id"] for item in personal["items"]]


def test_from_generation_is_idempotent_for_same_source_key(
    client: TestClient, api: str
) -> None:
    admin = admin_headers(client)
    owner, owner_h = make_user(client, admin, LIB_RW + ["library:generation:ingest"])
    key = _fresh_blob("dup.png", "darkorange")
    body = {
        "sourceModule": "template",
        "sourceTaskId": "run-7",
        "sourceKey": "template:run-7",
        "kind": "image",
        "name": "模板结果.png",
        "storageKey": key,
    }

    first = _ingest(client, api, owner_h, body)
    assert first.status_code == 201
    second = _ingest(client, api, owner_h, body)
    assert second.status_code == 200, "命中幂等不是错误，是「已经有了」"
    assert second.json()["data"]["created"] is False
    assert second.json()["data"]["assetId"] == first.json()["data"]["assetId"]

    drafts = client.get(
        f"{api}/library/assets", params={"state": "draft"}, headers=owner_h
    ).json()["data"]
    matches = [
        item for item in drafts["items"] if item["id"] == first.json()["data"]["assetId"]
    ]
    assert len(matches) == 1, "第二次入库不能产生第二行草稿"


def test_from_generation_dedupes_identical_bytes(client: TestClient, api: str) -> None:
    """内容相同的第二次生成不新建草稿：与上传的秒传同一口径。

    `uq_library_assets_owner_sha_kind` 是数据库级唯一约束，入库不走这套判定
    就会在第二次生成同样字节时撞 500——那是最难从日志里看出原因的一种失败。
    """
    admin = admin_headers(client)
    owner, owner_h = make_user(client, admin, LIB_RW + ["library:generation:ingest"])
    uploaded = upload_image(client, api, owner_h, name="same.png", color="rebeccapurple")
    key = _storage_key(client, api, owner_h, uploaded["id"])

    first = _ingest(
        client,
        api,
        owner_h,
        {
            "sourceModule": "task",
            "sourceTaskId": "task-1",
            "sourceKey": "task:task-1",
            "kind": "image",
            "storageKey": key,
        },
    )
    second = _ingest(
        client,
        api,
        owner_h,
        {
            "sourceModule": "task",
            "sourceTaskId": "task-2",
            "sourceKey": "task:task-2",
            "kind": "image",
            "storageKey": key,
        },
    )
    assert second.status_code == 200
    assert second.json()["data"]["assetId"] == first.json()["data"]["assetId"]


def test_from_generation_requires_its_own_permission(client: TestClient, api: str) -> None:
    admin = admin_headers(client)
    _, creator_h = make_user(client, admin, ["library:read", "library:create"])
    denied = _ingest(
        client,
        api,
        creator_h,
        {
            "sourceModule": "image",
            "sourceTaskId": "x",
            "sourceKey": "image:x",
            "kind": "image",
        },
    )
    assert denied.status_code == 403, "能上传不等于能伪造「这是某次生成的结果」"


def test_from_generation_rejects_bad_kind_and_missing_source(
    client: TestClient, api: str
) -> None:
    admin = admin_headers(client)
    _, owner_h = make_user(client, admin, LIB_RW + ["library:generation:ingest"])

    audio = _ingest(
        client,
        api,
        owner_h,
        {
            "sourceModule": "image",
            "sourceTaskId": "a",
            "sourceKey": "image:a",
            "kind": "audio",
        },
    )
    assert audio.status_code == 422, "只有图片与视频能进草稿箱"

    missing = _ingest(
        client,
        api,
        owner_h,
        {
            "sourceModule": "image",
            "sourceTaskId": "b",
            "sourceKey": "image:b",
            "kind": "image",
        },
    )
    assert missing.status_code == 400
    assert missing.json()["code"] == 40085


def test_snapshot_rejects_two_spellings_of_the_same_reference_column() -> None:
    """同一列的两种写法给了不同值 → 400，而不是「最后一个说了算」。

    `reference_library_ids`（列名）与 `referenceLibraryIds`（契约名）都映射到
    同一列：内部调用方（流水线）混着用是可能的，而静默取最后一个会让「档案里
    记的是哪几张图」取决于键在字典里的顺序——这种错平时看不见。
    """
    with pytest.raises(BadRequest, match="两个不同的取值"):
        _snapshot_values(
            {
                "reference_library_ids": ["a1"],
                "referenceLibraryIds": ["b2"],
            }
        )


def test_snapshot_accepts_either_spelling_of_the_reference_column() -> None:
    """同一列的两个键名都能用，且落到同一个列名上。

    HTTP 进来的 `GenerationSnapshotIn` 是模型对象，读的是属性名（snake）；
    流水线（生图 / 任务）传的是原始字典，生图用契约名 `referenceLibraryIds`。
    v1 那条独立的 `reference_asset_ids` 列已随 `assets` 表删除，所以这里只验
    「两种写法归一」，不再验「互不串列」。
    """
    assert _snapshot_values({"referenceLibraryIds": ["new"]})["reference_library_ids"] == ["new"]
    assert (
        _snapshot_values({"reference_library_ids": ["new"]})["reference_library_ids"] == ["new"]
    )
