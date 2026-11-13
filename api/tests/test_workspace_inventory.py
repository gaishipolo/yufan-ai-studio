"""`modules/template/workspace_inventory.py`：工作区盘点的判据。

这个模块是「要不要删工作区」的唯一依据，它错的方向很难看：

- 把**有档案的**目录算成孤儿 → 人照着清单删掉真实档案的验收依据（没有第二份）；
- 把**读不到档案表**当成「一个都没有」→ 每个目录都变孤儿，等于递给人一份
  「全都删得」的清单。

所以这里逐条钉住：归属怎么认、认不出时怎么降级、体积与最老最新怎么算。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from modules.template.workspace_inventory import human_size, inspect

RUN_A = "a" * 32
RUN_B = "b" * 32


def _workspace(root: Path, name: str, *, run_id: str = "", status: str = "succeeded") -> Path:
    """造一个工作区目录：`run.json` + `inputs/` 里一份小文件。"""
    path = root / name
    (path / "inputs").mkdir(parents=True)
    payload = {"status": status}
    if run_id:
        payload["id"] = run_id
    (path / "run.json").write_text(json.dumps(payload), encoding="utf-8")
    (path / "inputs" / "a.png").write_bytes(b"x" * 100)
    return path


def test_counts_size_and_marks_the_deleted_archive_as_orphan(tmp_path: Path) -> None:
    """有档案的算「在档案里」，档案行没了的才是孤儿——这是清理清单唯一的来源。"""
    _workspace(tmp_path, RUN_A, run_id=RUN_A)
    _workspace(tmp_path, RUN_B, run_id=RUN_B)

    report = inspect(tmp_path, {RUN_A})

    assert len(report.workspaces) == 2
    # 两个 workdir 各有 run.json（约 30 字节）+ a.png（100 字节），只断言≥文件本体
    assert report.total_size >= 200
    assert [item.name for item in report.orphans] == [RUN_B]
    text = "\n".join(report.lines())
    assert "档案已删：1 个" in text
    assert "在档案里：1 个" in text


def test_broken_run_json_falls_back_to_the_directory_name(tmp_path: Path) -> None:
    """`run.json` 坏了但目录名就是 run_id → 仍认得出归属。

    这条是「少删」的保险：认不出来就会被算成孤儿，而它可能是一份真实档案，
    只是文件正在被改写（共享盘上这就是常态）。
    """
    path = tmp_path / RUN_A
    (path / "inputs").mkdir(parents=True)
    (path / "run.json").write_text("{ 半个 JSON", encoding="utf-8")

    report = inspect(tmp_path, {RUN_A})

    assert [item.run_id for item in report.workspaces] == [RUN_A]
    assert report.orphans == ()


def test_legacy_dir_without_id_is_an_orphan(tmp_path: Path) -> None:
    """老布局（`product-<id>`）没有可认领的 id：与「档案被硬删」在清理上等价。"""
    _workspace(tmp_path, "product-legacy", run_id="")

    report = inspect(tmp_path, {RUN_A, RUN_B})

    assert [item.run_id for item in report.workspaces] == [""]
    assert len(report.orphans) == 1


def test_unreadable_archive_table_never_reports_orphans(tmp_path: Path) -> None:
    """读不到档案表 → `known_ids=None`，**不得**退化成空集合。

    空集合会让每个目录都成孤儿（「所有东西都可以删」）。这里宁可什么都不判断。
    """
    _workspace(tmp_path, RUN_A, run_id=RUN_A)

    report = inspect(tmp_path, None)

    assert report.orphans == ()
    text = "\n".join(report.lines())
    assert "归属未判断" in text
    assert "档案已删" not in text


def test_missing_root_is_not_an_error(tmp_path: Path) -> None:
    """路径不对（盘没挂上 / 首次部署）只说着「没找到」，不抛栈。"""
    report = inspect(tmp_path / "no-such-dir", {RUN_A})

    assert report.workspaces == ()
    assert "没有找到任何工作区目录" in report.lines()[1]


@pytest.mark.parametrize(
    ("size", "expected"),
    [(0, "0 B"), (999, "999 B"), (2048, "2.0 KB"), (5 * 1024**3, "5.0 GB")],
)
def test_size_is_rendered_for_humans(size: int, expected: str) -> None:
    assert human_size(size) == expected
