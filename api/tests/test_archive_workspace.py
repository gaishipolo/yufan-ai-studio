"""枚举与运行工作区：远端驱动能跑的前提（T17）。

这组测试守两件事：
1. **枚举走 `list_keys`**，不在 `root()` 上 glob —— 后者等于要求「有本地根目录」，
   切 S3/MinIO 后封装列表与任务发现整块不可用；
2. **运行工作区可以和归档存储分开** —— 执行器要真实目录，归档要可远端，
   两者不是同一个盘。
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from core.config import settings
from infra.archive import (
    get_archive_storage,
    index_key,
    read_pointer,
    reset_archive_storage,
    run_dir,
    runs_root,
    save_pointer,
)
from infra.archive.local import get_archive_settings
from modules.template.replacement_repository import ReplacementManifest, ReplacementRepository


@pytest.fixture
def archive_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "archive"
    root.mkdir()
    monkeypatch.setattr(get_archive_settings(), "root", str(root))
    monkeypatch.setattr(settings, "archive_driver", "local")
    monkeypatch.setattr(settings, "replacement_runs_root", "")
    reset_archive_storage()
    yield root
    reset_archive_storage()


def test_list_keys_filters_and_sorts(archive_root: Path) -> None:
    storage = get_archive_storage()
    storage.write("catalog/packs/b.json", {"id": "b"})
    storage.write("catalog/packs/a.json", {"id": "a"})
    storage.write("catalog/packs/note.txt", "忽略")

    assert storage.list_keys("catalog/packs", suffix=".json") == [
        "catalog/packs/a.json",
        "catalog/packs/b.json",
    ]
    # 前缀不存在 = 空表，不是异常：还没发布过任何一个包，和路径写错在调用方看来一样
    assert storage.list_keys("catalog/never") == []


def test_list_keys_is_recursive(archive_root: Path) -> None:
    storage = get_archive_storage()
    storage.write("catalog/pack_versions/x/1.json", {"version": 1})
    storage.write("catalog/pack_versions/x/2.json", {"version": 2})

    assert storage.list_keys("catalog/pack_versions", suffix=".json") == [
        "catalog/pack_versions/x/1.json",
        "catalog/pack_versions/x/2.json",
    ]


def test_runs_root_defaults_to_archive_runs(archive_root: Path) -> None:
    assert runs_root() == archive_root.resolve() / "runs"


def test_runs_root_follows_setting(tmp_path: Path, archive_root: Path) -> None:
    local = tmp_path / "local-runs"
    settings.replacement_runs_root = str(local)

    assert runs_root() == local.resolve()


def test_pointer_roundtrip_outside_archive_root(tmp_path: Path, archive_root: Path) -> None:
    """工作区换到归档盘外：指针仍写在归档里，目录解析到新根。"""
    settings.replacement_runs_root = str(tmp_path / "local-runs")
    storage = get_archive_storage()
    uid = uuid.uuid4().hex
    folder = runs_root() / "2026/11/07" / f"character-{uid}"
    (folder).mkdir(parents=True)
    (folder / "run.json").write_text("{}", encoding="utf-8")

    repo = ReplacementRepository(storage)
    repo.index(folder, uid)

    assert read_pointer(storage, uid) == f"2026/11/07/character-{uid}"
    assert repo.path(uid) == folder.resolve()
    assert (archive_root / index_key(uid)).is_file()


def test_legacy_pointer_path_still_resolves(archive_root: Path) -> None:
    """旧指针存的是「相对归档根」的 runs/...：两种格式都要认，省一次数据迁移。"""
    storage = get_archive_storage()
    uid = uuid.uuid4().hex
    folder = runs_root() / uid
    folder.mkdir(parents=True)
    (folder / "run.json").write_text("{}", encoding="utf-8")
    save_pointer(storage, uid, f"runs/{uid}")

    assert run_dir(storage, uid) == folder.resolve()


def test_dangling_pointer_is_not_listed(archive_root: Path) -> None:
    """指针在、工作区不在（换机器/没同步）：列表跳过，不把整页拖成 500。"""
    storage = get_archive_storage()
    uid = uuid.uuid4().hex
    folder = runs_root() / uid
    folder.mkdir(parents=True)
    (folder / "run.json").write_text("{}", encoding="utf-8")
    repo = ReplacementRepository(storage)
    repo.save(
        folder,
        ReplacementManifest(
            id=uid,
            kind="character",
            target="人物",
            owner_id="u_001",
            fingerprint="fixture",
            status="prepared",
        ),
    )
    repo.index(folder, uid)
    assert len(repo.all_paths()) == 1

    (folder / "run.json").unlink()
    assert repo.all_paths() == []
