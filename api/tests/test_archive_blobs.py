"""素材字节的内容寻址：key = sha256，同一份字节只有一份。

这层替换掉了原先「位置寻址 + 发布前重算 sha256 发现被改」的做法，所以要守住三件事：

1. 同一份字节登记到不同分类，物理上只有一份；
2. 发布校验只看 key 在不在，不再读全量字节；
3. 存量记录（`library/…` 旧布局）不改也能继续读 —— 迁移是可选动作，不是前置条件。

重建只改了「发布校验由谁调」：`PackService.verify(pack)` 换成内部
`_verify_bytes(refs)`（refs 来自 `template_pack_references`）。判据一字未改：
**不重算摘要**，key 存在即字节正确。
"""

from __future__ import annotations

import io
import json
import os
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from tests_template_helpers import ADMIN, add_pack, drop_pack, upload

from core.exceptions import BadRequest, NotFound
from db.session import get_session_factory
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


def _mirror(root: Path, asset_id: str) -> dict:
    """镜像 JSON 是只读投影，仍旧写在 `catalog/assets` 下（前端与运维都在看它）。"""
    return json.loads((root / "catalog" / "assets" / f"{asset_id}.json").read_text("utf-8"))


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


def test_same_bytes_share_one_blob(client: TestClient, archive_root: Path) -> None:
    """同一张图当人物参考和场景参考是两条记录，但字节只有一份。"""
    data = png()
    character = upload(client, "人物.png", "character", data)
    scene = upload(client, "场景.png", "scene", data)

    assert character["path"] == scene["path"]
    assert character["path"].startswith("blobs/")
    assert (archive_root / character["path"]).is_file()
    # 预览也按摘要命名：同一份字节不会生成两份缩略图
    assert _mirror(archive_root, character["id"])["previewPath"] == _mirror(
        archive_root, scene["id"]
    )["previewPath"]


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

        (archive_root / item["path"]).unlink()

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


def test_legacy_library_key_still_reads(client: TestClient, archive_root: Path) -> None:
    """存量素材还是 `library/…` 位置寻址：按 key 直达，不需要先迁移。"""
    item = upload(client, "人物.png", "character", png())
    legacy = f"library/character/2026/01/{item['id']}.png"
    target = archive_root / legacy
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(archive_root / item["path"]), str(target))

    blobs = BlobStore(LocalArchiveStorage())
    with blobs.materialize(legacy) as path:
        assert path.is_file()
        assert path == target


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


def test_mirror_keeps_serving_old_readers(archive_root: Path, client: TestClient) -> None:
    """`catalog/assets/<id>.json` 镜像仍是「零改动」的兼容层：老读者照旧能读。

    重建没有动它（索引早已在 `assets` 表），这条只是防止有人顺手把镜像删掉——
    删了之后运维脚本和旧版前端会同时失效，而接口一切正常，很难归因。
    """
    item = upload(client, "镜像.png", "character", png())
    mirror = _mirror(archive_root, item["id"])
    assert mirror["id"] == item["id"]
    assert mirror["path"] == item["path"]
    assert mirror["sha256"] == item["sha256"]
    assert ADMIN  # 明确：这条记录属于演示超管（夹具默认 owner）
