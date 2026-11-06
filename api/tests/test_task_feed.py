"""统一任务视图：真实归档、混合分页、权限与状态口径。"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from test_task_api import _bearer

from core.config import settings
from modules.template.replacement_repository import ReplacementManifest, ReplacementRepository


@pytest.fixture
def feed_runs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    monkeypatch.setattr(settings, "media_archive_root", str(tmp_path))
    repo = ReplacementRepository()
    ids = {}
    for index, status in enumerate(
        (
            "queued",
            "running",
            "success",
            "failed",
            "prepared",
            "submission_unknown",
            "waiting_free",
            "service_error",
        )
    ):
        uid = uuid.uuid4().hex
        ids[status] = uid
        path = tmp_path / "runs" / uid
        repo.save(
            path,
            ReplacementManifest(
                id=uid,
                kind="character",
                target=f"聚合测试{status}",
                owner_id="u_001",
                fingerprint="fixture",
                status=status,
                task_id=f"upstream-{status}",
                created_at=datetime(2030, 1, index + 1, tzinfo=UTC),
            ),
        )
        repo.index(path, uid)
        if status == "success":
            (path / "outputs").mkdir()
            (path / "outputs/generated.mp4").write_bytes(b"test-output")
    return ids


@pytest.mark.parametrize("sort", ["createdAt_desc", "createdAt_asc", "title_asc"])
def test_feed_mixed_pagination_and_stats(
    client: TestClient,
    feed_runs: dict[str, str],
    sort: str,
) -> None:
    url = "/api/v1/tasks/feed"
    all_data = client.get(url, params={"pageSize": 100, "sort": sort}).json()["data"]
    assert {item["source"] for item in all_data["items"]} == {"video", "replacement"}
    stats = all_data["stats"]
    assert stats["all"] == sum(stats[k] for k in ("running", "completed", "failed", "attention"))
    pages = [
        client.get(url, params={"pageSize": 3, "page": i, "sort": sort}).json()["data"]
        for i in range(1, (all_data["total"] + 2) // 3 + 1)
    ]
    keys = [item["key"] for page in pages for item in page["items"]]
    assert len(keys) == len(set(keys)) == all_data["total"]
    assert keys == [item["key"] for item in all_data["items"]]
    assert all(page["stats"] == stats for page in pages)


def test_feed_replacement_groups_search_and_output(
    client: TestClient,
    feed_runs: dict[str, str],
) -> None:
    url = "/api/v1/tasks/feed"

    def read(**params: str) -> dict:
        response = client.get(url, params={"source": "replacement", **params})
        assert response.status_code == 200, response.text
        return response.json()["data"]

    assert read(status="running")["total"] == 2  # queued 也在进行中
    attention = read(status="attention")
    assert {item["status"] for item in attention["items"]} == {
        "prepared",
        "waiting_free",
        "submission_unknown",
        "service_error",
    }
    found = read(keyword="upstream-success")
    assert found["total"] == 1
    item = found["items"][0]
    assert item["id"] == feed_runs["success"]
    assert item["video"] is None
    assert item["detailUrl"] == f"/templates?kind=character&run={item['id']}"
    assert item["outputUrl"].endswith(f"/{item['id']}/output")
    assert read(onlyAuthorized="true")["total"] == 1


def test_feed_trash_and_missing_output(client: TestClient, feed_runs: dict[str, str]) -> None:
    uid = feed_runs["queued"]
    assert client.delete(f"/api/v1/templates/replacement-runs/{uid}").status_code == 200
    params = {"source": "replacement"}
    data = client.get("/api/v1/tasks/feed", params=params).json()["data"]
    assert uid not in {item["id"] for item in data["items"]}
    assert data["stats"]["all"] == 7
    assert client.post(f"/api/v1/templates/replacement-runs/{uid}/restore").status_code == 200
    repo = ReplacementRepository()
    (repo.path(feed_runs["success"]) / "outputs/generated.mp4").unlink()
    data = client.get("/api/v1/tasks/feed", params=params).json()["data"]
    success = next(item for item in data["items"] if item["status"] == "success")
    assert success["group"] == "attention"
    assert data["stats"]["all"] == 8


def test_feed_permissions_and_owner(client: TestClient, feed_runs: dict[str, str]) -> None:
    url = "/api/v1/tasks/feed"
    # task:read:all 不授予任何替换档案权限，也不能突破其 owner 隔离。
    reader = _bearer(client, "feed-reader", permissions=("task:read", "task:read:all"))
    data = client.get(url, headers=reader).json()["data"]
    assert all(item["source"] == "video" for item in data["items"])
    reader = _bearer(
        client,
        "feed-other",
        permissions=(
            "task:read",
            "task:read:all",
            "template:read",
        ),
    )
    data = client.get(url, headers=reader, params={"source": "replacement", "scope": "all"})
    assert data.json()["data"]["total"] == 0
    denied = _bearer(client, "feed-denied", permissions=("template:read",))
    assert client.get(url, headers=denied).status_code == 403


def test_feed_offline_archive_is_explicit(
    client: TestClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "media_archive_root", str(tmp_path / "missing"))
    data = client.get("/api/v1/tasks/feed").json()["data"]
    assert data["warnings"]
    assert data["items"] and all(item["source"] == "video" for item in data["items"])
    video_only = client.get("/api/v1/tasks/feed", params={"source": "video"}).json()["data"]
    assert video_only["warnings"] == []


def test_feed_stats_only_skips_item_assembly(
    client: TestClient,
    feed_runs: dict[str, str],
) -> None:
    """角标只读统计：不给条目，但口径必须与完整列表一致。"""
    url = "/api/v1/tasks/feed"
    full = client.get(url, params={"pageSize": 100}).json()["data"]
    light = client.get(url, params={"statsOnly": "true", "pageSize": 1}).json()["data"]
    assert light["items"] == []
    assert light["stats"] == full["stats"]
    assert light["total"] == full["total"]


@pytest.mark.parametrize(
    "status", ["all", "queued", "running", "completed", "failed", "attention"]
)
def test_feed_stats_only_total_follows_status_tab(
    client: TestClient,
    feed_runs: dict[str, str],
    status: str,
) -> None:
    """`total` 是列表口径：切换状态页签后，统计接口给的总数必须与列表一致。"""
    url = "/api/v1/tasks/feed"
    full = client.get(url, params={"pageSize": 100, "status": status}).json()["data"]
    light = client.get(url, params={"statsOnly": "true", "status": status}).json()["data"]
    assert light["total"] == full["total"]


def test_replacement_title_uses_shared_rule(
    client: TestClient,
    feed_runs: dict[str, str],
) -> None:
    """任务中心的标题与模板页共用一套命名：类型、目标与 `#短码` 都要在。"""
    data = client.get(
        "/api/v1/tasks/feed", params={"source": "replacement"}
    ).json()["data"]
    item = next(item for item in data["items"] if item["id"] == feed_runs["running"])
    assert item["title"] == (
        f"历史档案未记录原片 · 人物替换 · 聚合测试running · #{feed_runs['running'][:8].upper()}"
    )
