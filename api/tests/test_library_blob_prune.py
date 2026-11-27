"""孤儿字节巡检：默认只报告，保护期内不删。"""

from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path

import pytest

from infra.storage.local import LocalStorage
from modules.library import maintenance


def _age(path: Path, hours: int) -> None:
    stamp = time.time() - hours * 3600
    os.utime(path, (stamp, stamp))


def test_prune_reports_then_deletes_only_old_orphans(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "storage"
    storage = LocalStorage(root=str(root))

    async def seed() -> None:
        await storage.put("library/blobs/aa/keep.png", b"keep")
        await storage.put("library/blobs/aa/orphan.png", b"orphan")
        await storage.put("library/blobs/aa/young.png", b"young")

    asyncio.run(seed())
    _age(root / "library/blobs/aa/keep.png", 48)
    _age(root / "library/blobs/aa/orphan.png", 48)

    async def referenced() -> set[str]:
        return {"library/blobs/aa/keep.png"}

    monkeypatch.setattr(maintenance, "get_storage", lambda: storage)
    monkeypatch.setattr(maintenance, "_referenced_object_keys", referenced)

    dry = asyncio.run(maintenance.run_blob_prune(prefix="library/blobs/"))
    assert dry.dry_run is True
    assert {key for key, _ in dry.orphans} == {"library/blobs/aa/orphan.png"}
    assert dry.young == 1
    assert asyncio.run(storage.exists("library/blobs/aa/orphan.png"))

    done = asyncio.run(maintenance.run_blob_prune(prefix="library/blobs/", execute=True))
    assert done.deleted == 1
    assert done.bytes_freed == len(b"orphan")
    assert not asyncio.run(storage.exists("library/blobs/aa/orphan.png"))
    # 被引用的与保护期内的都还在。
    assert asyncio.run(storage.exists("library/blobs/aa/keep.png"))
    assert asyncio.run(storage.exists("library/blobs/aa/young.png"))