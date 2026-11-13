"""`scripts/clean_var.py`：清 `api/var` 残留时的三条底线。

这个脚本按「不在保留清单里的全算残留」来干活，所以它的风险全在**边界**上：
往上一级跑（`api/`、仓库根）会删掉源码；把 `app.db` 或 `storage/` 当残留会删掉
开发库与真实素材字节。默认 dry-run、只认目录名 `var` 这两条不是保险丝，是这条
脚本唯一能存在的前提。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "clean_var.py"


def _load() -> object:
    spec = importlib.util.spec_from_file_location("_clean_var", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["_clean_var"] = module
    spec.loader.exec_module(module)
    return module


clean_var = _load()


def _make_var(tmp_path: Path) -> Path:
    """造一个像 `api/var` 的目录：保留项 + 残留项各几份。"""
    root = tmp_path / "var"
    (root / "storage").mkdir(parents=True)
    (root / "storage" / "blob.bin").write_bytes(b"r" * 10)
    (root / "runs" / "2026").mkdir(parents=True)
    (root / "app.db").write_bytes(b"d" * 10)
    (root / "app.db.bak-1639").write_bytes(b"d" * 10)
    (root / "pytest-tmp-a").mkdir()
    (root / "pytest-tmp-a" / "x.bin").write_bytes(b"j" * 100)
    (root / "dbg-1").mkdir()
    (root / "ruff.log").write_text("noise", encoding="utf-8")
    return root


def test_dry_run_deletes_nothing(tmp_path: Path, capsys) -> None:
    root = _make_var(tmp_path)

    assert clean_var.main(["--root", str(root)]) == 0  # type: ignore[attr-defined]

    assert "dry-run" in capsys.readouterr().out
    for kept in ("pytest-tmp-a", "dbg-1", "ruff.log"):
        assert (root / kept).exists()


def test_yes_deletes_residue_and_keeps_the_dev_database(tmp_path: Path, capsys) -> None:
    """`app.db` / `app.db.bak-*` / `storage/` / `runs/` / `.gitkeep` 一件都不能少。

    `.gitkeep` 是 `var/` 里**唯一**纳入版本控制的文件（`git ls-files api/var` 只有它），
    删掉就等于在提交里多出一行删除——它是「不在保留清单里的都算残留」这条规则的
    第一个受害者。
    """
    root = _make_var(tmp_path)
    (root / ".gitkeep").write_text("", encoding="utf-8")

    assert clean_var.main(["--root", str(root), "--yes"]) == 0  # type: ignore[attr-defined]
    capsys.readouterr()

    for kept in ("app.db", "app.db.bak-1639", "storage", "runs", ".gitkeep"):
        assert (root / kept).exists(), f"{kept} 被当成残留删掉了"
    for gone in ("pytest-tmp-a", "dbg-1", "ruff.log"):
        assert not (root / gone).exists()


def test_refuses_a_directory_that_is_not_named_var(tmp_path: Path) -> None:
    """同样一份内容换个名字 → 拒绝。这个脚本不能指向 `api/` 或仓库根。"""
    elsewhere = tmp_path / "api"
    elsewhere.mkdir()
    (elsewhere / "pyproject.toml").write_text("", encoding="utf-8")

    with pytest.raises(SystemExit) as caught:
        clean_var.main(["--root", str(elsewhere), "--yes"])  # type: ignore[attr-defined]

    assert "不叫 var" in str(caught.value)
    assert (elsewhere / "pyproject.toml").exists()


def test_symlink_is_removed_but_its_target_is_not(tmp_path: Path) -> None:
    """`var/` 下常有指到别处的链接；删链接可以，顺着删目标不行。"""
    root = _make_var(tmp_path)
    target = tmp_path / "real-data"
    target.mkdir()
    (target / "keep.bin").write_bytes(b"k" * 10)
    link = root / "link-to-data"
    try:
        link.symlink_to(target, target_is_directory=True)
    except (OSError, NotImplementedError):  # pragma: no cover - Windows 无权限时跳过
        return

    assert clean_var.main(["--root", str(root), "--yes"]) == 0  # type: ignore[attr-defined]

    assert not link.exists()
    assert (target / "keep.bin").exists()
