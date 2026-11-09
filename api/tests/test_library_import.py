"""旧素材导入（M1）：dry-run 对账、幂等、缺字节不中断。

真实字节搬运在开发库上手工验证过一次（19 条 dry-run、1 条真实导入、第二遍
`已存在 1`）。这里守的是三条**以后改代码最容易弄坏**的性质：

1. dry-run 绝不写库；
2. 有 `legacy:<id>` 的行就跳过（幂等）；
3. 单条字节缺失只记一笔，不中断整批、不写半条记录。
"""

from __future__ import annotations

import asyncio

from fastapi.testclient import TestClient

from infra import get_storage
from infra.archive import get_archive_storage
from modules.asset.models import Asset
from modules.library.import_archive import import_legacy_assets

OWNER = "library-import-test"


def _insert_legacy(asset_id: str, *, storage_key: str) -> None:
    async def run() -> None:
        from db.session import get_session_factory

        async with get_session_factory()() as session:
            session.add(
                Asset(
                    id=asset_id,
                    name="旧素材.png",
                    type="image",
                    mime_type="image/png",
                    size_bytes=10,
                    storage_key=storage_key,
                    owner_id=OWNER,
                    source="archive",
                )
            )
            await session.commit()

    asyncio.run(run())


def _run_import(**kwargs):
    async def run():
        from sqlalchemy import select

        from db.session import get_session_factory
        from modules.library.models import LibraryAsset

        async with get_session_factory()() as session:
            report = await import_legacy_assets(
                session,
                get_archive_storage(),
                get_storage(),
                owner=OWNER,
                **kwargs,
            )
            rows = (
                await session.scalars(
                    select(LibraryAsset).where(LibraryAsset.source_key.like("legacy:%"))
                )
            ).all()
            return report, rows

    return asyncio.run(run())


def test_dry_run_reports_without_writing(client: TestClient) -> None:
    _insert_legacy("legacydry001", storage_key="blobs/zz/never-exists.png")
    report, rows = _run_import(dry_run=True)
    assert report.dry_run is True
    assert report.total == 1
    assert report.imported == 1
    assert rows == [], "dry-run 不允许写任何行"


def test_missing_bytes_is_counted_but_does_not_abort(client: TestClient) -> None:
    _insert_legacy("legacymiss01", storage_key="blobs/zz/never-exists.png")
    report, rows = _run_import()
    # 前面的用例也往同一个 owner 下插了缺字节的行，所以这里是 ≥1 而不是 ==1
    assert report.missing_bytes >= 1
    assert rows == [], "字节缺失时不能留下一条打不开的资产"


def test_second_run_is_idempotent(client: TestClient) -> None:
    """先造一条能导入成功的行需要真实归档字节，这里退一步验证跳过逻辑：
    手工写一行 `source_key=legacy:<id>`，第二次跑必须记「已存在」。"""

    async def seed_v2() -> None:
        from db.session import get_session_factory
        from modules.library.models import LibraryAsset

        async with get_session_factory()() as session:
            session.add(
                LibraryAsset(
                    name="已导入.png",
                    kind="image",
                    origin="imported",
                    mime_type="image/png",
                    size_bytes=10,
                    owner_id=OWNER,
                    source_key="legacy:legacyidem01",
                )
            )
            await session.commit()

    _insert_legacy("legacyidem01", storage_key="blobs/zz/never-exists.png")
    asyncio.run(seed_v2())
    report, _ = _run_import()
    assert report.skipped_existing == 1
    assert report.imported == 0
