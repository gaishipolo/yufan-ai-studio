"""P1 验收：新模型与旧模板对同一组素材给出同一套编号（等价性守卫）。

旧 `video_templates` 把顺序存在 `media_order`：下标 +1 就是 `{{Mixed N}}`，
每项自带 role 与 asset id。新 spec 把顺序存在槽位 `position`，编号由
`compile_spec` 现场算。同一批素材、同一顺序，两者的「编号 → 素材」必须逐项相等
—— 否则把老模板迁到新模型会**悄悄换掉用户熟悉的编号**：页面上提示词还是
`{{Mixed 2}}`，指向的却已经是另一张图，而这种错不会报错，只会生成不对。

这条用例是 P3 迁移脚本的验收依据：迁移只要让两种构造在本用例下给出同一结果，
就不会改语义。

**为什么用固定槽位而不是用户槽位做基线**：旧模板的 `media_order` 是发布时定死的，
对应新模型里的 `binding=fixed`。用户槽位的编号会随"这次填了几张"变化，那是新模型
**故意**不同的地方（见 `test_template_spec_compile.py`），不是等价性要覆盖的。
"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient
from library_support import LIB_RW, admin_headers, make_user, suffix
from tests_template_helpers import png_bytes, upload, video_bytes

API = "/api/v1/template-specs"
PERMS = ["template_pack:read", "template_pack:create", "template_pack:publish", *LIB_RW]
MODEL = "libtv-wan3-replacement"


def _headers(client: TestClient) -> dict[str, str]:
    admin = admin_headers(client)
    _, headers = make_user(client, admin, PERMS)
    return headers


def _assets(client: TestClient, headers: dict[str, str]) -> tuple[str, list[str]]:
    """一支原片 + 三张图，返回 `(video_id, [front, multiview, detail])`。"""
    video = upload(
        client, f"clip-{suffix()}.mp4", "source", video_bytes(2, audio=True), headers=headers
    )
    images = [
        str(upload(client, f"{name}-{suffix()}.png", "image", png_bytes(), headers=headers)["id"])
        for name in ("front", "multiview", "detail")
    ]
    return str(video["id"]), images


def _pack(client: TestClient, headers: dict[str, str], images: list[str]) -> str:
    """把三张图按 `front → multiview → detail` 建成一个资产库封装。"""
    role_of = dict(zip(images, ("front", "multiview", "detail"), strict=True))
    response = client.post(
        "/api/v1/library/packs",
        json={
            "name": f"三视图-{suffix()}",
            "items": [
                {
                    "assetId": image,
                    "role": role_of[image],
                    "description": f"{role_of[image]} 视图",
                }
                for image in images
            ],
        },
        headers=headers,
    )
    assert response.status_code == 201, response.text
    return str(response.json()["data"]["id"])


def _legacy(
    video: str, images: list[str]
) -> list[tuple[str, str | None, str]]:
    """旧模板 `media_order` 的**冻结快照**：`[(token, asset_id, role)]`。

    这批常量来自 `cb9122d` 之前的 `video_templates.media_order`（旧链路已下线，
    表也删了，所以语义只能以常量的形式留在这里）。它的用途只有一个：给新模型
    一个不随实现漂移的对照物 —— 同一批素材、同一顺序，编号 → 素材必须逐项相等，
    否则迁移会**悄悄换掉用户熟悉的编号**：提示词里还是 `{{Mixed 2}}`，指向的却
    已经是另一张图，这种错不报错，只生成错。
    """
    ordered = [
        ("{{Mixed 1}}", video, "source_video"),
        ("{{Mixed 2}}", images[0], "front"),
        ("{{Mixed 3}}", images[1], "multiview"),
        ("{{Mixed 4}}", images[2], "detail"),
    ]
    return ordered


def _spec_draft(
    video: str, pack_id: str, *, binding: str, key: str
) -> dict[str, Any]:
    return {
        "key": key,
        "name": "等价性规格",
        "model": MODEL,
        "params": {},
        "prompt": {
            "locked": "",
            "editable": "把 {{slot:source}} 换成 {{slot:views*}}",
        },
        "slots": [
            {
                "key": "source",
                "type": "video",
                "binding": "fixed",
                "assetId": video,
                "position": 0,
                "label": "原片",
            },
            {
                "key": "views",
                "type": "image",
                "source": "pack",
                "binding": binding,
                "packId": pack_id if binding == "fixed" else None,
                "position": 1,
                "label": "产品三视图",
            },
        ],
    }


def _compile(
    client: TestClient,
    headers: dict[str, str],
    draft: dict[str, Any],
    slot_values: dict[str, list[str]],
) -> dict[str, Any]:
    assert client.post(API, json=draft, headers=headers).status_code == 200
    published = client.post(f"{API}/{draft['key']}/publish", headers=headers)
    assert published.status_code == 200, published.text
    body = client.post(
        f"{API}/{draft['key']}/compile",
        json={"slotValues": slot_values},
        headers=headers,
    )
    assert body.status_code == 200, body.text
    return body.json()["data"]


def test_compile_reproduces_legacy_media_order(client: TestClient) -> None:
    """固定槽位版本：`compile` 的编号与素材序列必须和旧 `media_order` 一致。"""
    headers = _headers(client)
    video, images = _assets(client, headers)
    pack_id = _pack(client, headers, images)
    legacy = _legacy(video, images)

    compiled = _compile(
        client,
        headers,
        _spec_draft(video, pack_id, binding="fixed", key=f"equiv-{suffix()}"),
        {},
    )

    # 旧：编号就是 `media_order` 的下标 +1；新：编号由 position + 填充量算。
    assert compiled["referenceLibraryIds"] == [asset for _token, asset, _role in legacy]
    assert compiled["prompt"] == "把 {{Mixed 1}} 换成 {{Mixed 2}}、{{Mixed 3}}、{{Mixed 4}}"

    per_slot = {item["key"]: (item["tokens"], item["assetIds"]) for item in compiled["slots"]}
    assert per_slot["source"] == (["{{Mixed 1}}"], [video])
    assert per_slot["views"] == (
        ["{{Mixed 2}}", "{{Mixed 3}}", "{{Mixed 4}}"],
        images,
    )

    # 角色也必须跟着走：编号对了但角色错了，提示词里的"三视图为准"就指向错的图。
    detail = client.get(f"/api/v1/library/assets/{pack_id}", headers=headers).json()["data"]
    members = sorted(detail["packItems"], key=lambda item: item["position"])
    assert [(item["assetId"], item["role"]) for item in members] == [
        (asset, role) for _token, asset, role in legacy if role != "source_video"
    ]


def test_user_binding_gives_the_same_numbers_as_fixed(client: TestClient) -> None:
    """`binding` 只决定"谁来填"，不影响编号：迁移时可以任选一种而不改语义。"""
    headers = _headers(client)
    video, images = _assets(client, headers)
    pack_id = _pack(client, headers, images)
    legacy = _legacy(video, images)

    compiled = _compile(
        client,
        headers,
        _spec_draft(video, pack_id, binding="user", key=f"equiv-{suffix()}"),
        {"views": [pack_id]},
    )

    assert compiled["referenceLibraryIds"] == [asset for _token, asset, _role in legacy]
    assert compiled["prompt"] == "把 {{Mixed 1}} 换成 {{Mixed 2}}、{{Mixed 3}}、{{Mixed 4}}"
