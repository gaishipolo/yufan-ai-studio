"""用量看板接口测试。

覆盖这几件事（每一条都对应一个真实会踩的坑）：

1. **没有 `usage:read` 就是 403** —— 看板是统计口径最敏感的一页。
2. **口径**：尝试次数含失败；已产出数量 / 时长只算成功的。
3. **越权降级**：没有 `usage:read:all` 却传 `scope=all`，不报错、降级成
   `mine`，并把**实际生效的 scope** 写回响应。
4. **跨来源合并**：视频 + 生图两路相加，趋势按桶合并。
5. **趋势补零**：区间里没有数据的那天也要有桶。
6. **明细分页**：跨来源合并后再切页，页码不能乱。
7. **没有来源时是空看板**，不是「用量为零」的假象。
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from library_support import admin_headers, make_user

TODAY = datetime.now(UTC).date()


def _insert_video(
    *,
    author_id: str,
    author_name: str,
    status: str,
    created_at: datetime,
    duration_sec: int = 12,
    title: str = "用量测试视频",
) -> str:
    """插一行视频任务。绕开接口是因为接口造不出 `completed` / `failed` 的行。"""
    from db.session import get_session_factory
    from modules.task.models import VideoTask

    async def run() -> str:
        async with get_session_factory()() as session:
            row = VideoTask(
                title=title,
                prompt="用量测试",
                status=status,
                model="Seedance 2.5",
                model_id="seedance-2.5",
                duration_sec=duration_sec,
                aspect_ratio="9:16",
                author_id=author_id,
                author_name=author_name,
                reference_library_ids=[],
            )
            row.created_at = created_at
            row.updated_at = created_at
            session.add(row)
            await session.commit()
            return row.id

    return asyncio.run(run())


def _insert_image(
    *,
    owner_id: str,
    status: str,
    created_at: datetime,
    count: int = 1,
    name: str = "用量测试生图",
) -> str:
    from db.session import get_session_factory
    from modules.image.models import ImageJob

    async def run() -> str:
        async with get_session_factory()() as session:
            row = ImageJob(
                name=name,
                prompt="用量测试",
                status=status,
                model="Agnes Image 2.5 Flash",
                model_id="agnes-image-2.5-flash",
                count=count,
                size="1024x1024",
                params_json="",
                reference_json="",
                text_json="",
                progress=100 if status == "succeeded" else 0,
                cover_url="",
                owner_id=owner_id,
            )
            row.created_at = created_at
            row.updated_at = created_at
            session.add(row)
            await session.commit()
            return row.id

    return asyncio.run(run())


def _make_user(client: TestClient, permissions: list[str]) -> tuple[dict, dict[str, str]]:
    """造一个只有指定权限的账号，返回 (用户, 请求头)。"""
    admin = admin_headers(client)
    return make_user(client, admin, permissions)


def _range(days: int = 30) -> dict[str, str]:
    start = TODAY - timedelta(days=days - 1)
    return {"start": start.isoformat(), "end": TODAY.isoformat()}


@pytest.fixture
def no_providers():
    """把用量来源清空（测「没接数据源」的中性值），用例结束后复原。"""
    from core.ports.usage_metrics import register_usage_metrics, reset_usage_metrics
    from modules.image.usage_provider import ImageUsageProvider
    from modules.task.usage_provider import VideoUsageProvider

    reset_usage_metrics()
    try:
        yield
    finally:
        register_usage_metrics("video", VideoUsageProvider())
        register_usage_metrics("image", ImageUsageProvider())


# --------------------------------------------------------------------------- #
# 权限
# --------------------------------------------------------------------------- #
def test_overview_requires_usage_read(client: TestClient, api: str) -> None:
    user, headers = _make_user(client, ["catalog:read"])
    resp = client.get(f"{api}/usage/overview", headers=headers)
    assert resp.status_code == 403, resp.text


def test_records_requires_usage_read(client: TestClient, api: str) -> None:
    user, headers = _make_user(client, ["catalog:read"])
    resp = client.get(f"{api}/usage/records", headers=headers)
    assert resp.status_code == 403, resp.text


# --------------------------------------------------------------------------- #
# 口径
# --------------------------------------------------------------------------- #
def test_overview_aggregates_video_and_image(client: TestClient, api: str) -> None:
    user, headers = _make_user(client, ["usage:read"])
    moment = datetime.now(UTC) - timedelta(days=1)

    _insert_video(
        author_id=user["id"],
        author_name=user.get("displayName") or user["username"],
        status="completed",
        created_at=moment,
        duration_sec=12,
    )
    _insert_video(
        author_id=user["id"],
        author_name=user.get("displayName") or user["username"],
        status="failed",
        created_at=moment,
        duration_sec=12,
    )
    _insert_image(owner_id=user["id"], status="succeeded", created_at=moment, count=3)

    data = client.get(f"{api}/usage/overview", headers=headers, params=_range()).json()["data"]
    summary = data["summary"]

    # 尝试次数含失败
    assert summary["total"] == 3
    assert summary["succeeded"] == 2
    assert summary["failed"] == 1
    # 已产出数量：视频 1 条 + 生图 3 张
    assert summary["quantity"] == 4
    # 已产出时长只算成功的视频
    assert summary["durationSec"] == 12
    assert summary["successRate"] == pytest.approx(2 / 3)
    # 两路来源都在
    assert {item["source"] for item in summary["sources"]} == {"video", "image"}
    assert set(data["sources"]) == {"video", "image"}


def test_failed_records_do_not_count_toward_output(client: TestClient, api: str) -> None:
    """失败的任务既不计时长，也不计数量 —— 用户没拿到东西。"""
    user, headers = _make_user(client, ["usage:read"])
    moment = datetime.now(UTC) - timedelta(days=1)
    _insert_video(
        author_id=user["id"],
        author_name="测试",
        status="failed",
        created_at=moment,
        duration_sec=60,
    )
    summary = (
        client.get(f"{api}/usage/overview", headers=headers, params=_range())
        .json()["data"]["summary"]
    )
    assert summary["total"] == 1
    assert summary["quantity"] == 0
    assert summary["durationSec"] == 0
    assert summary["avgDurationSec"] == 0


# --------------------------------------------------------------------------- #
# 数据范围
# --------------------------------------------------------------------------- #
def test_scope_all_is_downgraded_without_permission(client: TestClient, api: str) -> None:
    """越权不报错，降级成 mine，并把实际生效的 scope 写回响应。"""
    mine, headers = _make_user(client, ["usage:read"])
    other, _other_headers = _make_user(client, ["usage:read"])
    moment = datetime.now(UTC) - timedelta(days=1)

    _insert_video(
        author_id=mine["id"],
        author_name="我",
        status="completed",
        created_at=moment,
        duration_sec=5,
    )
    _insert_video(
        author_id=other["id"],
        author_name="别人",
        status="completed",
        created_at=moment,
        duration_sec=999,
    )

    data = (
        client.get(
            f"{api}/usage/overview", headers=headers, params={**_range(), "scope": "all"}
        ).json()["data"]
    )
    assert data["summary"]["scope"] == "mine"
    assert data["summary"]["total"] == 1
    assert data["summary"]["durationSec"] == 5


def test_scope_all_with_permission_sees_everyone(client: TestClient, api: str) -> None:
    mine, headers = _make_user(client, ["usage:read", "usage:read:all"])
    other, _other_headers = _make_user(client, ["usage:read"])
    moment = datetime.now(UTC) - timedelta(days=1)

    _insert_video(
        author_id=mine["id"],
        author_name="我",
        status="completed",
        created_at=moment,
        duration_sec=5,
    )
    _insert_video(
        author_id=other["id"],
        author_name="别人",
        status="completed",
        created_at=moment,
        duration_sec=999,
    )

    mine_only = (
        client.get(
            f"{api}/usage/overview", headers=headers, params={**_range(), "scope": "mine"}
        ).json()["data"]
    )
    data = (
        client.get(
            f"{api}/usage/overview", headers=headers, params={**_range(), "scope": "all"}
        ).json()["data"]
    )
    assert data["summary"]["scope"] == "all"
    # 「全部」是**别人的用量也算进来**，不是把自己的数字换个标签 ——
    # 所以断言用相对关系：比只看自己时多，而且别人的使用人出现在构成里。
    assert data["summary"]["total"] > mine_only["summary"]["total"]
    assert data["summary"]["durationSec"] >= mine_only["summary"]["durationSec"] + 999

    owners = {row["key"] for row in data["breakdown"] if row["dimension"] == "owner"}
    mine_owners = {row["key"] for row in mine_only["breakdown"] if row["dimension"] == "owner"}
    assert other["id"] in owners
    assert other["id"] not in mine_owners


# --------------------------------------------------------------------------- #
# 趋势与构成
# --------------------------------------------------------------------------- #
def test_trend_fills_empty_days(client: TestClient, api: str) -> None:
    """没有数据的那天也要有桶，否则折线会「跳过」那天，看起来像没生成过。"""
    user, headers = _make_user(client, ["usage:read"])
    moment = datetime.now(UTC) - timedelta(days=2)
    _insert_video(
        author_id=user["id"],
        author_name="我",
        status="completed",
        created_at=moment,
        duration_sec=5,
    )

    data = client.get(f"{api}/usage/overview", headers=headers, params=_range()).json()["data"]
    buckets = [point["bucket"] for point in data["trend"]]
    assert len(buckets) == 30
    assert buckets == sorted(buckets)
    assert (TODAY - timedelta(days=1)).isoformat() in buckets


def test_breakdown_covers_four_dimensions(client: TestClient, api: str) -> None:
    user, headers = _make_user(client, ["usage:read"])
    moment = datetime.now(UTC) - timedelta(days=1)
    _insert_video(
        author_id=user["id"],
        author_name="我",
        status="completed",
        created_at=moment,
        duration_sec=5,
    )
    _insert_image(owner_id=user["id"], status="failed", created_at=moment, count=2)

    data = client.get(f"{api}/usage/overview", headers=headers, params=_range()).json()["data"]
    dimensions = {row["dimension"] for row in data["breakdown"]}
    assert dimensions == {"model", "kind", "owner", "status"}
    for row in data["breakdown"]:
        assert 0 <= row["share"] <= 1


# --------------------------------------------------------------------------- #
# 明细
# --------------------------------------------------------------------------- #
def test_records_merge_sources_and_paginate(client: TestClient, api: str) -> None:
    user, headers = _make_user(client, ["usage:read"])
    base = datetime.now(UTC) - timedelta(days=1)

    for index in range(3):
        _insert_video(
            author_id=user["id"],
            author_name="我",
            status="completed",
            created_at=base - timedelta(minutes=index),
            duration_sec=5,
            title=f"视频 {index}",
        )
    _insert_image(owner_id=user["id"], status="succeeded", created_at=base, count=1, name="生图 0")

    params = {**_range(), "pageSize": 2}
    first = client.get(f"{api}/usage/records", headers=headers, params=params).json()["data"]
    assert first["total"] == 4
    assert len(first["items"]) == 2

    second = (
        client.get(
            f"{api}/usage/records", headers=headers, params={**params, "page": 2}
        ).json()["data"]
    )
    assert len(second["items"]) == 2

    # 两页不能重复：合并后按时间倒序切页，页码错乱会漏记录或重复记录
    ids_first = {item["id"] for item in first["items"]}
    ids_second = {item["id"] for item in second["items"]}
    assert not (ids_first & ids_second)

    # 明细按时间倒序
    times = [item["createdAt"] for item in first["items"] + second["items"]]
    assert times == sorted(times, reverse=True)

    sources = {item["source"] for item in first["items"] + second["items"]}
    assert sources == {"video", "image"}


# --------------------------------------------------------------------------- #
# 没接数据源时的中性值
# --------------------------------------------------------------------------- #
def test_overview_without_providers_is_neutral(client: TestClient, api: str, no_providers) -> None:
    """没接来源时是**空看板**，不是「用量为零」的假象 —— sources 为空即是信号。"""
    user, headers = _make_user(client, ["usage:read"])
    data = client.get(f"{api}/usage/overview", headers=headers, params=_range()).json()["data"]
    assert data["sources"] == []
    assert data["summary"]["total"] == 0
    assert data["summary"]["successRate"] == 0
    # 趋势照样把区间补满：前端画图不用特判「没来源」
    assert len(data["trend"]) == 30
