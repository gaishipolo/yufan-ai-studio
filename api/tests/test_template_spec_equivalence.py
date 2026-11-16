"""P1 验收：新模型与旧模板对同一组素材给出同一套编号（等价性守卫）。

旧 `video_templates` 把顺序存在 `media_order`：下标 +1 就是 `{{Mixed N}}`，
每项自带 role 与 asset id。新 spec 把顺序存在槽位 `position`，编号由
`compile_spec` 现场算。同一批素材、同一顺序，两者的「编号 → 素材」必须逐项相等
—— 否则把老模板迁到新模型会**悄悄换掉用户熟悉的编号**：页面上提示词还是
`{{Mixed 2}}`，指向的却已经是另一张图，而这种错不会报错，只会生成不对。

这条用例是 P3 迁移脚本的验收依据：迁移只要让两种构造在本用例下给出同一结果，
就不会改语义。

**为什么三视图那一组是「用户填」**：旧模板的 `media_order` 是发布时定死的，
对应新模型里的 `binding=fixed`。但固定槽位只指向**一个**素材，一组三视图只能
由使用者在填表时挑（`min/max` 可多值）—— 编号仍然由 position + 实际填充量
算出来，与旧链路的「下标 +1」等价。
"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient
from library_support import LIB_RW, admin_headers, make_user, suffix
from tests_template_helpers import png_bytes, upload, video_bytes

API = "/api/v1/template-specs"
PERMS = ["template_pack:read", "template_pack:create", "template_pack:publish", *LIB_RW]
MODEL = "libtv-wan3-replacement"

#: 用户填的图片槽位必须带一级标签（发布前校验）。这些用例不关心标签本身，
#: 用同一个占位 id —— 发布只检查"有没有给"。
_TAG = "tag-equiv-tests"


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


def _spec_draft(video: str, *, key: str) -> dict[str, Any]:
    return {
        "key": key,
        "name": "等价性规格",
        "model": MODEL,
        "params": {},
        "prompt": "把 {{slot:source}} 换成 {{slot:views*}}",
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
                # 一组多图 = 一个可多值的图片槽位：使用者挑几张就占几个号。
                "type": "image",
                "binding": "user",
                "tagId": _TAG,
                "minCount": 1,
                "maxCount": 3,
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
    """`compile` 的编号与素材序列必须和旧 `media_order` 一致。"""
    headers = _headers(client)
    video, images = _assets(client, headers)
    legacy = _legacy(video, images)

    compiled = _compile(
        client,
        headers,
        _spec_draft(video, key=f"equiv-{suffix()}"),
        {"views": images},
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
