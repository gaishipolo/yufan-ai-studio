"""DB 单一事实来源：镜像漂移必须能自动收敛，而不是靠人工核对。

覆盖 `app rebuild-mirrors` 与 `@periodic("asset.mirror")` 共用的那份实现：
补缺、更新落后版本、清孤儿、幂等、dry-run，以及投影失败不回滚已提交事务。
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi.testclient import TestClient
from test_asset_api import png_bytes  # noqa: F401
from tests_archive_helpers import archive

from infra.archive import get_archive_storage
from modules.asset.mirror import (
    MirrorProjector,
    MirrorRow,
    project_after_commit,
    run_mirror_reconcile,
)


def _upload(client: TestClient, name: str) -> dict[str, Any]:
    return archive.upload(client, name, "product", png_bytes((20, 20), "blue"))


def test_rebuild_restores_missing_mirror_from_db(client: TestClient, api: str) -> None:
    """JSON 删掉后按 DB 重建：这是 doctor 以前给错建议（import-archive-index）的场景。"""
    asset = _upload(client, "mirror-missing.png")
    storage = get_archive_storage()
    key = MirrorProjector.key(asset["id"])
    assert storage.exists(key)
    storage.remove(key)

    report = asyncio.run(run_mirror_reconcile())
    assert asset["id"] in report.written
    assert storage.exists(key)
    value = storage.read(key)
    assert isinstance(value, dict)
    assert value["id"] == asset["id"]
    assert value["stateVersion"] == asset["stateVersion"]
    assert value["path"] == asset["path"]


def test_rebuild_updates_stale_version(client: TestClient, api: str) -> None:
    """镜像内容旧了但文件还在：靠 stateVersion 发现并重写，不是靠 mtime。"""
    asset = _upload(client, "mirror-stale.png")
    renamed = client.put(
        f"{api}/assets/{asset['id']}/name",
        json={"expectedVersion": asset["stateVersion"], "name": "改过名.png"},
    )
    assert renamed.status_code == 200, renamed.text
    new_name = renamed.json()["data"]["name"]

    storage = get_archive_storage()
    key = MirrorProjector.key(asset["id"])
    value = storage.read(key)
    assert isinstance(value, dict)
    value["stateVersion"] = 0
    value["name"] = "旧名字.png"
    storage.write(key, value)

    report = asyncio.run(run_mirror_reconcile())
    assert asset["id"] in report.written
    rebuilt = storage.read(key)
    assert isinstance(rebuilt, dict)
    assert rebuilt["name"] == new_name


def test_rebuild_removes_orphan_mirror(client: TestClient, api: str) -> None:
    storage = get_archive_storage()
    orphan = "catalog/assets/orphan-no-db-row.json"
    storage.write(orphan, {"id": "orphan-no-db-row", "stateVersion": 1})

    report = asyncio.run(run_mirror_reconcile())
    assert "orphan-no-db-row" in report.removed
    assert not storage.exists(orphan)


def test_reconcile_is_idempotent(client: TestClient, api: str) -> None:
    asset = _upload(client, "mirror-idempotent.png")
    first = asyncio.run(run_mirror_reconcile())
    second = asyncio.run(run_mirror_reconcile())
    assert asset["id"] in first.written or first.unchanged >= 1
    assert asset["id"] not in second.written
    assert second.unchanged >= 1
    assert second.healthy


def test_dry_run_reports_without_writing(client: TestClient, api: str) -> None:
    asset = _upload(client, "mirror-dryrun.png")
    storage = get_archive_storage()
    key = MirrorProjector.key(asset["id"])
    storage.remove(key)

    report = asyncio.run(run_mirror_reconcile(execute=False))
    assert asset["id"] in report.written
    assert not storage.exists(key), "dry-run 不该写盘"


def test_failed_projection_does_not_raise(monkeypatch: Any) -> None:
    """投影失败只记日志：DB 已提交，抛 500 会让用户重试出重复记录。"""
    from modules.asset import mirror as mirror_module

    class _Broken:
        def snapshot(self, row: Any) -> MirrorRow:
            return MirrorRow(asset_id="a" * 32, version=1, payload={"id": "a" * 32})

        def write(self, snapshot: MirrorRow) -> None:
            raise OSError("归档盘掉线")

    monkeypatch.setattr(mirror_module, "MirrorProjector", lambda *_a, **_k: _Broken())
    assert asyncio.run(project_after_commit(object())) is False
