"""归档存储字节入库：为模板库替换成片预留的能力，不接生成链路。

模板库成片由 `template/worker.publish_output` 搬进 `ArchiveStorage`
（`runs/{id}/outputs/generated.mp4`），与资产库用的主对象存储是两个驱动。
本文件只验证「把归档盘的字节登记成草稿」这条路本身；
生成侧的接入已于 2026-11-13 落地（`ReplacementPipeline.archive_request`），
翻译层由 `test_replacement_ingest.py` 钉住。

断言方向：

1. 落点固定 `draft / personal / generated`，且字节复制进资产库自己的 blob；
2. `sourceKey` 幂等，重复回调不产生第二行；
3. 归档 key 取不到字节时报错，不留下半截资产；
4. 三种来源的优先级（storageKey > archiveKey > sourceUrl）；
5. 端点仍由 `library:generation:ingest` 守护；
6. **端口协议直调也成立** —— 这正是 `archive_request` 现在的调用方式，不经过 HTTP。
"""

from __future__ import annotations

import asyncio
import tempfile
import uuid
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from library_support import LIB_RW, admin_headers, make_user, png_bytes

from core.ports.library_ingest import IngestRequest
from core.ports.library_lookup import LibraryAssetKind
from db.session import get_session_factory
from infra import get_storage
from infra.archive import get_archive_storage
from modules.library.ingest import SqlLibraryIngest
from modules.library.models import LibraryAsset

INGEST = "library:generation:ingest"


def _archive_blob(color: str) -> tuple[str, bytes]:
    """往归档盘写一份 PNG，返回 (key, 原始字节)。"""
    key = f"tests/library-ingest-archive/{uuid.uuid4().hex}/sample.png"
    data = png_bytes(color)
    with tempfile.TemporaryDirectory() as folder:
        staging = Path(folder) / "sample.png"
        staging.write_bytes(data)
        get_archive_storage().copy(staging, key)
    return key, data


def _body(archive_key: str = "") -> dict[str, Any]:
    task_id = uuid.uuid4().hex
    return {
        "sourceModule": "template",
        "sourceTaskId": task_id,
        "sourceKey": f"template:{task_id}:v0",
        "kind": "image",
        "name": "替换成片.png",
        "archiveKey": archive_key,
    }


def _ingest(client: TestClient, api: str, headers: dict[str, str], body: dict):
    return client.post(f"{api}/library/assets/from-generation", json=body, headers=headers)


def _row(asset_id: str) -> LibraryAsset:
    async def run() -> LibraryAsset:
        async with get_session_factory()() as session:
            row = await session.get(LibraryAsset, asset_id)
            assert row is not None
            return row

    return asyncio.run(run())


def test_archive_key_lands_in_draft_with_own_bytes(client: TestClient, api: str) -> None:
    """归档盘的字节被物化后写进资产库自己的 blob，不依赖归档盘指针。"""
    admin = admin_headers(client)
    _, owner_h = make_user(client, admin, LIB_RW + [INGEST])
    key, data = _archive_blob("mediumseagreen")

    response = _ingest(client, api, owner_h, _body(key))
    assert response.status_code == 201, response.text
    assert response.json()["data"]["created"] is True

    row = _row(response.json()["data"]["assetId"])
    assert (row.state, row.origin, row.visibility, row.kind) == (
        "draft",
        "generated",
        "personal",
        "image",
    )
    assert row.storage_key and row.storage_key != key
    assert row.sha256

    async def read_blob() -> bytes:
        return await get_storage().get(row.storage_key)

    assert asyncio.run(read_blob()) == data


def test_archive_key_is_idempotent_by_source_key(client: TestClient, api: str) -> None:
    admin = admin_headers(client)
    _, owner_h = make_user(client, admin, LIB_RW + [INGEST])
    key, _ = _archive_blob("lightseagreen")
    body = _body(key)

    first = _ingest(client, api, owner_h, body)
    assert first.status_code == 201, first.text
    second = _ingest(client, api, owner_h, body)
    assert second.status_code == 200, second.text
    assert second.json()["data"] == {
        "assetId": first.json()["data"]["assetId"],
        "created": False,
    }


def test_missing_archive_key_is_rejected(client: TestClient, api: str) -> None:
    admin = admin_headers(client)
    _, owner_h = make_user(client, admin, LIB_RW + [INGEST])
    missing = f"tests/library-ingest-archive/{uuid.uuid4().hex}/missing.png"

    response = _ingest(client, api, owner_h, _body(missing))
    assert response.status_code == 400, response.text


def test_storage_key_takes_priority_over_archive_key(client: TestClient, api: str) -> None:
    """同时给两种来源时走主存储，归档 key 不存在也不该让入库失败。"""
    admin = admin_headers(client)
    _, owner_h = make_user(client, admin, LIB_RW + [INGEST])
    storage_key = f"library/blobs/gen/{uuid.uuid4().hex}.png"
    asyncio.run(get_storage().put(storage_key, png_bytes("tomato"), "image/png"))
    missing = f"tests/library-ingest-archive/{uuid.uuid4().hex}/missing.png"

    body = _body(missing)
    body["storageKey"] = storage_key
    response = _ingest(client, api, owner_h, body)
    assert response.status_code == 201, response.text
    row = _row(response.json()["data"]["assetId"])
    assert row.storage_key != missing


def test_archive_key_still_requires_ingest_permission(client: TestClient, api: str) -> None:
    admin = admin_headers(client)
    _, plain_h = make_user(client, admin, LIB_RW)
    key, _ = _archive_blob("slateblue")

    response = _ingest(client, api, plain_h, _body(key))
    assert response.status_code == 403, response.text


def test_port_ingest_accepts_archive_key_without_http(client: TestClient) -> None:
    """`library-ingest` 端口直调也认 archiveKey：将来 archive_request 只需一行。"""
    admin = admin_headers(client)
    owner, _ = make_user(client, admin, LIB_RW)
    key, _ = _archive_blob("darkorange")
    task_id = uuid.uuid4().hex

    async def run():
        async with get_session_factory()() as session:
            return await SqlLibraryIngest().ingest_generated(
                session,
                IngestRequest(
                    source_module="template",
                    source_task_id=task_id,
                    source_key=f"template:{task_id}:v0",
                    kind=LibraryAssetKind.IMAGE,
                    owner_id=owner["id"],
                    name="替换成片.png",
                    archive_key=key,
                ),
            )

    result = asyncio.run(run())
    assert result.created is True
    row = _row(result.asset_id)
    assert (row.state, row.origin) == ("draft", "generated")
    assert row.owner_id == owner["id"]
