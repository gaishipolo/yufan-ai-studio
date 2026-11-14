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
from pathlib import Path

import imageio_ffmpeg

from core.ports.library_ingest import IngestRequest, get_library_ingest, has_library_ingest
from core.ports.library_lookup import LibraryAssetKind
from db.session import get_session_factory
from generation.pipeline import get_pipeline
from infra import get_storage
from modules.library.models import LibraryAsset
from modules.task.enums import TaskStatus
from modules.task.models import VideoTask
from modules.task.pipeline import KIND_VIDEO


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
