"""替换任务的两道后台闸门。

这两条都不是「代码写得对不对」，而是「出事的时候钱会不会继续花」：发起人被停用后
队列还在替他跑、上游一直忙就无限重试。测试钉的就是这两个边界。

重建后闸门的**位置**变了（旧写在 `replacement_worker`，现在在 `worker.py`，
状态从 `run.json` 换到行上），判据一字没改：

- 退避 30s 起、翻倍、封顶 10 分钟，第 6 次转人工；
- 停用只挡**新提交**：已经拿到上游 `task_id` 的继续轮询与下载（那笔钱已经花出去）。
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from tests_template_helpers import (
    ADMIN as ADMIN_ID,
)
from tests_template_helpers import (
    API,
    add_input,
    add_pack,
    add_run,
    drop_pack,
    drop_run,
    login,
    png_bytes,
    read_run,
    upload,
)

from db.session import get_session_factory
from infra.queue.base import Job
from modules.library.models import LibraryAsset
from modules.template import worker
from modules.template.authorization import authorization_gap
from modules.template.models import TemplatePack
from modules.template.repository import RunRepository

ADMIN = ("admin", "admin@12345")


def test_backoff_starts_small_and_caps() -> None:
    """退避必须两端都成立：起步别太急（打上游），上限别太大（用户等到以为卡死）。"""
    assert [worker.backoff_delay(n) for n in (1, 2, 3, 4)] == [30.0, 60.0, 120.0, 240.0]
    assert worker.backoff_delay(9) == 600.0  # 封顶
    assert worker.backoff_delay(0) == 30.0  # 计数异常时按第一次算，不返回 0


async def test_retry_is_bounded_and_lands_in_service_error() -> None:
    run = await add_run(status="uploaded")
    try:
        delays = []
        for _ in range(worker.MAX_RETRIES):
            async with get_session_factory()() as session:
                delay = await worker._bump_retry(session, run.id, reason="执行器持续被占用")
            assert delay is not None
            delays.append(delay)
        assert delays == [30.0, 60.0, 120.0, 240.0, 480.0]

        # 第 6 次不再排队：返回 None 即为「别再 enqueue」，run 转人工
        async with get_session_factory()() as session:
            assert await worker._bump_retry(session, run.id, reason="执行器持续被占用") is None

        saved = await read_run(run.id)
        assert saved is not None
        assert saved.status == "service_error"
        assert saved.error_code == "RETRY_EXHAUSTED"
        assert saved.retry_count == worker.MAX_RETRIES + 1
        assert "转人工" in (saved.last_error or "")
    finally:
        await drop_run(run.id)


async def _disabled_owner(client: TestClient) -> str:
    """建一个账号再删掉：`owner_disabled` 只认库里真实存在的账号。"""
    suffix = uuid.uuid4().hex[:8]
    admin = login(client, *ADMIN)
    role = client.post(
        f"{API}/identity/roles",
        json={"code": f"guard{suffix}", "name": "guard", "permissions": []},
        headers=admin,
    )
    assert role.status_code == 201, role.text
    created = client.post(
        f"{API}/identity/users",
        json={
            "username": f"guard_owner_{suffix}",
            "password": "pass-12345",
            "displayName": "guard",
            "roleIds": [role.json()["data"]["id"]],
        },
        headers=admin,
    )
    assert created.status_code == 201, created.text
    user_id = created.json()["data"]["id"]

    assert await worker.owner_disabled(user_id) is False
    assert client.delete(f"{API}/identity/users/{user_id}", headers=admin).status_code == 200
    assert await worker.owner_disabled(user_id) is True

    # 历史档案里的合成 owner / 已删除的 id：不拦（否则老数据全部卡死）
    assert await worker.owner_disabled("u_that_never_existed") is False
    return user_id


async def test_disabled_owner_blocks_new_submission(client: TestClient) -> None:
    """停用账号之后，排在队列里的「还没提交」不能继续：下一步就是花钱。"""
    user_id = await _disabled_owner(client)
    run = await add_run(owner=user_id, status="prepared")
    try:
        job = Job(name="template.replacement.prepare", payload={"id": run.id})
        await worker.prepare_upload(job)
        saved = await read_run(run.id)
        assert saved is not None
        assert saved.status == "service_error"
        assert saved.error_code == "OWNER_DISABLED"
        assert "停用" in (saved.last_error or "")
    finally:
        await drop_run(run.id)


async def test_disabled_owner_does_not_stop_an_already_submitted_run(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """已经拿到上游任务号的不拦：那笔钱已经花出去了，停在这里成片就白买。

    这一条是「停用」与「取消」的分界：停用是别再花新钱，不是把已付的丢掉。
    """
    user_id = await _disabled_owner(client)
    run = await add_run(owner=user_id, status="running", task_id="upstream-existing")
    resumed: list[str] = []

    class _Runner:
        async def resume(self, kind: str, run_id: str) -> None:
            resumed.append(f"{kind}:{run_id}")

    monkeypatch.setattr(worker, "get_runner", lambda: _Runner())
    try:
        job = Job(name="template.replacement.prepare", payload={"id": run.id})
        await worker.prepare_upload(job)
        saved = await read_run(run.id)
        assert saved is not None
        assert saved.status == "running", "已提交的任务不该被停用改成终态"
        assert resumed == [f"template_replacement:{run.id}"]
    finally:
        await drop_run(run.id)


async def test_submitted_run_is_never_resubmitted(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """「提交中/提交结果未知」一律 return：重提交等于第二次计费。

    这条和 `reconfirm` 出口是同一件事的两面：后台不能自己放行，必须由人拍板。
    """
    calls: list[str] = []

    async def fake_stage(run_id: str, stage: str):
        calls.append(stage)
        raise AssertionError("不该走到执行器")

    monkeypatch.setattr(worker, "execute_stage", fake_stage)
    for status, attempts in (("submitting", 1), ("submission_unknown", 1), ("uploaded", 1)):
        run = await add_run(status=status, submit_attempts=attempts)
        try:
            await worker.prepare_upload(
                Job(name="template.replacement.prepare", payload={"id": run.id})
            )
            saved = await read_run(run.id)
            assert saved is not None and saved.status == status, "状态不能被后台悄悄推进"
        finally:
            await drop_run(run.id)
    assert calls == []


async def test_sync_roles_backfills_only_missing_permission(client: TestClient) -> None:
    """上线带新权限点的模块时，老库里的 editor 不会自动长出新权限。

    症状是「超管测都正常，非超管点提交 403」。`sync-roles` 只补差集，不删任何
    手工配置——所以这个用例同时钉住「补齐」与「幂等」两件事。
    """
    from modules.identity.repository import RoleRepository
    from modules.identity.service import IdentityService

    admin = login(client, *ADMIN)
    roles = client.get(f"{API}/identity/roles", headers=admin).json()["data"]
    editor = next(role for role in roles if role["code"] == "editor")
    assert "template:use" in editor["permissions"]  # 新库：声明即生效

    async with get_session_factory()() as session:
        await RoleRepository(session).set_permissions(
            editor["id"], [code for code in editor["permissions"] if code != "template:use"]
        )
        await session.commit()

    suffix = uuid.uuid4().hex[:6]
    resp = client.post(
        f"{API}/identity/users",
        json={
            "username": f"guard_editor_{suffix}",
            "password": "pass-12345",
            "displayName": "editor",
            "roleIds": [editor["id"]],
        },
        headers=admin,
    )
    assert resp.status_code == 201, resp.text
    headers = login(client, f"guard_editor_{suffix}", "pass-12345")

    submit = f"{API}/templates/replacement-runs"
    assert client.post(submit, json={}, headers=headers).status_code == 403

    async with get_session_factory()() as session:
        added = await IdentityService(session).sync_builtin_role_permissions()
    assert added.get("editor") == ["template:use"]

    # 补齐后同一张令牌就能过权限检查，落到参数校验（422）
    assert client.post(submit, json={}, headers=headers).status_code == 422

    async with get_session_factory()() as session:
        assert await IdentityService(session).sync_builtin_role_permissions() == {}


async def test_disabled_pack_stops_the_queue_before_upload(client: TestClient) -> None:
    """排队期间封装被停用：作业必须在 upload 之前停下，而不是照常提交。

    这是「最终授权」的第三次调用点。排队前 `execute()` 已查过一次，但 upload
    阶段超时 1500 秒，期间封装被停用完全来得及。旧实现把这道闸放在执行器的
    submit 里（窗口为零），搬到业务层时如果只在排队前查，等于把窗口拉长到几分钟。
    """
    login(client, "admin", "admin@12345")
    pack = await add_pack(
        owner=ADMIN_ID, kind="character", name="人物", status="published", version=1
    )
    run = await add_run(pack_id=pack.id, pack_version=1, status="uploaded")
    try:
        async with get_session_factory()() as session:
            row = await session.get(TemplatePack, pack.id)
            assert row is not None
            row.status = "disabled"
            await session.commit()

        await worker.prepare_upload(
            Job(name="template.replacement.prepare", payload={"id": run.id})
        )

        saved = await read_run(run.id)
        assert saved is not None
        assert saved.status == "service_error"
        assert saved.error_code == "AUTHORIZATION_REVOKED"
        assert "停用" in (saved.last_error or "")
    finally:
        await drop_run(run.id)
        await drop_pack(pack.id)


async def test_trashed_input_asset_counts_as_gone(client: TestClient) -> None:
    """输入素材被移入回收站也要拦：只查「id 还在不在」永远查不到。

    `LibraryLookup.get_many` 不按生命周期过滤——行还在、`state` 已是 `trash`。
    按「键是否存在」判断会放行一份已经不存在的输入，等执行器 upload 阶段
    才以 ENOENT 的形式暴露，而那时任务已经排在付费队列里了。

    这里直接改库而不是走删除接口：接口那一步会被引用检查挡下（409），
    本用例要验的是「已经删了之后」的判断。
    """
    login(client, "admin", "admin@12345")
    image = upload(client, "character.png", "character", png_bytes())
    pack = await add_pack(
        owner=ADMIN_ID, kind="character", name="人物", reference_ids=[image["id"]]
    )
    run = await add_run(pack_id=pack.id, pack_version=pack.version, status="prepared")
    await add_input(run.id, image["id"], position=1)
    try:
        async with get_session_factory()() as session:
            row = await session.get(LibraryAsset, image["id"])
            assert row is not None
            row.state = "trash"
            # 草稿/回收站必须同时是私有的（`models.py` 的不变量 A）
            row.visibility = "personal"
            await session.commit()

        async with get_session_factory()() as session:
            stored = await RunRepository(session).get_or_404(run.id)
            gap = await authorization_gap(session, stored)

        assert gap == "本次输入的素材已被删除，请重新准备"
    finally:
        await drop_run(run.id)
        await drop_pack(pack.id)


async def test_live_run_passes_the_authorization_gate(client: TestClient) -> None:
    """反过来也要钉住：还能用的输入不该被这道闸误伤。

    没有这一条，「一律返回拒绝」的实现也能让上面两条通过。
    """
    login(client, "admin", "admin@12345")
    image = upload(client, "character.png", "character", png_bytes())
    pack = await add_pack(
        owner=ADMIN_ID, kind="character", name="人物", reference_ids=[image["id"]]
    )
    run = await add_run(pack_id=pack.id, pack_version=pack.version, status="prepared")
    await add_input(run.id, image["id"], position=1)
    try:
        async with get_session_factory()() as session:
            stored = await RunRepository(session).get_or_404(run.id)
            assert await authorization_gap(session, stored) is None
    finally:
        await drop_run(run.id)
        await drop_pack(pack.id)


def test_executor_does_not_report_authorization_codes() -> None:
    """执行器不该再产出 `AUTHORIZATION_REVOKED`：授权判断已经在业务层。

    它以前把 `MediaCatalog.check_run()` 的失败映射成这个码。搬到业务层之后
    如果不删，能命中 `except AppError` 的就只剩 `NotFound`（归档根没挂、
    账号锁路径解析不到）之类——那些会被说成「授权被撤销」，排查方向直接跑偏。
    现在业务层用同一个码（`_stop_if_unauthorized`），语义才对得上。

    断言用 `code="..."` 精确匹配，不看注释里提到的名字。
    """
    source = (
        Path(__file__).resolve().parents[1] / "src/modules/model_driver/libtv/process.py"
    ).read_text(encoding="utf-8")
    assert 'code="AUTHORIZATION_REVOKED"' not in source
    assert 'code="EXECUTOR_FAILED"' in source, "兜底分支要诚实地表示「执行器没跑成」"
