"""批量下载：异步打包、逐条跳过、上限闸门、取件与过期清理。

五个断言方向：

1. **真能解出文件**：不只看 `status=succeeded`，要把 zip 读回来数条目；
2. **草稿 / 回收站不整单失败**：能下的照下，不能下的逐条给原因；
3. **闸门在展开之后**：条目数与总字节数都按展开结果算，超限 413；
4. **任务归属**：别人的任务一律 404，未完成的取件 409；
5. **过期清理**：字节删掉、状态置 `expired`、行留 7 天，取件 409。
"""

from __future__ import annotations

import asyncio
import io
import zipfile

import pytest
from fastapi.testclient import TestClient
from library_support import (
    LIB_RW,
    admin_headers,
    backdate_download,
    build_download_job,
    fetch_download_job,
    make_user,
    set_visibility,
    upload_image,
)


def _create(client: TestClient, api: str, headers: dict[str, str], ids: list[str]):
    return client.post(f"{api}/library/downloads", json={"ids": ids}, headers=headers)


def test_download_builds_a_zip_with_every_file(client: TestClient, api: str) -> None:
    admin = admin_headers(client)
    owner, owner_h = make_user(client, admin, LIB_RW)
    first = upload_image(client, api, owner_h, name="猫.png", color="crimson")
    second = upload_image(client, api, owner_h, name="dog.png", color="gold")

    created = _create(client, api, owner_h, [first["id"], second["id"]])
    assert created.status_code == 202, created.text
    job = created.json()["data"]
    assert job["status"] == "queued"
    assert job["assetCount"] == 2
    assert job["downloadUrl"] is None, "还没压完就给下载地址，点开必然是 404"

    build_download_job(job["id"])

    status = client.get(f"{api}/library/downloads/{job['id']}", headers=owner_h).json()["data"]
    assert status["status"] == "succeeded"
    assert status["sizeBytes"] > 0
    assert status["downloadUrl"].endswith(f"/library/downloads/{job['id']}/file")

    served = client.get(f"{api}/library/downloads/{job['id']}/file", headers=owner_h)
    assert served.status_code == 200, served.text
    assert served.headers["content-type"] == "application/zip"
    with zipfile.ZipFile(io.BytesIO(served.content)) as archive:
        names = archive.namelist()
    assert len(names) == 2
    # `序号_原名`：中文名要保留，序号保证解压后的顺序与选择一致
    assert names[0].startswith("001_")
    assert "猫" in names[0]
    assert names[1].startswith("002_")


def test_download_skips_drafts_and_trash_without_failing(
    client: TestClient, api: str
) -> None:
    admin = admin_headers(client)
    owner, owner_h = make_user(client, admin, LIB_RW)
    keep = upload_image(client, api, owner_h, name="keep.png", color="lime")
    trash = upload_image(client, api, owner_h, name="gone.png", color="maroon")
    client.delete(f"{api}/library/assets/{trash['id']}", headers=owner_h)

    created = _create(client, api, owner_h, [keep["id"], trash["id"]])
    assert created.status_code == 202, created.text
    job = created.json()["data"]
    skipped = job["skipped"]
    assert [item["id"] for item in skipped] == [trash["id"]]
    assert skipped[0]["reason"] == "回收站内容不可下载"

    build_download_job(job["id"])
    with zipfile.ZipFile(
        io.BytesIO(
            client.get(f"{api}/library/downloads/{job['id']}/file", headers=owner_h).content
        )
    ) as archive:
        assert len(archive.namelist()) == 1

    assert fetch_download_job(job["id"])["asset_ids"] == [keep["id"]]  # type: ignore[index]


def test_download_rejects_when_everything_is_skipped(client: TestClient, api: str) -> None:
    admin = admin_headers(client)
    owner, owner_h = make_user(client, admin, LIB_RW)
    trash = upload_image(client, api, owner_h, color="olive")
    client.delete(f"{api}/library/assets/{trash['id']}", headers=owner_h)

    rejected = _create(client, api, owner_h, [trash["id"]])
    assert rejected.status_code == 400
    assert rejected.json()["code"] == 40086


def test_download_enforces_item_and_byte_limits(
    client: TestClient, api: str, monkeypatch
) -> None:
    from core.config import settings

    admin = admin_headers(client)
    owner, owner_h = make_user(client, admin, LIB_RW)
    first = upload_image(client, api, owner_h, color="aqua")
    second = upload_image(client, api, owner_h, color="beige")

    monkeypatch.setattr(settings, "library_download_max_items", 1)
    too_many = _create(client, api, owner_h, [first["id"], second["id"]])
    assert too_many.status_code == 413
    assert too_many.json()["details"]["unit"] == "items"
    assert too_many.json()["details"]["actual"] == 2

    monkeypatch.setattr(settings, "library_download_max_items", 100)
    monkeypatch.setattr(settings, "library_download_max_bytes", 10)
    too_big = _create(client, api, owner_h, [first["id"]])
    assert too_big.status_code == 413
    assert too_big.json()["details"]["unit"] == "bytes"


def test_download_is_owner_only_and_not_ready_until_built(
    client: TestClient, api: str
) -> None:
    admin = admin_headers(client)
    owner, owner_h = make_user(client, admin, LIB_RW)
    _, other_h = make_user(client, admin, LIB_RW)
    asset = upload_image(client, api, owner_h, color="indigo")

    job = _create(client, api, owner_h, [asset["id"]]).json()["data"]

    early = client.get(f"{api}/library/downloads/{job['id']}/file", headers=owner_h)
    assert early.status_code == 409
    assert early.json()["code"] == 40989

    stranger = client.get(f"{api}/library/downloads/{job['id']}", headers=other_h)
    assert stranger.status_code == 404, "别人的任务既不该能看，也不该确认它存在"


def test_download_gc_expires_and_purges_rows(client: TestClient, api: str) -> None:
    from modules.library.downloads import run_download_gc

    admin = admin_headers(client)
    owner, owner_h = make_user(client, admin, LIB_RW)
    asset = upload_image(client, api, owner_h, color="firebrick")
    job = _create(client, api, owner_h, [asset["id"]]).json()["data"]
    build_download_job(job["id"])
    key = fetch_download_job(job["id"])["storage_key"]  # type: ignore[index]
    assert key

    backdate_download(job["id"], expires_days=1)
    report = asyncio.run(run_download_gc())
    assert report.expired >= 1
    row = fetch_download_job(job["id"])
    assert row is not None and row["status"] == "expired"  # type: ignore[index]
    assert row["storage_key"] == ""  # type: ignore[index]

    expired = client.get(f"{api}/library/downloads/{job['id']}/file", headers=owner_h)
    assert expired.status_code == 409
    assert expired.json()["details"]["status"] == "expired"

    # 再往后 8 天：行也该清掉（字节早没了，留着只是死行）
    backdate_download(job["id"], expires_days=8)
    asyncio.run(run_download_gc())
    assert fetch_download_job(job["id"]) is None


def test_download_gc_keeps_the_row_when_deleting_the_bytes_fails(
    client: TestClient, api: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """删字节失败时必须**保留 key 与状态**，让下一轮 GC 重试。

    原来这里是「先 `row.storage_key = ""` 再删字节」，而删除异常被
    `contextlib.suppress` 吞掉：事务照常提交，那一行变成 expired 且没有 key，
    而下一轮 GC 只选 succeeded/failed —— 那个最大 2GB 的 zip 就永久留在对象
    存储里，没有任何记录还知道它存在。
    """
    from infra import get_storage
    from modules.library.downloads import run_download_gc

    admin = admin_headers(client)
    owner, owner_h = make_user(client, admin, LIB_RW)
    asset = upload_image(client, api, owner_h, color="firebrick")
    job = _create(client, api, owner_h, [asset["id"]]).json()["data"]
    build_download_job(job["id"])
    key = fetch_download_job(job["id"])["storage_key"]
    assert key

    backdate_download(job["id"], expires_days=1)

    async def failing_delete(key: str) -> None:
        raise RuntimeError("对象存储暂时不可用")

    monkeypatch.setattr(get_storage(), "delete", failing_delete)

    asyncio.run(run_download_gc())

    row = fetch_download_job(job["id"])
    assert row is not None
    assert row["status"] == "succeeded", "删失败不该把行改成 expired（下一轮就选不到了）"
    assert row["storage_key"] == key, "删失败必须保留 key，下一轮才能重试"


def test_claiming_a_download_job_is_atomic() -> None:
    """queued → running 必须是**一条条件更新**：两个 worker 只有一个能领到。

    原来这里是「读行 → 判 queued → 置 running → commit」，两个 worker 同时读到
    queued 就会各自压一遍包（同一个 key 互相覆盖、CPU 与磁盘翻倍）。
    """
    from db.session import get_session_factory
    from modules.library.downloads import _claim_job
    from modules.library.models import LibraryDownloadJob

    async def run() -> tuple[bool, bool]:
        async with get_session_factory()() as session:
            job = LibraryDownloadJob(owner_id="system", asset_ids=[], status="queued")
            session.add(job)
            await session.commit()
            first = await _claim_job(session, job.id)
            second = await _claim_job(session, job.id)
            return first, second

    first, second = asyncio.run(run())
    assert first is True, "第一个 worker 要抢到"
    assert second is False, "第二个 worker 必须抢不到（否则会双压包）"


def test_finishing_a_download_job_does_not_overwrite_a_finished_row() -> None:
    """终态写入也是条件更新：只覆盖仍在 running 的那一次运行。

    失败分支原来无条件把整行改成 failed（连 storage_key 一起清空），于是
    「另一个 worker 已经压成功」的那一行会被覆盖——用户看到「打包失败」，GC
    之后又按 failed 把那个其实可用的 zip 删掉。
    """
    from db.session import get_session_factory
    from modules.library.downloads import _finish_job
    from modules.library.models import LibraryDownloadJob

    async def run() -> bool:
        async with get_session_factory()() as session:
            job = LibraryDownloadJob(
                owner_id="system",
                asset_ids=[],
                status="succeeded",
                storage_key="library/downloads/x.zip",
            )
            session.add(job)
            await session.commit()
            return await _finish_job(session, job.id, status="failed", error="boom")

    assert asyncio.run(run()) is False, "已经结束的任务不该再被改写"

    async def read() -> str | None:
        from sqlalchemy import select

        from db.session import get_session_factory
        from modules.library.models import LibraryDownloadJob

        async with get_session_factory()() as session:
            rows = (
                await session.scalars(
                    select(LibraryDownloadJob).where(
                        LibraryDownloadJob.storage_key == "library/downloads/x.zip"
                    )
                )
            ).all()
            return rows[0].status if rows else None

    assert asyncio.run(read()) == "succeeded"


def test_download_gc_marks_stuck_jobs_failed(client: TestClient, api: str) -> None:
    from modules.library.downloads import run_download_gc

    admin = admin_headers(client)
    owner, owner_h = make_user(client, admin, LIB_RW)
    asset = upload_image(client, api, owner_h, color="tan")
    job = _create(client, api, owner_h, [asset["id"]]).json()["data"]

    # 造一条「三小时前发起、至今没人跑」的任务：worker 停了就是这个样子
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import update

    from db.session import get_session_factory
    from modules.library.models import LibraryDownloadJob

    async def backdate() -> None:
        async with get_session_factory()() as session:
            await session.execute(
                update(LibraryDownloadJob)
                .where(LibraryDownloadJob.id == job["id"])
                .values(created_at=datetime.now(UTC) - timedelta(hours=3))
            )
            await session.commit()

    asyncio.run(backdate())
    report = asyncio.run(run_download_gc())
    assert report.failed >= 1
    row = fetch_download_job(job["id"])
    assert row["status"] == "failed"  # type: ignore[index]
    assert "超时" in row["error"]  # type: ignore[index]


def test_remote_download_file_streams_instead_of_redirecting(
    client: TestClient, api: str
) -> None:
    """远端驱动下取件必须是同源的 200，不能是 307。

    `307` 到对象存储，对「浏览器用 XHR 取 blob 再保存」就是一次跨域请求：
    目标不回 CORS 头就被拦成 `blocked by CORS policy`。而桶的 CORS 是环境配置
    （MinIO 当前的 `PutBucketCors` 直接回 NotImplemented），换个部署就复发。

    这里把本地驱动换成一个「有字节流、没有本地路径」的替身来模拟 S3。只替换这
    一个请求的依赖，其余用例照常用真实驱动。
    """
    from collections.abc import AsyncIterator
    from pathlib import Path

    from api.deps import get_storage

    admin = admin_headers(client)
    owner, owner_h = make_user(client, admin, LIB_RW)
    image = upload_image(client, api, owner_h, name="stream.png", color="teal")
    job = _create(client, api, owner_h, [image["id"]]).json()["data"]
    build_download_job(job["id"])

    row = fetch_download_job(job["id"])
    assert row is not None and row["storage_key"]
    payload = asyncio.run(get_storage().get(row["storage_key"]))

    class _Remote:
        """远端驱动的关键特征：`local_path` 为 None，字节只能流式拿。"""

        def local_path(self, key: str) -> Path | None:
            return None

        def iter_chunks(
            self, key: str, *, chunk_size: int = 1024 * 1024
        ) -> AsyncIterator[bytes]:
            async def _gen() -> AsyncIterator[bytes]:
                yield payload

            return _gen()

        async def presigned_download_url(self, key: str, *, expires_in: int = 900) -> str:
            return f"https://bucket.example/{key}?sig=stub"

    app = client.app
    app.dependency_overrides[get_storage] = lambda: _Remote()  # type: ignore[attr-defined]
    try:
        served = client.get(f"{api}/library/downloads/{job['id']}/file", headers=owner_h)
    finally:
        app.dependency_overrides.pop(get_storage, None)  # type: ignore[attr-defined]

    assert served.status_code == 200, served.text
    assert "location" not in served.headers, "还在 307 到桶：浏览器会开始报 CORS"
    assert served.content == payload
    assert served.headers["content-length"] == str(len(payload))
    # 走 307 时文件名是 key 的最后一段（一串 uuid），存下来只看到乱码名
    assert served.headers["content-disposition"].endswith(f"library-{job['id']}.zip")



def test_download_rechecks_read_access_before_packing(
    client: TestClient, api: str
) -> None:
    """建任务后被取消共享：打包时要按同一套判据重判一次。

    建任务时判过「能不能读」，但打包是异步的——真正压包可能在几分钟后，中间
    封装可能被取消共享、人可能被移出小组。只在建任务时判，等于把当时的权限
    冻结进任务里：人已经失权，包照样生成、照样能取件（取件只校验 owner）。
    """
    admin = admin_headers(client)
    owner, owner_h = make_user(client, admin, LIB_RW)
    _, viewer_h = make_user(client, admin, LIB_RW)
    image = upload_image(client, api, owner_h, name="shared.png", color="khaki")
    set_visibility(image["id"], "team")

    created = _create(client, api, viewer_h, [image["id"]])
    assert created.status_code == 202, created.text
    job = created.json()["data"]

    set_visibility(image["id"], "personal")  # 打包之前撤销共享

    build_download_job(job["id"])
    stored = fetch_download_job(job["id"])
    assert stored["status"] == "failed", "失权后不该还能打出文件来"
    assert "没有可打包的文件" in (stored["error"] or "")


class _RemoteStorage:
    """只实现 `_stage` 用得到的三件事，用来盯住「怎么取字节」。

    远端驱动的 `local_path()` 返回 None，取字节只有 `get()` 与 `iter_chunks()`
    两条路。前者把整个对象读进内存——2GB 的包会在压包阶段把进程打挂，而且
    是「小文件测不出来、只有真实大素材才炸」的那种。
    """

    def __init__(self, chunks: list[bytes], *, fail_after: int | None = None) -> None:
        self.chunks = chunks
        self.fail_after = fail_after

    def local_path(self, key: str) -> None:
        return None

    async def get(self, key: str) -> bytes:
        raise AssertionError("打包不该把整个对象读进内存（要用 iter_chunks 分块）")

    async def iter_chunks(self, key: str, *, chunk_size: int = 1024 * 1024):  # noqa: ANN201
        for index, chunk in enumerate(self.chunks):
            if self.fail_after is not None and index >= self.fail_after:
                raise RuntimeError("网络中断")
            yield chunk


def test_staging_a_remote_object_streams_to_disk(tmp_path) -> None:  # noqa: ANN001
    """远端对象要分块落盘，不能 `get()` 全量进内存。"""
    from modules.library.downloads import _stage

    storage = _RemoteStorage([b"abc", b"def"])
    target = asyncio.run(_stage(storage, "k", tmp_path, 0))  # type: ignore[arg-type]
    assert target is not None
    assert target.read_bytes() == b"abcdef", "分块拼接后必须与原对象逐字节一致"


def test_staging_drops_a_half_written_file(tmp_path) -> None:  # noqa: ANN001
    """中途失败不能留下半个文件：那会让「文件读取失败」变成「打包出损坏文件」。"""
    from modules.library.downloads import _stage

    storage = _RemoteStorage([b"partial", b"rest"], fail_after=1)
    assert asyncio.run(_stage(storage, "k", tmp_path, 7)) is None  # type: ignore[arg-type]
    assert list(tmp_path.iterdir()) == []
