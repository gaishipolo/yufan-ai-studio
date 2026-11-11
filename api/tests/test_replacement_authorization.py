"""Final authorization applies to new submissions, while paid tasks remain recoverable."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import delete
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
from modules.asset.models import Asset
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


@pytest.mark.parametrize(
    ("source_owner", "shared", "allowed"),
    [("other-owner", False, False), ("other-owner", True, True), (ADMIN, False, True)],
)
async def test_new_submission_rechecks_source_sharing_without_requiring_shared_pack_images(
    source_owner: str, shared: bool, allowed: bool
) -> None:
    source = Asset(
        id=uuid.uuid4().hex, name="source.mp4", type="video", category="source",
        owner_id=source_owner, shared=True, duration=5, has_audio=True,
    )
    reference = Asset(
        id=uuid.uuid4().hex, name="private.png", type="image", category="character",
        owner_id="pack-owner", shared=False,
    )
    async with get_session_factory()() as session:
        session.add_all([source, reference])
        await session.commit()
    pack = await add_pack(owner="pack-owner", reference_ids=[reference.id])
    run = await add_run(pack_id=pack.id, pack_version=1)
    await add_input(run.id, source.id, role="source_video")
    await add_input(run.id, reference.id, role="identity", position=1)
    payload = PrepareReplacement(
        request_id=uuid.uuid4(), kind="character", target="test", source_id=source.id,
        reference_ids=[reference.id], pack_id=pack.id, pack_version=1,
    )
    try:
        async with get_session_factory()() as session:
            service = ReplacementService(session)
            # It was valid at prepare time, including the pack owner's private image.
            await service._validate_inputs(payload, ADMIN, "pack-owner")
            stored_source = await session.get(Asset, source.id)
            stored_source.shared = shared
            await session.commit()
        async with get_session_factory()() as session:
            service = ReplacementService(session)
            stored = await service.runs.get_or_404(run.id)
            gap = await authorization_gap(session, stored)
            if allowed:
                assert gap is None
                await service._validate_inputs(payload, ADMIN, "pack-owner")
            else:
                assert gap == "原片不存在或已停止共享"
                with pytest.raises(NotFound):
                    await service._validate_inputs(payload, ADMIN, "pack-owner")
                with pytest.raises(BadRequest, match="停止共享"):
                    await service.execute(run.id, ADMIN)
    finally:
        await drop_run(run.id)
        await drop_pack(pack.id)
        async with get_session_factory()() as session:
            await session.execute(delete(Asset).where(Asset.id.in_([source.id, reference.id])))
            await session.commit()
