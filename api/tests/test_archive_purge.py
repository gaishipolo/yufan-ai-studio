"""清空回收站：软删只是标记，真删要连带镜像与字节，且不能打断共享同一份字节的人。

回归的是 A2 的三条：
1. `DELETE /assets/archive/{id}` 只接受已在回收站的记录，且要过引用检查；
2. 内容寻址下同一份字节可能被多个分类/多个人共享，删一条不能删字节；
3. `prune-blobs --include-trash` 的 dry-run 与 execute 看到的是同一批。
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi.testclient import TestClient
from test_asset_api import png_bytes  # noqa: F401
from tests_archive_helpers import archive

from core.ports.assets import has_asset_reference_checker
from db.session import get_session_factory
from infra.archive import get_archive_storage
from modules.asset.models import Asset


async def _row(asset_id: str) -> Asset | None:
    async with get_session_factory()() as session:
        return await session.get(Asset, asset_id)


def _trash(client: TestClient, api: str, asset: dict[str, Any]) -> None:
    response = client.put(
        f"{api}/assets/archive/{asset['id']}/deleted",
        json={"expectedVersion": asset["stateVersion"], "deleted": True},
    )
    assert response.status_code == 200, response.text


def test_purge_removes_row_mirror_and_bytes(client: TestClient, api: str) -> None:
    asset = archive.upload(client, "purge-me.png", "product", png_bytes((20, 20), "red"))
    storage = get_archive_storage()
    mirror = f"catalog/assets/{asset['id']}.json"
    blob = asset["path"]
    assert storage.exists(blob) and storage.exists(mirror)

    _trash(client, api, asset)
    response = client.delete(f"{api}/assets/archive/{asset['id']}")
    assert response.status_code == 204, response.text

    assert asyncio.run(_row(asset["id"])) is None
    assert not storage.exists(mirror)
    assert not storage.exists(blob), "没人再引用的字节必须真的释放"


def test_purge_requires_trash_first(client: TestClient, api: str) -> None:
    """两步走不是形式：没进回收站就不允许物理删除。"""
    asset = archive.upload(client, "not-in-trash.png", "product", png_bytes((20, 20), "blue"))
    response = client.delete(f"{api}/assets/archive/{asset['id']}")
    assert response.status_code == 400
    assert asyncio.run(_row(asset["id"])) is not None


def test_purge_keeps_bytes_shared_by_another_record(client: TestClient, api: str) -> None:
    """同一个人把同一份图登记到两个分类：只删一条，另一条仍要能取到字节。"""
    data = png_bytes((24, 24), "green")
    first = archive.upload(client, "shared-a.png", "product", data)
    second = archive.upload(client, "shared-b.png", "scene", data)
    assert first["path"] == second["path"], "内容寻址应共用同一个 blob"
    storage = get_archive_storage()

    _trash(client, api, first)
    assert client.delete(f"{api}/assets/archive/{first['id']}").status_code == 204

    assert storage.exists(second["path"]), "另一条记录还在，字节被误删了"
    assert client.get(f"{api}/assets/archive/{second['id']}/thumbnail").status_code in (200, 307)


def test_purge_blocked_when_reference_appears_after_trash(client: TestClient, api: str) -> None:
    """软删时没引用，不代表真删时也没有：每次都要重新问一遍。"""
    assert has_asset_reference_checker()
    asset = archive.upload(client, "late-ref.png", "product", png_bytes((20, 20), "yellow"))
    _trash(client, api, asset)

    key = "catalog/packs/late-pack.json"
    storage = get_archive_storage()
    storage.write(
        key,
        {"id": "late-pack", "name": "后加的封装", "referenceIds": [asset["id"]]},
    )
    try:
        response = client.delete(f"{api}/assets/archive/{asset['id']}")
        assert response.status_code == 409, response.text
        assert "后加的封装" in str(response.json()["details"])
        assert asyncio.run(_row(asset["id"])) is not None
    finally:
        storage.remove(key)
