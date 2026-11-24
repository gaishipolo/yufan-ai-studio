"""本地上传分支与资产原地替换：不访问真实上游。"""

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from library_support import png_bytes
from sqlalchemy import select

from core.exceptions import BadRequest, UpstreamError
from core.ports.library_ingest import IngestRequest
from core.ports.library_lookup import LibraryAssetKind
from db.session import get_session_factory
from infra import get_storage
from infra.ai.base import Capability, GenerationRequest
from modules.library.ingest import SqlLibraryIngest
from modules.library.models import LibraryAsset
from modules.model_driver.drivers import libtv


@pytest.mark.parametrize("failure", [TimeoutError, asyncio.CancelledError])
async def test_executor_stops_child_before_leaving_on_timeout_or_cancel(monkeypatch, failure):
    process = SimpleNamespace(
        returncode=None, kill=Mock(),
        communicate=AsyncMock(side_effect=[failure(), (b"", b"")]),
    )
    monkeypatch.setattr(
        libtv.executor.asyncio, "create_subprocess_exec", AsyncMock(return_value=process)
    )
    expected = asyncio.CancelledError if failure is asyncio.CancelledError else UpstreamError
    with pytest.raises(expected):
        await libtv.executor.submit_desubtitle(
            account="http://127.0.0.1:9412", source="source.mp4",
            width=720, height=1280, duration=4,
        )
    process.kill.assert_called_once()
    assert process.communicate.await_count == 2


@pytest.mark.parametrize("fails", [False, True])
async def test_local_output_is_materialized_and_always_cleaned(monkeypatch, fails):
    source = f"videos/{uuid4().hex}.mp4"
    await get_storage().put(source, b"test-video-bytes", "video/mp4")
    seen = []

    async def submit(**kwargs):
        local = Path(kwargs["source"])
        seen.append(local)
        assert await asyncio.to_thread(local.read_bytes) == b"test-video-bytes"
        assert kwargs["width"] == 1280
        if fails:
            raise RuntimeError("上游拒绝")
        return {"taskId": "upstream-id", "power": 0}

    monkeypatch.setattr(libtv.executor, "select_browser", lambda *a, **kw: "http://localhost:9412")
    monkeypatch.setattr(libtv.executor, "submit_desubtitle", submit)
    request = GenerationRequest(
        capability=Capability.VIDEO, model="libtv-subtitle-eraser", prompt="",
        params={"sourceUrl": source, "width": 1280}, request_id=uuid4().hex,
    )
    if fails:
        with pytest.raises(RuntimeError, match="上游拒绝"):
            await libtv.LibTVProvider().submit(request)
    else:
        assert await libtv.LibTVProvider().submit(request) == "desub|http://localhost:9412|upstream-id"
    assert seen and not seen[0].exists()


async def test_http_source_uses_direct_path(monkeypatch):
    submit = AsyncMock(return_value={"taskId": "upstream-id"})
    monkeypatch.setattr(libtv.executor, "select_browser", lambda *a, **kw: "http://localhost:9412")
    monkeypatch.setattr(libtv.executor, "submit_desubtitle", submit)
    source = "https://cdn.example.com/source.mp4"
    await libtv.LibTVProvider().submit(GenerationRequest(
        capability=Capability.VIDEO, model="libtv-subtitle-eraser", prompt="",
        params={"sourceUrl": source},
    ))
    assert submit.await_args.kwargs["source"] == source


@pytest.mark.parametrize(
    "status,result_url,expected_state,expected_error",
    [
        (2, "https://cdn.example.com/out.mp4", "succeeded", None),
        # 成功却没产物地址：不能报 succeeded（框架会拿空地址去入库）
        (2, "", "running", None),
        (3, "", "failed", "上游去字幕失败"),
        # 超时是终态：以前漏判，任务会一直停在「生成中」直到墙钟兜底
        (4, "", "failed", "上游去字幕超时"),
    ],
)
async def test_desubtitle_poll_maps_terminal_statuses(
    monkeypatch, status, result_url, expected_state, expected_error
):
    from modules.model_driver.libtv.account import QueueItem

    item = QueueItem(
        task_id="upstream-id",
        status=status,
        percent=100 if status == 2 else 50,
        queue_reason="",
        queue_position=0,
        queue_total=0,
        queue_limit=0,
        power=0,
        benefit_tag="",
        result_url=result_url,
    )
    monkeypatch.setattr(
        libtv.libtv_account, "progress", AsyncMock(return_value=[item])
    )

    progress = await libtv.LibTVProvider().poll("desub|http://127.0.0.1:9412|upstream-id")

    assert progress.state.value == expected_state
    if expected_error:
        assert expected_error in progress.error
    if expected_state == "succeeded":
        assert progress.output_url == result_url
        assert progress.persist_required is True
    if expected_state == "running":
        assert progress.output_url is None


@pytest.mark.parametrize("same_bytes", [False, True])
async def test_replacement_keeps_asset_id_and_one_library_row(same_bytes):
    owner, source_key = uuid4().hex, uuid4().hex
    storage = get_storage()
    first_key, second_key = f"generated/{source_key}.png", f"generated/{source_key}-new.png"
    await storage.put(first_key, png_bytes("red"), "image/png")
    await storage.put(second_key, png_bytes("red" if same_bytes else "blue"), "image/png")
    async with get_session_factory()() as session:
        ingest = SqlLibraryIngest()
        original = await ingest.ingest_generated(session, IngestRequest(
            source_module="video", source_task_id=source_key, source_key=f"original:{source_key}",
            kind=LibraryAssetKind.IMAGE, owner_id=owner, name="原片", storage_key=first_key,
        ))
        request = IngestRequest(
            source_module="video", source_task_id=source_key, source_key=f"erased:{source_key}",
            kind=LibraryAssetKind.IMAGE, owner_id=owner, name="成片-去字幕",
            storage_key=second_key, replaces_asset_id=original.asset_id,
        )
        result = await ingest.ingest_generated(session, request)
        again = await ingest.ingest_generated(session, request)
        rows = (await session.scalars(
            select(LibraryAsset).where(LibraryAsset.owner_id == owner)
        )).all()
        assert len(rows) == 1
        assert result.asset_id == again.asset_id == original.asset_id
        assert "去字幕" in rows[0].name
        assert rows[0].deleted_at is None


@pytest.mark.parametrize("unavailable", ["foreign", "deleted", "missing"])
async def test_unavailable_replacement_falls_back_to_new_asset(unavailable):
    owner, source_key = uuid4().hex, uuid4().hex
    storage = get_storage()
    key = f"generated/{source_key}.png"
    await storage.put(key, png_bytes("green"), "image/png")
    async with get_session_factory()() as session:
        original = LibraryAsset(
            name="原资产", kind="image", origin="generated", mime_type="image/png",
            storage_key="old.png", sha256=uuid4().hex, size_bytes=1,
            owner_id="another" if unavailable == "foreign" else owner,
        )
        session.add(original)
        if unavailable == "deleted":
            original.deleted_at = datetime.now(UTC)
        await session.commit()
        result = await SqlLibraryIngest().ingest_generated(session, IngestRequest(
            source_module="video", source_task_id=source_key, source_key=source_key,
            kind=LibraryAssetKind.IMAGE, owner_id=owner, name="去字幕结果", storage_key=key,
            replaces_asset_id="missing" if unavailable == "missing" else original.id,
        ))
        assert result.created
        assert result.asset_id != original.id
        assert original.storage_key == "old.png"


async def test_desubtitle_output_is_persisted_to_storage_while_original_http_stays_direct(
    monkeypatch,
):
    from modules.task import pipeline as task_pipeline
    from modules.task.models import VideoTask

    async def fake_fetch(url: str, target: Path, **kwargs):
        await asyncio.to_thread(target.write_bytes, b"persisted-erased-mp4")
        return SimpleNamespace(path=target, content_type="video/mp4", final_url=url, size=20)

    monkeypatch.setattr(task_pipeline, "fetch_to_file", fake_fetch)

    async with get_session_factory()() as session:
        orig = VideoTask(
            title="原片", prompt="p", model="Agnes", model_id="agnes-video",
            status="completed", author_id="u1", author_name="u1", variant="original",
        )
        child = VideoTask(
            title="原片（去字幕）", prompt="p", model="LibTV · 智能去字幕",
            model_id="libtv-subtitle-eraser", status="running", author_id="u1",
            author_name="u1", derived_from_task_id=orig.id, variant="subtitle_erased",
        )
        session.add_all([orig, child])
        await session.commit()

        pipe = task_pipeline.VideoGenerationPipeline()
        try:
            assert await pipe.persist_output(orig.id, "https://cdn.example.com/orig.mp4") == (
                "https://cdn.example.com/orig.mp4"
            )
            stored = await pipe.persist_output(child.id, "https://cdn.example.com/erased.mp4")
            assert stored == f"videos/{child.id}.mp4"
            assert await get_storage().get(stored) == b"persisted-erased-mp4"
        finally:
            await session.delete(child)
            await session.delete(orig)
            await session.commit()


async def test_replacement_handles_existing_duplicate_of_another_asset():
    owner, source_key = uuid4().hex, uuid4().hex
    storage = get_storage()
    orig_key, dup_key = f"generated/{source_key}-orig.png", f"generated/{source_key}-dup.png"
    await storage.put(orig_key, png_bytes("red"), "image/png")
    await storage.put(dup_key, png_bytes("blue"), "image/png")
    async with get_session_factory()() as session:
        ingest = SqlLibraryIngest()
        original = await ingest.ingest_generated(session, IngestRequest(
            source_module="video", source_task_id=source_key, source_key=f"orig:{source_key}",
            kind=LibraryAssetKind.IMAGE, owner_id=owner, name="原片", storage_key=orig_key,
        ))
        existing_dup = await ingest.ingest_generated(session, IngestRequest(
            source_module="video", source_task_id=source_key, source_key=f"dup:{source_key}",
            kind=LibraryAssetKind.IMAGE, owner_id=owner, name="已有同字节资产", storage_key=dup_key,
        ))
        result = await ingest.ingest_generated(session, IngestRequest(
            source_module="video", source_task_id=source_key, source_key=f"erased:{source_key}",
            kind=LibraryAssetKind.IMAGE, owner_id=owner, name="成片-去字幕",
            storage_key=dup_key, replaces_asset_id=original.asset_id,
        ))
        assert result.asset_id == existing_dup.asset_id
        assert not result.created
        orig_row = await session.get(LibraryAsset, original.asset_id)
        assert orig_row is not None and orig_row.storage_key != ""


async def test_desubtitle_submission_unknown_raises_upstream_error(monkeypatch):
    async def fake_exec(*args, **kwargs):
        return SimpleNamespace(
            returncode=1,
            communicate=AsyncMock(
                return_value=(b'{"code":"SUBMISSION_UNKNOWN","error":"timeout"}\n', b"")
            ),
        )

    monkeypatch.setattr(libtv.executor.asyncio, "create_subprocess_exec", fake_exec)
    with pytest.raises(UpstreamError, match="提交结果未知"):
        await libtv.executor.submit_desubtitle(
            account="http://127.0.0.1:9412",
            source="https://cdn.example.com/orig.mp4",
            width=720,
            height=1280,
            duration=4,
        )


async def test_desubtitle_pre_submit_error_still_raises_bad_request(monkeypatch):
    async def fake_exec(*args, **kwargs):
        return SimpleNamespace(
            returncode=1,
            communicate=AsyncMock(return_value=(b'{"error":"missing canvas"}\n', b"")),
        )

    monkeypatch.setattr(libtv.executor.asyncio, "create_subprocess_exec", fake_exec)
    with pytest.raises(BadRequest, match="去字幕未提交"):
        await libtv.executor.submit_desubtitle(
            account="http://127.0.0.1:9412",
            source="https://cdn.example.com/orig.mp4",
            width=720,
            height=1280,
            duration=4,
        )


async def test_runner_does_not_fallback_to_cdn_url_when_persist_required(monkeypatch):
    from generation import runner as gen_runner
    from generation.pipeline import get_pipeline
    from generation.runner import GenerationRunner, OutputNotPersisted
    from infra.ai.base import GenerationProgress, GenerationState
    from infra.queue.memory import MemoryJobQueue

    monkeypatch.setattr(gen_runner, "_PERSIST_RETRY_BACKOFF_SEC", 0.0)
    pipeline = get_pipeline("video")
    attempts = 0

    async def broken_persist(record_id: str, url: str) -> str:
        nonlocal attempts
        attempts += 1
        raise RuntimeError("CDN 下载失败")

    monkeypatch.setattr(pipeline, "persist_output", broken_persist)
    runner = GenerationRunner(MemoryJobQueue())
    with pytest.raises(OutputNotPersisted):
        await runner._persist(
            "video",
            "rec-1",
            "https://temp-cdn.example.com/erased.mp4",
            persist_required=True,
        )
    assert attempts == 3

    prog = GenerationProgress(
        external_id="desub|http://127.0.0.1:9412|1",
        state=GenerationState.SUCCEEDED,
        output_url="https://temp-cdn.example.com/erased.mp4",
        persist_required=True,
    )
    assert prog.persist_required is True
