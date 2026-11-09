"""字节与归属分离：`blob` 的写入、共享、引用计数与回填。

这组用例守的是「同一份内容只有一份字节」这条前提。它一旦破，症状不是报错：

- 复制到另一个分类变成了两份字节 → 配额翻倍，用户以为「只复制了一次」；
- 软删不减引用 → 清空回收站时删掉别人在用的字节（不可逆）；
- 回填把两份不同的文件当成一份 → 换桶后其中一条裂图，且无法判断哪条是对的。

所以每条断言都对着一个**具体的数据形状**（blob 条数、ref_count），
而不是「接口返回 200」。
"""

from __future__ import annotations

import asyncio

from fastapi.testclient import TestClient
from sqlalchemy import func, select
from tests_template_helpers import API, login, png_bytes, upload

from db.session import get_session_factory
from modules.asset.blob_repository import backfill_blobs
from modules.asset.library_models import Blob
from modules.asset.models import Asset


def _admin(client: TestClient) -> dict[str, str]:
    return login(client)


async def _blob_of(asset_id: str) -> tuple[str | None, int, int]:
    """返回 (blob_id, ref_count, 该 blob 的素材行数)。"""
    async with get_session_factory()() as session:
        asset = await session.get(Asset, asset_id)
        assert asset is not None
        blob = await session.get(Blob, asset.blob_id) if asset.blob_id else None
        rows = int(
            (
                await session.scalar(
                    select(func.count())
                    .select_from(Asset)
                    .where(Asset.blob_id == asset.blob_id)
                )
            )
            or 0
        )
        return asset.blob_id, (blob.ref_count if blob else -1), rows


def test_upload_creates_blob_and_links_asset(client: TestClient) -> None:
    """一次上传产生「一条 blob + 一条素材」，引用计数为 1。"""
    item = upload(client, "blob-a.png", "product", png_bytes())
    blob_id, ref_count, linked = asyncio.run(_blob_of(item["id"]))
    assert blob_id, "上传后素材必须挂上 blob"
    assert ref_count == 1
    assert linked == 1


def test_same_file_in_two_categories_shares_one_blob(client: TestClient) -> None:
    """同一份字节传到两个分类：两条素材、**一个** blob。

    这是整个改造的核心收益，也是配额口径的前提：复制不该让存储翻倍。
    写成 `SUM(size_bytes)` 的配额查询会在这里静默翻倍——所以这条用例
    同时守住存储与配额两件事。
    """
    content = png_bytes()
    first = upload(client, "dup-a.png", "product", content)
    second = upload(client, "dup-b.png", "character", content)
    assert first["id"] != second["id"]

    first_blob, first_refs, _ = asyncio.run(_blob_of(first["id"]))
    second_blob, second_refs, linked = asyncio.run(_blob_of(second["id"]))
    assert first_blob == second_blob
    assert first_refs == second_refs == 2
    assert linked == 2


def test_trash_releases_and_restore_reclaims_the_reference(client: TestClient) -> None:
    """软删 -1、恢复 +1。

    漏掉恢复那一半的症状只在「恢复过一次 + 之后彻底删除」这个组合下出现：
    清空回收站会把 `ref_count = 0` 却仍被引用的字节删掉，另一条素材当场裂图。
    """
    content = png_bytes()
    first = upload(client, "ref-a.png", "product", content)
    second = upload(client, "ref-b.png", "character", content)
    headers = _admin(client)

    response = client.put(
        f"{API}/assets/{first['id']}/deleted",
        json={"expectedVersion": first["stateVersion"], "deleted": True},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    _, refs, _ = asyncio.run(_blob_of(second["id"]))
    assert refs == 1

    version = response.json()["data"]["stateVersion"]
    response = client.put(
        f"{API}/assets/{first['id']}/deleted",
        json={"expectedVersion": version, "deleted": False},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    _, refs, _ = asyncio.run(_blob_of(second["id"]))
    assert refs == 2


def test_backfill_links_legacy_rows_and_is_idempotent(client: TestClient) -> None:
    """回填给存量行补 blob，跑第二遍不新建任何东西。

    造一条「有 storage_key、没有 blob_id」的行来模拟迁移刚跑完的状态：
    读路径回落旧列仍然可用，doctor 记一条 WARN，回填之后两者一致。
    """

    async def stage() -> tuple[str, str]:
        async with get_session_factory()() as session:
            row = Asset(
                name="legacy-without-blob.png",
                type="image",
                media_type="image",
                mime_type="image/png",
                size_bytes=1024,
                storage_key="blobs/aa/legacy.png",
                sha256="f" * 64,
                category="product",
                source="archive",
                owner_id="u_001",
                visibility="personal",
            )
            session.add(row)
            await session.commit()
            return row.id, "f" * 64

    async def backfill() -> tuple[int, int]:
        async with get_session_factory()() as session:
            first = await backfill_blobs(session, batch_size=50)
            second = await backfill_blobs(session, batch_size=50)
            return first.blobs_created, second.blobs_created

    asset_id, digest = asyncio.run(stage())
    created_first, created_second = asyncio.run(backfill())
    assert created_first >= 1
    assert created_second == 0, "第二次回填不该新建 blob（幂等）"

    blob_id, ref_count, _ = asyncio.run(_blob_of(asset_id))
    assert blob_id, "回填后应立即挂上 blob"
    assert ref_count >= 1

    async def digest_of() -> str:
        async with get_session_factory()() as session:
            blob = await session.get(Blob, blob_id)
            assert blob is not None
            return blob.sha256 or ""

    assert asyncio.run(digest_of()) == digest
