"""单账号「在途任务上限」：一个人攒几十条会把队列顶满。

为什么要这道闸门：后台只有 `WORKER_CONCURRENCY` 个槽位（默认 2），队列是
**FIFO**（先到先跑），而 `task.create` 的限流只拦**频率**（每分钟几条）。
没有上限时，一个人连点三十条就足以把所有人的任务推到十几分钟之后，而界面上
每一条都显示「已提交」—— 受害者（和他自己）都看不出发生了什么。

判据与前端「还要不要轮询」同一口径：**queued + running 才算在途**，
completed / failed 立刻释放名额。

用例不假设库里干净：先探出当前在途数，再按「基线 + N」设上限。这样它跟同一
session 里别的用例造出来的任务互不干扰。
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi.testclient import TestClient

TASK_MODEL = "libtv-wan3-replacement"


# --------------------------------------------------------------- 直连数据库的小工具


def _author_of(task_id: str) -> str:
    async def run() -> str:
        from db.session import get_session_factory
        from modules.task.models import VideoTask

        async with get_session_factory()() as session:
            row = await session.get(VideoTask, task_id)
            assert row is not None, f"任务不存在：{task_id}"
            return row.author_id

    return asyncio.run(run())


def _in_flight(author_id: str) -> int:
    """走仓储的同一条判据，不另写一份 SQL —— 否则这条用例守的是我抄的那份。"""

    async def run() -> int:
        from db.session import get_session_factory
        from modules.task.repository import TaskRepository

        async with get_session_factory()() as session:
            return await TaskRepository(session).count_in_flight(author_id)

    return asyncio.run(run())


def _set_status(task_id: str, status: str) -> None:
    async def run() -> None:
        from db.session import get_session_factory
        from modules.task.models import VideoTask

        async with get_session_factory()() as session:
            row = await session.get(VideoTask, task_id)
            assert row is not None
            row.status = status
            await session.commit()

    asyncio.run(run())


def _create(client: TestClient, api: str, title: str) -> Any:
    return client.post(
        f"{api}/tasks",
        json={"prompt": title, "title": title, "model": TASK_MODEL},
    )


def _cap(monkeypatch: Any, value: int) -> None:
    """改的是 `get_settings()` 那个单例上的字段：服务里每次调用都现读。"""
    from core.config import get_settings

    monkeypatch.setattr(get_settings(), "task_max_in_flight_per_user", value)


# -------------------------------------------------------------------------- 用例


def test_达到上限后多出来的一条被拒(client: TestClient, api: str, monkeypatch: Any) -> None:
    _cap(monkeypatch, 0)  # 先不限：探出这个账号已经积了多少条
    first = _create(client, api, "在途-探底")
    assert first.status_code == 201, first.text
    author = _author_of(first.json()["data"]["id"])
    baseline = _in_flight(author)

    _cap(monkeypatch, baseline + 1)
    second = _create(client, api, "在途-卡到上限")
    assert second.status_code == 201, second.text  # 正好等于上限，仍然放行

    third = _create(client, api, "在途-超出")
    assert third.status_code == 429, third.text
    message = third.json()["message"]
    # 消息要能自己解释「为什么被拒、怎么才能继续」：
    # 只说「请求过于频繁」会让人以为是网络问题去重试，越重试越堵。
    assert f"上限 {baseline + 1} 条" in message
    assert "跑完" in message


def test_上限为0表示不限(client: TestClient, api: str, monkeypatch: Any) -> None:
    _cap(monkeypatch, 0)
    for index in range(3):
        response = _create(client, api, f"不限-{index}")
        assert response.status_code == 201, response.text


def test_一条跑完就能再提交(client: TestClient, api: str, monkeypatch: Any) -> None:
    """名额随终态释放 —— 否则「上限」会变成「这辈子只能提交 N 条」。"""
    _cap(monkeypatch, 0)
    probe = _create(client, api, "释放-探底")
    assert probe.status_code == 201, probe.text
    probe_id = probe.json()["data"]["id"]
    author = _author_of(probe_id)
    baseline = _in_flight(author)

    _cap(monkeypatch, baseline)
    blocked = _create(client, api, "释放-已满")
    assert blocked.status_code == 429, blocked.text

    _set_status(probe_id, "completed")
    allowed = _create(client, api, "释放-腾出名额后")
    assert allowed.status_code == 201, allowed.text


def test_重试同样受上限约束(client: TestClient, api: str, monkeypatch: Any) -> None:
    """重试也是往队列里加一条 —— 只堵 create 等于堵了一半。"""
    _cap(monkeypatch, 0)
    task = _create(client, api, "重试-造一条失败的")
    assert task.status_code == 201, task.text
    task_id = task.json()["data"]["id"]
    _set_status(task_id, "failed")
    author = _author_of(task_id)

    _cap(monkeypatch, _in_flight(author))
    response = client.post(f"{api}/tasks/{task_id}/retry")
    assert response.status_code == 429, response.text
    assert "上限" in response.json()["message"]
