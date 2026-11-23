"""下载任务的资产清单拆成关联表之后要守住的六条不变量。

## 为什么值得单独立一组用例

清单原来在 `library_download_jobs.asset_ids` 这个 JSON 列里，引用检查（「这条
资产能不能彻底删除」）靠 `LIKE '%"id"%'` 在 JSON **文本**里粗筛。粗筛是「字符串
里恰好出现了这个 id」，不是「这个 id 在清单里」——而这个判据的落点是**不可逆
删除**：漏查 = 没人引用 = 删除放行 = 引用方的包裂开。

清单还带着**顺序**语义（包内按 `001_名`、`002_名` 命名，序号来自用户勾选的
顺序），藏在 JSON 数组里既没法用索引，也没法拉平成可 join 的行。

现在清单在 `library_download_job_assets`（`job_id` / `asset_id` / `position`），
引用检查走一次普通的 `IN` 查询，索引直接可用。

这几条用例盯的是「拆完之后最容易悄悄坏掉的地方」：

1. 清单真的在关联表里，JSON 列真的没了（**模型层与库层都要对**）；
2. `position` 保住用户勾选的顺序（包内文件名的序号来自它）；
3. 引用检查**精确**：只报真正被引用的资产；
4. 不许再退回子串粗筛（源码守卫——删掉粗筛是这次改动的核心）；
5. 清理过期任务时关联行一并删掉（关联表刻意**没加外键**，级联得自己写）。

## 已知的一处不对称

关联表**没有外键**（与 `library_pack_items` 同），因为本模块的约定是「关系由
应用层维护」，而引用检查本身要回答的正是「还引用着吗」——用外键会把这个判断
挪到写入时刻，变成约束冲突。这条取舍的代价就是第 5 条用例：级联删除得自己
写，忘了就是孤儿行。
"""

from __future__ import annotations

import asyncio
import io
import zipfile
from pathlib import Path

from fastapi.testclient import TestClient
from library_support import (
    LIB_RW,
    admin_headers,
    backdate_download,
    build_download_job,
    make_user,
    upload_image,
)


def _make_job(asset_ids: list[str], *, status: str = "succeeded") -> str:
    """造一条下载任务，并把资产清单写进关联表（与生产写入同形状）。"""

    async def run() -> str:
        from db.session import get_session_factory
        from modules.library.models import LibraryDownloadJob, LibraryDownloadJobAsset

        async with get_session_factory()() as session:
            job = LibraryDownloadJob(
                owner_id="system",
                asset_count=len(asset_ids),
                status=status,
                storage_key="",
            )
            session.add(job)
            await session.flush()
            for position, asset_id in enumerate(asset_ids):
                session.add(
                    LibraryDownloadJobAsset(
                        job_id=job.id, asset_id=asset_id, position=position
                    )
                )
            await session.commit()
            return job.id

    return asyncio.run(run())


def _rows(job_id: str) -> list[tuple[str, int]]:
    """关联表里这个任务的清单（按 `position` 排序）。"""

    async def run() -> list[tuple[str, int]]:
        from sqlalchemy import select

        from db.session import get_session_factory
        from modules.library.models import LibraryDownloadJobAsset

        async with get_session_factory()() as session:
            rows = (
                await session.execute(
                    select(
                        LibraryDownloadJobAsset.asset_id,
                        LibraryDownloadJobAsset.position,
                    )
                    .where(LibraryDownloadJobAsset.job_id == job_id)
                    .order_by(LibraryDownloadJobAsset.position)
                )
            ).all()
            return [(str(asset_id), int(position)) for asset_id, position in rows]

    return asyncio.run(run())


def _references(asset_ids: list[str]) -> dict[str, list[str]]:
    """走聚合入口问「这些资产还被谁引用」。"""

    async def run() -> dict[str, list[str]]:
        from core.ports.library_references import ask_library_references
        from db.session import get_session_factory

        async with get_session_factory()() as session:
            found = await ask_library_references(session, asset_ids)
            return {key: list(value) for key, value in found.items()}

    return asyncio.run(run())


def _table_columns(table: str) -> list[str]:
    """库层看这张表有哪些列。

    sqlite 专用（`PRAGMA table_info`），而测试环境就是 sqlite —— 与
    `tests/test_migrations.py` 用的是同一套办法。
    """
    from sqlalchemy import text

    from db.session import get_session_factory

    async def run() -> list[str]:
        async with get_session_factory()() as session:
            rows = (await session.execute(text(f"PRAGMA table_info({table})"))).all()
            return [str(row[1]) for row in rows]

    return asyncio.run(run())


def test_清单在关联表里_json列已经没了(client: TestClient) -> None:
    """模型层与库层都要对：只改模型不改库（或反过来）都会在这里露馅。"""
    from modules.library.models import LibraryDownloadJob, LibraryDownloadJobAsset

    columns = {column.name for column in LibraryDownloadJob.__table__.columns}
    assert "asset_ids" not in columns, "模型里还有 asset_ids 列"
    assert "asset_count" in columns, "模型里缺 asset_count（冗余的个数）"

    table_columns = set(_table_columns("library_download_jobs"))
    assert "asset_ids" not in table_columns, "库里还有 asset_ids 列"
    assert "asset_count" in table_columns, "库里缺 asset_count 列"

    assert "library_download_job_assets" in {
        table.name for table in LibraryDownloadJobAsset.metadata.tables.values()
    }


def test_关联行的顺序等于提交顺序(client: TestClient) -> None:
    """`position` 保住用户勾选的顺序。

    顺序是有语义的：包内文件按 `001_名`、`002_名` 命名，序号来自它。
    JSON 数组本来也保住了顺序，但存进表里就得靠 `position` 显式留住——
    这一列要是丢了或写错了，包里文件的序号就乱了，而 zip 不会报错。
    """
    job_id = _make_job(["asset-c", "asset-a", "asset-b"])

    assert _rows(job_id) == [("asset-c", 0), ("asset-a", 1), ("asset-b", 2)]


def test_包内文件顺序跟着关联表走(client: TestClient, api: str) -> None:
    """端到端：勾选顺序 → 包内文件序号。"""
    admin = admin_headers(client)
    owner, owner_h = make_user(client, admin, LIB_RW)
    first = upload_image(client, api, owner_h, name="一.png", color="crimson")
    second = upload_image(client, api, owner_h, name="二.png", color="gold")
    third = upload_image(client, api, owner_h, name="三.png", color="lime")

    created = client.post(
        f"{api}/library/downloads",
        json={"ids": [first["id"], second["id"], third["id"]]},
        headers=owner_h,
    )
    assert created.status_code == 202, created.text
    job = created.json()["data"]
    assert job["assetCount"] == 3

    build_download_job(job["id"])

    served = client.get(f"{api}/library/downloads/{job['id']}/file", headers=owner_h)
    assert served.status_code == 200, served.text
    with zipfile.ZipFile(io.BytesIO(served.content)) as archive:
        names = archive.namelist()

    assert [name[:4] for name in names] == ["001_", "002_", "003_"], names
    assert "一" in names[0], names
    assert "二" in names[1], names
    assert "三" in names[2], names


def test_引用检查只报真正被引用的资产(client: TestClient) -> None:
    """精确：清单里有的报出来，不在清单里的一个字都不提。

    这条是「能不能彻底删除」的判据——多报是骚扰，少报是**数据丢失**。
    """
    _make_job(["asset-in-list"])

    found = _references(["asset-in-list", "asset-not-in-list"])

    assert "asset-in-list" in found, found
    assert "asset-not-in-list" not in found, found
    assert any("下载任务" in desc for desc in found["asset-in-list"]), found


def test_前缀相似的两个id不互相误报(client: TestClient) -> None:
    """`asset-ab` 与 `asset-abc` 是两个不同的资产。

    子串粗筛（`LIKE '%asset-ab%'`）会把后者捞出来——它只能靠**二次精确
    比对**兜住，而那正是「查不到 = 没人引用」这类误判的来源。关联表上的
    `IN` 查询一次就是精确的。
    """
    _make_job(["asset-ab"])

    found = _references(["asset-ab", "asset-abc"])

    assert set(found) == {"asset-ab"}, found


def test_引用检查不再做子串粗筛() -> None:
    """源码守卫：下载任务那支 checker 不许再出现 `like(`。

    粗筛的问题不是「慢」，是「它的正确性依赖二次比对」——而二次比对一旦
    因为格式变化（清单里存的不再是裸 id）失效，漏查就会伪装成「没人引用」。
    """
    path = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "modules"
        / "library"
        / "references.py"
    )
    text = path.read_text(encoding="utf-8")

    assert ".like(" not in text, "下载任务引用检查退回子串粗筛了"


def test_清理过期任务后关联行不残留(client: TestClient) -> None:
    """关联表**刻意没加外键**，级联删除得自己写。

    漏写的后果不是「多几行垃圾」：孤儿行会让引用检查报出「这个资产还被
    下载任务 xxx 引用」，而那个任务早就不存在了——于是这条资产**永远删不掉**，
    而且看不出原因。
    """
    from modules.library.downloads import run_download_gc

    job_id = _make_job(["asset-old"], status="succeeded")
    # 过期时间挪到 8 天前：既过期了，也过了「过期后保留 7 天」的清理线。
    backdate_download(job_id, expires_days=8)

    first = asyncio.run(run_download_gc())
    assert first.expired >= 1, first
    assert _rows(job_id) == [("asset-old", 0)], "过期只是改状态，行还在（要等清理轮）"

    second = asyncio.run(run_download_gc())
    assert second.purged >= 1, second
    assert _rows(job_id) == [], "任务行删了，关联行还留着——孤儿行会让资产永远删不掉"
