"""Frozen provider routing and billing must survive restarts and defaults changing."""

from __future__ import annotations

import uuid

import pytest
from pydantic import ValidationError
from tests_template_helpers import ADMIN, add_run, drop_run, workspace

from core.exceptions import Conflict
from db.session import get_session_factory
from infra.ai.libtv import MODEL_ID
from modules.template import worker
from modules.template.pipeline import ReplacementStore
from modules.template.schema import PrepareReplacement


async def test_disabled_channel_poll_preserves_task_and_never_uses_fallback(monkeypatch, tmp_path):
    from tests_template_helpers import read_run

    import infra
    from generation import runner as generation_runner
    from infra.ai.base import GenerationProgress, GenerationState
    from infra.ai.echo import EchoProvider
    from infra.ai.router import ModelRouter
    from infra.archive.local import get_archive_settings
    from infra.queue.base import Job
    from infra.queue.memory import MemoryJobQueue

    monkeypatch.setattr(get_archive_settings(), "root", str(tmp_path))
    router = ModelRouter()
    provider = EchoProvider()
    calls = []

    async def wrong_poll(external_id, **kwargs):
        calls.append(external_id)
        return GenerationProgress(external_id, GenerationState.FAILED, error="wrong channel")

    monkeypatch.setattr(provider, "poll", wrong_poll)
    router.register(provider)
    monkeypatch.setattr(infra, "get_model_router", lambda: router)
    monkeypatch.setattr(generation_runner, "get_model_router", lambda: router)
    queue = MemoryJobQueue()
    run = await add_run(configuration={"channel": "bailian"}, task_id="paid-task",
                        status="running", framework_status="running", submit_mode="async")
    workspace(run.id)
    try:
        await generation_runner.GenerationRunner(queue).handle_poll(Job(
            name="generation.poll", payload={"kind": "template_replacement", "record_id": run.id},
        ))
        assert calls == []
        saved = await read_run(run.id)
        assert saved.framework_status == "running"
        assert saved.task_id == "paid-task"
        assert await queue.size() == 1
    finally:
        await drop_run(run.id)


async def test_disabled_channel_submit_does_not_call_fallback_or_mark_billing_unknown(
    monkeypatch, tmp_path,
):
    from tests_template_helpers import read_run

    import infra
    from generation import runner as generation_runner
    from infra.ai.echo import EchoProvider
    from infra.ai.router import ModelRouter
    from infra.archive.local import get_archive_settings
    from infra.queue.base import Job
    from infra.queue.memory import MemoryJobQueue

    monkeypatch.setattr(get_archive_settings(), "root", str(tmp_path))
    router = ModelRouter()
    provider = EchoProvider()
    calls = []

    async def wrong_submit(request):
        calls.append(request)
        return "wrong-channel-task"

    monkeypatch.setattr(provider, "submit", wrong_submit)
    router.register(provider)
    monkeypatch.setattr(infra, "get_model_router", lambda: router)
    monkeypatch.setattr(generation_runner, "get_model_router", lambda: router)
    run = await add_run(configuration={"channel": "bailian"},
                        status="uploaded", framework_status="queued")
    workspace(run.id)
    try:
        await generation_runner.GenerationRunner(MemoryJobQueue()).handle_submit(Job(
            name="generation.submit",
            payload={"kind": "template_replacement", "record_id": run.id},
        ))
        assert calls == []
        saved = await read_run(run.id)
        assert saved.task_id is None
        assert saved.submit_attempts == 0
        assert saved.status == "service_error"
    finally:
        await drop_run(run.id)


def test_prepare_defaults_to_libtv_and_rejects_unknown_channel():
    payload = {"request_id": uuid.uuid4(), "kind": "scene", "source_id": "source"}
    assert PrepareReplacement(**payload).channel == "libtv"
    assert PrepareReplacement(**payload, channel="bailian").channel == "bailian"
    with pytest.raises(ValidationError):
        PrepareReplacement(**payload, channel="unknown")


@pytest.mark.parametrize("channel,model", [
    (None, MODEL_ID), ("libtv", MODEL_ID),
    ("bailian", "bailian-wan3-replacement"),
    ("bailian-prime", "bailian-wan3-prime-replacement"),
])
async def test_pipeline_routes_frozen_channel(channel, model):
    run = await add_run(configuration={"channel": channel} if channel else None,
                        task_id="upstream-123", submit_mode="async")
    try:
        async with get_session_factory()() as session:
            record = await ReplacementStore(session).load(run.id)
            assert record.model == model
            assert record.external_id == f"{run.id}:upstream-123"
    finally:
        await drop_run(run.id)


async def test_bailian_cannot_use_libtv_credit_mode():
    run = await add_run(configuration={"channel": "bailian"})
    try:
        async with get_session_factory()() as session:
            with pytest.raises(Conflict, match="百炼"):
                await worker.set_billing_mode(session, run.id, ADMIN, "credits")
    finally:
        await drop_run(run.id)


async def test_recovery_reattaches_bailian_receipt_without_resubmitting(monkeypatch):
    import json
    import tempfile
    from datetime import UTC, datetime, timedelta

    from infra.archive.local import get_archive_settings

    monkeypatch.setattr(get_archive_settings(), "root", tempfile.mkdtemp())
    run = await add_run(configuration={"channel": "bailian"}, status="submission_unknown",
                        framework_status="failed", submit_attempts=1,
                        started_at=datetime.now(UTC) - timedelta(hours=2),
                        finished_at=datetime.now(UTC) - timedelta(hours=1))
    path = workspace(run.id)
    (path / "records/bailian-submission.json").write_text(
        json.dumps({"output": {"task_id": "accepted-123", "task_status": "PENDING"}}),
        encoding="utf-8",
    )
    try:
        async with get_session_factory()() as session:
            assert await worker.recover_state(session, run.id) == "poll"
            loaded = await ReplacementStore(session).load(run.id)
            assert loaded.external_id == f"{run.id}:accepted-123"
            assert loaded.submit_attempts == 0
            assert loaded.timeout_started_at.replace(tzinfo=UTC) > (
                datetime.now(UTC) - timedelta(minutes=1)
            )
    finally:
        await drop_run(run.id)


def test_default_channel_keeps_legacy_idempotency_fingerprint():
    import hashlib
    import json

    from modules.template.replacement_service import _fingerprint

    payload = PrepareReplacement(request_id=uuid.uuid4(), kind="scene", source_id="source")
    legacy = payload.model_dump(mode="json", exclude={"channel"})
    expected = hashlib.sha256((ADMIN + json.dumps(
        legacy, ensure_ascii=False, separators=(",", ":")
    )).encode()).hexdigest()
    assert _fingerprint(ADMIN, payload) == expected


async def test_explicit_reconfirm_archives_unknown_receipt(monkeypatch, tmp_path):
    import json

    from infra.archive.local import get_archive_settings
    from modules.template.replacement_service import ReplacementService

    monkeypatch.setattr(get_archive_settings(), "root", str(tmp_path))
    run = await add_run(configuration={"channel": "bailian"}, status="submission_unknown",
                        framework_status="failed", submit_attempts=1)
    path = workspace(run.id)
    receipt = path / "records/bailian-submission.json"
    receipt.write_text(json.dumps({"status": "submission_unknown", "model":
                                   "bailian-wan3-replacement"}))
    scheduled = []

    async def schedule(*args):
        scheduled.append(args[1])

    monkeypatch.setattr(worker, "schedule", schedule)
    try:
        async with get_session_factory()() as session:
            await ReplacementService(session).reconfirm(run.id, ADMIN, confirm_charged=True)
        assert not receipt.exists()
        assert len(list((path / "records").glob("bailian-reconfirmed-*.json"))) == 1
        assert scheduled == [run.id]
    finally:
        await drop_run(run.id)


async def test_startup_recovers_known_receipt_in_unknown_db_state(monkeypatch, tmp_path):
    import json

    from core.config import settings
    from infra.archive.local import get_archive_settings

    monkeypatch.setattr(get_archive_settings(), "root", str(tmp_path))
    monkeypatch.setattr(settings, "worker_enabled", True)
    run = await add_run(configuration={"channel": "bailian"}, status="submission_unknown",
                        framework_status="failed", submit_attempts=1)
    path = workspace(run.id)
    (path / "records/bailian-submission.json").write_text(json.dumps({"task_id": "accepted"}))
    captured = []

    async def enqueue(run_id, **kwargs):
        captured.append(run_id)

    monkeypatch.setattr(worker, "enqueue_recovery", enqueue)
    try:
        await worker.resume()
        assert run.id in captured
    finally:
        await drop_run(run.id)


def test_unknown_channel_on_a_stored_row_degrades_instead_of_raising() -> None:
    """历史脏行不能把恢复链和进程启动拖下水。

    `run_channel` 只读**已经落库的行**；新请求的渠道由 `PrepareReplacement.channel`
    （Literal）与 `require_channel` 校验。历史行里出现过的渠道只有 libtv，
    所以「认不出来」的正确答案是降级 + 留痕，而不是抛 `BadRequest`——
    它处在启动恢复、轮询、详情投影三条链路上，一次异常就是整个进程起不来。
    """
    from modules.template.channels import run_channel

    assert run_channel({"channel": "wan-legacy"}) == "libtv"
    assert run_channel(["not", "a", "mapping"]) == "libtv"  # type: ignore[arg-type]
    assert run_channel(None) == "libtv"
    assert run_channel({}) == "libtv"
