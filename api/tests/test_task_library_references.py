"""视频任务的参考图删除保护：`video_tasks.reference_library_ids`（v2 资产 id）。

任务行存的是**资产库 v2 的 id**（前端先上传拿 id 再下发），所以保护挂在 v2 的
`purge` 上。以前这一列叫 `reference_asset_ids`，声明的却是 v1 的
`AssetReferenceChecker`——两套 id 空间对不上，等于从来没保护过：删掉一张被任务
引用的图，任务详情页的参考图直接裂，而 `purge` 不可逆。

两个方向各钉一条：**被引用时拦得住**（并说清是哪个任务），**引用解除后删得掉**
（「拦」不能变成「永远删不掉」）。
"""

from __future__ import annotations

import asyncio

from fastapi.testclient import TestClient
from library_support import blob_exists, upload_image


def _create_task(client: TestClient, api: str, title: str, library_ids: list[str]) -> dict:
    response = client.post(
        f"{api}/tasks",
        json={"prompt": title, "title": title, "referenceLibraryIds": library_ids},
    )
    assert response.status_code == 201, response.text
    return response.json()["data"]


def _delete_tasks(task_ids: list[str]) -> None:
    """用独立会话删任务行：「引用解除」这件事不该顺带依赖任务接口的可见性。"""

    async def run() -> None:
        from sqlalchemy import delete

        from db.session import get_session_factory
        from modules.task.models import VideoTask

        async with get_session_factory()() as session:
            await session.execute(delete(VideoTask).where(VideoTask.id.in_(task_ids)))
            await session.commit()

    asyncio.run(run())


def _trash(client: TestClient, api: str, asset_id: str) -> None:
    response = client.delete(f"{api}/library/assets/{asset_id}", headers={})
    assert response.status_code == 200, response.text


def test_purge_blocked_by_referencing_task(client: TestClient, api: str) -> None:
    asset = upload_image(client, api, {}, name="referenced.png", color="seagreen")
    task = _create_task(client, api, "圣诞活动主片", [asset["id"]])
    try:
        _trash(client, api, asset["id"])

        blocked = client.delete(f"{api}/library/trash/{asset['id']}", headers={})
        assert blocked.status_code == 409, blocked.text
        assert blocked.json()["code"] == 40986
        references = blocked.json()["details"]["references"]
        # 描述要能直接给人看：资源（视频任务）+ 标题 + id
        assert any("圣诞活动主片" in item for item in references), references
        assert any(task["id"] in item for item in references), references

        # 拦截不能是「删了一半」：行与字节都还在
        detail = client.get(f"{api}/library/assets/{asset['id']}", headers={})
        assert detail.status_code == 200, detail.text
        assert blob_exists(detail.json()["data"]["archive"]["storageKey"])
    finally:
        _delete_tasks([task["id"]])


def test_purge_allowed_after_task_removed(client: TestClient, api: str) -> None:
    """引用解除后必须能删——否则「拦」会变成「再也删不掉」。"""
    asset = upload_image(client, api, {}, name="released.png", color="khaki")
    task = _create_task(client, api, "已清理的任务", [asset["id"]])
    _delete_tasks([task["id"]])
    _trash(client, api, asset["id"])

    purged = client.delete(f"{api}/library/trash/{asset['id']}", headers={})
    assert purged.status_code == 200, purged.text
