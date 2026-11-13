"""封装草稿的资产库契约：`referenceLibraryIds` / `coverLibraryId`。

v1 素材库已于 2026-11-13 下线，「两套字段谁优先」的判断随 `assets` 表一起删掉了：
现在只可能有一套。剩下的判据都是「错了不会报错」的那类：

1. 封面必须出现在 `referenceLibraryIds` 里，只给 `coverLibraryId` 直接 400；
2. 包内预览由「已发布版本」授权：图走库自己的缩略图，不做资产库授权检查
   （那是另一个端点的事），也不拼 `storage_key` 当旧 `path`。
3. 封装引用的图不能被「彻底删除」：这条走注入口全链路，只声明来源不接实现
   一样会静默放行。
"""

from __future__ import annotations

import asyncio
import hashlib
import uuid

import pytest
from tests_template_helpers import ADMIN, drop_pack, login, png_bytes, upload, video_bytes

from db.session import get_session_factory
from infra import get_storage
from modules.library.models import LibraryAsset

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


def test_cover_must_be_one_of_the_references(client) -> None:
    """只给封面、不给参考图 = 封面落在一个不在包内的 id 上，必须 400。

    v1 下线前这里还有一半是「两套字段同时填 → 400」；入参只剩一套之后那半边
    已经没有输入空间了，删掉而不是留着一条永远为真的断言。
    """
    headers = login(client)
    key = f"library/blobs/ac/{uuid.uuid4().hex}.png"
    content = png_bytes()
    asyncio.run(get_storage().put(key, content, "image/png"))
    asset_id = asyncio.run(_library_asset(content, key=key))
    try:
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
        assert "封面必须从参考图里选" in cover_only["body"]["message"]
    finally:
        asyncio.run(get_storage().delete(key))
        asyncio.run(_drop_assets([asset_id]))


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


def test_published_v2_pack_can_actually_run(client) -> None:
    """整条链的验收：草稿写 v2 图 → 发布 → 选它跑一次准备。

    三段分开看都「绿」，合起来曾经断在中间（`prepare` 拿 v2 的 id 去旧表查）。
    这里不构造任何内部行，全部走 HTTP：封装发布的版本就是调用时冻结的那一份。
    """
    headers = login(client)
    key = f"library/blobs/ac/{uuid.uuid4().hex}.png"
    content = png_bytes((48, 64))
    asyncio.run(get_storage().put(key, content, "image/png"))
    asset_id = asyncio.run(_library_asset(content, key=key))
    pack_id = uuid.uuid4().hex
    source = upload(client, "v2-pack-run.mp4", "source", video_bytes(2))
    try:
        created = _draft(
            client,
            headers,
            {
                "requestId": pack_id,
                "kind": "product",
                "name": "能跑起来的 v2 封装",
                "referenceLibraryIds": [asset_id],
                "bindings": [{"role": "front", "description": ""}],
                "supportedScopes": ["worn"],
            },
        )
        assert created["status"] == 200, created["text"]
        version = client.post(
            f"{API}/templates/replacement-packs/{pack_id}/publish",
            headers=headers,
            json={"expectedRevision": created["body"]["data"]["revision"]},
        ).json()["data"]["version"]

        response = client.post(
            f"{API}/templates/replacement-runs",
            headers=headers,
            json={
                "requestId": uuid.uuid4().hex,
                "kind": "product",
                "sourceLibraryId": source["id"],
                "packId": pack_id,
                "packVersion": version,
                "replacementScope": "人物上身穿着",
            },
        )
        assert response.status_code == 201, response.text
        run = response.json()["data"]
        # 没绑视频模板的普通链路顺序是「参考图 + 原片」（与 v1 素材包一致）：
        # 两个 id 空间各来一张，一个不少、谁是谁也不能混
        assets = run["inputSummary"]["assets"]
        assert [item["id"] for item in assets] == [asset_id, source["id"]]
        # 出参的 role 是「编译器认识的角色」，不是封装里的用途（`front`）：
        # v2 图必须被当成产品图喂进去，而不是因为来源不同就丢了角色
        assert [item["role"] for item in assets] == ["target_product", "source_video"]
        # 归档快照落在本次任务目录下：v2 的字节确实被复制进来，而不是只记了个 id
        archived = client.get(assets[0]["url"], headers=headers)
        assert archived.status_code == 200, archived.text
        assert archived.content == content
    finally:
        asyncio.run(drop_pack(pack_id))
        asyncio.run(get_storage().delete(key))
        asyncio.run(_drop_assets([asset_id]))
