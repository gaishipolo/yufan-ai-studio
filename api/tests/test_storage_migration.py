"""`migrate-storage` 的行为：按 key 原样搬、跳过中转目录、默认不删源。"""

from __future__ import annotations

from pathlib import Path

import pytest

from infra.storage.local import LocalStorage
from infra.storage.migration import migrate_local_objects


def _source(tmp_path: Path) -> Path:
    root = tmp_path / "src"
    (root / "assets" / "u_001" / "2026" / "11").mkdir(parents=True)
    (root / "assets" / "u_001" / "2026" / "11" / "a.png").write_bytes(b"png-bytes")
    (root / "images").mkdir()
    (root / "images" / "b.mp4").write_bytes(b"video-bytes")
    # 上传中转目录：写到一半的临时文件，不是对象
    (root / "incoming").mkdir()
    (root / "incoming" / "half.upload").write_bytes(b"partial")
    return root


async def test_migrates_by_relative_key_and_skips_incoming(tmp_path: Path) -> None:
    source = _source(tmp_path)
    target_root = tmp_path / "dst"

    report = await migrate_local_objects(LocalStorage(root=str(target_root)), source)

    assert (report.scanned, report.copied, report.failed) == (2, 2, 0)
    assert (target_root / "assets/u_001/2026/11/a.png").read_bytes() == b"png-bytes"
    assert (target_root / "images/b.mp4").read_bytes() == b"video-bytes"
    # 中转目录里的临时文件不该进桶
    assert not (target_root / "incoming").exists()


async def test_dry_run_writes_nothing(tmp_path: Path) -> None:
    source = _source(tmp_path)
    target_root = tmp_path / "dst"

    report = await migrate_local_objects(LocalStorage(root=str(target_root)), source, dry_run=True)

    assert (report.scanned, report.copied, report.dry_run) == (2, 2, True)
    assert not any(target_root.rglob("*"))


async def test_source_kept_unless_delete_flag(tmp_path: Path) -> None:
    source = _source(tmp_path)
    first = await migrate_local_objects(LocalStorage(root=str(tmp_path / "dst1")), source)
    assert first.deleted == 0
    assert (source / "images" / "b.mp4").is_file()

    # 重跑不产生第二份（同 key 覆盖），也不会因为「源还在」而多搬
    second = await migrate_local_objects(LocalStorage(root=str(tmp_path / "dst2")), source)
    assert second.copied == 2

    third = await migrate_local_objects(
        LocalStorage(root=str(tmp_path / "dst3")), source, delete_source=True
    )
    assert third.deleted == 2
    assert not (source / "images" / "b.mp4").exists()
    # 中转目录不参与删除
    assert (source / "incoming" / "half.upload").is_file()


async def test_missing_source_reports_instead_of_raising(tmp_path: Path) -> None:
    report = await migrate_local_objects(
        LocalStorage(root=str(tmp_path / "dst")), tmp_path / "nope"
    )
    assert report.copied == 0
    assert report.problems


class _FailingStorage(LocalStorage):
    """单个对象写失败时不该中断整批。"""

    async def put_file(self, key: str, path: Path, content_type: str = "") -> str:
        if key.endswith(".mp4"):
            raise OSError("模拟写失败")
        return await super().put_file(key, path, content_type)


async def test_one_failure_does_not_stop_the_batch(tmp_path: Path) -> None:
    report = await migrate_local_objects(
        _FailingStorage(root=str(tmp_path / "dst")), _source(tmp_path)
    )
    assert (report.copied, report.failed) == (1, 1)
    assert any("b.mp4" in item for item in report.problems)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__]))
