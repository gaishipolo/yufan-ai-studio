"""生成成功 → 草稿箱（`generation.runner._archive`）。

2026-11-10 之前这条链路是断的：`POST /assets/from-generation` 与
`library-ingest` 端口都建好、也都有用例，但**没有任何代码调用它们**，
于是草稿箱永远是空的（用户生成完图去草稿箱，一条都没有）。

三件事各一条断言：

1. **落点**：生图成功后，库里出现 `state=draft` / `visibility=personal` /
   `origin=generated` 的一行，且能在草稿箱接口里查到；
2. **重试要出新草稿**：同一个作业重试出另一张图时，草稿箱里要多一条，
   不能被幂等键吃掉；
3. **不反噬**：入库失败（这里用「存储里没有这份字节」制造）只留日志，
   不能把已经成功的生成改成失败。
"""

from __future__ import annotations

import asyncio

import pytest
from library_support import png_bytes, put_blob, suffix
from sqlalchemy import delete, select

from db.session import get_session_factory
from generation.runner import JOB_POLL, GenerationRunner
from infra.ai.base import GenerationProgress, GenerationState
from infra.ai.echo import EchoProvider
from infra.queue.base import Job
from infra.queue.memory import MemoryJobQueue
from modules.identity.models import User
from modules.image.models import ImageJob
from modules.image.pipeline import ImageGenerationPipeline
from modules.library.models import LibraryAsset

_EXTERNAL_A = "echo-image-deadbeef-1"
_EXTERNAL_B = "echo-image-cafebabe-2"


def _admin_id() -> str:
    async def run() -> str:
        async with get_session_factory()() as session:
            found = await session.scalar(select(User.id).where(User.username == "admin"))
        assert found is not None
        return found

    return asyncio.run(run())


def _seed_job(owner_id: str, *, name: str = "产品主图") -> str:
    """造一条「正在跑」的生图作业（轮询的下游只认记录，不认 HTTP 入口）。"""

    async def run() -> str:
        async with get_session_factory()() as session:
            job = ImageJob(
                name=name,
                prompt="一只柴犬，棚拍",
                size="1024x1024",
                count=1,
                status="running",
                # 必须写 echo 声明过的模型 id：`provider_for` 只在「路由里只有
                # 一个供应商」时对未声明模型兜底，而 `model_driver` 的示例驱动
                # 会自动注册进同一个路由（测试环境 = echo + md_async + md_sync），
                # 兜底分支不再成立。这里要钉的是「生成成功 → 草稿」，不是模型解析。
                model="seedream-3",
                model_id="seedream-3",
                progress=10,
                owner_id=owner_id,
                external_id=_EXTERNAL_A,
            )
            session.add(job)
            await session.commit()
            return job.id

    return asyncio.run(run())


def _reset_for_retry(job_id: str, external_id: str) -> None:
    """模拟业务侧点「重试」：状态回 running、换供应商任务号、清掉上一版产物。"""

    async def run() -> None:
        async with get_session_factory()() as session:
            job = await session.get(ImageJob, job_id)
            assert job is not None
            job.status = "running"
            job.progress = 0
            job.external_id = external_id
            job.output_url = None
            await session.commit()

    asyncio.run(run())


def _drafts_of(job_id: str) -> list[LibraryAsset]:
    """这个作业产生的草稿（按幂等键前缀找，不锁死键的具体格式）。"""

    async def run() -> list[LibraryAsset]:
        async with get_session_factory()() as session:
            rows = await session.scalars(
                select(LibraryAsset)
                .where(LibraryAsset.source_key.like(f"image:{job_id}%"))
                .order_by(LibraryAsset.created_at)
            )
            return list(rows)

    return asyncio.run(run())


def _job_status(job_id: str) -> str:
    async def run() -> str:
        async with get_session_factory()() as session:
            job = await session.get(ImageJob, job_id)
            assert job is not None
            return job.status

    return asyncio.run(run())


def _drop(job_id: str, asset_ids: list[str]) -> None:
    async def run() -> None:
        async with get_session_factory()() as session:
            await session.execute(delete(ImageJob).where(ImageJob.id == job_id))
            if asset_ids:
                await session.execute(delete(LibraryAsset).where(LibraryAsset.id.in_(asset_ids)))

            await session.commit()

    asyncio.run(run())


def _poll_once(job_id: str) -> None:
    asyncio.run(
        GenerationRunner(MemoryJobQueue()).handle_poll(
            Job(name=JOB_POLL, payload={"kind": "image", "record_id": job_id, "polls": 0})
        )
    )


class _Output:
    """当前这一跳该返回的产物 key。重试用例要换图，所以不能写死成字符串。"""

    key = ""

    def fresh(self, color: str) -> str:
        self.key = f"images/{suffix()}.png"
        put_blob(self.key, png_bytes(color))
        return self.key


@pytest.fixture
def succeeded_output(monkeypatch: pytest.MonkeyPatch) -> _Output:
    """让 echo 的 poll 直接给成功，产物就是我们预先放进存储的那个 key。

    用 `monkeypatch` 换 `poll`：真实 echo 要等 `_FAKE_DURATION_SEC`，而这里要测的
    不是供应商，是「成功之后有没有人把结果送进草稿箱」。
    """
    holder = _Output()
    holder.fresh("teal")

    async def fake_poll(
        self: EchoProvider, external_id: str, *, model: str = ""
    ) -> GenerationProgress:
        return GenerationProgress(
            external_id=external_id,
            state=GenerationState.SUCCEEDED,
            progress=100,
            output_url=holder.key,
        )

    async def fake_persist(self: ImageGenerationPipeline, record_id: str, url: str) -> str:
        return url  # 已经是对象 key（模拟落盘成功）

    monkeypatch.setattr(EchoProvider, "poll", fake_poll)
    monkeypatch.setattr(ImageGenerationPipeline, "persist_output", fake_persist)
    return holder


def test_generated_image_lands_in_draft(client, api: str, succeeded_output: _Output) -> None:
    owner = _admin_id()
    job_id = _seed_job(owner)
    created: list[str] = []
    try:
        _poll_once(job_id)

        rows = _drafts_of(job_id)
        assert len(rows) == 1, "生成成功之后草稿箱里必须有且只有这一条"
        row = rows[0]
        created.append(row.id)
        assert (row.state, row.visibility, row.origin) == ("draft", "personal", "generated")
        assert row.kind == "image"
        assert row.owner_id == owner
        # 名字带真实后缀：卡片显示时会剥掉，打包下载时要有
        assert row.name == "产品主图.png"
        assert row.size_bytes > 0 and (row.storage_key or "").startswith("library/blobs/")
        assert row.cover_key, "草稿也要有缩略图，否则卡片只能显示渐变块"

        admin = client.post(
            f"{api}/identity/login",
            json={"username": "admin", "password": "admin@12345"},
        )
        headers = {"Authorization": f"Bearer {admin.json()['data']['accessToken']}"}
        items = client.get(
            f"{api}/library/assets", params={"state": "draft", "pageSize": 100}, headers=headers
        ).json()["data"]["items"]
        assert row.id in {item["id"] for item in items}, "草稿箱接口要能查到这一条"
    finally:
        _drop(job_id, created)


def test_retried_job_gets_its_own_draft(client, api: str, succeeded_output: _Output) -> None:
    """重试出的是**另一张图**，草稿箱里必须多一条，而不是被幂等吃掉。

    重试复用同一个作业行（清 `external_id` 重投），所以幂等键只用作业 id 的话，
    第二张图会被判成「已经入过库」，草稿箱永远停着上一版——这正是「改完看效果
    还是旧图」那类最难解释的反馈。
    """
    owner = _admin_id()
    job_id = _seed_job(owner)
    created: list[str] = []
    try:
        _poll_once(job_id)
        first = _drafts_of(job_id)
        assert len(first) == 1
        created.append(first[0].id)

        _reset_for_retry(job_id, _EXTERNAL_B)
        succeeded_output.fresh("purple")
        _poll_once(job_id)

        rows = _drafts_of(job_id)
        assert len(rows) == 2, "重试后的新图也要进草稿箱"
        created.extend(row.id for row in rows if row.id not in created)
        assert rows[0].sha256 != rows[1].sha256, "换没换图这条用例就白跑了"
    finally:
        _drop(job_id, created)


def test_archive_failure_does_not_flip_generation_to_failed(
    client, api: str, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """入库失败（存储里没有这份字节）只留日志，生成仍是成功。"""
    owner = _admin_id()
    job_id = _seed_job(owner)
    missing = f"images/{suffix()}-missing.png"  # 故意不 put_blob

    async def fake_poll(
        self: EchoProvider, external_id: str, *, model: str = ""
    ) -> GenerationProgress:
        return GenerationProgress(
            external_id=external_id,
            state=GenerationState.SUCCEEDED,
            progress=100,
            output_url=missing,
        )

    async def fake_persist(self: ImageGenerationPipeline, record_id: str, url: str) -> str:
        return url

    monkeypatch.setattr(EchoProvider, "poll", fake_poll)
    monkeypatch.setattr(ImageGenerationPipeline, "persist_output", fake_persist)

    created: list[str] = []
    try:
        with caplog.at_level("ERROR", logger="generation.runner"):
            _poll_once(job_id)  # 不许抛出去：生成已经成功，入库只是附带动作

        assert _job_status(job_id) == "succeeded"
        assert _drafts_of(job_id) == [], "取不到字节时不许写半条草稿"
        # 失败必须留痕：静默跳过与「根本没接这条链路」在日志里长得一样就白搭了
        assert any("生成结果入库失败" in record.getMessage() for record in caplog.records)
    finally:
        _drop(job_id, created)
