"""生图记录删除：`DELETE /images`（2026-11-15）。

用户拍板「直接让生图任务也能删」——删除即放弃重试，失败记录上的 `external_id`
也不再是核对凭证。这里守四条：

1. 有 `image:delete` 才能删（viewer 只有 `image:read`，必须 403）；
2. 数据范围仍按 `image:read:all` 判：没有这个开关时，别人的记录进 `skipped`
   而不是整批 403（一条越权不该让其余九条白删）；
3. 不存在的 id 进 `skipped`，接口不 500；
4. 删掉的记录真的没了（不是软删、也不残留在列表里）。
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from db.session import get_session_factory
from modules.image.models import ImageJob

API = "/api/v1"


def _login(client: TestClient, username: str, password: str) -> dict[str, str]:
    resp = client.post(f"{API}/identity/login", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['data']['accessToken']}"}


def _insert(**overrides: Any) -> str:
    """直接落一条生图记录：删除接口不关心它有没有产物。"""
    base: dict[str, Any] = {
        "name": "delete-probe",
        "prompt": "待删除的生图记录",
        "size": "1024x1024",
        "cover_url": "",
        "owner_id": "u_001",
    }
    base.update(overrides)
    job = ImageJob(**base)

    async def run() -> str:
        async with get_session_factory()() as session:
            session.add(job)
            await session.commit()
            return job.id

    return asyncio.run(run())


def _exists(job_id: str) -> bool:
    async def run() -> bool:
        async with get_session_factory()() as session:
            return await session.get(ImageJob, job_id) is not None

    return asyncio.run(run())


def _cleanup(ids: Iterator[str] | list[str]) -> None:
    async def run() -> None:
        async with get_session_factory()() as session:
            for job_id in ids:
                row = await session.get(ImageJob, job_id)
                if row is not None:
                    await session.delete(row)
            await session.commit()

    asyncio.run(run())


@pytest.fixture(autouse=True)
def _cleanup_after() -> Iterator[list[str]]:
    """用例造的记录用完就删：seed 只跑一次，残留会让「总数」类断言互相污染。"""
    made: list[str] = []
    yield made
    _cleanup(made)


def test_admin_deletes_own_record(client: TestClient, _cleanup_after: list[str]) -> None:
    admin = _login(client, "admin", "admin@12345")
    job_id = _insert(owner_id="u_001")
    _cleanup_after.append(job_id)

    resp = client.request(
        "DELETE", f"{API}/images", headers=admin, json={"ids": [job_id]}
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()["data"]
    assert (body["deleted"], body["skipped"]) == (1, [])
    assert not _exists(job_id), "删完还查得到，说明只是标了标记"


def test_viewer_without_delete_permission_is_forbidden(
    client: TestClient, _cleanup_after: list[str]
) -> None:
    """viewer 只有 `image:read`：连自己的记录也不能删，权限码就是这层意思。"""
    viewer = _login(client, "viewer", "viewer@12345")
    job_id = _insert(owner_id="u_003")
    _cleanup_after.append(job_id)

    resp = client.request(
        "DELETE", f"{API}/images", headers=viewer, json={"ids": [job_id]}
    )

    assert resp.status_code == 403
    assert _exists(job_id)


def test_editor_can_delete_others_because_of_resource_wildcard(
    client: TestClient, _cleanup_after: list[str]
) -> None:
    """editor 的 `image:*` 按资源前缀通配，**顺带覆盖了 `image:read:all`**。

    这不是这个用例要证明的新行为，而是既有口径（「内容编辑 = 内容生产全量 + 查看他人」），
    写下来是为了防止有人把 `image:*` 改成逐码枚举时无感知地收窄了 editor 的能力。
    """
    editor = _login(client, "editor", "editor@12345")
    others = _insert(owner_id="u_001")
    _cleanup_after.append(others)

    resp = client.request(
        "DELETE", f"{API}/images", headers=editor, json={"ids": [others]}
    )

    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["deleted"] == 1
    assert not _exists(others)


def test_skips_others_without_read_all(_cleanup_after: list[str]) -> None:
    """只有 `image:delete` + `image:read`（没有 `:read:all`）时，别人的记录进 skipped。

    内置角色里没有这种组合（editor 是 `image:*`、viewer 没有 delete），所以直接用
    合成主体调 service —— 这条断的是数据范围的判据本身，不是某个角色的配置。
    """
    from core.context import CurrentUser
    from modules.image.service import ImageService

    others = _insert(owner_id="u_master")
    mine = _insert(owner_id="u_007")
    _cleanup_after.extend([others, mine])
    actor = CurrentUser(
        id="u_007",
        name="小型编辑",
        permissions=frozenset({"image:read", "image:delete"}),
    )

    async def run() -> tuple[int, list[str]]:
        async with get_session_factory()() as session:
            return await ImageService(session).delete_jobs([mine, others], actor)

    deleted, skipped = asyncio.run(run())
    assert deleted == 1
    assert skipped == [others], "越权的那条要被如实报出来，不能静默吞掉"
    assert not _exists(mine)
    assert _exists(others)



def test_unknown_id_lands_in_skipped(client: TestClient) -> None:
    """不存在的 id 不 500：批量场景里「这条没删成」由调用方自己展示。"""
    admin = _login(client, "admin", "admin@12345")

    resp = client.request(
        "DELETE", f"{API}/images", headers=admin, json={"ids": ["not-a-real-id"]}
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()["data"]
    assert body["deleted"] == 0
    assert body["skipped"] == ["not-a-real-id"]
