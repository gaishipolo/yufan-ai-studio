"""备份与恢复：只验「备份能不能真的恢复」，不验打印文案。

这类功能最危险的形态是「命令跑成功了、恢复时才发现文件不可用」。所以用例完整走
一遍：建库 → 写数据 → 备份 → 清数据 → 恢复 → 读回。中间任何一步偷懒，
这条用例就失去意义。
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from sqlalchemy import delete, func, select


@pytest.fixture()
def temp_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from core.config import get_settings
    from db import session as db_session

    db_file = tmp_path / "app.db"
    monkeypatch.setenv("DB_URL", f"sqlite+aiosqlite:///{db_file.as_posix()}")
    get_settings.cache_clear()
    db_session.reset_state_for_tests()
    try:
        yield db_file
    finally:
        asyncio.run(db_session.dispose_engine())
        get_settings.cache_clear()
        db_session.reset_state_for_tests()


def test_sqlite_backup_restore_roundtrip(temp_db: Path, tmp_path: Path) -> None:
    from db import backup
    from db import session as db_session
    from db.outbox import OutboxEvent

    async def prepare() -> None:
        await db_session.init_db()
        async with db_session.get_session_factory()() as session:
            session.add(OutboxEvent(name="demo.ping", payload="{}"))
            await session.commit()

    asyncio.run(prepare())

    target, manifest = asyncio.run(backup.create_backup(tmp_path / "backups", note="演练"))
    assert manifest.tables["outbox_events"] == 1
    assert (target / "manifest.json").is_file()
    # create_all 建的库没有 alembic_version：清单里应留空而不是编一个版本号
    assert manifest.schema_head == ""

    async def wipe() -> None:
        await db_session.dispose_engine()
        async with db_session.get_session_factory()() as session:
            await session.execute(delete(OutboxEvent))
            await session.commit()

    asyncio.run(wipe())

    async def count() -> int:
        async with db_session.get_session_factory()() as session:
            return int(
                await session.scalar(select(func.count()).select_from(OutboxEvent)) or 0
            )

    assert asyncio.run(count()) == 0, "清数据失败，后面的恢复断言就没有意义了"

    asyncio.run(db_session.dispose_engine())
    backup.restore_backup(target, force=True)
    assert asyncio.run(count()) == 1
    # 覆盖前必须留下现场，否则「恢复了一个更旧的库」本身无法回退
    assert list(temp_db.parent.glob("app.db.pre-restore-*"))


def test_restore_refuses_to_overwrite_without_force(temp_db: Path, tmp_path: Path) -> None:
    from db import backup
    from db import session as db_session

    asyncio.run(db_session.init_db())
    target, _ = asyncio.run(backup.create_backup(tmp_path / "backups"))
    asyncio.run(db_session.dispose_engine())

    with pytest.raises(backup.BackupError) as excinfo:
        backup.restore_backup(target, force=False)
    assert "--force" in str(excinfo.value)


def test_restore_rejects_cross_dialect_backup(temp_db: Path, tmp_path: Path) -> None:
    import json

    from db import backup

    target = tmp_path / "fake"
    target.mkdir()
    (target / backup.DB_FILE_NAME).write_bytes(b"")
    (target / backup.MANIFEST_NAME).write_text(
        json.dumps(
            {"created_at": "2026-01-01T00:00:00+00:00", "app_env": "prod",
             "dialect": "postgresql", "file": backup.DB_FILE_NAME}
        ),
        encoding="utf-8",
    )
    with pytest.raises(backup.BackupError) as excinfo:
        backup.restore_backup(target, force=True)
    assert "跨方言" in str(excinfo.value)
