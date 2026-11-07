"""素材引用检查的真实实现：`video_tasks.reference_asset_ids`。

注入口的契约（服务层确实问、描述原样透出）由 `test_asset_api` 用替身覆盖；
这里验的是接上真实数据之后**删除到底拦不拦**——那正是上一轮唯一靠「协议在、
实现空」蒙过去的地方：删掉一张被任务引用的图，任务详情页的参考图直接变空白。
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi.testclient import TestClient
from test_asset_api import png_bytes  # noqa: F401  同一套造图工具

from core.ports.assets import (
    has_asset_reference_checker,
    reference_source_count,
    reference_source_gaps,
    reset_asset_reference_checker,
)
from modules.task.models import VideoTask


def _create_task(title: str, asset_ids: list[str]) -> str:
    """直接写库造任务：任务接口要积分、要模型目录，这里只想造一条引用关系。"""

    async def run() -> str:
        from db.session import get_session_factory

        async with get_session_factory()() as session:
            task = VideoTask(
                title=title,
                prompt="雨夜霓虹街角",
                status="succeeded",
                model="Seedance 2.5",
                model_id="seedance-2.5",
                reference_asset_ids=asset_ids,
                author_id="u_001",
                author_name="老大",
            )
            session.add(task)
            await session.commit()
            return task.id

    return asyncio.run(run())


def _delete_tasks(task_ids: list[str]) -> None:
    """用独立会话删任务：素材的软清理不认「谁在引用」，留着会污染后续用例。"""

    async def run() -> None:
        from sqlalchemy import delete

        from db.session import get_session_factory

        async with get_session_factory()() as session:
            await session.execute(delete(VideoTask).where(VideoTask.id.in_(task_ids)))
            await session.commit()

    asyncio.run(run())


def _upload(client: TestClient, api: str, name: str) -> dict[str, Any]:
    response = client.post(
        f"{api}/assets/upload", content=png_bytes((20, 20), "orange"), params={"filename": name}
    )
    assert response.status_code == 201, response.text
    return response.json()["data"]


def test_checker_is_registered_at_startup(client: TestClient) -> None:
    """声明集合与注册集合零缺口，删除才被允许。

    旧断言只数「>= 2 个来源」：第三个来源漏登记时照样全绿，而失败方向是永久丢数据。
    """
    assert has_asset_reference_checker()
    assert reference_source_count() >= 2
    assert not reference_source_gaps(), reference_source_gaps()


def test_delete_blocked_by_referencing_task(client: TestClient, api: str) -> None:
    asset = _upload(client, api, "referenced.png")
    task_id = _create_task("圣诞活动主片", [asset["id"]])
    try:
        response = client.delete(f"{api}/assets/{asset['id']}")
        assert response.status_code == 409, response.text
        assert response.json()["code"] == 40951
        references = response.json()["details"]["references"]
        # 描述要能直接给人看：模块（视频任务）+ 资源（标题）+ id
        assert any("圣诞活动主片" in item for item in references), references
        assert any(task_id in item for item in references), references

        # 拦截不能是「删了一半」：库里的行与字节都还在
        still_there = client.get(f"{api}/assets/{asset['id']}")
        assert still_there.status_code == 200, still_there.text
    finally:
        _delete_tasks([task_id])
        client.delete(f"{api}/assets/{asset['id']}")


def test_delete_allowed_after_task_removed(client: TestClient, api: str) -> None:
    """引用解除后必须能删——否则「拦」会变成「再也删不掉」。"""
    asset = _upload(client, api, "released.png")
    task_id = _create_task("已清理的任务", [asset["id"]])
    _delete_tasks([task_id])

    response = client.delete(f"{api}/assets/{asset['id']}")

    assert response.status_code == 204, response.text


def test_unregistered_checker_refuses_delete(client: TestClient, api: str) -> None:
    """fail-closed：来源集合未知时删除必须 503，不能静默放行。

    这是唯一「中性默认会造成不可逆数据丢失」的注入口：空映射等于「没人引用、
    可以删」，漏登记一个来源就是永久裂图。宁可暂时删不掉，也不能猜。
    """
    asset = _upload(client, api, "no-checker.png")
    reset_asset_reference_checker()
    response = client.delete(f"{api}/assets/{asset['id']}")
    assert response.status_code == 503
    assert "引用检查不可用" in response.json()["message"]
