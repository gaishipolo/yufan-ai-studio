"""导出功能端到端：建记录 → worker 生成 → 下载 → 回收。

这些用例刻意**真的跑 worker**（不是 mock 掉生成步骤）：导出最容易错的地方
全在那条链上——入队与 commit 的顺序、CSV 的 BOM、存储 key 与 `local_path()`
是否对得上、过期后是 410 还是 404。mock 掉就一条都测不到。
"""

from __future__ import annotations

import asyncio
import tracemalloc
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
from modules.export.errors import ExportTooLarge
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


def test_audit_failure_does_not_break_the_accepted_export(
    client: TestClient, admin: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """审计失败不能让**已受理**的导出变成 500。

    到这一步记录已落库、活已排上队列；接口一旦报失败，用户会重试，于是多出
    一条重复的导出记录（`_reject_duplicate` 只拦 ACTIVE 状态，失败态不拦）。
    审计是旁路：它挂了要能查日志，但不能改写业务结果。
    """
    async def boom(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("审计库连不上")

    monkeypatch.setattr("core.audit.record", boom)

    resp = _create(client, admin)
    assert resp.status_code == 202, resp.text
    export_id = resp.json()["data"]["id"]

    detail = client.get(f"{API}/exports/{export_id}", headers=admin)
    assert detail.status_code == 200, detail.text
    assert detail.json()["data"]["status"] == "queued"


def test_audit_failure_does_not_block_the_download(
    client: TestClient, admin: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """下载同理：产物已经在磁盘上，审计写不进去不该让用户拿不到文件。"""
    export_id = _create(client, admin).json()["data"]["id"]
    _run(build_export(Job(name="export.build", payload={"id": export_id})))

    async def boom(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("审计库连不上")

    monkeypatch.setattr("core.audit.record", boom)

    downloaded = client.get(f"{API}/exports/{export_id}/download", headers=admin)
    assert downloaded.status_code == 200, downloaded.text
    assert downloaded.content.startswith(b"\xef\xbb\xbf")


def test_render_csv_keeps_peak_memory_close_to_the_output_size() -> None:
    """渲染时的峰值内存不该是产物的好几倍。

    旧写法（`StringIO` → 拼 BOM → `encode`）在构造过程中同时留着 str 缓冲、
    拼接后的 str、编码后的 bytes 三份副本，实测峰值约为产物的 **5.5 倍**；
    现在直接写二进制流，实测约 **1.1 倍**。

    worker 是共享进程（见 `core/config.py` 里 `export_max_rows` 的注释）：
    多出来的那几份全量副本够把同进程里别的模块的 job 一起拖下水。
    """
    rows = [[f"任务-{i}", "完成中", "model-x"] for i in range(20_000)]
    columns = ["id", "title", "status", "model"]

    # 先造行再开追踪：否则峰值里混着输入行本身，量出来的是「输入 + 渲染」
    tracemalloc.start()
    try:
        payload = render_csv(columns, rows)
        _current, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    assert peak <= 2 * len(payload), (
        f"峰值内存 {peak} 相对产物 {len(payload)} 偏大：说明渲染过程留了全量副本"
    )


def test_render_csv_fails_when_the_output_exceeds_the_byte_limit() -> None:
    """字节熔断：超限直接失败，不把内存吃穿。"""
    with pytest.raises(ExportTooLarge):
        render_csv(["id", "title"], [["x", "y"] * 100], max_bytes=16)


def test_worker_fails_the_export_when_the_output_is_too_large(
    client: TestClient, admin: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """超限时导出记录置失败，且给用户一句能照做的提示（不是内部异常）。"""
    from core.config import get_settings

    settings = get_settings().model_copy(update={"export_max_bytes": 64})
    monkeypatch.setattr("modules.export.worker.get_settings", lambda: settings)

    export_id = _create(client, admin).json()["data"]["id"]
    _run(build_export(Job(name="export.build", payload={"id": export_id})))

    row = _run(_fetch(export_id))
    assert row is not None
    assert row.status == ExportStatus.FAILED.value
    assert "过大" in (row.error or "")


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


def test_failed_export_does_not_leak_the_raw_exception(
    client: TestClient, admin: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """对外的 error 字段不能是原始异常。

    `str(exc)` 里可能有内部路径、SQL 片段、数据库连接串、上游地址——
    `ExportOut.error` 会随列表/详情接口返回前端，等于开了一条信息泄露通道
    （见 REVIEW_2026-11-23 重要#3）。完整 traceback 只进日志。
    """

    async def boom(*args: Any, **kwargs: Any) -> list[Any]:
        raise RuntimeError(
            "connection failed: postgres://app:secret@10.0.0.7:5432/prod SELECT * FROM task"
        )

    source = SimpleNamespace(columns=("id",), fetch=boom)
    monkeypatch.setattr("modules.export.worker.lookup_source", lambda name: source)

    export_id = _create(client, admin).json()["data"]["id"]
    _run(build_export(Job(name="export.build", payload={"id": export_id})))

    row = _run(_fetch(export_id))
    assert row is not None
    assert row.status == ExportStatus.FAILED.value
    error = row.error or ""
    assert error, "失败必须给出原因"
    leaks = ("postgres://", "secret", "10.0.0.7", "SELECT", "RuntimeError", "connection failed")
    for leak in leaks:
        assert leak not in error, f"对外错误信息泄露了内部细节：{error}"


def test_failed_export_maps_known_errors_to_user_wording(
    client: TestClient, admin: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """已知异常翻译成对应的用户文案，而不是一律兜底。"""
    from core.exceptions import Forbidden

    async def boom(*args: Any, **kwargs: Any) -> list[Any]:
        raise Forbidden("用户 u_1 没有导出权限")

    source = SimpleNamespace(columns=("id",), fetch=boom)
    monkeypatch.setattr("modules.export.worker.lookup_source", lambda name: source)

    export_id = _create(client, admin).json()["data"]["id"]
    _run(build_export(Job(name="export.build", payload={"id": export_id})))

    row = _run(_fetch(export_id))
    assert row is not None
    assert row.status == ExportStatus.FAILED.value
    error = row.error or ""
    assert "权限" in error
    assert "u_1" not in error, "对外文案不应带出内部标识"


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


def test_csv_escapes_a_leading_dash_but_keeps_negative_numbers() -> None:
    """`-` 也是公式触发字符，但不能把负数改成文本。

    `-2+3+cmd|' /C calc'!A0` 会被 Excel 当公式执行；而 "-2" 是合法业务值，
    一律加前缀就成了数据损坏（见 REVIEW_2026-11-23 重要#4）。
    """
    payload = render_csv(
        ("v",),
        [
            ["-2+3+cmd|' /C calc'!A0"],
            ["-cmd|'/c calc'!A0"],
            ["-2"],
            ["-1.5"],
            ["- 3"],
            ["-1e3"],
        ],
    )
    lines = payload.decode("utf-8-sig").splitlines()
    assert lines[1] == "'-2+3+cmd|' /C calc'!A0"
    assert lines[2] == "'-cmd|'/c calc'!A0"
    # 负数原样输出，不能被改成文本
    assert lines[3] == "-2"
    assert lines[4] == "-1.5"
    assert lines[5] == "- 3"
    assert lines[6] == "-1e3"


def test_csv_renders_none_bool_datetime() -> None:
    from datetime import UTC, datetime

    payload = render_csv(
        ("a", "b", "c"), [[None, True, datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)]]
    )
    assert payload.decode("utf-8-sig").splitlines()[1] == ",true,2026-01-02T03:04:05+00:00"


def test_fail_stale_judges_a_running_export_by_when_it_started() -> None:
    """巡检判「卡死」要按**开始时间**，不是创建时间。

    一小时前创建、**刚刚**被 worker 接手的导出不是卡死的 —— 它正在好好地跑。
    原来两种状态都看创建时间，于是这种导出会被巡检置成 failed，worker 干完
    又写 succeeded：用户先看到「失败」再看到「成功」，而产物其实早就好了。
    """
    from db.repository import BaseRepository
    from modules.export.service import ExportService

    async def make() -> tuple[str, str]:
        factory = get_session_factory()
        async with factory() as session:
            old = Export(
                data_source="tasks",
                params={},
                status=ExportStatus.RUNNING.value,
                requested_by="u_001",
            )
            # 创建于很久以前
            old.created_at = utcnow() - timedelta(hours=5)
            fresh = Export(
                data_source="tasks",
                params={},
                status=ExportStatus.RUNNING.value,
                requested_by="u_001",
            )
            fresh.created_at = utcnow() - timedelta(hours=5)
            # 但这一条**刚开始跑**
            fresh.started_at = utcnow()
            session.add_all([old, fresh])
            await session.commit()
            return old.id, fresh.id

    old_id, fresh_id = asyncio.run(make())

    async def sweep() -> int:
        factory = get_session_factory()
        async with factory() as session:
            return await ExportService(session, BaseRepository(session, Export)).fail_stale(
                older_than_sec=3600
            )

    asyncio.run(sweep())

    async def statuses() -> dict[str, str]:
        factory = get_session_factory()
        async with factory() as session:
            rows = (
                await session.execute(
                    select(Export).where(Export.id.in_([old_id, fresh_id]))
                )
            ).scalars().all()
            return {row.id: row.status for row in rows}

    result = asyncio.run(statuses())
    assert result[old_id] == ExportStatus.FAILED.value
    assert result[fresh_id] == ExportStatus.RUNNING.value
