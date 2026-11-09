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

from fastapi.testclient import TestClient
from library_support import (
    LIB_RW,
    admin_headers,
    backdate_download,
    build_download_job,
    fetch_download_job,
    make_user,
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


def test_download_expands_packs_to_members(client: TestClient, api: str) -> None:
    admin = admin_headers(client)
    owner, owner_h = make_user(client, admin, LIB_RW)
    first = upload_image(client, api, owner_h, name="m1.png", color="coral")
    second = upload_image(client, api, owner_h, name="m2.png", color="cyan")
    pack = client.post(
        f"{api}/library/packs",
        json={"name": "整包下", "assetIds": [first["id"], second["id"]]},
        headers=owner_h,
    ).json()["data"]

    created = _create(client, api, owner_h, [pack["id"]])
    assert created.status_code == 202, created.text
    job = created.json()["data"]
    assert job["assetCount"] == 2, "封装要展开成成员，不是把 pack 行本身打包"

    build_download_job(job["id"])
    with zipfile.ZipFile(
        io.BytesIO(
            client.get(f"{api}/library/downloads/{job['id']}/file", headers=owner_h).content
        )
    ) as archive:
        assert len(archive.namelist()) == 2


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
