"""模板目录的存储行为：写前快照、滚动份数、错误文案不泄露部署路径。

这些行为在真正出事时才用得上（改坏提示词回捞、共享盘部署），平时跑不到，
所以更需要机器守住——尤其「备份是不是上一版」和「保留多少份」这两个语义，
写错一个都不会在功能上表现出来。
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from core.exceptions import BadRequest
from modules.template import video_template_store as store

API_ROOT = Path(__file__).resolve().parents[1]


def _catalog(tmp_path: Path) -> Path:
    return tmp_path / "catalog.json"


def _items(path: Path) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))["items"]


def test_write_keeps_the_previous_version_as_backup(monkeypatch, tmp_path: Path) -> None:
    target = _catalog(tmp_path)
    monkeypatch.setattr(store, "CATALOG_PATH", target)

    store._write({"version": 1, "items": [{"id": "first"}]})
    store._write({"version": 1, "items": [{"id": "second"}]})

    backups = sorted((tmp_path / "backup").glob("catalog-*.json"))
    assert len(backups) == 1
    # 快照存的是「上一次写进去的内容」——后悔药要能回到改之前，不是改之后。
    assert _items(backups[0])[0]["id"] == "first"
    assert _items(target)[0]["id"] == "second"


def test_backup_rolls_and_keeps_the_newest(monkeypatch, tmp_path: Path) -> None:
    target = _catalog(tmp_path)
    monkeypatch.setattr(store, "CATALOG_PATH", target)

    for index in range(25):
        store._write({"version": 1, "items": [{"id": f"t{index}"}]})

    backups = sorted((tmp_path / "backup").glob("catalog-*.json"))
    assert len(backups) == store._BACKUP_KEEP
    # 第 25 次写之前刚写进去的是 t23，它就是最新的一份快照。
    assert _items(backups[-1])[0]["id"] == "t23"
    # 最老的几份按数量裁掉，留下的时间戳仍然连续递增。
    stamps = [path.name for path in backups]
    assert stamps == sorted(stamps)


def test_corrupt_catalog_message_does_not_leak_the_deploy_path(
    monkeypatch, tmp_path: Path
) -> None:
    """面向用户的错误文案不回显共享盘路径：部署结构不该出现在接口响应里。"""
    target = _catalog(tmp_path)
    target.write_text(json.dumps({"items": "not-a-list"}), encoding="utf-8")
    monkeypatch.setattr(store, "CATALOG_PATH", target)

    with pytest.raises(BadRequest) as caught:
        store._read()

    assert str(tmp_path) not in caught.value.message
    assert "catalog.json" not in caught.value.message
    assert "目录文件损坏" in caught.value.message


def test_packager_state_dir_follows_settings(monkeypatch, tmp_path: Path) -> None:
    """封装进度目录由 `TEMPLATE_PACKAGER_STATE_DIR` 决定，留空才回落到 api/var。"""
    from core.config import settings
    from template_packager.__main__ import _state_dir

    monkeypatch.setattr(settings, "template_packager_state_dir", str(tmp_path / "shared"))
    assert _state_dir() == tmp_path / "shared"

    monkeypatch.setattr(settings, "template_packager_state_dir", "")
    fallback = _state_dir()
    assert fallback.name == "template-packager"
    assert fallback.parent.name == "var"


def _load_subtitle_script():
    spec = importlib.util.spec_from_file_location(
        "ensure_no_subtitle_prompt",
        API_ROOT / "scripts" / "ensure_no_subtitle_prompt.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_subtitle_script_refuses_to_report_compliance_when_catalog_is_missing(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    """目录读不到时必须报错退出，不能给出「全部提示词都带全片无字幕」这个结论。

    `_read()` 对「文件不存在」和「文件里没有条目」都返回空 items —— 如果脚本直接
    照这个结果走，一个路径配错或共享盘没挂上的盘点会打印最令人放心的结论并返回 0。
    """
    script = _load_subtitle_script()
    monkeypatch.setattr(store, "CATALOG_PATH", tmp_path / "absent.json")

    assert script.main([]) == 2
    captured = capsys.readouterr()
    assert "全部提示词都带" not in captured.out
    assert "目录文件不存在" in captured.err
