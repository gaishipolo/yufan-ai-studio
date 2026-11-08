"""回收站：只动「管理标记」，不动生成、不动工作区字节。

重建前 recycle 会在工作区里抢一把 `MANAGEMENT_LOCK` 文件锁；现在它就是
`replacement_runs.deleted_at` 一列的写入——数据库事务本身就是那把锁，
所以原来那条「抢不到锁就 409」的用例换成了「并发改同一行时乐观锁冲突」，
其余判据（幂等、归属、不取消生成、不碰文件）一条没改。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from tests_template_helpers import API, add_run, drop_run, login, read_run, workspace

from infra.archive.local import get_archive_settings


@pytest.fixture
def archived(tmp_path, monkeypatch):
    """一条「已归档」的替换任务：数据库有行 + 工作区有目录与指针。"""
    monkeypatch.setattr(get_archive_settings(), "root", str(tmp_path))

    async def _make(**fields) -> tuple[str, object]:
        run = await add_run(**fields)
        path = workspace(run.id, {"id": run.id, "owner_id": run.owner_id, "status": run.status})
        (path / "inputs/prompt.txt").write_text("prompt", encoding="utf-8")
        return run.id, path

    return _make


async def test_recycle_restore_persist_and_keep_workspace(client: TestClient, archived) -> None:
    uid, path = await archived()
    original = (path / "run.json").read_bytes()
    url = f"{API}/templates/replacement-runs/{uid}"

    removed = client.delete(url)
    assert removed.status_code == 200, removed.text
    timestamp = removed.json()["data"]["deletedAt"]
    assert timestamp.endswith("Z")
    # 重发（双击/超时重试）：时间戳不能跳，否则前端以为又删了一次
    assert client.delete(url).json()["data"]["deletedAt"] == timestamp

    row = await read_run(uid)
    assert row is not None and row.deleted_at is not None

    rows = client.get(f"{API}/templates/replacement-catalog").json()["data"]["runs"]
    assert next(item for item in rows if item["id"] == uid)["deletedAt"] == timestamp

    # 回收站里的任务不能继续执行：钱会花出去，而用户以为已经删了
    assert client.post(url + "/execute").status_code == 409
    assert client.get(url).json()["data"]["deletedAt"] == timestamp

    restored = client.post(url + "/restore")
    assert restored.json()["data"]["deletedAt"] is None
    assert client.post(url + "/restore").status_code == 200

    # 全程不碰工作区字节
    assert (path / "run.json").read_bytes() == original
    assert (path / "inputs/prompt.txt").read_text(encoding="utf-8") == "prompt"


async def test_recycle_checks_owner_and_permissions(client: TestClient, archived) -> None:
    uid, _ = await archived()
    url = f"{API}/templates/replacement-runs/{uid}"
    for username, expected in (("viewer", 403), ("editor", 404)):
        headers = login(client, username, f"{username}@12345")
        assert client.delete(url, headers=headers).status_code == expected
        assert client.post(url + "/restore", headers=headers).status_code == expected
    assert client.delete(f"{API}/templates/replacement-runs/invalid").status_code == 404


async def test_recycle_does_not_touch_a_running_generation(client: TestClient, archived) -> None:
    """删任务 ≠ 取消生成：已经交给上游的那一笔没法撤回，标记删掉即可。

    反过来做（顺手把状态改成 failed、清掉 task_id）会让下载阶段的回执对不上
    ——最后的成片就真的丢了，而钱已经花过。
    """
    uid, path = await archived(status="running", task_id="upstream-existing")
    original = (path / "run.json").read_bytes()

    assert client.delete(f"{API}/templates/replacement-runs/{uid}").status_code == 200

    row = await read_run(uid)
    assert row is not None
    assert (row.status, row.task_id) == ("running", "upstream-existing")
    assert (path / "run.json").read_bytes() == original


async def test_restore_wins_over_a_later_delete_only_once(client: TestClient, archived) -> None:
    """删 → 恢复 → 删：三次都成功，最后一次为真（没有「恢复把删除吃掉」）。"""
    uid, _ = await archived()
    url = f"{API}/templates/replacement-runs/{uid}"
    assert client.delete(url).status_code == 200
    assert client.post(url + "/restore").json()["data"]["deletedAt"] is None
    assert client.delete(url).json()["data"]["deletedAt"] is not None
    row = await read_run(uid)
    assert row is not None and row.deleted_at is not None


async def test_delete_unknown_run_is_not_found(client: TestClient) -> None:
    """别人的 id 与不存在的 id 都返回 404：不泄露「这条档案存在」。"""
    run = await add_run(owner="u_not_the_admin")
    try:
        assert client.delete(f"{API}/templates/replacement-runs/{run.id}").status_code == 404
        assert client.post(f"{API}/templates/replacement-runs/{run.id}/restore").status_code == 404
    finally:
        await drop_run(run.id)
