"""运行工作区的部署形态：指针在归档、过程文件在本地。

远端归档下「列表能看到档案、点开说档案不存在」不是一个 bug，而是「换了台机器」。
`app runs-status` 把这个差异变成可查的数字，下面的用例守住这个口径。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from infra.archive import get_archive_storage
from infra.archive.local import get_archive_settings
from infra.archive.runs import (
    orphan_pointers,
    pointer_ids,
    save_pointer,
    workspace_note,
)


@pytest.fixture
def archive_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(get_archive_settings(), "root", str(tmp_path))
    return tmp_path


def test_pointer_without_workspace_is_visible_not_broken(archive_root: Path) -> None:
    storage = get_archive_storage()
    run_id = "a" * 32
    save_pointer(storage, run_id, "2026/11/07/abc")

    assert pointer_ids() == [run_id], "指针在归档里：列表照样能看到这条档案"
    assert orphan_pointers() == [run_id], "工作区不在本机：点开就是「档案不存在」"


def test_workspace_present_clears_the_warning(archive_root: Path) -> None:
    storage = get_archive_storage()
    run_id = "b" * 32
    save_pointer(storage, run_id, "2026/11/07/def")

    folder = archive_root / "runs" / "2026/11/07/def"
    folder.mkdir(parents=True)
    (folder / "run.json").write_text("{}", encoding="utf-8")

    assert orphan_pointers() == []


def test_workspace_note_names_the_shared_directory_when_configured(
    archive_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """配了 REPLACEMENT_RUNS_ROOT 就是「所有实例挂载同一份」，不再有本机歧义。"""
    from core.config import settings

    shared = archive_root / "shared-runs"
    monkeypatch.setattr(settings, "replacement_runs_root", str(shared))

    note = workspace_note()

    assert str(shared) in note
    assert "所有 API 实例必须挂载同一份" in note
