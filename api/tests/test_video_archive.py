"""视频生成结果入资产库草稿箱：`VideoGenerationPipeline.archive_request`。

这个钩子是「AI 生成结果进草稿箱」的唯一入口。没有它时 `generation.runner._archive()`
会在 `getattr(pipeline, "archive_request", None)` 那一步**静默返回**（连日志都不打）：
用户视频生成完了，资产库里一条都没有。2026-11-11 线上实测就是这个状态——
图片来源入库 6 条、视频 0 条，`video_tasks` 里却有一条 completed 且 URL 完好。

断言方向：

1. 钩子存在，且把 `video_tasks` 翻译成合法的 `IngestRequest`；
2. 幂等键带**供应商任务号**：重试会换出另一条片子，不能判成「已入过库」；
3. 条目名补上 `.mp4`：标题是没有后缀的，库里存无后缀文件双击打不开；
4. 快照键名是资产库认得的 snake_case（写成 `durationSec` 会被静默丢弃）；
5. 直链进 `sourceUrl`、自有 key 进 `storageKey`，两者不混；
6. 真实链路（端口直调）落点是 `draft / generated`，字节进库自己的 blob。
"""

from __future__ import annotations

import asyncio
import os
import re
import subprocess
import uuid
from collections.abc import Iterator
from pathlib import Path

import imageio_ffmpeg
import pytest

from core.exceptions import NotFound
from core.ports.library_ingest import IngestRequest, get_library_ingest, has_library_ingest
from core.ports.library_lookup import LibraryAssetKind
from db.session import get_session_factory
from generation.pipeline import get_pipeline
from infra import get_storage
from modules.library.models import LibraryAsset
from modules.task.enums import TaskStatus
from modules.task.models import VideoTask
from modules.task.pipeline import KIND_VIDEO


def _insert_reference_assets() -> None:
    """把任务行引用的两条参考素材插进资产库。

    为什么需要：任务行记的是**资产库 id**，受理期校验过的引用在 `load()` 里
    要能重新找回（`referenceKeys` 与内联 data URI 都靠它）。查不到就**整条
    拒绝** —— 「参考素材已不可用」比「悄悄少一张图」好。

    用完即删：测试库是会话级共享的，留着会污染别的用例的资产计数。
    """

    async def run() -> None:
        async with get_session_factory()() as session:
            for asset_id in ("lib_a", "lib_b"):
                if await session.get(LibraryAsset, asset_id) is not None:
                    continue
                session.add(
                    LibraryAsset(
                        id=asset_id,
                        name=f"{asset_id}.png",
                        kind="image",
                        origin="upload",
                        mime_type="image/png",
                        size_bytes=1024,
                        storage_key=f"media/{asset_id}.png",
                        cover_key="",
                        sha256=None,
                        width=512,
                        height=512,
                        duration=0.0,
                        has_audio=False,
                        owner_id="u_001",
                        visibility="personal",
                        state="active",
                        state_version=1,
                        source_key=None,
                        purpose="",
                    )
                )
            await session.commit()

    asyncio.run(run())


def _delete_reference_assets() -> None:
    """删掉上面造的两条素材，不给别的用例留垃圾。"""

    async def run() -> None:
        from sqlalchemy import delete

        async with get_session_factory()() as session:
            await session.execute(
                delete(LibraryAsset).where(LibraryAsset.id.in_(("lib_a", "lib_b")))
            )
            await session.commit()

    asyncio.run(run())


@pytest.fixture(autouse=True)
def _reference_assets() -> Iterator[None]:
    """造出任务行引用的两条素材，用完删掉。"""
    _insert_reference_assets()
    try:
        yield
    finally:
        _delete_reference_assets()


def _create_task(**overrides: object) -> str:
    """建一条已完成的视频任务，返回 id。默认值挑成「有内容可断言」的一组。"""

    async def run() -> str:
        fields: dict[str, object] = {
            "title": "一只狸花猫在草地上奔跑",
            "prompt": "一只狸花猫在草地上奔跑，电影感风格",
            "status": TaskStatus.COMPLETED.value,
            "model": "Agnes Video 2.5 Flash",
            "model_id": "agnes-video-2.5-flash",
            "duration_sec": 8,
            "aspect_ratio": "9:16",
            "style_preset": "电影感",
            "reference_library_ids": ["lib_a", "lib_b"],
            "progress": 100,
            "external_id": "task_abc123",
            "output_url": "https://cdn.example/video/task_abc123.mp4",
            "author_id": "u_001",
            "author_name": "老大",
            "downloadable": True,
        }
        fields.update(overrides)
        async with get_session_factory()() as session:
            task = VideoTask(**fields)  # type: ignore[arg-type]
            session.add(task)
            await session.commit()
            return task.id

    return asyncio.run(run())


def _build(task_id: str, stored: str) -> IngestRequest:
    """走框架同一条路径：store().load() → archive_request()。"""

    async def run() -> IngestRequest:
        async with get_session_factory()() as session:
            pipeline = get_pipeline(KIND_VIDEO)
            build = getattr(pipeline, "archive_request", None)
            assert build is not None, "视频 pipeline 必须实现 archive_request，否则产物不入库"
            record = await pipeline.store(session).load(task_id)
            assert record is not None
            return await build(session, record, stored)

    return asyncio.run(run())


def test_hook_exists_and_builds_draft_request() -> None:
    """钩子存在 + 字段映射正确。缺了它 runner 静默跳过，是这一整批问题的根因。"""
    task_id = _create_task()

    request = _build(task_id, "https://cdn.example/video/task_abc123.mp4")

    assert request.kind is LibraryAssetKind.VIDEO
    assert request.source_module == KIND_VIDEO == "video"
    assert request.source_task_id == task_id
    assert request.owner_id == "u_001", "归属取生成任务的作者，不能猜默认账号"
    assert request.source_url == "https://cdn.example/video/task_abc123.mp4"
    assert request.storage_key == "", "直链走 sourceUrl，不要塞进 storageKey"


def test_source_key_carries_external_id_so_retry_lands_separately() -> None:
    """重试会清空 external_id 重投（`service.retry_task`），新片子必须能再入一次库。

    只用 `video:{id}` 的话，重试后被幂等判成「已入过库」，草稿箱永远停在上一版。
    """
    task_id = _create_task()
    first = _build(task_id, "https://cdn.example/a.mp4")

    async def clear_external() -> None:
        async with get_session_factory()() as session:
            task = await session.get(VideoTask, task_id)
            assert task is not None
            task.external_id = None
            await session.commit()

    asyncio.run(clear_external())
    second = _build(task_id, "https://cdn.example/b.mp4")

    assert first.source_key == f"video:{task_id}:task_abc123"
    assert second.source_key == f"video:{task_id}:v0"
    assert first.source_key != second.source_key


def test_asset_name_is_author_and_timestamp_with_real_suffix() -> None:
    """条目名 = `作者-年-月-日-时-分-秒` + **真实产物**的后缀。

    标题不再进名字：标题是一句描述，同一句话能生成十次，下载目录里分不清哪个是
    新的（见 `core.naming`）。后缀只认 `stored` —— 标题里自带什么都不算数，
    否则库里会出现「双击打不开」的文件。

    时间戳用正则断言：这条用例跑在「现在」，具体到秒的值不稳定。
    """
    stamp = r"\d{4}-\d{2}-\d{2}-\d{2}-\d{2}-\d{2}"

    plain = _build(_create_task(title="雨夜街道"), "https://cdn.example/x.mp4")
    assert re.fullmatch(rf".+-{stamp}\.mp4", plain.name), plain.name
    assert "雨夜街道" not in plain.name, "标题不该再进文件名"

    # 产物没有后缀时保持原样，不凭空造一个
    no_suffix = _build(
        _create_task(title="雨夜街道"), f"videos/{os.urandom(4).hex()}"
    )
    assert re.fullmatch(rf".+-{stamp}", no_suffix.name), no_suffix.name


def test_snapshot_uses_library_field_names() -> None:
    """快照键必须是资产库 `_SNAPSHOT_FIELDS` 里的名字，写 camelCase 等于没记。"""
    task_id = _create_task()

    snapshot = _build(task_id, "https://cdn.example/z.mp4").generation

    assert snapshot["model"] == "Agnes Video 2.5 Flash"
    assert snapshot["model_id"] == "agnes-video-2.5-flash"
    assert snapshot["duration_sec"] == 8.0
    assert snapshot["aspect_ratio"] == "9:16"
    assert snapshot["style_preset"] == "电影感"
    assert snapshot["reference_library_ids"] == ["lib_a", "lib_b"]
    assert snapshot["external_id"] == "task_abc123"
    assert "durationSec" not in snapshot and "aspectRatio" not in snapshot


def test_own_storage_key_goes_to_storage_key_field() -> None:
    """供应商若给的是自有相对地址（已落盘），走 storageKey 零拷贝，不该去下载。"""
    key = f"videos/{os.urandom(6).hex()}.mp4"

    request = _build(_create_task(), f"/{key}")

    assert request.storage_key == key
    assert request.source_url == ""


def _make_video(path: Path, size: str = "64x64") -> Path:
    """造一份真 mp4：入库要做 ftyp 探测与抽帧封面，假字节过不去。"""
    subprocess.run(
        [
            imageio_ffmpeg.get_ffmpeg_exe(),
            "-y",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"color=c=0x{os.urandom(3).hex()}:s={size}:d=2",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=2",
            "-c:v",
            "libx264",
            "-c:a",
            "aac",
            "-shortest",
            str(path),
        ],
        check=True,
        capture_output=True,
    )
    return path


def test_completed_video_lands_in_draft_box(tmp_path: Path) -> None:
    """端到端：钩子产出 → `library-ingest` 端口 → 草稿箱一行，且幂等。"""
    assert has_library_ingest(), "注入口没注册时整条链路是断的"

    key = f"videos/{os.urandom(6).hex()}.mp4"
    data = _make_video(tmp_path / "sample.mp4").read_bytes()
    asyncio.run(get_storage().put(key, data, "video/mp4"))
    task_id = _create_task()

    async def ingest_twice() -> tuple[object, object]:
        async with get_session_factory()() as session:
            request = await get_pipeline(KIND_VIDEO).archive_request(
                session,
                await get_pipeline(KIND_VIDEO).store(session).load(task_id),
                f"/{key}",
            )
            first = await get_library_ingest().ingest_generated(session, request)
            second = await get_library_ingest().ingest_generated(session, request)
            return first, second

    first, second = asyncio.run(ingest_twice())
    assert first.created is True
    assert second.created is False, "同一 sourceKey 重复回调不能产生第二行"
    assert second.asset_id == first.asset_id

    async def row() -> LibraryAsset:
        async with get_session_factory()() as session:
            found = await session.get(LibraryAsset, first.asset_id)
            assert found is not None
            return found

    asset = asyncio.run(row())
    assert (asset.kind, asset.state, asset.origin) == ("video", "draft", "generated")
    assert asset.owner_id == "u_001"
    assert asset.duration > 0, "时长来自 ffprobe，不能是 0"
    assert asset.storage_key and asset.storage_key != key, "字节要复制进库自己的 blob"
    assert asset.cover_key, "封面由 ffmpeg 抽首帧派生"


def test_attach_cover_writes_library_key_back_to_task() -> None:
    """封面写回：入库派生好的首帧 key 落到 `video_tasks.cover_url`。

    供应商（Agnes）只回 mp4 直链、不回首帧，所以任务卡片的封面只能来自入库时
    抽的那一帧。写成对象 key 而不是地址：`/tasks` 与 `feed` 出参时按当前存储
    驱动现签，换驱动不用改库。
    """
    task_id = _create_task(cover_url="")
    cover_key = "library/previews/aa/bb.jpg"

    async def attach() -> str:
        async with get_session_factory()() as session:
            ok = await get_pipeline(KIND_VIDEO).attach_cover(session, task_id, cover_key)
            assert ok is True
            await session.commit()
            task = await session.get(VideoTask, task_id)
            assert task is not None
            return task.cover_url

    assert asyncio.run(attach()) == cover_key


def test_attach_cover_reports_missing_task() -> None:
    """任务被删掉之后补封面：返回 False，不抛异常（轮询收尾不能被一条陈旧回调打断）。"""

    async def attach() -> bool:
        async with get_session_factory()() as session:
            return await get_pipeline(KIND_VIDEO).attach_cover(
                session, "task_not_exists", "library/previews/aa/bb.jpg"
            )

    assert asyncio.run(attach()) is False


# ---------- 成片搬进对象存储（本地执行器型供应商） ----------
#
# LibTV 这类供应商不给公网地址：执行器把 mp4 下载到运行工作区，驱动只能回
# 一个指向工作区的相对地址。P3 删 `modules/template` 后那条路由没了，于是
# 「任务成功、地址 404、草稿箱空」同时成立。`persist_output` 负责在收尾时
# 把字节搬进对象存储，之后与其它供应商同一套口径（库里存 key、出参现签）。

_MP4_BYTES = b"\x00\x00\x00\x18ftypmp42" + b"0" * 2048


@pytest.fixture
def local_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    """造一个带成片的运行工作区，返回它的 run_id。"""
    from core.config import settings
    from infra.archive import get_archive_storage, reset_archive_storage, runs_root, save_pointer
    from infra.archive.local import get_archive_settings
    from infra.archive.runs import relative_key

    root = tmp_path / "archive"
    root.mkdir()
    monkeypatch.setattr(get_archive_settings(), "root", str(root))
    monkeypatch.setattr(settings, "archive_driver", "local")
    monkeypatch.setattr(settings, "task_runs_root", "")
    reset_archive_storage()

    run_id = uuid.uuid4().hex
    folder = runs_root() / "2026/11/15" / f"video-{run_id}"
    (folder / "outputs").mkdir(parents=True)
    (folder / "run.json").write_text("{}", encoding="utf-8")
    (folder / "outputs/generated.mp4").write_bytes(_MP4_BYTES)
    save_pointer(get_archive_storage(), run_id, relative_key(folder))
    yield run_id
    reset_archive_storage()


def test_persist_output_moves_run_video_into_storage(local_run: str) -> None:
    url = f"/api/v1/templates/replacement-runs/{local_run}/output"

    key = asyncio.run(get_pipeline(KIND_VIDEO).persist_output("t_persist", url))

    assert key == "videos/t_persist.mp4"
    assert asyncio.run(get_storage().get(key))[:8] == _MP4_BYTES[:8]


def test_persist_output_leaves_vendor_link_alone() -> None:
    """供应商直链长期有效，多搬一份只是浪费存储。"""
    url = "https://cos-platform-outputs.example.com/videos/a.mp4"

    assert asyncio.run(get_pipeline(KIND_VIDEO).persist_output("t_persist2", url)) == url


def test_persist_output_reports_missing_run() -> None:
    """工作区不存在/没有成片时抛异常。

    框架（`runner._persist`）会记一条日志并退回原地址，不会把「生成成功」
    改成失败；这里只保证**别静默返回一个空 key**——那会让入库把空串当 key。
    """
    url = f"/api/v1/templates/replacement-runs/{'0' * 32}/output"

    with pytest.raises(NotFound):
        asyncio.run(get_pipeline(KIND_VIDEO).persist_output("t_persist4", url))


# ---------- 终态保护：迟到的回调不许改写已经结束的任务 ----------

def test_terminal_guard_is_a_status_predicate_in_the_update_sql() -> None:
    """终态保护必须写进 UPDATE 的 WHERE，而不是只在 Python 里判。

    只判「读到的状态」挡不住「读完到写完之间别人提交了终态」——那正是这次
    审查的问题 2。这里直接看 SQL：更新语句里必须带 `status NOT IN (...)`。
    """
    from sqlalchemy import event

    from db.session import get_engine
    from modules.task.pipeline import VideoTaskStore

    task_id = _create_task(status=TaskStatus.RUNNING.value, progress=40)
    seen: list[str] = []
    engine = get_engine().sync_engine

    def capture(
        conn: object,
        cursor: object,
        statement: str,
        params: object,
        context: object,
        executemany: bool,
    ) -> None:
        if statement.lstrip().upper().startswith("UPDATE") and "video_tasks" in statement:
            seen.append(statement)

    async def run() -> None:
        async with get_session_factory()() as session:
            await VideoTaskStore(session).mark_progress(task_id, 60)
            await session.commit()

    event.listen(engine, "before_cursor_execute", capture)
    try:
        asyncio.run(run())
    finally:
        event.remove(engine, "before_cursor_execute", capture)

    assert seen, "mark_progress 没有发出 UPDATE"
    assert any("STATUS NOT IN" in sql.upper() for sql in seen), seen


def test_late_progress_cannot_reopen_a_completed_task() -> None:
    """另一个进程已经把它标成 completed，本会话缓存的还是 running。

    没有这道守卫时 `mark_progress` 无条件写 `RUNNING`：出片之后来一条迟到的
    进度回调，任务就被拖回「生成中」，进度永远停在 99%，前端一直转圈。
    """
    task_id = _create_task(status=TaskStatus.RUNNING.value, progress=40)

    async def run() -> tuple[bool, str, int]:
        from modules.task.pipeline import VideoTaskStore

        async with get_session_factory()() as stale:
            store = VideoTaskStore(stale)
            assert await store.load(task_id) is not None  # 把 running 版本塞进 identity map

            async with get_session_factory()() as writer:
                row = await writer.get(VideoTask, task_id)
                assert row is not None
                row.status = TaskStatus.COMPLETED.value
                row.progress = 100
                await writer.commit()

            applied = await store.mark_progress(task_id, 60)
            await stale.commit()

        async with get_session_factory()() as check:
            row = await check.get(VideoTask, task_id)
            assert row is not None
            return applied, row.status, row.progress

    applied, status, progress = asyncio.run(run())
    assert applied is False, "终态之后不该再接受写"
    assert status == TaskStatus.COMPLETED.value
    assert progress == 100


def test_late_failure_cannot_overwrite_a_completed_task() -> None:
    task_id = _create_task()

    async def run() -> bool:
        from modules.task.pipeline import VideoTaskStore

        async with get_session_factory()() as session:
            store = VideoTaskStore(session)
            assert await store.load(task_id) is not None
            applied = await store.mark_failed(task_id, "迟到的失败")
            await session.commit()
            return applied

    assert asyncio.run(run()) is False

    async def check() -> str:
        async with get_session_factory()() as session:
            row = await session.get(VideoTask, task_id)
            assert row is not None
            return f"{row.status}:{row.error_message}"

    assert asyncio.run(check()) == f"{TaskStatus.COMPLETED.value}:None"
