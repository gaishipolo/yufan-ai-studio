"""周期 GC 的注册与门控：默认关闭，且没有引用检查器时绝不删回收站。"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from core.config import settings
from core.ports.assets import (
    register_asset_reference_checker,
    reset_asset_reference_checker,
)
from core.scheduler import periodic_jobs
from modules.asset import maintenance
from modules.asset.maintenance import archive_gc, run_archive_gc
from modules.task.asset_references import TaskAssetReferences
from modules.template.asset_references import PackAssetReferences


def _restore_defaults() -> None:
    reset_asset_reference_checker()
    register_asset_reference_checker(TaskAssetReferences())
    register_asset_reference_checker(PackAssetReferences())


def test_gc_job_is_registered_with_module_prefix() -> None:
    names = [job.name for job in periodic_jobs()]
    assert "asset.gc" in names
    job = next(job for job in periodic_jobs() if job.name == "asset.gc")
    assert job.every == max(1.0, settings.archive_gc_interval_sec)


def test_gc_is_off_unless_explicitly_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    called: list[Any] = []

    async def fake_gc(**kwargs: Any) -> maintenance.GcReport:
        called.append(kwargs)
        return maintenance.GcReport()

    monkeypatch.setattr(settings, "archive_gc_enabled", False)
    monkeypatch.setattr(maintenance, "run_archive_gc", fake_gc)
    asyncio.run(archive_gc())
    assert called == [], "默认配置下周期任务不能删任何东西"

    monkeypatch.setattr(settings, "archive_gc_enabled", True)
    asyncio.run(archive_gc())
    assert len(called) == 1 and called[0]["execute"] is True
    assert called[0]["include_trash"] is True


def test_gc_refuses_to_purge_without_reference_checker() -> None:
    """CLI / 周期任务忘了加载模块时，宁可失败也不能静默全删。"""
    reset_asset_reference_checker()
    try:
        with pytest.raises(RuntimeError, match="引用检查器未注册"):
            asyncio.run(run_archive_gc(include_trash=True, execute=False))
    finally:
        _restore_defaults()
