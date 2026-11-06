"""归档存储必须走驱动注册（A2）。

以前 `get_archive_storage()` 恒返回 `LocalArchiveStorage()`，而 provider 里还有 5 处
直接 `LocalArchiveStorage()`：`.env.example` 里「换实现只改这里」对归档盘不成立，
要上 S3 得逐个改调用点。这两个测试守住新增的那条路。
"""

from __future__ import annotations

import pytest

from core.config import settings
from infra.archive import get_archive_storage, reset_archive_storage
from infra.archive.local import Driver


@pytest.fixture(autouse=True)
def _reset() -> None:
    reset_archive_storage()
    yield
    reset_archive_storage()


def test_default_driver_is_local() -> None:
    assert isinstance(get_archive_storage(), Driver)


def test_unknown_driver_fails_loudly(monkeypatch: pytest.MonkeyPatch) -> None:
    """写错驱动名必须启动期炸，而不是悄悄落回本地目录。"""
    monkeypatch.setattr(settings, "archive_driver", "s3_not_implemented")
    reset_archive_storage()
    with pytest.raises(NotImplementedError, match="infra/archive/s3_not_implemented.py"):
        get_archive_storage()


def test_driver_is_resolved_by_name(monkeypatch: pytest.MonkeyPatch) -> None:
    """换个名字就换实现 —— 这是 A2 的全部意义：加文件 + 改配置，不改调用方。"""
    import sys
    import types
    from pathlib import Path

    class InMemory:
        def root(self) -> Path:
            return Path("/nowhere")

    module = types.ModuleType("infra.archive.inmemory")
    module.Driver = InMemory  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "infra.archive.inmemory", module)
    monkeypatch.setattr(settings, "archive_driver", "inmemory")
    reset_archive_storage()

    assert isinstance(get_archive_storage(), InMemory)
