"""导出功能端到端：建记录 → worker 生成 → 下载 → 回收。

这些用例刻意**真的跑 worker**（不是 mock 掉生成步骤）：导出最容易错的地方
全在那条链上——入队与 commit 的顺序、CSV 的 BOM、存储 key 与 `local_path()`
是否对得上、过期后是 410 还是 404。mock 掉就一条都测不到。
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from core.exceptions import Forbidden
from db.base import utcnow
from db.session import get_session_factory
from infra.queue.base import Job
from modules.export.enums import ExportStatus
from modules.export.models import Export
from modules.export.sources import render_csv, resolve_source
from modules.export.worker import build_export

API = "/api/v1"


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _clean_exports() -> Any:
    """每个用例前清空导出表：判重、列表断言都依赖「只有本次的记录」。"""

    async def wipe() -> None:
        async with get_session_factory()() as session:
            await session.execute(delete(Export))
            await session.commit()

    _run(wipe())
    yield


@pytest.fixture
def admin(client: TestClient) -> dict[str, str]:
    login = client.post(
        f"{API}/identity/login", json={"username": "admin", "password": "admin@12345"}
    )
    assert login.status_code == 200, login.text
    return {"Authorization": f"Bearer {login.json()['data']['accessToken']}"}


def _create(client: TestClient, admin: dict[str, str], **body: Any) -> Any:
    payload = {"dataSource": "tasks", **body}
    return client.post(f"{API}/exports", headers=admin, json=payload)


# --------------------------------------------------------------------------- #
# 受理
# --------------------------------------------------------------------------- #
def test_create_returns_202_and_is_queued(
    client: TestClient, admin: dict[str, str]
) -> None:
    """202 而不是 201：这一刻只有排队记录，文件还不存在。"""
    resp = _create(client, admin)
    assert resp.status_code == 202, resp.text
    data = resp.json()["data"]
    assert data["status"] == "queued"
    assert data["dataSource"] == "tasks"
    assert data["rowCount"] == 0


def test_same_params_twice_is_409(client: TestClient, admin: dict[str, str]) -> None:
    assert _create(client, admin).status_code == 202
    second = _create(client, admin)
    assert second.status_code == 409, second.text
    assert second.json()["code"] == 40991  # slot 9, seq 1


def test_different_params_are_not_duplicates(
    client: TestClient, admin: dict[str, str]
) -> None:
    assert _create(client, admin, params={"status": "succeeded"}).status_code == 202
    assert _create(client, admin, params={"status": "failed"}).status_code == 202


def test_unknown_source_is_400(client: TestClient, admin: dict[str, str]) -> None:
    resp = client.post(
        f"{API}/exports", headers=admin, json={"dataSource": "not_a_source"}
    )
    # 枚举校验先拦下来（422），说明契约层面就不允许乱传
    assert resp.status_code in (400, 422), resp.text


def test_audit_source_needs_its_own_permission() -> None:
    """`audit_events` 含他人行为，只有 `export:read:audit` 能导。

    直接调 `resolve_source`：走 HTTP 需要一个恰好没有该权限的账号，
    那会把这条用例变成「身份模块的测试」。
    """
    plain = SimpleNamespace(has=lambda code: code == "export:read")
    with pytest.raises(Forbidden):
        resolve_source("audit_events", plain)  # type: ignore[arg-type]

    auditor = SimpleNamespace(has=lambda code: True)
    assert resolve_source("audit_events", auditor).name == "audit_events"  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# 生成与下载
# --------------------------------------------------------------------------- #
def test_worker_builds_csv_and_download_returns_it(
    client: TestClient, admin: dict[str, str]
) -> None:
    created = _create(client, admin).json()["data"]
    export_id = created["id"]

    # 还没生成时下载必须是 409，不能是 500 或空文件
    early = client.get(f"{API}/exports/{export_id}/download", headers=admin)
    assert early.status_code == 409, early.text

    _run(build_export(Job(name="export.build", payload={"id": export_id})))

    detail = client.get(f"{API}/exports/{export_id}", headers=admin).json()["data"]
    assert detail["status"] == "succeeded"
    assert detail["downloadable"] is True
    assert detail["byteSize"] > 0
    assert detail["expiresAt"] is not None

    downloaded = client.get(f"{API}/exports/{export_id}/download", headers=admin)
    assert downloaded.status_code == 200, downloaded.text
    body = downloaded.content
    # BOM：Excel 打开中文 CSV 不乱码的唯一可靠办法
    assert body.startswith(b"\xef\xbb\xbf")
    header = body.decode("utf-8-sig").splitlines()[0]
    assert header.startswith("id,title,status,model")


def test_worker_is_idempotent(client: TestClient, admin: dict[str, str]) -> None:
    """重复执行同一个 job 不该把已成功的记录改回 running。"""
    export_id = _create(client, admin).json()["data"]["id"]
    job = Job(name="export.build", payload={"id": export_id})

    _run(build_export(job))
    _run(build_export(job))  # 第二次应直接返回

    row = _run(_fetch(export_id))
    assert row is not None
    assert row.status == ExportStatus.SUCCEEDED.value


def test_download_after_expiry_is_410(client: TestClient, admin: dict[str, str]) -> None:
    """记录还在、文件没了 → 410（不是 404，也不是 500）。"""
    export_id = _create(client, admin).json()["data"]["id"]
    _run(build_export(Job(name="export.build", payload={"id": export_id})))

    _run(_patch(export_id, status=ExportStatus.EXPIRED.value))
    resp = client.get(f"{API}/exports/{export_id}/download", headers=admin)
    assert resp.status_code == 410, resp.text
    assert resp.json()["code"] == 41091


async def _fetch(export_id: str) -> Export | None:
    async with get_session_factory()() as session:
        return (
            await session.execute(select(Export).where(Export.id == export_id))
        ).scalar_one_or_none()


async def _patch(export_id: str, **fields: Any) -> None:
    async with get_session_factory()() as session:
        row = (
            await session.execute(select(Export).where(Export.id == export_id))
        ).scalar_one()
        for key, value in fields.items():
            setattr(row, key, value)
        await session.commit()


# --------------------------------------------------------------------------- #
# 列表与数据范围
# --------------------------------------------------------------------------- #
def test_list_only_returns_own_records(
    client: TestClient, admin: dict[str, str]
) -> None:
    _create(client, admin)

    async def make_foreign() -> None:
        async with get_session_factory()() as session:
            session.add(
                Export(
                    data_source="tasks",
                    params={},
                    status=ExportStatus.QUEUED.value,
                    requested_by="someone-else",
                )
            )
            await session.commit()

    _run(make_foreign())

    listed = client.get(f"{API}/exports", headers=admin).json()["data"]
    assert listed["total"] == 1
    assert all(item["requestedBy"] != "someone-else" for item in listed["items"])


def test_detail_of_others_is_404(client: TestClient, admin: dict[str, str]) -> None:
    """别人的导出给 404 而不是 403：403 等于确认「这条 id 是真的」。"""
    async def make_foreign() -> None:
        async with get_session_factory()() as session:
            session.add(
                Export(
                    data_source="tasks",
                    params={},
                    status=ExportStatus.SUCCEEDED.value,
                    requested_by="someone-else",
                )
            )
            await session.commit()

    _run(make_foreign())
    row = _run(_first())
    assert row is not None
    resp = client.get(f"{API}/exports/{row.id}", headers=admin)
    assert resp.status_code == 404, resp.text


async def _first() -> Export | None:
    async with get_session_factory()() as session:
        return (await session.execute(select(Export))).scalars().first()


# --------------------------------------------------------------------------- #
# 删除与回收
# --------------------------------------------------------------------------- #
def test_delete_running_is_rejected(
    client: TestClient, admin: dict[str, str]
) -> None:
    export_id = _create(client, admin).json()["data"]["id"]
    _run(_patch(export_id, status=ExportStatus.RUNNING.value))

    resp = client.delete(f"{API}/exports/{export_id}", headers=admin)
    assert resp.status_code == 409, resp.text


def test_delete_removes_record_and_bytes(
    client: TestClient, admin: dict[str, str]
) -> None:
    export_id = _create(client, admin).json()["data"]["id"]
    _run(build_export(Job(name="export.build", payload={"id": export_id})))
    row = _run(_fetch(export_id))
    assert row is not None and row.object_key

    from infra import get_storage

    path = get_storage().local_path(row.object_key)
    assert path is not None and path.exists()

    resp = client.delete(f"{API}/exports/{export_id}", headers=admin)
    assert resp.status_code == 204, resp.text
    assert not path.exists()
    assert _run(_fetch(export_id)) is None


def test_reclaim_marks_expired_and_keeps_row(
    client: TestClient, admin: dict[str, str]
) -> None:
    """到期回收只删字节，记录行保留（状态转 expired）。"""
    export_id = _create(client, admin).json()["data"]["id"]
    _run(build_export(Job(name="export.build", payload={"id": export_id})))
    # 把到期时间挪到过去
    _run(_patch(export_id, expires_at=utcnow() - timedelta(hours=1)))

    from db.repository import BaseRepository
    from modules.export.service import ExportService

    async def reclaim() -> dict[str, int]:
        async with get_session_factory()() as session:
            service = ExportService(session, BaseRepository(session, Export))
            return await service.reclaim_expired()

    stats = _run(reclaim())
    assert stats["reclaimed"] == 1

    row = _run(_fetch(export_id))
    assert row is not None, "记录行不能删：用户要能查到「上周导过什么」"
    assert row.status == ExportStatus.EXPIRED.value
    assert row.object_key is None


# --------------------------------------------------------------------------- #
# CSV 细节（单元）
# --------------------------------------------------------------------------- #
def test_csv_keeps_bom_and_escapes_formula() -> None:
    """公式注入：标题写成 `=cmd|...` 时，Excel 打开会执行。"""
    payload = render_csv(("title",), [["=cmd|'/c calc'!A1"], ["普通标题"]])
    text = payload.decode("utf-8-sig")
    lines = text.splitlines()
    assert lines[1] == "'=cmd|'/c calc'!A1"
    assert lines[2] == "普通标题"


def test_csv_renders_none_bool_datetime() -> None:
    from datetime import UTC, datetime

    payload = render_csv(
        ("a", "b", "c"), [[None, True, datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)]]
    )
    assert payload.decode("utf-8-sig").splitlines()[1] == ",true,2026-01-02T03:04:05+00:00"
