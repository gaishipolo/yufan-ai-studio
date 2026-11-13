"""封装草稿的资产库 v2 契约字段：`referenceLibraryIds` / `coverLibraryId`。

三条判据（都是「错了不会报错」的那类）：

1. 空间只按**字段名**判，两套字段同时填直接 400，不做「谁优先」的静默选择；
2. 出参带回 `referenceIdSpace`，否则前端编辑一次 v2 封装就会用 `referenceIds`
   回写，把同一批 id 重新解释成 v1；
3. 包内预览由「已发布版本」授权：v2 图走库自己的缩略图，不做资产库授权检查
   （那是另一个端点的事），也不拼 `storage_key` 当旧 `path`。
4. 封装引用的 v2 图不能被「彻底删除」：这条走注入口全链路，只声明来源不接实现
   一样会静默放行。
"""

from __future__ import annotations

import asyncio
import hashlib
import uuid

import pytest
from tests_template_helpers import ADMIN, drop_pack, login, png_bytes, upload

from db.session import get_session_factory
from infra import get_storage
from modules.library.models import LibraryAsset
from modules.template.repository import PackRepository

API = "/api/v1"


async def _library_asset(
    content: bytes,
    *,
    key: str,
    cover_key: str = "",
    owner: str = ADMIN,
    kind: str = "image",
    state: str = "active",
) -> str:
    row = LibraryAsset(
        id=uuid.uuid4().hex,
        name=f"v2-pack-{uuid.uuid4().hex[:8]}.png",
        kind=kind,
        origin="upload",
        mime_type="image/png",
        size_bytes=len(content),
        sha256=hashlib.sha256(content).hexdigest(),
        storage_key=key,
        cover_key=cover_key,
        width=64,
        height=96,
        owner_id=owner,
        visibility="personal",
        state=state,
    )
    async with get_session_factory()() as session:
        session.add(row)
        await session.commit()
    return row.id


async def _drop_assets(asset_ids: list[str]) -> None:
    async with get_session_factory()() as session:
        for asset_id in asset_ids:
            row = await session.get(LibraryAsset, asset_id)
            if row is not None:
                await session.delete(row)
        await session.commit()


async def _reference_spaces(pack_id: str) -> set[str]:
    async with get_session_factory()() as session:
        return {ref.asset_id_space for ref in await PackRepository(session).references(pack_id)}


def _draft(client, headers, body: dict) -> dict:
    response = client.put(
        f"{API}/templates/replacement-packs/draft", headers=headers, json=body
    )
    return {
        "status": response.status_code,
        "body": response.json(),
        "text": response.text,
    }


def test_library_reference_draft_publishes_and_previews_v2_bytes(client) -> None:
    headers = login(client)
    key = f"library/blobs/ab/{uuid.uuid4().hex}.png"
    content = png_bytes((40, 60))
    asyncio.run(get_storage().put(key, content, "image/png"))
    asset_id = asyncio.run(_library_asset(content, key=key))
    pack_id = uuid.uuid4().hex
    try:
        created = _draft(
            client,
            headers,
            {
                "requestId": pack_id,
                "kind": "product",
                "name": "资产库 v2 封装",
                "referenceLibraryIds": [asset_id],
                "bindings": [{"role": "front", "description": ""}],
                "supportedScopes": ["worn"],
            },
        )
        assert created["status"] == 200, created["text"]
        data = created["body"]["data"]
        assert data["referenceIds"] == [asset_id]
        # 前端靠它决定回写哪个字段
        assert data["referenceIdSpace"] == "v2"
        assert asyncio.run(_reference_spaces(pack_id)) == {"v2"}

        response = client.post(
            f"{API}/templates/replacement-packs/{pack_id}/publish",
            headers=headers,
            json={"expectedRevision": data["revision"]},
        )
        assert response.status_code == 200, response.text
        version = response.json()["data"]["version"]

        preview = client.get(
            f"{API}/templates/replacement-packs/{pack_id}/preview/{asset_id}",
            headers=headers,
            params={"version": version},
        )
        assert preview.status_code == 200, preview.text
        assert preview.content == content
        assert preview.headers["content-type"].startswith("image/")

        catalog = client.get(
            f"{API}/templates/replacement-catalog", headers=headers
        ).json()["data"]["packs"]
        listed = next(item for item in catalog if item["id"] == pack_id)
        assert listed["referenceIdSpace"] == "v2"
        assert listed["previewUrls"] == [
            f"/api/v1/templates/replacement-packs/{pack_id}/preview/{asset_id}"
            f"?version={version}"
        ]
    finally:
        asyncio.run(drop_pack(pack_id))
        asyncio.run(get_storage().delete(key))
        asyncio.run(_drop_assets([asset_id]))


def test_preview_prefers_library_thumbnail(client) -> None:
    """有 `cover_key` 就发缩略图：列表里一次几十张，拿原件当缩略图是白烧带宽。"""
    headers = login(client)
    key = f"library/blobs/cd/{uuid.uuid4().hex}.png"
    cover_key = f"library/covers/ef/{uuid.uuid4().hex}.jpg"
    content = png_bytes((40, 60))
    cover = png_bytes((8, 8))
    asyncio.run(get_storage().put(key, content, "image/png"))
    asyncio.run(get_storage().put(cover_key, cover, "image/jpeg"))
    asset_id = asyncio.run(_library_asset(content, key=key, cover_key=cover_key))
    pack_id = uuid.uuid4().hex
    try:
        created = _draft(
            client,
            headers,
            {
                "requestId": pack_id,
                "kind": "product",
                "name": "带缩略图的 v2 封装",
                "referenceLibraryIds": [asset_id],
                "bindings": [{"role": "front", "description": ""}],
                "supportedScopes": ["worn"],
            },
        )
        assert created["status"] == 200, created["text"]
        revision = created["body"]["data"]["revision"]
        version = client.post(
            f"{API}/templates/replacement-packs/{pack_id}/publish",
            headers=headers,
            json={"expectedRevision": revision},
        ).json()["data"]["version"]
        preview = client.get(
            f"{API}/templates/replacement-packs/{pack_id}/preview/{asset_id}",
            headers=headers,
            params={"version": version},
        )
        assert preview.status_code == 200, preview.text
        assert preview.content == cover
    finally:
        asyncio.run(drop_pack(pack_id))
        asyncio.run(get_storage().delete(key))
        asyncio.run(get_storage().delete(cover_key))
        asyncio.run(_drop_assets([asset_id]))


def test_mixing_reference_fields_is_rejected(client) -> None:
    headers = login(client)
    key = f"library/blobs/ac/{uuid.uuid4().hex}.png"
    content = png_bytes()
    asyncio.run(get_storage().put(key, content, "image/png"))
    asset_id = asyncio.run(_library_asset(content, key=key))
    legacy = upload(client, "v1-混用.png", "product", png_bytes())
    try:
        mixed = _draft(
            client,
            headers,
            {
                "requestId": uuid.uuid4().hex,
                "kind": "product",
                "name": "混用两套字段",
                "referenceIds": [legacy["id"]],
                "referenceLibraryIds": [asset_id],
            },
        )
        assert mixed["status"] == 400, mixed["text"]
        assert "不能同时使用" in mixed["body"]["message"]

        cover_only = _draft(
            client,
            headers,
            {
                "requestId": uuid.uuid4().hex,
                "kind": "product",
                "name": "只有 v2 封面",
                "coverLibraryId": asset_id,
            },
        )
        assert cover_only["status"] == 400, cover_only["text"]
        assert "coverLibraryId" in cover_only["body"]["message"]
    finally:
        asyncio.run(get_storage().delete(key))
        asyncio.run(_drop_assets([asset_id]))


def test_legacy_field_still_writes_v1_space(client) -> None:
    """旧 UI 不变：`referenceIds` 仍按当前写入空间（v1）解释，出参也如实标注。"""
    headers = login(client)
    image = upload(client, "v1-空格回归.png", "product", png_bytes())
    pack_id = uuid.uuid4().hex
    try:
        created = _draft(
            client,
            headers,
            {
                "requestId": pack_id,
                "kind": "product",
                "name": "v1 封装",
                "referenceIds": [image["id"]],
                "bindings": [{"role": "front", "description": ""}],
            },
        )
        assert created["status"] == 200, created["text"]
        assert created["body"]["data"]["referenceIdSpace"] == "v1"
        assert asyncio.run(_reference_spaces(pack_id)) == {"v1"}
    finally:
        asyncio.run(drop_pack(pack_id))


@pytest.mark.parametrize(
    ("kind", "state"),
    [("image", "draft"), ("image", "trash"), ("video", "active")],
)
def test_only_active_image_can_be_packaged(client, kind: str, state: str) -> None:
    """草稿、回收站、非图片都不行：可读 ≠ 可进模板（生命周期是另一道判据）。"""
    headers = login(client)
    key = f"library/blobs/ad/{uuid.uuid4().hex}.png"
    content = png_bytes()
    asyncio.run(get_storage().put(key, content, "image/png"))
    asset_id = asyncio.run(_library_asset(content, key=key, kind=kind, state=state))
    try:
        created = _draft(
            client,
            headers,
            {
                "requestId": uuid.uuid4().hex,
                "kind": "product",
                "name": "不该被封装",
                "referenceLibraryIds": [asset_id],
                "supportedScopes": ["worn"],
            },
        )
        assert created["status"] == 400, created["text"]
        assert "只能封装可读的已采纳参考图片" in created["body"]["message"]
    finally:
        asyncio.run(get_storage().delete(key))
        asyncio.run(_drop_assets([asset_id]))


def test_pack_reference_blocks_library_purge(client) -> None:
    """封装引用的 v2 图不能被彻底删除。

    走的是注入口全链路（`TemplatePackReference.asset_id` → `ask_library_references`
    → 409），不是直接调 checker：漏了 `ModuleSpec.library_reference_sources` 时
    保存草稿、发布、预览全都正常，只有这里会红——这正是「不可逆删除被静默放行」
    的唯一可观测点。
    """
    headers = login(client)
    key = f"library/blobs/ae/{uuid.uuid4().hex}.png"
    content = png_bytes((32, 48))
    asyncio.run(get_storage().put(key, content, "image/png"))
    asset_id = asyncio.run(_library_asset(content, key=key))
    pack_id = uuid.uuid4().hex
    try:
        created = _draft(
            client,
            headers,
            {
                "requestId": pack_id,
                "kind": "product",
                "name": "挡住删除的封装",
                "referenceLibraryIds": [asset_id],
                "bindings": [{"role": "front", "description": ""}],
                "supportedScopes": ["worn"],
            },
        )
        assert created["status"] == 200, created["text"]

        trashed = client.delete(f"{API}/library/assets/{asset_id}", headers=headers)
        assert trashed.status_code == 200, trashed.text

        blocked = client.delete(f"{API}/library/trash/{asset_id}", headers=headers)
        assert blocked.status_code == 409, blocked.text
        assert blocked.json()["code"] == 40986
        references = blocked.json()["details"]["references"]
        # 文案要能让用户认出是哪个封装，否则只能去库里翻
        assert any("挡住删除的封装" in item for item in references), references

        # 封装不存在了 → 不再算引用，回收站条目可以彻底删掉（否则会永久卡住）
        asyncio.run(drop_pack(pack_id))
        purged = client.delete(f"{API}/library/trash/{asset_id}", headers=headers)
        assert purged.status_code == 200, purged.text
    finally:
        asyncio.run(drop_pack(pack_id))
        asyncio.run(get_storage().delete(key))
        asyncio.run(_drop_assets([asset_id]))
