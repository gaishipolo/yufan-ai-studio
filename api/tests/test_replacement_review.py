"""隔离归档验证评价、历史兼容与快照授权，不调用生成供应商。"""

from __future__ import annotations

import asyncio
import json
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from infra.archive import lease
from infra.archive.local import LocalArchiveStorage, get_archive_settings
from modules.template.replacement_repository import ReplacementManifest, ReplacementRepository
from modules.template.replacement_review import REVIEW_LOCK
from modules.template.replacement_schema import ReplacementReviewIn
from modules.template.replacement_service import ReplacementService


@pytest.fixture
def saved_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[str, Path]:
    monkeypatch.setattr(get_archive_settings(), "root", str(tmp_path))
    uid = uuid.uuid4().hex
    path = tmp_path / "runs" / uid
    (path / "outputs").mkdir(parents=True)
    (path / "outputs/generated.mp4").write_bytes(b"original-result")
    (path / "inputs").mkdir()
    (path / "inputs/original.png").write_bytes(b"input-snapshot")
    repo = ReplacementRepository()
    repo.save(path, ReplacementManifest(
        id=uid, kind="character", target="目标", owner_id="u_001",
        fingerprint="fixture", status="success",
    ))
    repo.index(path, uid)
    repo.storage.write(path / "inputs/input-map.json", [{
        "id": "image", "name": "归档参考", "role": "identity", "type": "image",
        "file": "inputs/original.png",
    }])
    repo.storage.write(path / "inputs/configuration.json", {
        "requestId": str(uuid.UUID(uid)), "kind": "character", "target": "目标",
        "sourceId": "source", "referenceIds": ["image"], "notes": "保留原动作",
    })
    return uid, path


def login(client: TestClient, username: str, password: str) -> dict[str, str]:
    response = client.post("/api/v1/identity/login", json={
        "username": username, "password": password,
    })
    assert response.status_code == 200
    return {"Authorization": "Bearer " + response.json()["data"]["accessToken"]}


def test_review_revisions_persist_without_changing_generation(
    client: TestClient, saved_run: tuple[str, Path],
) -> None:
    uid, path = saved_run
    before = (path / "run.json").read_bytes()
    url = f"/api/v1/templates/replacement-runs/{uid}"
    first = client.put(url + "/review", json={"status": "accepted", "expectedVersion": 0})
    assert first.status_code == 200, first.text
    data = first.json()["data"]
    assert data["status"] == "success" and data["reviewStatus"] == "accepted"
    assert data["review"]["version"] == 1
    assert data["review"]["reviewedBy"] == "u_001"
    assert data["review"]["reviewedAt"].endswith("Z")
    second = client.put(url + "/review", json={
        "status": "rejected", "expectedVersion": 1,
        "reasonCodes": ["visual"], "comment": "画面异常",
    })
    assert second.status_code == 200, second.text
    # 新 service / repo 实例读取同一个持久化文件，不依赖进程内状态。
    restored = asyncio.run(ReplacementService().detail(uid, "u_001"))
    assert restored.review_status == "rejected"
    assert restored.review is not None and restored.review.version == 2
    assert [item.status for item in restored.review.history] == ["pending", "accepted"]
    assert (path / "run.json").read_bytes() == before
    assert (path / "outputs/generated.mp4").read_bytes() == b"original-result"
    assert (path / "inputs/original.png").read_bytes() == b"input-snapshot"


def test_stale_review_conflicts_and_keeps_latest(
    client: TestClient, saved_run: tuple[str, Path],
) -> None:
    uid, _ = saved_run
    url = f"/api/v1/templates/replacement-runs/{uid}/review"
    payload = {"status": "accepted", "expectedVersion": 0}
    assert client.put(url, json=payload).status_code == 200
    assert client.put(url, json=payload).status_code == 409
    assert client.get(url.removesuffix("/review")).json()["data"]["review"]["version"] == 1


def test_review_permissions_owner_and_input_snapshot(
    client: TestClient, saved_run: tuple[str, Path],
) -> None:
    uid, path = saved_run
    editor = login(client, "editor", "editor@12345")
    viewer = login(client, "viewer", "viewer@12345")
    base = f"/api/v1/templates/replacement-runs/{uid}"
    payload = {"status": "accepted", "expectedVersion": 0}
    assert client.put(base + "/review", json=payload, headers=viewer).status_code == 403
    assert client.put(base + "/review", json=payload, headers=editor).status_code == 404
    assert client.get(base + "/inputs/image", headers=editor).status_code == 404
    assert client.get(base + "/inputs/image").content == b"input-snapshot"
    summary = client.get(base).json()["data"]["inputSummary"]
    assert summary["assets"][0]["name"] == "归档参考"
    assert client.get(summary["assets"][0]["url"]).content == b"input-snapshot"
    assert summary["notes"] == "保留原动作"
    assert not (path / "review/review.json").exists()


@pytest.mark.parametrize("status,output", [("running", True), ("success", False)])
def test_review_requires_saved_success(
    client: TestClient, saved_run: tuple[str, Path], status: str, output: bool,
) -> None:
    uid, path = saved_run
    repo = ReplacementRepository()
    meta = repo.load(path)
    meta.status = status
    repo.save(path, meta)
    if not output:
        (path / "outputs/generated.mp4").unlink()
    response = client.put(f"/api/v1/templates/replacement-runs/{uid}/review", json={
        "status": "accepted", "expectedVersion": 0,
    })
    assert response.status_code == 409


def test_legacy_review_preserved_and_invalid_reasons_rejected(
    client: TestClient, saved_run: tuple[str, Path],
) -> None:
    uid, path = saved_run
    repo = ReplacementRepository()
    meta = repo.load(path)
    meta.review_status = "accepted"
    repo.save(path, meta)
    repo.storage.write(path / "review/review.json", {"status": "pending"})
    base = f"/api/v1/templates/replacement-runs/{uid}"
    assert client.get(base).json()["data"]["reviewStatus"] == "accepted"
    for payload in [
        {"status": "rejected", "expectedVersion": 0},
        {"status": "accepted", "expectedVersion": -1},
        {"status": "rejected", "expectedVersion": 0, "reasonCodes": ["invalid"]},
    ]:
        assert client.put(base + "/review", json=payload).status_code == 422
    assert json.loads((path / "review/review.json").read_text())["status"] == "pending"


def test_review_write_failure_does_not_pretend_saved(
    saved_run: tuple[str, Path], monkeypatch: pytest.MonkeyPatch,
) -> None:
    uid, _ = saved_run

    def fail_write(self: LocalArchiveStorage, path: Path, value: object) -> None:
        raise OSError("disk unavailable")

    monkeypatch.setattr(LocalArchiveStorage, "write", fail_write)
    with pytest.raises(OSError):
        asyncio.run(ReplacementService().review(
            uid, ReplacementReviewIn(status="accepted", expected_version=0), "u_001",
        ))
    assert asyncio.run(ReplacementService().detail(uid, "u_001")).review_status == "pending"


def test_review_lease_conflicts_without_lost_updates(
    client: TestClient, saved_run: tuple[str, Path],
) -> None:
    uid, path = saved_run
    with lease(path / REVIEW_LOCK):
        response = client.put(f"/api/v1/templates/replacement-runs/{uid}/review", json={
            "status": "accepted", "expectedVersion": 0,
        })
    assert response.status_code == 409
    assert not (path / "review/review.json").exists()


def test_snapshot_binding_cannot_escape_run(
    client: TestClient, saved_run: tuple[str, Path],
) -> None:
    uid, path = saved_run
    repo = ReplacementRepository()
    repo.storage.write(path / "inputs/input-map.json", [{
        "id": "image", "name": "bad", "role": "identity", "type": "image",
        "file": "../outside.png",
    }])
    assert client.get(f"/api/v1/templates/replacement-runs/{uid}/inputs/image").status_code == 400
