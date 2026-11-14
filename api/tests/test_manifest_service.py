"""manifest DB 化的端到端契约：预检 / 幂等 / 发布 / 对外端口。

这里的用例盯的是「从 CLI 搬到服务层」最容易丢的三件事：

1. 素材必须真的来自资产库且当前可读（不是随便一个字符串能存进去）；
2. 同 key 同配置只产生一行；已发布配置不许被悄悄改掉；
3. 发布之后 `core.ports.template_catalog` 能读到完整的规则与素材顺序。
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi.testclient import TestClient
from library_support import LIB_RW, admin_headers, insert_asset, make_user, suffix

MANIFESTS = "/api/v1/template-manifests"

PERMS = [
    "template_pack:read",
    "template_pack:create",
    "template_pack:publish",
    *LIB_RW,
]


def _user_id(client: TestClient, headers: dict[str, str]) -> str:
    response = client.get("/api/v1/identity/me", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()["data"]["id"]


def _headers(client: TestClient) -> dict[str, str]:
    admin = admin_headers(client)
    _, headers = make_user(client, admin, PERMS)
    return headers


def _media(client: TestClient, headers: dict[str, str]) -> tuple[str, str]:
    owner = _user_id(client, headers)
    video = insert_asset(
        owner_id=owner,
        name=f"clip-{suffix()}.mp4",
        kind="video",
        mime_type="video/mp4",
        duration=3.0,
        has_audio=True,
        state="active",
    )
    image = insert_asset(
        owner_id=owner,
        name=f"front-{suffix()}.png",
        kind="image",
        mime_type="image/png",
        state="active",
    )
    return video, image


def _spec(video_id: str, image_id: str, **overrides: Any) -> dict[str, Any]:
    spec: dict[str, Any] = {
        "key": f"demo-{suffix()}",
        "name": "演示",
        "video": {"assetId": video_id},
        "packs": [
            {
                "key": "dress",
                "name": "连衣裙",
                "kind": "product",
                "supportedScopes": ["worn"],
                "references": [
                    {"key": "front", "assetId": image_id, "role": "front"}
                ],
            }
        ],
        "functions": [
            {
                "key": "replace",
                "name": "产品替换",
                "pack": "dress",
                "prompt": "对 {{Mixed 1}} 使用 {{Mixed 2}}。",
            }
        ],
    }
    spec.update(overrides)
    return spec


def _catalog_templates() -> list[Any]:
    from core.ports.template_catalog import get_template_catalog
    from db.session import get_session_factory

    async def run() -> list[Any]:
        port = get_template_catalog()
        assert port is not None, "template-catalog 端口未注册"
        async with get_session_factory()() as session:
            return await port.list_templates(session)

    return asyncio.run(run())


def test_validate_save_and_idempotency(client: TestClient) -> None:
    headers = _headers(client)
    video, image = _media(client, headers)
    spec = _spec(video, image)

    checked = client.post(f"{MANIFESTS}/validate", json=spec, headers=headers)
    assert checked.status_code == 200, checked.text
    fingerprint = checked.json()["data"]["fingerprint"]
    assert len(fingerprint) == 64

    created = client.post(MANIFESTS, json=spec, headers=headers)
    assert created.status_code == 201, created.text
    body = created.json()["data"]
    assert body["fingerprint"] == fingerprint
    assert body["status"] == "draft"
    # 没显式排 mediaOrder 时按参考图顺序补齐，且算进指纹。
    assert body["functions"][0]["mediaOrder"] == ["front"]

    again = client.post(MANIFESTS, json=spec, headers=headers)
    assert again.status_code == 201, again.text
    assert again.json()["data"]["id"] == body["id"], "同键同配置必须返回原行"

    detail = client.get(f"{MANIFESTS}/{spec['key']}", headers=headers)
    assert detail.status_code == 200, detail.text
    assert detail.json()["data"]["video"]["assetId"] == video
    assert detail.json()["data"]["packs"][0]["references"][0]["assetId"] == image


def test_publish_exposes_data_via_catalog_port(client: TestClient) -> None:
    headers = _headers(client)
    video, image = _media(client, headers)
    spec = _spec(video, image)
    assert client.post(MANIFESTS, json=spec, headers=headers).status_code == 201

    published = client.post(f"{MANIFESTS}/{spec['key']}/publish", headers=headers)
    assert published.status_code == 200, published.text
    body = published.json()["data"]
    assert body["version"] == 1
    assert len(body["templates"]) == 1
    assert body["templates"][0]["workbenchPath"].startswith("/templates?sourceId=")

    templates = _catalog_templates()
    match = next(
        (item for item in templates if item.template_key.endswith(":replace")),
        None,
    )
    assert match is not None, "已发布卡片必须经端口可见"
    assert match.prompt == spec["functions"][0]["prompt"]
    assert match.rules["workbenchLabel"] == "产品替换"
    assert match.source_video_asset_id == video
    assert match.media_order == ("front",)


def test_published_config_is_immutable(client: TestClient) -> None:
    headers = _headers(client)
    video, image = _media(client, headers)
    spec = _spec(video, image)
    assert client.post(MANIFESTS, json=spec, headers=headers).status_code == 201
    assert (
        client.post(f"{MANIFESTS}/{spec['key']}/publish", headers=headers).status_code
        == 200
    )

    changed = _spec(video, image, key=spec["key"])
    changed["functions"][0]["prompt"] = "把 {{Mixed 1}} 换成 {{Mixed 2}} 吧。"
    conflict = client.post(MANIFESTS, json=changed, headers=headers)
    assert conflict.status_code == 409, conflict.text


def test_draft_is_private_until_published(client: TestClient) -> None:
    author = _headers(client)
    other = _headers(client)
    video, image = _media(client, author)
    spec = _spec(video, image)
    assert client.post(MANIFESTS, json=spec, headers=author).status_code == 201

    assert client.get(f"{MANIFESTS}/{spec['key']}", headers=other).status_code == 404
    assert (
        client.post(f"{MANIFESTS}/{spec['key']}/publish", headers=author).status_code
        == 200
    )
    assert client.get(f"{MANIFESTS}/{spec['key']}", headers=other).status_code == 200


def test_disable_removes_from_catalog(client: TestClient) -> None:
    headers = _headers(client)
    video, image = _media(client, headers)
    spec = _spec(video, image, key=f"dis-{suffix()}")
    assert client.post(MANIFESTS, json=spec, headers=headers).status_code == 201
    assert (
        client.post(f"{MANIFESTS}/{spec['key']}/publish", headers=headers).status_code
        == 200
    )
    keys = {item.template_key for item in _catalog_templates()}
    assert any(key.startswith(spec["key"]) for key in keys)

    disabled = client.post(f"{MANIFESTS}/{spec['key']}/disable", headers=headers)
    assert disabled.status_code == 200, disabled.text
    assert disabled.json()["data"]["status"] == "disabled"
    keys = {item.template_key for item in _catalog_templates()}
    assert not any(key.startswith(spec["key"]) for key in keys)


def test_invalid_specs_are_rejected(client: TestClient) -> None:
    headers = _headers(client)
    video, image = _media(client, headers)

    cases: list[tuple[str, dict[str, Any]]] = [
        ("重复 pack key", {"packs": _spec(video, image)["packs"] * 2}),
        (
            "缺必备用途",
            {
                "packs": [
                    {
                        "key": "dress",
                        "name": "连衣裙",
                        "kind": "product",
                        "supportedScopes": ["worn"],
                        "references": [
                            {"key": "side", "assetId": image, "role": "side"}
                        ],
                    }
                ]
            },
        ),
        ("复用包缺 packId", {"packs": [{"key": "p", "kind": "product"}]}),
        (
            "提示词编号越界",
            {
                "functions": [
                    {
                        "key": "replace",
                        "name": "产品替换",
                        "pack": "dress",
                        "prompt": "用 {{Mixed 1}} 和 {{Mixed 9}}。",
                    }
                ]
            },
        ),
        ("素材不存在", {"video": {"assetId": "missing-video"}}),
        ("未声明的键", {"unexpected": True}),
    ]
    for label, override in cases:
        spec = _spec(video, image, **override)
        response = client.post(f"{MANIFESTS}/validate", json=spec, headers=headers)
        assert response.status_code in (400, 422), (
            f"{label}: {response.status_code} {response.text}"
        )


# ---------------------------------------------------------------------------
# 发布回执（packager 的 journal 语义）
# ---------------------------------------------------------------------------


def _rows(key: str) -> tuple[list[Any], list[Any]]:
    """按 template_key 精确取子行：用例之间共享测试库，不能全表扫。"""
    from sqlalchemy import select

    from db.session import get_session_factory
    from modules.template_pack.models import (
        TemplateManifest,
        TemplateManifestFunction,
        TemplateManifestPack,
    )

    async def run() -> tuple[list[Any], list[Any]]:
        async with get_session_factory()() as session:
            head = (
                await session.scalars(
                    select(TemplateManifest).where(TemplateManifest.template_key == key)
                )
            ).one_or_none()
            assert head is not None, f"配置 {key} 不存在"
            packs = list(
                (
                    await session.scalars(
                        select(TemplateManifestPack).where(
                            TemplateManifestPack.manifest_id == head.id
                        )
                    )
                ).all()
            )
            functions = list(
                (
                    await session.scalars(
                        select(TemplateManifestFunction).where(
                            TemplateManifestFunction.manifest_id == head.id
                        )
                    )
                ).all()
            )
            return packs, functions

    return asyncio.run(run())


def _publish(
    client: TestClient, headers: dict[str, str], key: str
) -> dict[str, Any]:
    response = client.post(f"{MANIFESTS}/{key}/publish", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()["data"]


def _reuse_spec(video: str, image: str, pack_id: str) -> dict[str, Any]:
    return _spec(
        video,
        image,
        packs=[{"key": "reuse", "kind": "product", "packId": pack_id}],
        functions=[
            {
                "key": "replace",
                "name": "产品替换",
                "pack": "reuse",
                "prompt": "对 {{Mixed 2}} 使用 {{Mixed 1}}。",
                "bindings": {
                    "{{Mixed 1}}": "source_video",
                    "{{Mixed 2}}": "front",
                },
            }
        ],
    )


def test_publish_freezes_receipts(client: TestClient) -> None:
    """草稿不带回执；发布把「哪几行、第几版」冻进配置层，端口快照带出去。"""
    headers = _headers(client)
    video, image = _media(client, headers)
    spec = _spec(video, image)
    key = spec["key"]

    created = client.post(MANIFESTS, json=spec, headers=headers)
    assert created.status_code == 201, created.text
    draft_packs, draft_functions = _rows(key)
    assert draft_packs and draft_functions
    assert all(
        row.published_pack_id == "" and row.published_pack_version == 0
        for row in draft_packs
    )
    assert all(row.published_template_id == "" for row in draft_functions)

    body = _publish(client, headers, key)
    assert body["version"] == 1
    packs, functions = _rows(key)
    assert packs[0].published_pack_id == packs[0].id
    assert packs[0].published_pack_version == 1
    assert functions[0].published_template_id == functions[0].id
    assert body["templates"][0]["templateId"] == functions[0].id

    matched = [
        row
        for row in _catalog_templates()
        if row.template_key == f"{key}:replace"
    ]
    assert len(matched) == 1
    assert matched[0].published_template_id == functions[0].id


def test_reused_pack_receipt_points_to_source_version(client: TestClient) -> None:
    """复用包的回执指向被复用的那一行及其发布版本，不是自己、也不是当前版本。"""
    headers = _headers(client)
    video, image = _media(client, headers)
    source = _spec(video, image)
    assert client.post(MANIFESTS, json=source, headers=headers).status_code == 201
    _publish(client, headers, source["key"])
    source_packs, _ = _rows(source["key"])

    reuse = _reuse_spec(video, image, source_packs[0].id)
    checked = client.post(f"{MANIFESTS}/validate", json=reuse, headers=headers)
    assert checked.status_code == 200, checked.text
    saved = client.post(MANIFESTS, json=reuse, headers=headers)
    assert saved.status_code == 201, saved.text
    _publish(client, headers, reuse["key"])

    packs, functions = _rows(reuse["key"])
    assert packs[0].published_pack_id == source_packs[0].id
    assert packs[0].published_pack_version == 1
    assert functions[0].published_template_id == functions[0].id


def test_reuse_unknown_pack_is_rejected(client: TestClient) -> None:
    """预检就要挡住「指向不存在的已发布包」，别等发布时才发现。"""
    headers = _headers(client)
    video, image = _media(client, headers)
    spec = _reuse_spec(video, image, "not-a-published-pack")
    response = client.post(f"{MANIFESTS}/validate", json=spec, headers=headers)
    assert response.status_code == 400, response.text
    detail = client.get(f"{MANIFESTS}/{spec['key']}", headers=headers)
    assert detail.status_code == 404, "预检失败不能顺手落库"


def test_disable_keeps_receipts_but_hides_catalog(client: TestClient) -> None:
    """停用是下架，不是抹掉历史：端口读不到，回执仍在库里。"""
    headers = _headers(client)
    video, image = _media(client, headers)
    spec = _spec(video, image)
    assert client.post(MANIFESTS, json=spec, headers=headers).status_code == 201
    _publish(client, headers, spec["key"])
    card_key = f"{spec['key']}:replace"
    assert any(row.template_key == card_key for row in _catalog_templates())

    disabled = client.post(f"{MANIFESTS}/{spec['key']}/disable", headers=headers)
    assert disabled.status_code == 200, disabled.text
    assert all(row.template_key != card_key for row in _catalog_templates())
    packs, functions = _rows(spec["key"])
    assert packs[0].published_pack_version == 1
    assert functions[0].published_template_id
