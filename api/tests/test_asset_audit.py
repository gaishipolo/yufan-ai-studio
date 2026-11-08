"""素材状态变更留痕：日志给排障，审计给对账。

复用 `core.audit` 的注入口（本来就有写入端 `db/audit_sink.py`），不新建表。
重点验证动作按语义分开、且一次「状态没变的重试」不会记第二笔。
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient
from test_asset_api import png_bytes  # noqa: F401
from tests_archive_helpers import archive

from core.audit import AuditEntry, set_audit_sink
from db.audit_sink import write
from modules.asset.archive_schema import ArchiveChange
from modules.asset.archive_service import _change_action


@pytest.fixture
def entries() -> list[AuditEntry]:
    collected: list[AuditEntry] = []

    async def collect(entry: AuditEntry) -> None:
        collected.append(entry)

    set_audit_sink(collect)
    try:
        yield collected
    finally:
        set_audit_sink(write)


def test_change_action_names_are_semantic() -> None:
    cases = [
        ({"shared": True}, "asset.archive.share"),
        ({"shared": False}, "asset.archive.unshare"),
        ({"deleted": True}, "asset.archive.trash"),
        ({"deleted": False}, "asset.archive.restore"),
        ({"name": "新名字"}, "asset.archive.rename"),
    ]
    for changes, expected in cases:
        assert _change_action(ArchiveChange(expected_version=0, **changes)) == expected


def test_state_changes_are_recorded(client: TestClient, api: str, entries: list[Any]) -> None:
    asset = archive.upload(client, "audited.png", "product", png_bytes((20, 20), "red"))
    base = f"{api}/assets/{asset['id']}"

    renamed = client.put(
        f"{base}/name", json={"expectedVersion": asset["stateVersion"], "name": "改名后"}
    )
    assert renamed.status_code == 200

    version = renamed.json()["data"]["stateVersion"]
    trashed = client.put(f"{base}/deleted", json={"expectedVersion": version, "deleted": True})
    assert trashed.status_code == 200

    version = trashed.json()["data"]["stateVersion"]
    # 相同状态的重试：服务直接返回事实，不该再记一笔
    retry = client.put(f"{base}/deleted", json={"expectedVersion": version - 1, "deleted": True})
    assert retry.status_code == 200

    version = trashed.json()["data"]["stateVersion"]
    restored = client.put(f"{base}/deleted", json={"expectedVersion": version, "deleted": False})
    assert restored.status_code == 200

    version = restored.json()["data"]["stateVersion"]
    assert client.put(
        f"{base}/deleted", json={"expectedVersion": version, "deleted": True}
    ).status_code == 200
    assert client.delete(base).status_code == 204

    actions = [entry.action for entry in entries]
    assert actions == [
        "asset.archive.rename",
        "asset.archive.trash",
        "asset.archive.restore",
        "asset.archive.trash",
        "asset.archive.purge",
    ], actions
    assert all(entry.target_id == asset["id"] for entry in entries)
    assert entries[-1].detail["bytesRemoved"], "彻底删除要记下释放了哪些字节"
