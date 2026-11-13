"""用途绑定模式下「只声明槽位」的封装：A 不绑默认包，B 调用时按用途取图。

这条链的失败方向是「看起来封装成功、实际永远调不起来」，所以要钉三处：

1. 不传 `packId` 也能创建（媒体清单里的槽位 `assetId` 为空、名字写「运行时选…」）；
2. 未声明用途绑定时仍拒绝（否则服务端不知道要几张图、各是什么用途）；
3. 用这个模板跑一次真实准备：图按用途从**调用者选的包**里取，编号不动。
"""

from __future__ import annotations

import asyncio
import uuid

from tests_template_helpers import add_pack, drop_pack, login, png_bytes, upload, video_bytes
from video_template_support import remove as remove_template

API = "/api/v1"


def _create(client, headers, *, source_id: str, pack_id: str | None = None) -> dict:
    body: dict[str, object] = {
        "name": f"只声明槽位 {uuid.uuid4().hex[:6]}",
        "prompt": "对 {{Mixed 1}} 做替换，产品主图为 {{Mixed 2}}。",
        "defaultKind": "product",
        "sourceVideoAssetId": source_id,
        "sourceVideoName": "slot-source.mp4",
        "sourceDurationSec": 2,
        "rules": {
            "referenceBindings": {
                "{{Mixed 1}}": "source_video",
                "{{Mixed 2}}": "front",
            }
        },
    }
    if pack_id is not None:
        body["packId"] = pack_id
        body["packVersion"] = 1
    response = client.post(f"{API}/templates/video-templates", headers=headers, json=body)
    return {"status": response.status_code, "body": response.json(), "text": response.text}


def test_template_can_declare_slots_without_a_default_pack(client) -> None:
    headers = login(client)
    source = upload(client, "只声明槽位-原片.mp4", "source", video_bytes(2))
    template_id = ""
    try:
        created = _create(client, headers, source_id=source["id"])
        assert created["status"] == 200, created["text"]
        out = created["body"]["data"]
        template_id = out["id"]
        assert out["packId"] in (None, "")
        media = out["mediaOrder"]
        assert [item["token"] for item in media] == ["{{Mixed 1}}", "{{Mixed 2}}"]
        # 槽位不是真图：没有 assetId，名字说清「运行时选」
        assert media[1]["assetId"] in (None, "")
        assert media[1]["role"] == "front"
        assert "运行时选" in media[1]["name"]
    finally:
        if template_id:
            remove_template(template_id)


def test_slots_without_declared_bindings_are_still_rejected(client) -> None:
    """没声明用途就没有槽位可生成，不能落成一条「0 张图」的空模板。"""
    headers = login(client)
    source = upload(client, "无用途声明-原片.mp4", "source", video_bytes(2))
    response = client.post(
        f"{API}/templates/video-templates",
        headers=headers,
        json={
            "name": "没有用途声明",
            "prompt": "对 {{Mixed 1}} 做替换，产品主图为 {{Mixed 2}}。",
            "defaultKind": "product",
            "sourceVideoAssetId": source["id"],
            "sourceDurationSec": 2,
        },
    )
    assert response.status_code == 400, response.text
    assert "packId" in response.json()["message"]


def test_run_takes_images_from_the_callers_pack_by_role(client) -> None:
    """调用者选的包与封装者无关：`{{Mixed 2}}` 永远是那个包的正面图。"""
    headers = login(client)
    source = upload(client, "只声明槽位-准备.mp4", "source", video_bytes(2))
    front = upload(client, "只声明槽位-正面.png", "product", png_bytes())
    detail = upload(client, "只声明槽位-细节.png", "product", png_bytes())
    pack = None
    template_id = ""
    try:
        # 包里故意把细节图排在前面：顺序必须由「用途」决定，不是包内位置
        pack = asyncio.run(
            add_pack(
                kind="product",
                reference_ids=[detail["id"], front["id"]],
                roles=["detail", "front"],
            )
        )
        created = _create(client, headers, source_id=source["id"])
        assert created["status"] == 200, created["text"]
        template_id = created["body"]["data"]["id"]

        response = client.post(
            f"{API}/templates/replacement-runs",
            headers=headers,
            json={
                "requestId": uuid.uuid4().hex,
                "kind": "product",
                "sourceId": source["id"],
                "videoTemplateId": template_id,
                "packId": pack.id,
                "packVersion": 1,
            },
        )
        assert response.status_code == 201, response.text
        run = response.json()["data"]
        # 原片 + 正面（用途排第一）+ 细节（辅助图全量附带）
        assert [item["id"] for item in run["inputSummary"]["assets"]] == [
            source["id"],
            front["id"],
            detail["id"],
        ]
    finally:
        if template_id:
            remove_template(template_id)
        if pack is not None:
            asyncio.run(drop_pack(pack.id))
