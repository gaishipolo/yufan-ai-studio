"""统一任务视图：两路来源混合分页、状态分组、权限与统计口径。

2026-11-14 P3 之后只剩两路：`tasks`（视频，权限 `task:read`）与 `image_jobs`
（生图，权限 `image:read`，经 `core.ports.generation_feed` 注入口）。替换档案那一路
随替换链下线——替换现在是普通任务，落在第一路里。

两条口径仍要钉死：

1. `statsOnly` 只算数字不装配条目（侧栏角标每页都挂，不能拉全量）；
2. `total` 是列表口径，切状态页签后统计接口给的总数必须与列表一致。
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from test_task_api import _bearer
from tests_template_helpers import ADMIN, API

from core.ports.generation_feed import (
    has_generation_feed,
    register_generation_feed,
    reset_generation_feed,
)
from db.session import get_session_factory
from generation.spec import (
    STATUS_FAILED,
    STATUS_QUEUED,
    STATUS_RUNNING,
    STATUS_SUCCEEDED,
)
from modules.identity.permissions import (
    SCOPE_TEAM,
    TEAM_BASE_PERMISSIONS,
    TEAM_SCOPE_ID,
)
from modules.identity.repository import PermissionLayerRepository
from modules.image.feed_provider import read_generation_feed
from modules.image.models import ImageJob
from modules.task.enums import TaskStatus
from modules.task.models import VideoTask


@pytest.fixture
async def feed_videos() -> dict[str, str]:
    """四种状态的视频任务，标题与时间都不一样，够混排与分组。"""
    from sqlalchemy import delete

    rows: list[tuple[str, VideoTask]] = []
    async with get_session_factory()() as session:
        for index, status in enumerate(
            (TaskStatus.QUEUED, TaskStatus.RUNNING, TaskStatus.COMPLETED, TaskStatus.FAILED)
        ):
            task = VideoTask(
                title=f"聚合测试{status.value}",
                prompt="聚合测试",
                status=status.value,
                model="Seedance 2.5",
                author_id=ADMIN,
                author_name="聚合测试",
                created_at=datetime(2030, 2, index + 1, tzinfo=UTC),
            )
            session.add(task)
            rows.append((status.value, task))
        await session.flush()
        ids = {key: task.id for key, task in rows}
        await session.commit()

    yield ids

    async with get_session_factory()() as session:
        await session.execute(delete(VideoTask).where(VideoTask.id.in_(list(ids.values()))))
        await session.commit()


IMAGE_SHAPES = (
    ("queued", STATUS_QUEUED, "", ""),
    ("running", STATUS_RUNNING, "", ""),
    ("succeeded", STATUS_SUCCEEDED, "images/flat.png", ""),
    ("failed", STATUS_FAILED, "", "供应商超时"),
    ("no_output", STATUS_SUCCEEDED, "", ""),
)


@pytest.fixture
async def feed_images() -> dict[str, str]:
    """五种形状的生图记录；用完删掉，别给别的用例留下额外来源。"""
    from sqlalchemy import delete

    jobs: list[tuple[str, ImageJob]] = []
    async with get_session_factory()() as session:
        for index, (key, status, output, error) in enumerate(IMAGE_SHAPES):
            job = ImageJob(
                name=f"聚合测试生图{key}",
                prompt="聚合测试",
                size="1024x1024",
                count=1,
                status=status,
                model="生图模型",
                owner_id=ADMIN,
                output_url=output or None,
                cover_url="images/cover.png" if output else "",
                error_message=error or None,
                created_at=datetime(2030, 3, index + 1, tzinfo=UTC),
            )
            session.add(job)
            jobs.append((key, job))
        await session.flush()
        ids = {key: job.id for key, job in jobs}
        await session.commit()

    yield ids

    async with get_session_factory()() as session:
        await session.execute(delete(ImageJob).where(ImageJob.id.in_(list(ids.values()))))
        await session.commit()


@pytest.mark.parametrize("sort", ["createdAt_desc", "createdAt_asc", "title_asc"])
async def test_feed_mixed_pagination_and_stats(
    client: TestClient, feed_videos, feed_images, sort: str
) -> None:
    url = f"{API}/tasks/feed"
    all_data = client.get(url, params={"pageSize": 100, "sort": sort}).json()["data"]
    assert {item["source"] for item in all_data["items"]} == {"video", "image"}
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


async def test_feed_video_source_groups_and_filters(
    client: TestClient, feed_videos
) -> None:
    url = f"{API}/tasks/feed"

    def read(**params: str) -> dict:
        # 用 keyword 把范围收到本用例造的四条：库里还有演示种子造的任务。
        response = client.get(
            url, params={"source": "video", "keyword": "聚合测试", **params}
        )
        assert response.status_code == 200, response.text
        return response.json()["data"]

    data = read(pageSize="100")
    assert data["total"] == len(feed_videos)
    assert {item["source"] for item in data["items"]} == {"video"}
    assert all(item["image"] is None and item["video"] for item in data["items"])

    by_status = {item["status"]: item for item in data["items"]}
    assert by_status["queued"]["group"] == "running", "排队也算进行中"
    assert by_status["running"]["group"] == "running"
    assert by_status["completed"]["group"] == "completed"
    assert by_status["failed"]["group"] == "failed"
    assert by_status["completed"]["video"]["title"] == "聚合测试completed"

    assert read(status="running")["total"] == 2
    assert read(status="failed")["total"] == 1


def test_feed_permissions_and_owner(client: TestClient, feed_videos) -> None:
    """`_bearer` 自己会建账号（内部 `asyncio.run`），所以这条用例保持同步。"""
    url = f"{API}/tasks/feed"
    reader = _bearer(client, "feed-reader", permissions=("task:read", "task:read:all"))
    data = client.get(url, headers=reader).json()["data"]
    assert all(item["source"] == "video" for item in data["items"])

    # 三层权限上线后，团队基础权限给所有人（包括被摘权限的账号）发了 `task:read`。
    # 「没有 task:read 就该 403」仍然要守，所以这里临时摘掉团队层再验；
    # finally 必须还原，否则后续用例会连锁 403，而且症状看起来像接口坏了。
    async def set_team(codes: list[str]) -> None:
        async with get_session_factory()() as session:
            await PermissionLayerRepository(session).set_grants(
                SCOPE_TEAM, TEAM_SCOPE_ID, codes
            )
            await session.commit()

    asyncio.run(set_team([]))
    try:
        denied = _bearer(
            client, "feed-denied-" + uuid.uuid4().hex[:6], permissions=("image:read",)
        )
        assert client.get(url, headers=denied).status_code == 403
    finally:
        asyncio.run(set_team(list(TEAM_BASE_PERMISSIONS)))


async def test_feed_stats_only_skips_item_assembly(client: TestClient, feed_videos) -> None:
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
    client: TestClient, feed_videos, status: str
) -> None:
    """`total` 是列表口径：切状态页签后，统计接口给的总数必须与列表一致。"""
    url = f"{API}/tasks/feed"
    params = {"pageSize": 100, "status": status}
    full = client.get(url, params=params).json()["data"]
    light = client.get(url, params={**params, "statsOnly": "true"}).json()["data"]
    assert light["total"] == full["total"]


async def test_feed_image_source_groups_search_and_signed_urls(
    client: TestClient, feed_images
) -> None:
    """生图是第二路：分组、搜索、只看成图，以及产物 key → 可访问地址。"""
    url = f"{API}/tasks/feed"

    def read(**params: str) -> dict:
        response = client.get(
            url, params={"source": "image", "keyword": "聚合测试生图", **params}
        )
        assert response.status_code == 200, response.text
        return response.json()["data"]

    data = read(pageSize="100")
    assert data["total"] == len(IMAGE_SHAPES)
    assert data["stats"]["all"] == len(IMAGE_SHAPES)
    assert {item["source"] for item in data["items"]} == {"image"}
    assert all(item["video"] is None and item["image"] for item in data["items"])

    by_key = {
        item["title"].removeprefix("聚合测试生图"): item for item in data["items"]
    }
    assert by_key["queued"]["group"] == "running", "排队也算进行中"
    assert by_key["running"]["group"] == "running"
    assert by_key["succeeded"]["group"] == "completed"
    assert by_key["failed"]["group"] == "failed"
    assert by_key["no_output"]["group"] == "attention", "成功但成图不在 = 待处理"
    assert by_key["failed"]["errorMessage"] == "供应商超时"

    done = by_key["succeeded"]
    assert done["image"]["size"] == "1024x1024"
    assert done["image"]["model"] == "生图模型"
    assert done["image"]["detailUrl"].startswith("/image?job=")
    # 库里是对象 key，出参必须按当前存储驱动现签，不能直接把 key 发给前端
    assert done["outputUrl"] and done["outputUrl"] != "images/flat.png"
    assert done["image"]["coverUrl"] != "images/cover.png"

    assert read(status="attention")["total"] == 1
    assert read(onlyAuthorized="true")["total"] == 1
    assert read(keyword=feed_images["failed"])["total"] == 1, "记录 ID 也要能搜到"


def test_feed_image_permissions_and_unregistered_reader(client: TestClient) -> None:
    """没有 `image:read` 的人连自己的生图都看不到；注入口没接上整页照常可用。

    `_bearer` 内部用 `asyncio.run` 建账号，所以这条用例保持同步（见
    `test_feed_permissions_and_owner` 的注释）。
    """
    url = f"{API}/tasks/feed"
    sub = "feed-no-image-" + uuid.uuid4().hex[:6]
    keyword = "无读权限生图"

    async def set_team(codes: list[str]) -> None:
        async with get_session_factory()() as session:
            await PermissionLayerRepository(session).set_grants(
                SCOPE_TEAM, TEAM_SCOPE_ID, codes
            )
            await session.commit()

    async def add_job() -> None:
        from sqlalchemy import delete

        async with get_session_factory()() as session:
            await session.execute(delete(ImageJob).where(ImageJob.owner_id == sub))
            session.add(
                ImageJob(
                    name=keyword,
                    prompt=keyword,
                    size="1024x1024",
                    status=STATUS_SUCCEEDED,
                    model="生图模型",
                    owner_id=sub,
                    output_url="images/owned.png",
                )
            )
            await session.commit()

    async def drop_job() -> None:
        from sqlalchemy import delete

        async with get_session_factory()() as session:
            await session.execute(delete(ImageJob).where(ImageJob.owner_id == sub))
            await session.commit()

    # 团队基础权限默认发 `image:read`，先摘掉才能造出「没有生图读权限」的账号
    asyncio.run(set_team([]))
    try:
        denied = _bearer(client, sub, permissions=("task:read",))
        asyncio.run(add_job())
        data = client.get(
            url, headers=denied, params={"source": "image", "keyword": keyword}
        ).json()["data"]
        assert data["total"] == 0
        assert data["items"] == []
    finally:
        asyncio.run(set_team(list(TEAM_BASE_PERMISSIONS)))
        asyncio.run(drop_job())

    assert has_generation_feed()
    reset_generation_feed()
    try:
        image = client.get(url, params={"source": "image", "keyword": keyword}).json()["data"]
        assert image["items"] == [] and image["total"] == 0
        mixed = client.get(url, params={"pageSize": 100}).json()["data"]
        assert "image" not in {item["source"] for item in mixed["items"]}
        assert mixed["warnings"] == [], "未接上生图来源是中性状态，不是故障"
    finally:
        register_generation_feed(read_generation_feed)
