"""Final authorization applies to new submissions, while paid tasks remain recoverable."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from tests_template_helpers import (
    ADMIN,
    add_input,
    add_pack,
    add_run,
    drop_pack,
    drop_run,
    workspace,
)

from core.exceptions import BadRequest, Conflict, NotFound
from db.session import get_session_factory
from modules.library.models import LibraryAsset
from modules.template import worker
from modules.template.authorization import authorization_gap
from modules.template.models import TemplatePack
from modules.template.replacement_service import ReplacementService
from modules.template.schema import PrepareReplacement


@pytest.mark.parametrize("revocation", ["pack", "input"])
async def test_execute_resumes_paid_task_after_input_authorization_changes(
    monkeypatch: pytest.MonkeyPatch, revocation: str
) -> None:
    pack = await add_pack()
    run = await add_run(
        pack_id=pack.id, pack_version=1, status="service_error", task_id="already-paid"
    )
    if revocation == "pack":
        async with get_session_factory()() as session:
            stored_pack = await session.get(TemplatePack, pack.id)
            stored_pack.status = "disabled"
            await session.commit()
    else:
        await add_input(run.id, uuid.uuid4().hex, role="source_video")
    resumed = []

    class Runner:
        async def resume(self, kind: str, run_id: str) -> None:
            resumed.append(run_id)

    monkeypatch.setattr(worker.settings, "worker_enabled", True)
    monkeypatch.setattr(worker, "get_runner", lambda: Runner())
    try:
        async with get_session_factory()() as session:
            result = await ReplacementService(session).execute(run.id, ADMIN)
        assert result.task_id == "already-paid"
        assert result.status == "running"
        assert resumed == [run.id]
    finally:
        await drop_run(run.id)
        await drop_pack(pack.id)


@pytest.mark.parametrize("restriction", ["owner", "trash"])
async def test_execute_still_checks_owner_and_trash_for_paid_task(restriction: str) -> None:
    run = await add_run(
        status="service_error", task_id="already-paid",
        deleted_at=datetime.now(UTC) if restriction == "trash" else None,
    )
    try:
        async with get_session_factory()() as session:
            with pytest.raises(NotFound if restriction == "owner" else Conflict):
                await ReplacementService(session).execute(
                    run.id, "other-owner" if restriction == "owner" else ADMIN
                )
    finally:
        await drop_run(run.id)


async def test_execute_keeps_paid_task_billing_mode_locked() -> None:
    run = await add_run(status="service_error", task_id="already-paid", billing_mode="free")
    try:
        async with get_session_factory()() as session:
            with pytest.raises(Conflict, match="免费队列"):
                await ReplacementService(session).execute(run.id, ADMIN, "credits")
    finally:
        await drop_run(run.id)


async def test_execute_recovers_receipt_before_disabled_pack_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pack = await add_pack(status="disabled")
    run = await add_run(
        pack_id=pack.id, pack_version=1, status="submission_unknown", submit_attempts=1,
    )
    workspace(run.id, {"id": run.id, "task_id": "accepted-before-crash"})
    resumed = []

    class Runner:
        async def resume(self, kind: str, run_id: str) -> None:
            resumed.append(run_id)

    monkeypatch.setattr(worker.settings, "worker_enabled", True)
    monkeypatch.setattr(worker, "get_runner", lambda: Runner())
    try:
        async with get_session_factory()() as session:
            result = await ReplacementService(session).execute(run.id, ADMIN)
        assert result.task_id == "accepted-before-crash"
        assert result.status == "running"
        assert resumed == [run.id]
    finally:
        await drop_run(run.id)
        await drop_pack(pack.id)


async def _library_asset(
    owner: str,
    *,
    kind: str = "video",
    visibility: str = "personal",
    duration: float = 5.0,
    has_audio: bool = True,
) -> str:
    asset_id = uuid.uuid4().hex
    async with get_session_factory()() as session:
        session.add(
            LibraryAsset(
                id=asset_id,
                name=f"{kind}.{'mp4' if kind == 'video' else 'png'}",
                kind=kind,
                origin="upload",
                mime_type="video/mp4" if kind == "video" else "image/png",
                storage_key=f"tests/replacement-auth/{asset_id}",
                owner_id=owner,
                visibility=visibility,
                state="active",
                duration=duration,
                has_audio=has_audio,
            )
        )
        await session.commit()
    return asset_id


@pytest.mark.parametrize(
    ("source_visibility", "allowed"),
    [("personal", False), ("team", True)],
)
async def test_new_submission_rechecks_source_sharing(
    source_visibility: str, allowed: bool
) -> None:
    """prepare 通过之后原片被别人收回共享，重提必须当场拦住。

    v1 时代这条靠 `assets.shared` 开关表达；v2 里等价的是 `visibility`
    （`personal` 只有本人可读，`team` 全站可读），判据在
    `resolve_assets().readable_v2` 与 `authorization_gap()`。
    """
    source = await _library_asset(
        "other-owner", kind="video", visibility="team", duration=5.0
    )
    reference = await _library_asset("pack-owner", kind="image", visibility="team")
    pack = await add_pack(owner="pack-owner", reference_ids=[reference])
    run = await add_run(pack_id=pack.id, pack_version=1)
    await add_input(run.id, source, role="source_video")
    await add_input(run.id, reference, role="identity", position=1)
    payload = PrepareReplacement(
        request_id=uuid.uuid4(),
        kind="character",
        target="test",
        source_library_id=source,
        reference_ids=[reference],
        pack_id=pack.id,
        pack_version=1,
    )
    try:
        async with get_session_factory()() as session:
            service = ReplacementService(session)
            # prepare 时有效：原片对外共享、封装图也可读。
            await service._validate_inputs(payload, ADMIN)
            stored_source = await session.get(LibraryAsset, source)
            assert stored_source is not None
            stored_source.visibility = source_visibility
            await session.commit()
        async with get_session_factory()() as session:
            service = ReplacementService(session)
            stored = await service.runs.get_or_404(run.id)
            gap = await authorization_gap(session, stored)
            if allowed:
                assert gap is None
                await service._validate_inputs(payload, ADMIN)
            else:
                assert gap == "原片不存在或已停止共享"
                with pytest.raises(NotFound):
                    await service._validate_inputs(payload, ADMIN)
                with pytest.raises(BadRequest, match="停止共享"):
                    await service.execute(run.id, ADMIN)
    finally:
        await drop_run(run.id)
        await drop_pack(pack.id)
        async with get_session_factory()() as session:
            await session.delete(await session.get(LibraryAsset, source))
            await session.delete(await session.get(LibraryAsset, reference))
            await session.commit()
