"""回收站仅修改管理标记，隔离存储验证归属、权限和持久化。"""

import asyncio
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from infra.archive import lease
from infra.archive.local import get_archive_settings
from modules.template.replacement_management import MANAGEMENT_LOCK
from modules.template.replacement_repository import ReplacementManifest, ReplacementRepository
from modules.template.replacement_service import ReplacementService


@pytest.fixture
def archived(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[str, Path]:
    monkeypatch.setattr(get_archive_settings(), "root", str(tmp_path))
    uid = uuid.uuid4().hex
    path = tmp_path / "runs" / uid
    repo = ReplacementRepository()
    repo.save(path, ReplacementManifest(
        id=uid, kind="character", target="人物", owner_id="u_001",
        fingerprint="fixture", status="prepared",
    ))
    repo.index(path, uid)
    (path / "inputs").mkdir()
    (path / "inputs/prompt.txt").write_text("prompt", encoding="utf-8")
    return uid, path


def test_recycle_restore_persist_and_keep_archive(
    client: TestClient, archived: tuple[str, Path],
) -> None:
    uid, path = archived
    url = f"/api/v1/templates/replacement-runs/{uid}"
    original = (path / "run.json").read_bytes()
    removed = client.delete(url)
    assert removed.status_code == 200, removed.text
    timestamp = removed.json()["data"]["deletedAt"]
    assert timestamp.endswith("Z")
    assert client.delete(url).json()["data"]["deletedAt"] == timestamp
    assert asyncio.run(ReplacementService().detail(uid, "u_001")).deleted_at is not None
    rows = client.get("/api/v1/templates/replacement-catalog").json()["data"]["runs"]
    assert rows[0]["deletedAt"] == timestamp
    assert client.post(url + "/execute").status_code == 409
    ReplacementRepository().repair_indexes()
    assert client.get(url).json()["data"]["deletedAt"] == timestamp
    assert client.post(url + "/restore").json()["data"]["deletedAt"] is None
    assert client.post(url + "/restore").status_code == 200
    assert (path / "run.json").read_bytes() == original
    assert (path / "inputs/prompt.txt").read_text() == "prompt"


def test_recycle_checks_owner_and_permissions(
    client: TestClient, archived: tuple[str, Path],
) -> None:
    uid, _ = archived
    url = f"/api/v1/templates/replacement-runs/{uid}"
    for username, expected in (("viewer", 403), ("editor", 404)):
        login = client.post("/api/v1/identity/login", json={
            "username": username, "password": f"{username}@12345",
        })
        headers = {"Authorization": "Bearer " + login.json()["data"]["accessToken"]}
        assert client.delete(url, headers=headers).status_code == expected
        assert client.post(url + "/restore", headers=headers).status_code == expected
    assert client.delete("/api/v1/templates/replacement-runs/invalid").status_code == 404


def test_recycle_conflict_keeps_record(client: TestClient, archived: tuple[str, Path]) -> None:
    uid, path = archived
    with lease(path / MANAGEMENT_LOCK):
        assert client.delete(f"/api/v1/templates/replacement-runs/{uid}").status_code == 409
    data = client.get(f"/api/v1/templates/replacement-runs/{uid}").json()["data"]
    assert data["deletedAt"] is None


def test_recycle_running_task_does_not_cancel(
    client: TestClient, archived: tuple[str, Path],
) -> None:
    uid, path = archived
    repo = ReplacementRepository()
    meta = repo.load(path)
    meta.status = "running"
    meta.task_id = "upstream-existing"
    repo.save(path, meta)
    original = (path / "run.json").read_bytes()
    assert client.delete(f"/api/v1/templates/replacement-runs/{uid}").status_code == 200
    assert (path / "run.json").read_bytes() == original
