"""孤儿字节回收：只删「没人引用 + 过了保护期」的 blob（T18）。

登记失败时有意不删字节（同一个 key 可能正被并发登记的另一条记录共享），
所以回收必须是独立动作，且必须能证明「删的是真的没人要的东西」。
"""

from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from tests_archive_helpers import archive

from infra.archive.local import get_archive_settings
from modules.asset.cli import prune_archive_blobs


@pytest.fixture
def archive_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(get_archive_settings(), "root", str(tmp_path))
    return tmp_path


def _blob(root: Path, name: str, *, age_hours: float) -> Path:
    path = root / "blobs" / "ab" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"orphan")
    stamp = time.time() - age_hours * 3600
    os.utime(path, (stamp, stamp))
    return path


def _upload_png(client: TestClient, root: Path) -> dict:
    image = root / "reference.png"
    Image.new("RGB", (16, 16), tuple(os.urandom(3))).save(image, format="PNG")
    return archive.upload(client, "reference.png", "character", image.read_bytes())


def test_prune_keeps_referenced_and_recent_blobs(client: TestClient, archive_root: Path) -> None:
    media = _upload_png(client, archive_root)
    referenced = archive_root / media["path"]
    assert referenced.is_file()
    old_orphan = _blob(archive_root, "old-orphan.png", age_hours=48)
    fresh_orphan = _blob(archive_root, "fresh-orphan.png", age_hours=0)

    orphans, deleted, protected = asyncio.run(
        prune_archive_blobs(execute=False, min_age_hours=24)
    )
    assert (orphans, deleted, protected) == (1, 0, 1)
    # 只报告不动手：dry-run 之后三份字节都还在
    assert old_orphan.is_file() and fresh_orphan.is_file() and referenced.is_file()

    orphans, deleted, protected = asyncio.run(prune_archive_blobs(execute=True, min_age_hours=24))
    assert (orphans, deleted, protected) == (1, 1, 1)
    assert not old_orphan.exists()  # 过期孤儿被回收
    assert fresh_orphan.is_file()  # 保护期内：可能正处在「字节写完、行没提交」的窗口
    assert referenced.is_file()  # 库里还引用着，永远不动
