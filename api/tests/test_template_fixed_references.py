"""跨封装固定图片引用：`rules.fixedReferences` 与提示词一起保存、一起回放。

模板目录落表之后，这里造数据从「写一份 catalog.json」变成「插一行」，
断言也从「文件没被改」变成「那一行没被改」——后者才是用户能看到的真相。
"""

from types import SimpleNamespace

import pytest
from video_template_support import get_template, put_template, update_prompt

from core.exceptions import BadRequest
from modules.template.replacement_service import _media_in_mixed_order

_REF_BINDINGS = {
    "referenceBindings": {"{{Mixed 1}}": "source_video", "{{Mixed 2}}": "multiview"}
}


def test_fixed_references_saved_with_verbatim_prompt_and_original_bindings() -> None:
    put_template("template-1", rules=_REF_BINDINGS)
    fixed = [
        {
            "token": "{{Mixed 5}}",
            "packId": "other",
            "packVersion": 2,
            "assetId": "fixed",
            "name": "另一个产品正面图.png",
            "url": "/preview",
        }
    ]
    raw = "  编辑 {{Mixed 1}}，参考 {{Mixed 5}}。\n\n"
    updated = update_prompt("template-1", raw, fixed)
    assert updated.prompt == raw
    assert updated.rules["fixedReferences"] == fixed
    assert updated.rules["referenceBindings"] == _REF_BINDINGS["referenceBindings"]
    assert get_template("template-1").media_order[-1].asset_id == "fixed"

    source, a, b, c, extra, fixed_image = [
        SimpleNamespace(id=name) for name in ["source", "a", "b", "c", "extra", "fixed"]
    ]
    ordered = _media_in_mixed_order(
        updated,
        source,
        [a, b, c, extra],
        {"a": "multiview", "b": "multiview", "c": "multiview", "extra": "detail"},
        fixed_refs={"{{Mixed 5}}": fixed_image},
    )
    assert [item.id for item in ordered] == ["source", "a", "b", "c", "fixed", "extra"]


def test_invalid_fixed_reference_number_does_not_change_template() -> None:
    put_template("template-1")
    before = get_template("template-1")
    with pytest.raises(BadRequest):
        update_prompt(
            "template-1",
            "{{Mixed 1}} {{Mixed 9}}",
            [
                {
                    "token": "{{Mixed 9}}",
                    "packId": "p",
                    "packVersion": 1,
                    "assetId": "fixed",
                    "name": "image",
                },
            ],
        )
    assert get_template("template-1") == before


def test_product_template_uses_fixed_image_from_another_kind_of_pack(client) -> None:
    import asyncio
    from uuid import uuid4

    from tests_template_helpers import add_pack, login, png_bytes, upload, video_bytes

    headers = login(client)
    source = upload(client, "product-fixed-source.mp4", "source", video_bytes(2))
    front = upload(client, "产品正面.png", "product", png_bytes())
    identity = upload(client, "人物正面.png", "character", png_bytes())
    base = asyncio.run(add_pack(kind="product", reference_ids=[front["id"]]))
    other = asyncio.run(add_pack(kind="character", reference_ids=[identity["id"]]))
    put_template(
        "template-1",
        sourceVideoAssetId=source["id"],
        packId=base.id,
        packVersion=1,
        mediaOrder=[
            {"token": "{{Mixed 1}}", "type": "video", "role": "source_video"},
            {
                "token": "{{Mixed 2}}",
                "type": "image",
                "role": "front",
                "assetId": front["id"],
                "name": front["name"],
            },
        ],
        rules={"referenceBindings": {"{{Mixed 1}}": "source_video", "{{Mixed 2}}": "front"}},
    )
    raw = " \n编辑 {{Mixed 1}}，产品 {{Mixed 2}}，参考人物 {{Mixed 3}}。\n\n"
    response = client.patch(
        "/api/v1/templates/video-templates/template-1/prompt",
        headers=headers,
        json={
            "prompt": raw,
            "references": [
                {
                    "token": "{{Mixed 3}}",
                    "packId": other.id,
                    "packVersion": 1,
                    "assetId": identity["id"],
                }
            ],
        },
    )
    assert response.status_code == 200, response.text
    response = client.post(
        "/api/v1/templates/replacement-runs",
        headers=headers,
        json={
            "requestId": str(uuid4()),
            "kind": "product",
            "sourceLibraryId": source["id"],
            "videoTemplateId": "template-1",
            "packId": base.id,
            "packVersion": 1,
        },
    )
    assert response.status_code == 201, response.text
    result = response.json()["data"]
    assert result["prompt"] == raw
    assert [item["id"] for item in result["inputSummary"]["assets"]] == [
        source["id"],
        front["id"],
        identity["id"],
    ]


def test_pack_name_search_metadata_save_reopen_prepare_and_revoke(client) -> None:
    from uuid import uuid4

    from tests_template_helpers import login, png_bytes, upload, video_bytes

    headers = login(client)
    source = upload(client, "fixed-ref-source.mp4", "source", video_bytes(2))
    image = upload(client, "海边机位蓝图.png", "scene", png_bytes())
    pack_id = uuid4().hex
    response = client.put(
        "/api/v1/templates/replacement-packs/draft",
        headers=headers,
        json={
            "requestId": pack_id,
            "kind": "scene",
            "name": "海边场景封装",
            "referenceLibraryIds": [image["id"]],
            "bindings": [{"role": "main", "description": "不得附加这段描述"}],
        },
    )
    assert response.status_code == 200, response.text
    revision = response.json()["data"]["revision"]
    response = client.post(
        f"/api/v1/templates/replacement-packs/{pack_id}/publish",
        headers=headers,
        json={"expectedRevision": revision},
    )
    assert response.status_code == 200, response.text
    version = response.json()["data"]["version"]
    packages = client.get("/api/v1/templates/replacement-catalog", headers=headers).json()["data"][
        "packs"
    ]
    public = next(item for item in packages if item["id"] == pack_id)
    assert public["referenceNames"] == ["海边机位蓝图.png"]
    original = "  编辑 {{Mixed 1}}，场景 {{ScenePrompt}}。\n\n"
    response = client.post(
        "/api/v1/templates/video-templates",
        headers=headers,
        json={
            "name": "跨封装场景测试",
            "prompt": original,
            "defaultKind": "scene",
            "sourceVideoAssetId": source["id"],
            "sourceVideoName": source["name"],
            "sourceDurationSec": 2,
            "rules": {"inputMode": "video_scene_text", "scenePromptDefault": "海边"},
        },
    )
    assert response.status_code == 200, response.text
    template_id = response.json()["data"]["id"]
    raw = original + "参考 {{Mixed 2}}。\n"
    reference = {
        "token": "{{Mixed 2}}",
        "packId": pack_id,
        "packVersion": version,
        "assetId": image["id"],
    }
    response = client.patch(
        f"/api/v1/templates/video-templates/{template_id}/prompt",
        headers=headers,
        json={"prompt": raw, "references": [reference]},
    )
    assert response.status_code == 200, response.text
    reopened = client.get(
        f"/api/v1/templates/video-templates/{template_id}", headers=headers
    ).json()["data"]
    assert reopened["prompt"] == raw
    assert reopened["rules"]["fixedReferences"][0]["name"] == image["name"]
    response = client.post(
        "/api/v1/templates/replacement-runs",
        headers=headers,
        json={
            "requestId": str(uuid4()),
            "kind": "scene",
            "sourceLibraryId": source["id"],
            "videoTemplateId": template_id,
        },
    )
    assert response.status_code == 201, response.text
    run = response.json()["data"]
    assert run["prompt"] == raw.replace("{{ScenePrompt}}", "海边")
    assert [item["id"] for item in run["inputSummary"]["assets"]] == [source["id"], image["id"]]
    assert "不得附加这段描述" not in run["prompt"]
    bad = {**reference, "packVersion": version + 999}
    response = client.patch(
        f"/api/v1/templates/video-templates/{template_id}/prompt",
        headers=headers,
        json={"prompt": raw, "references": [bad]},
    )
    assert response.status_code == 400
    assert get_template(template_id).prompt == raw
    response = client.post(
        f"/api/v1/templates/replacement-packs/{pack_id}/disable",
        headers=headers,
        json={"expectedRevision": revision},
    )
    assert response.status_code == 200, response.text
    response = client.post(
        f"/api/v1/templates/replacement-runs/{run['id']}/execute", headers=headers
    )
    assert response.status_code == 400, response.text
    assert "固定引用" in response.json()["message"]
    response = client.post(
        "/api/v1/templates/replacement-runs",
        headers=headers,
        json={
            "requestId": str(uuid4()),
            "kind": "scene",
            "sourceLibraryId": source["id"],
            "videoTemplateId": template_id,
        },
    )
    assert response.status_code == 400
    response = client.patch(
        f"/api/v1/templates/video-templates/{template_id}/prompt",
        headers=headers,
        json={"prompt": original, "references": []},
    )
    assert response.status_code == 200, response.text
    assert len(response.json()["data"]["mediaOrder"]) == 1


def test_fixed_reference_budget_counts_images_not_slots() -> None:
    """base_count 含 1 个原片：图片预算是 MAX_REFERENCE_IMAGES，不是 Mixed 槽位数。

    保存端与 prepare 端共用这个常量，否则会出现「模板存下了、换个图更多的包准备被拒」。
    """
    from modules.template.video_template_schema import MAX_REFERENCE_IMAGES

    put_template("template-1")
    before = get_template("template-1")
    base_count = len(before.media_order)
    overflow = MAX_REFERENCE_IMAGES - (base_count - 1) + 1
    fixed = [
        {
            "token": f"{{{{Mixed {base_count + index + 1}}}}}",
            "packId": "p",
            "packVersion": 1,
            "assetId": f"fixed-{index}",
            "name": f"fixed-{index}",
        }
        for index in range(overflow)
    ]
    prompt = "{{Mixed 1}} " + " ".join(item["token"] for item in fixed)
    with pytest.raises(BadRequest, match="最多"):
        update_prompt("template-1", prompt, fixed)
    assert get_template("template-1") == before
