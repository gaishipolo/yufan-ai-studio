"""统一任务视图：两个来源混合分页、状态分组、权限与统计口径。

重建后这里的数据源从「扫归档盘的指针」变成「查 `replacement_runs`」，语义没变：
`source=replacement` 只看替换任务，`status=running` 把 `queued` 也算进去，
`statsOnly` 只算数字不给条目。

**多了一条以前测不出来的能力**：归档盘不可用时任务列表照常显示（状态在库里），
只需要在打开详情时才说「档案不存在」。旧实现整个列表会塌成一条 warning。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from test_task_api import _bearer
from tests_template_helpers import ADMIN, API, add_run, purge_runs, workspace

from db.session import get_session_factory
from infra.archive.local import get_archive_settings
from modules.task.enums import TaskStatus
from modules.task.models import VideoTask

STATUSES = (
    "queued",
    "running",
    "success",
    "failed",
    "prepared",
    "submission_unknown",
    "waiting_free",
    "service_error",
)


@pytest.fixture
async def feed_runs(tmp_path, monkeypatch) -> dict[str, str]:
    """八种状态的替换任务 + 一条视频任务，工作区按需造。"""
    monkeypatch.setattr(get_archive_settings(), "root", str(tmp_path))
    await purge_runs()
    owner = ADMIN

    ids: dict[str, str] = {}
    for index, status in enumerate(STATUSES):
        run = await add_run(
            owner=owner,
            status=status,
            target=f"聚合测试{status}",
            task_id=f"upstream-{status}",
            created_at=datetime(2030, 1, index + 1, tzinfo=UTC),
        )
        path = workspace(run.id, {"id": run.id, "owner_id": owner, "status": status})
        if status == "success":
            (path / "outputs/generated.mp4").write_bytes(b"test-output")
        ids[status] = run.id

    # 混排用例需要一个视频来源：没有它「混合」是句空话
    async with get_session_factory()() as session:
        session.add(
            VideoTask(
                title="视频任务",
                prompt="聚合测试",
                status=TaskStatus.COMPLETED.value,
                model="Seedance 2.5",
                author_id=owner,
                author_name="聚合测试",
                created_at=datetime(2030, 2, 1, tzinfo=UTC),
            )
        )
        await session.commit()
    return ids


@pytest.mark.parametrize("sort", ["createdAt_desc", "createdAt_asc", "title_asc"])
async def test_feed_mixed_pagination_and_stats(client: TestClient, feed_runs, sort: str) -> None:
    url = f"{API}/tasks/feed"
    all_data = client.get(url, params={"pageSize": 100, "sort": sort}).json()["data"]
    assert {item["source"] for item in all_data["items"]} == {"video", "replacement"}
    stats = all_data["stats"]
    assert stats["all"] == sum(
        stats[key] for key in ("running", "completed", "failed", "attention")
    )

    pages = [
        client.get(url, params={"pageSize": 3, "page": index, "sort": sort}).json()["data"]
        for index in range(1, (all_data["total"] + 2) // 3 + 1)
    ]
    keys = [item["key"] for page in pages for item in page["items"]]
    assert len(keys) == len(set(keys)) == all_data["total"], "分页之间不能重复也不能漏"
    assert keys == [item["key"] for item in all_data["items"]]
    assert all(page["stats"] == stats for page in pages), "每页的角标数字必须一致"


async def test_feed_replacement_groups_search_and_output(client: TestClient, feed_runs) -> None:
    url = f"{API}/tasks/feed"

    def read(**params: str) -> dict:
        response = client.get(url, params={"source": "replacement", **params})
        assert response.status_code == 200, response.text
        return response.json()["data"]

    assert read(status="running")["total"] == 2, "queued 也算进行中"
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
    # 只有成片在的那条能被「只看有结果」筛出来
    assert read(onlyAuthorized="true")["total"] == 1


async def test_feed_hides_trashed_and_flags_missing_output(client: TestClient, feed_runs) -> None:
    uid = feed_runs["queued"]
    assert client.delete(f"{API}/templates/replacement-runs/{uid}").status_code == 200
    data = client.get(f"{API}/tasks/feed", params={"source": "replacement"}).json()["data"]
    assert uid not in {item["id"] for item in data["items"]}
    assert data["stats"]["all"] == len(STATUSES) - 1

    assert client.post(f"{API}/templates/replacement-runs/{uid}/restore").status_code == 200

    # 成片丢了：状态仍是 success，但分组必须落到「待处理」——否则用户以为能下载
    success = feed_runs["success"]
    from modules.template.workspace import run_path

    (run_path(success) / "outputs/generated.mp4").unlink()
    data = client.get(f"{API}/tasks/feed", params={"source": "replacement"}).json()["data"]
    item = next(row for row in data["items"] if row["id"] == success)
    assert item["group"] == "attention"
    assert data["stats"]["all"] == len(STATUSES)


def test_feed_permissions_and_owner(client: TestClient, feed_runs) -> None:
    """`_bearer` 自己会建账号（内部 `asyncio.run`），所以这条用例保持同步。"""
    url = f"{API}/tasks/feed"
    # task:read:all 不授予任何替换档案权限，也不能突破 owner 隔离
    reader = _bearer(client, "feed-reader", permissions=("task:read", "task:read:all"))
    data = client.get(url, headers=reader).json()["data"]
    assert all(item["source"] == "video" for item in data["items"])

    other = _bearer(
        client,
        "feed-other-" + uuid.uuid4().hex[:6],
        permissions=("task:read", "task:read:all", "template:read"),
    )
    data = client.get(url, headers=other, params={"source": "replacement"})
    assert data.json()["data"]["total"] == 0

    denied = _bearer(client, "feed-denied-" + uuid.uuid4().hex[:6], permissions=("template:read",))
    assert client.get(url, headers=denied).status_code == 403


async def test_feed_survives_offline_archive(
    client: TestClient, feed_runs, tmp_path, monkeypatch
) -> None:
    """归档盘不可用：状态照常显示，只是点开详情才会说档案不存在。

    旧实现整块列表依赖指针枚举，盘一掉线就只剩一条 warning；状态搬进数据库之后
    「能看到任务、看不到档案」变成两件独立的事。
    """
    monkeypatch.setattr(get_archive_settings(), "root", str(tmp_path / "offline-share"))
    data = client.get(f"{API}/tasks/feed", params={"source": "replacement"}).json()["data"]
    assert data["items"], "盘掉线不该让任务列表变空"
    assert all(item["source"] == "replacement" for item in data["items"])


async def test_feed_stats_only_skips_item_assembly(client: TestClient, feed_runs) -> None:
    url = f"{API}/tasks/feed"
    full = client.get(url, params={"pageSize": 100}).json()["data"]
    light = client.get(url, params={"statsOnly": "true", "pageSize": 1}).json()["data"]
    assert light["items"] == []
    assert light["stats"] == full["stats"]
    assert light["total"] == full["total"]


@pytest.mark.parametrize(
    "status", ["all", "queued", "running", "completed", "failed", "attention"]
)
async def test_feed_stats_only_total_follows_status_tab(
    client: TestClient, feed_runs, status: str
) -> None:
    """`total` 是列表口径：切状态页签后，统计接口给的总数必须与列表一致。"""
    url = f"{API}/tasks/feed"
    params = {"pageSize": 100, "status": status}
    full = client.get(url, params=params).json()["data"]
    light = client.get(url, params={**params, "statsOnly": "true"}).json()["data"]
    assert light["total"] == full["total"]


async def test_replacement_title_uses_shared_rule(client: TestClient, feed_runs) -> None:
    """任务中心的标题与模板页共用一套命名：类型、目标与 `#短码` 都要在。"""
    data = client.get(f"{API}/tasks/feed", params={"source": "replacement"}).json()["data"]
    uid = feed_runs["running"]
    item = next(row for row in data["items"] if row["id"] == uid)
    assert item["title"] == f"历史档案未记录原片 · 人物替换 · 聚合测试running · #{uid[:8].upper()}"
