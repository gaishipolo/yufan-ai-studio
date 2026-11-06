"""归档索引：DB 是真相源，JSON 只是替换模板的镜像。

重点验三件事：

1. 列表/详情/取文件全部读 `assets` 表 —— 把 JSON 删掉也不影响列表。
2. `app import-archive-index` 幂等：跑第二遍 `updated=0 / created=0`。
3. 归档与上传各自一套去重与唯一约束：同一份文件两边都能存在。

用接口造数、用接口或 DB 断言，不另开事件循环去动共享引擎（loop 绑定隐患）。
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import uuid
from collections.abc import AsyncIterator
from datetime import UTC
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import delete, select

from db.session import get_session_factory
from infra.archive import preview_key
from infra.archive.local import get_archive_settings
from modules.asset.archive_import import import_archive_index
from modules.asset.models import Asset

API = "/api/v1"


@pytest.fixture
def archive_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(get_archive_settings(), "root", str(tmp_path))
    return tmp_path


def png(size: tuple[int, int] = (20, 30)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, tuple(os.urandom(3))).save(buffer, format="PNG")
    return buffer.getvalue()


def upload(client: TestClient, name: str, category: str, content: bytes) -> dict:
    response = client.post(
        f"{API}/assets/archive/upload",
        params={"filename": name, "category": category},
        content=content,
        headers={"Content-Type": "application/octet-stream"},
    )
    assert response.status_code == 201, response.text
    data = response.json()["data"]
    assert isinstance(data, dict)
    return data


def seed_index(root: Path, *, owner: str, content: bytes | None = None) -> dict:
    """在磁盘上造一条「历史归档索引」：文件 + catalog/assets/*.json。"""
    asset_id = uuid.uuid4().hex
    payload = content if content is not None else png()
    key = f"library/character/2026/01/{asset_id}.png"
    target = root / key
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(payload)

    media = {
        "id": asset_id,
        "name": "历史人物.png",
        "category": "character",
        "type": "image",
        "mimeType": "image/png",
        "sizeBytes": len(payload),
        "path": key,
        "sha256": hashlib.sha256(payload).hexdigest(),
        "createdAt": "2026-01-02T03:04:05Z",
        "width": 20,
        "height": 30,
        "duration": 0,
        "hasAudio": False,
        "url": f"{API}/assets/archive/{asset_id}/file",
        "previewUrl": f"{API}/assets/archive/{asset_id}/thumbnail",
        "ownerId": owner,
        "previewPath": f"library/previews/{asset_id}.jpg",
    }
    folder = root / "catalog" / "assets"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{asset_id}.json").write_text(
        json.dumps(media, ensure_ascii=False), encoding="utf-8"
    )
    return media


@pytest.fixture
async def session() -> AsyncIterator:
    async with get_session_factory()() as value:
        yield value


async def _clear(owner: str) -> None:
    async with get_session_factory()() as s:
        await s.execute(delete(Asset).where(Asset.owner_id == owner))
        await s.commit()


# ---------------------------------------------------------------- 索引即 DB
def test_upload_writes_row_and_json_mirror(client: TestClient, archive_root: Path) -> None:
    item = upload(client, "新人物.png", "character", png())

    row = archive_root / "catalog" / "assets" / f"{item['id']}.json"
    assert row.is_file(), "替换模板仍按文件读素材，镜像必须写"
    mirror = json.loads(row.read_text(encoding="utf-8"))
    assert mirror["ownerId"] == "u_001"
    assert mirror["path"] == item["path"]
    # 字节按 sha256 落库：位置由内容决定，同一份文件传两次只有一份
    assert mirror["path"].startswith("blobs/")
    # 预览 key 带画质版本：升级转码参数时旧缓存自动失效（不手工清目录）
    assert mirror["previewPath"] == preview_key(item["sha256"])

    listed = client.get(f"{API}/assets/archive", params={"category": "character"})
    ids = [value["id"] for value in listed.json()["data"]["items"]]
    assert item["id"] in ids


def test_list_survives_lost_json(client: TestClient, archive_root: Path) -> None:
    """索引在 DB：JSON 丢了，列表与取文件仍要正常（镜像不是真相源）。"""
    item = upload(client, "镜像丢失.png", "character", png())
    (archive_root / "catalog" / "assets" / f"{item['id']}.json").unlink()

    listed = client.get(f"{API}/assets/archive").json()["data"]
    assert item["id"] in [value["id"] for value in listed["items"]]
    assert client.get(f"{API}/assets/archive/{item['id']}/file").status_code == 200


def test_upload_deduplicates_within_archive(client: TestClient, archive_root: Path) -> None:
    content = png()
    first = upload(client, "重复.png", "character", content)
    second = upload(client, "重复.png", "character", content)

    assert second["id"] == first["id"], "同一个人重复登记同一文件应返回已有记录"


def test_same_bytes_in_different_categories_coexist(client: TestClient, archive_root: Path) -> None:
    """一张图同时当人物参考和产品参考是正常用法，不该被内容去重合并。"""
    content = png()
    character = upload(client, "双用.png", "character", content)
    product = upload(client, "双用.png", "product", content)

    assert character["id"] != product["id"]


def test_same_bytes_can_live_in_both_libraries(client: TestClient, archive_root: Path) -> None:
    """网页上传与归档登记是两条记录：两套物理副本、两套 URL。"""
    content = png()
    archived = upload(client, "同文件.png", "character", content)
    uploaded = client.post(
        f"{API}/assets/upload", content=content, params={"filename": "同文件.png"}
    )
    assert uploaded.status_code == 201, uploaded.text
    assert uploaded.json()["data"]["id"] != archived["id"]


def test_web_library_excludes_archive_rows(client: TestClient, archive_root: Path) -> None:
    archived = upload(client, "归档.png", "character", png())

    listed = client.get(f"{API}/assets", params={"pageSize": 100}).json()["data"]
    assert archived["id"] not in [value["id"] for value in listed["items"]]
    # 归档行没有对象存储里的文件，走 /assets/{id}/file 只会拼出必然 404 的地址
    assert client.get(f"{API}/assets/{archived['id']}").status_code == 404


# ---------------------------------------------------------------- 导入命令
async def test_import_is_idempotent(archive_root: Path, session) -> None:
    owner = f"import-{uuid.uuid4().hex[:8]}"
    media = seed_index(archive_root, owner=owner)
    try:
        first = await import_archive_index(session, archive_root)
        assert (first.created, first.updated, first.unchanged) == (1, 0, 0)

        second = await import_archive_index(session, archive_root)
        assert (second.created, second.updated) == (0, 0)
        assert second.unchanged == 1, "第二次执行不该再写库"

        rows = (await session.scalars(select(Asset).where(Asset.owner_id == owner))).all()
        assert len(rows) == 1
        assert rows[0].source == "archive"
        assert rows[0].storage_key == media["path"]
        assert rows[0].cover_key == media["previewPath"]

        mirror = archive_root / "catalog" / "assets" / f"{media['id']}.json"
        assert mirror.is_file(), "导入不删 JSON：它是替换模板的输入"
    finally:
        await _clear(owner)


async def test_import_preserves_created_at_and_category(archive_root: Path, session) -> None:
    owner = f"import-{uuid.uuid4().hex[:8]}"
    media = seed_index(archive_root, owner=owner)
    try:
        await import_archive_index(session, archive_root)
        row = await session.get(Asset, media["id"])
        assert row is not None
        assert row.category == "character"
        assert row.created_at.replace(tzinfo=UTC).isoformat().startswith("2026-01-02T03:04:05")
    finally:
        await _clear(owner)


async def test_import_skips_missing_files(archive_root: Path, session) -> None:
    owner = f"import-{uuid.uuid4().hex[:8]}"
    media = seed_index(archive_root, owner=owner)
    (archive_root / media["path"]).unlink()
    try:
        report = await import_archive_index(session, archive_root)
        assert report.missing_file == 1
        assert report.created == 0
        assert await session.get(Asset, media["id"]) is None
    finally:
        await _clear(owner)


async def test_import_dry_run_changes_nothing(archive_root: Path, session) -> None:
    owner = f"import-{uuid.uuid4().hex[:8]}"
    media = seed_index(archive_root, owner=owner)
    try:
        report = await import_archive_index(session, archive_root, dry_run=True)
        assert report.created == 1
        assert await session.get(Asset, media["id"]) is None
    finally:
        await _clear(owner)


async def test_import_updates_only_changed_rows(archive_root: Path, session) -> None:
    owner = f"import-{uuid.uuid4().hex[:8]}"
    media = seed_index(archive_root, owner=owner)
    path = archive_root / "catalog" / "assets" / f"{media['id']}.json"
    try:
        await import_archive_index(session, archive_root)
        media["name"] = "改过名字.png"
        path.write_text(json.dumps(media, ensure_ascii=False), encoding="utf-8")

        report = await import_archive_index(session, archive_root)
        assert (report.created, report.updated, report.unchanged) == (0, 1, 0)
        row = await session.get(Asset, media["id"])
        assert row is not None and row.name == "改过名字.png"
    finally:
        await _clear(owner)


def test_import_command_reports_missing_root(tmp_path: Path) -> None:
    from cli import main

    with pytest.raises(SystemExit) as exc:
        main(["import-archive-index", "--root", str(tmp_path)])
    assert exc.value.code == 1


def test_import_command_prints_preview(archive_root: Path, capsys) -> None:
    """命令行的接线（参数 → 报告 → 输出）；dry-run 不写库，不用清理。"""
    from cli import main

    seed_index(archive_root, owner=f"cli-{uuid.uuid4().hex[:8]}")
    main(["import-archive-index", "--root", str(archive_root), "--dry-run"])

    out = capsys.readouterr().out
    assert "预演（未写入）" in out
    assert "扫描 1 个索引" in out
    assert "新建 1" in out
