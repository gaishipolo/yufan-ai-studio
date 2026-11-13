"""素材字节的内容寻址：key = sha256，同一份字节只有一份。

这层替换掉了原先「位置寻址 + 发布前重算 sha256 发现被改」的做法，所以要守住三件事：

1. 同一份字节再传一次不落第二条行、也不写第二份 blob（秒传）；
2. 发布校验只看 key 在不在，不再读全量字节；
3. 存量记录（`library/…` 旧布局）不改也能继续读 —— 迁移是可选动作，不是前置条件。

v1 时代这里还守着「两条记录共享一个 path」与 `catalog/assets/<id>.json` 镜像；
前者随 `assets` 表变成「秒传直接命中同一行」，后者由 `modules/asset` 的镜像投影
写出、随该模块一起删掉了。
"""

from __future__ import annotations

import asyncio
import io
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from tests_template_helpers import add_pack, drop_pack, login, upload

from core.exceptions import BadRequest, NotFound
from db.session import get_session_factory
from infra import get_storage
from infra.archive import BlobStore, blob_key, is_blob_key, preview_key
from infra.archive.local import LocalArchiveStorage, get_archive_settings
from modules.template.pack_service import PackService
from modules.template.repository import PackRepository


@pytest.fixture
def archive_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(get_archive_settings(), "root", str(tmp_path))
    return tmp_path


def png(color: str | None = None) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (32, 48), color or "#" + os.urandom(3).hex()).save(buffer, format="PNG")
    return buffer.getvalue()


def _storage_key(client: TestClient, asset_id: str) -> str:
    """字节在对象存储里的 key。

    v2 不把 `storage_key` 放在列表出参里（那是内部寻址细节），只在**详情**的
    `archive` 段给所有者与管理员看——所以这里要带登录头并且是单条读。
    """
    detail = client.get(
        f"/api/v1/library/assets/{asset_id}", headers=login(client)
    ).json()["data"]
    return detail["archive"]["storageKey"]


def test_blob_key_is_content_addressed() -> None:
    digest = "a" * 64
    # 前两位分桶，扩展名统一小写
    assert blob_key(digest, ".PNG") == f"blobs/aa/{digest}.png"
    assert is_blob_key(blob_key(digest))
    assert preview_key(digest) == f"previews/aa/{digest}.hd-v2.jpg"


def test_blob_key_rejects_non_digest() -> None:
    """摘要不是任意字符串：它会被拼进路径，必须先过白名单。"""
    with pytest.raises(BadRequest):
        blob_key("../etc/passwd")


def test_same_bytes_are_deduplicated_by_digest(client: TestClient) -> None:
    """同一份字节再传一次：直接命中同一行，也不再写第二份 blob。

    v1 的判据是「两条记录共享一个 path」（分类不同算两条记录）；v2 的判据更强：
    同一个人 + 同摘要 + 同类型直接秒传回已有那条，连记录都不会多一条。
    """
    data = png()
    first = upload(client, "人物.png", "character", data)
    again = upload(client, "人物-副本.png", "character", data)

    assert again["id"] == first["id"]
    key = _storage_key(client, first["id"])
    assert key.startswith("library/blobs/")
    # 资产库的字节在 STORAGE_LOCAL_ROOT 下，与归档盘的 ARCHIVE_ROOT 是两个根
    assert asyncio.run(get_storage().exists(key))


async def test_publish_checks_blob_presence_not_content(
    client: TestClient, archive_root: Path
) -> None:
    """发布只问「字节在不在」：key 就是摘要，在即正确。

    删掉字节才报缺失——这条正是「不重算 sha256」的回归：如果实现又回去全量读一遍，
    这里依然会红，因为报错文案会变成「字节被篡改」。
    """
    item = upload(client, "人物.png", "character", png())
    pack = await add_pack(name="测试封装", reference_ids=[item["id"]], status="draft")

    try:
        async with get_session_factory()() as session:
            refs = await PackRepository(session).references(pack.id)
            await PackService(session)._verify_bytes(refs)

        await get_storage().delete(_storage_key(client, item["id"]))

        async with get_session_factory()() as session:
            refs = await PackRepository(session).references(pack.id)
            with pytest.raises(BadRequest, match="缺失"):
                await PackService(session)._verify_bytes(refs)
    finally:
        await drop_pack(pack.id)


def test_materialize_missing_key_is_not_found(archive_root: Path) -> None:
    """缺字节是「素材不存在」，不该冒泡成 500。"""
    blobs = BlobStore(LocalArchiveStorage())
    with pytest.raises(NotFound), blobs.materialize(blob_key("f" * 64, ".png")):
        pass


def test_legacy_library_key_still_reads(archive_root: Path) -> None:
    """存量素材还是 `library/…` 位置寻址：按 key 直达，不需要先迁移。

    这里不经过上传接口：要验的是 `BlobStore.materialize` 认不认任意 key，
    与「这条记录怎么来的」无关。v1 的 `migrate-blobs` 命令已随 `modules/asset`
    删除，所以「存量还在原位」这个状态只能手工造。
    """
    legacy = "library/character/2026/01/asset.png"
    target = archive_root / legacy
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"legacy-bytes")

    blobs = BlobStore(LocalArchiveStorage())
    with blobs.materialize(legacy) as path:
        assert path.is_file()
        assert path.read_bytes() == b"legacy-bytes"


def test_link_prefers_hardlink(archive_root: Path) -> None:
    """同一文件系统内硬链接：run 目录不再为原片存第二份 300MB 副本。"""
    storage = LocalArchiveStorage()
    source = archive_root / "sample.bin"
    source.write_bytes(b"payload-under-test")

    storage.link(source, "runs/a/inputs/copy.bin", None)
    target = archive_root / "runs/a/inputs/copy.bin"
    assert target.read_bytes() == b"payload-under-test"
    assert target.stat().st_ino == source.stat().st_ino


def test_link_refuses_conflicting_content(archive_root: Path) -> None:
    """目标已有且摘要不符时拒绝覆盖：快照不能被第二次 prepare 改掉。"""
    storage = LocalArchiveStorage()
    source = archive_root / "sample.bin"
    source.write_bytes(b"payload-under-test")
    storage.link(source, "runs/a/inputs/copy.bin", None)

    with pytest.raises(Exception, match="禁止覆盖"):
        storage.link(source, "runs/a/inputs/copy.bin", "0" * 64)


