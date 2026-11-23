"""配置热生效：mtime 指纹缓存 vs 原 lru_cache。

钉住 D3 拍板：改 `.env` / 环境变量后**下次调用即生效**，无需重启。
"""

from __future__ import annotations

import ast
import os
from pathlib import Path

import modules.model_driver.kit.settings as kit_settings
from modules.model_driver.kit.settings import clear_cache, vendor_settings


class FakeSettings:
    def __init__(self) -> None:
        self.key = os.environ.get("TESTVENDOR_KEY", "")


def test_env_change_invalidates_cache(monkeypatch) -> None:  # noqa: ANN001
    clear_cache()
    getter = vendor_settings(FakeSettings, env_prefix="TESTVENDOR_")

    monkeypatch.setenv("TESTVENDOR_KEY", "first")
    assert getter().key == "first"

    # 文件没变，但环境变量变了 → 快照指纹变 → 重建
    monkeypatch.setenv("TESTVENDOR_KEY", "second")
    assert getter().key == "second"


def test_same_fingerprint_reuses_instance() -> None:
    clear_cache()
    getter = vendor_settings(FakeSettings, env_prefix="TESTVENDOR_")
    assert getter() is getter()  # 命中缓存，不重建


def test_env_file_mtime_change_invalidates(monkeypatch) -> None:  # noqa: ANN001
    clear_cache()
    getter = vendor_settings(FakeSettings, env_prefix="TESTVENDOR_")
    first = getter()

    monkeypatch.setattr(kit_settings, "_file_fingerprint", lambda: (123456789, 99))
    second = getter()
    assert second is not first  # 指纹变了 → 重建


def test_bad_config_not_stuck(monkeypatch) -> None:  # noqa: ANN001
    """坏配置（空 key）改好后立刻可用，不被粘住。"""
    clear_cache()
    getter = vendor_settings(FakeSettings, env_prefix="TESTVENDOR_")

    monkeypatch.delenv("TESTVENDOR_KEY", raising=False)
    assert getter().key == ""

    monkeypatch.setenv("TESTVENDOR_KEY", "fixed")
    assert getter().key == "fixed"


def test_cache_does_not_grow_without_bound(monkeypatch) -> None:  # noqa: ANN001
    """缓存键含 env 快照 → 每次 env 变化都新增一条键，必须有上限。

    密钥轮转 / 容器注入频繁变动时，若无上限缓存会一直涨。
    """
    clear_cache()
    getter = vendor_settings(FakeSettings, env_prefix="TESTVENDOR_")

    for index in range(kit_settings._MAX_CACHE_ENTRIES + 10):
        monkeypatch.setenv("TESTVENDOR_KEY", f"value-{index}")
        assert getter().key == f"value-{index}"

    assert len(kit_settings._cache) <= kit_settings._MAX_CACHE_ENTRIES
    clear_cache()


def test_no_driver_builds_its_own_cached_settings_getter() -> None:
    """**防漂移守卫**：驱动不得自建 `lru_cache` 配置 getter。

    `@lru_cache` 把 `.env` 的读取**永久**缓存住 —— 改配置必须重启进程。
    这正是 2026-11-23 空 key 事故的直接原因之一（改 `.env` 不生效，
    进程还在用旧值），也是 D3 要消灭的东西。

    统一走 `kit/settings.py::vendor_settings`（mtime 指纹）：改 `.env`
    下次请求即生效。

    `kit/settings.py` 自己不算违规（它在文档字符串里提到 `lru_cache`，
    且不导入它）。
    """
    offenders: list[str] = []
    root = Path(__file__).resolve().parent.parent / "src" / "modules" / "model_driver"

    for path in root.rglob("*.py"):
        if path.name == "settings.py" and path.parent.name == "kit":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for decorator in node.decorator_list:
                target = decorator.func if isinstance(decorator, ast.Call) else decorator
                if isinstance(target, ast.Name) and target.id == "lru_cache":
                    offenders.append(f"{path.relative_to(root)}:{node.lineno}: {node.name}")

    assert not offenders, (
        "这些地方自建了 lru_cache 配置 getter，必须改用 kit/settings.py 的 "
        "vendor_settings（改 .env 下次请求即生效）：" + ", ".join(offenders)
    )
