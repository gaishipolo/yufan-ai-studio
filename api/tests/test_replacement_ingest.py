"""`ReplacementPipeline.archive_request`：替换成片进 v2 草稿箱的翻译层。

`archive_key` 这条通道本身由 `test_library_ingest_archive.py` 钉住（字节会复制进
资产库自己的 blob、`sourceKey` 幂等）。这里只钉「翻译」这一步，三条判据各自对应
一种「成功却静默丢东西」：

1. **必须给 `archiveKey`**。给 `storageKey` 会在主对象存储里找一个不存在的对象；
   给 `sourceUrl` 会把站内取件地址（`/api/v1/templates/replacement-runs/{id}/output`）
   存进库——run 一删就裂，而库里的记录看起来一切正常。
2. **没有 `output_key` 就不入库**。那时只可能存下站内地址；搬不上去由
   `replacement_archive_publish_total{result=failed}` 暴露，不在这里造半条记录。
3. **幂等键带供应商任务号**。`reconfirm` 重提会出另一条片子，只用 run id 会把
   新片判成「已入过库」，草稿箱停在旧版。

反向验证：删掉 `ReplacementPipeline.archive_request`，第 1、3 条立刻变红
（`runner._archive` 在 `getattr(pipeline, "archive_request", None)` 那一步静默返回）。
"""

from __future__ import annotations

import uuid

from tests_template_helpers import ADMIN, add_run, patch_run

from core.ports.library_ingest import IngestRequest
from core.ports.library_lookup import LibraryAssetKind
from db.session import get_session_factory
from generation.spec import GenerationRecord
from modules.template.pipeline import KIND_REPLACEMENT, ReplacementPipeline

#: `libtv.poll()` 返回的站内取件地址，就是 `stored` 在替换链上的真实取值。
STATION_URL = "/api/v1/templates/replacement-runs/{}/output"


def _record(run_id: str, *, task_id: str | None) -> GenerationRecord:
    return GenerationRecord(
        id=run_id,
        kind=KIND_REPLACEMENT,
        model="libtv-wan3-replacement",
        prompt="把 {{Mixed 1}} 换成产品",
        status="success",
        owner_id=ADMIN,
        external_id=f"{run_id}:{task_id}" if task_id else None,
    )


async def test_archive_request_uses_archive_key(client) -> None:
    run_id = uuid.uuid4().hex
    run = await add_run(
        id=run_id,
        kind="product",
        target="秋季外套",
        status="success",
        framework_status="success",
        task_id="task_abc",
        output_key=f"runs/{run_id}/outputs/generated.mp4",
        prompt="把 {{Mixed 1}} 换成产品",
        duration=6,
        configuration={"aspect_ratio": "9:16", "channel": "libtv"},
        pack_id="pack_1",
        pack_version=3,
    )

    async with get_session_factory()() as session:
        request = await ReplacementPipeline().archive_request(
            session, _record(run.id, task_id="task_abc"), STATION_URL.format(run.id)
        )

    assert isinstance(request, IngestRequest)
    # 字节在归档存储里：三个来源字段里只有 archiveKey 能填，另两个必须为空，
    # 否则入库实现会按优先级去主对象存储或站内地址取字节。
    assert request.archive_key == run.output_key
    assert request.storage_key == ""
    assert request.source_url == ""
    assert request.kind == LibraryAssetKind.VIDEO
    assert request.mime_type == "video/mp4"
    assert request.owner_id == ADMIN
    assert request.source_task_id == run.id
    assert request.source_key == f"template:{run.id}:task_abc"
    assert request.name.startswith("秋季外套")
    # 详情页的「生成参数」要能还原当时怎么跑的：这几个键与
    # `modules/library/ingest.py::_SNAPSHOT_FIELDS` 的左列对齐。
    assert request.generation["aspect_ratio"] == "9:16"
    assert request.generation["pack_id"] == "pack_1"
    assert request.generation["pack_version"] == 3
    assert request.generation["duration_sec"] == 6
    assert request.generation["external_id"] == "task_abc"


async def test_archive_request_skips_runs_without_output_key(client) -> None:
    """成片还没搬进归档存储时不入库：此时只可能存下一个会失效的站内地址。"""
    run = await add_run(status="success", framework_status="success", task_id="task_new")

    async with get_session_factory()() as session:
        request = await ReplacementPipeline().archive_request(
            session, _record(run.id, task_id="task_new"), STATION_URL.format(run.id)
        )

    assert request is None


async def test_source_key_changes_when_the_upstream_task_changes(client) -> None:
    """`reconfirm` 重提会换 task_id：新片必须是一行新草稿，不能命中旧幂等键。"""
    run_id = uuid.uuid4().hex
    run = await add_run(
        id=run_id,
        status="success",
        framework_status="success",
        task_id="task_first",
        output_key=f"runs/{run_id}/outputs/generated.mp4",
    )

    async with get_session_factory()() as session:
        first = await ReplacementPipeline().archive_request(
            session, _record(run.id, task_id="task_first"), ""
        )
    # 重提会改的是**数据库行**上的 task_id（`mark_submitted` 写的），不是调用方
    # 手里的 `GenerationRecord`；只改后者是测不出这条判据的。
    await patch_run(run.id, task_id="task_second")
    async with get_session_factory()() as session:
        second = await ReplacementPipeline().archive_request(
            session, _record(run.id, task_id="task_second"), ""
        )

    assert first is not None and second is not None
    assert first.source_key != second.source_key
    assert first.source_key.endswith(":task_first")
    assert second.source_key.endswith(":task_second")
