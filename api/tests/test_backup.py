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
    from core.config import reload_settings
    from db import session as db_session

    db_file = tmp_path / "app.db"
    monkeypatch.setenv("DB_URL", f"sqlite+aiosqlite:///{db_file.as_posix()}")
    reload_settings()
    db_session.reset_state_for_tests()
    try:
        yield db_file
    finally:
        asyncio.run(db_session.dispose_engine())
        reload_settings()
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


def test_backup_and_restore_close_their_sqlite_connections(
    temp_db: Path, tmp_path: Path
) -> None:
    """拷完必须放开文件句柄，不能等 GC。

    `with sqlite3.connect(...)` 是**事务**上下文管理器：`__exit__` 只 commit /
    rollback，不 close。连接要等引用计数归零才由 `__del__` 关闭，其间：

    - Windows 上文件仍被占用，「恢复时替换数据库文件」会 `PermissionError`；
    - 每次备份漏一个 fd（备份是周期任务，一天漏 2 个）；
    - CPython 的 `__del__` 打 `ResourceWarning: unclosed database`，在 CI 日志里
      会被标成 failure annotation——一个绿色构建挂着两个红叉。

    这条用例不看实现，只看结果：临时屏蔽 gc，把 `REFCNT` 之外的清理手段挡掉，
    然后**显式 gc.collect()**，此时若还有未关闭的连接就会冒出 ResourceWarning。
    """
    import gc
    import warnings

    from db import backup
    from db import session as db_session

    asyncio.run(db_session.init_db())
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always", ResourceWarning)
        target, _ = asyncio.run(backup.create_backup(tmp_path / "backups"))
        asyncio.run(db_session.dispose_engine())
        backup.restore_backup(target, force=True)
        gc.collect()

    leaks = [
        str(w.message)
        for w in caught
        # 只认裸 `sqlite3.Connection`：这条用例管的是 `_dump_sqlite` /
        # `_restore_sqlite` 里那两个直接 connect 的连接。`aiosqlite` 的连接是引擎池
        # 管的，它的少量残留来自「每个操作一个 asyncio.run」这个测试架构（跨 loop
        # 关闭池连接不彻底），不是本函数的责任，见报告 §9 的限制清单。
        if issubclass(w.category, ResourceWarning) and "sqlite3.Connection" in str(w.message)
    ]
    assert not leaks, f"备份/恢复没有关闭 sqlite 连接 -> {leaks}"


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


# --------------------------------------------------------------------------- #
# 保留期清理
# --------------------------------------------------------------------------- #
def _fake_backup(out_dir: Path, name: str, created_at: str) -> Path:
    import json

    from db import backup

    target = out_dir / name
    target.mkdir(parents=True)
    (target / backup.MANIFEST_NAME).write_text(
        json.dumps(
            {
                "created_at": created_at,
                "app_env": "test",
                "dialect": "sqlite",
                "file": backup.DB_FILE_NAME,
            }
        ),
        encoding="utf-8",
    )
    return target


def test_prune_uses_manifest_time_not_mtime(tmp_path: Path) -> None:
    """按清单时间判断，不按目录 mtime。

    恢复演练会把备份 `cp` 到别处，mtime 变成今天；按 mtime 排会留着最老的一份、
    把其余全删——正好删反。这里刻意把「旧备份」的 mtime 刷成现在来复现这个陷阱。
    """
    import os
    from datetime import UTC, datetime, timedelta

    from db import backup

    out = tmp_path / "backups"
    old = _fake_backup(
        out, "backup-old", (datetime.now(tz=UTC) - timedelta(days=30)).isoformat()
    )
    fresh = _fake_backup(
        out, "backup-fresh", (datetime.now(tz=UTC) - timedelta(days=1)).isoformat()
    )
    os.utime(old, None)  # 模拟「刚 cp 出来」：mtime 是最新的

    assert backup.prune_backups(out, retention_days=7) == 1
    assert not old.exists(), "过期备份应该被删除（mtime 新不代表数据新）"
    assert fresh.exists()


def test_prune_never_touches_foreign_directories(tmp_path: Path) -> None:
    """`BACKUP_DIR` 指错到 `var/` 时，通配符删除会把整个数据目录带走。"""
    from datetime import UTC, datetime, timedelta

    from db import backup

    out = tmp_path / "backups"
    stray = _fake_backup(
        out, "not-a-backup", (datetime.now(tz=UTC) - timedelta(days=999)).isoformat()
    )
    no_manifest = out / "backup-manual"
    no_manifest.mkdir()

    assert backup.prune_backups(out, retention_days=7) == 0
    assert stray.exists(), "前缀不是 backup- 的目录不许碰"
    assert no_manifest.exists(), "没有清单的目录不许碰（可能正是唯一现场）"


def test_prune_disabled_when_retention_not_positive(tmp_path: Path) -> None:
    from datetime import UTC, datetime, timedelta

    from db import backup

    out = tmp_path / "backups"
    ancient = _fake_backup(
        out, "backup-ancient", (datetime.now(tz=UTC) - timedelta(days=999)).isoformat()
    )

    assert backup.prune_backups(out, retention_days=0) == 0
    assert ancient.exists(), "0 表示永不清理：删备份不可逆，不做默认删除"


# --------------------------------------------------------------------------- #
# 自动备份周期任务
# --------------------------------------------------------------------------- #
def test_backup_loop_does_nothing_when_disabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from core.config import reload_settings
    from db import backup

    monkeypatch.setenv("BACKUP_AUTO_ENABLED", "false")
    monkeypatch.setenv("BACKUP_DIR", str(tmp_path / "auto"))
    reload_settings()

    asyncio.run(backup.backup_loop())

    assert not (tmp_path / "auto").exists(), "开关关闭时不该碰磁盘"


def test_backup_loop_writes_backup_and_prunes(
    temp_db: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from core.config import reload_settings
    from db import backup
    from db import session as db_session

    out_dir = tmp_path / "auto"
    monkeypatch.setenv("BACKUP_AUTO_ENABLED", "true")
    monkeypatch.setenv("BACKUP_DIR", str(out_dir))
    monkeypatch.setenv("BACKUP_RETENTION_DAYS", "7")
    reload_settings()

    asyncio.run(db_session.init_db())
    # 先放一份过期备份进去，验证同一轮里「备份 + 清理」都会发生
    from datetime import UTC, datetime, timedelta

    stale = _fake_backup(
        out_dir, "backup-stale", (datetime.now(tz=UTC) - timedelta(days=30)).isoformat()
    )

    asyncio.run(backup.backup_loop())

    created = [p for p in out_dir.iterdir() if p.name.startswith("backup-") and p != stale]
    assert len(created) == 1, "应该生成一份新备份"
    manifest = backup.read_manifest(created[0])
    assert manifest.note.startswith("auto:")
    assert not stale.exists(), "过期备份应在同一轮里被清掉"


def test_external_dump_command_has_a_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    """`pg_dump` 卡住必须被终止，而不是永远等下去。

    这段代码跑在 `asyncio.to_thread` 里：取消协程不会杀掉子进程，也不会回收那个
    线程。不设超时的话，每小时攒一个卡死的 `pg_dump`，线程池填满之后连审计落库、
    预览派生这些无关的事都要排队——症状是「服务整体变慢」，而根因在一小时前。
    """
    import subprocess

    from db import backup

    def fake_run(command, **kwargs):  # type: ignore[no-untyped-def]
        assert kwargs.get("timeout"), "调用 pg_dump 时必须带 timeout"
        raise subprocess.TimeoutExpired(cmd=command, timeout=kwargs["timeout"])

    monkeypatch.setattr(backup.subprocess, "run", fake_run)

    with pytest.raises(backup.BackupError, match="未返回"):
        backup._run(["pg_dump", "postgresql://db/x"], what="pg_dump")


async def test_auto_backup_survives_unexpected_errors_and_still_prunes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """备份失败不能把这一轮的清理一起带走，也不能让异常冒到调度器。

    注释写的是「不让异常冒出去」，但原来只捕 `BackupError` / `OSError`。
    库连不上抛的是 `sqlalchemy.exc.OperationalError`（不是 OSError），于是
    备份失败 → 异常穿出去 → 清理那一步被跳过；而清理是**唯一**能让磁盘不涨的
    东西，恰恰在出故障的时候最需要它。
    """
    from sqlalchemy.exc import OperationalError

    from db import backup

    async def boom(out_dir: Path, *, note: str = "") -> tuple[Path, backup.BackupManifest]:
        raise OperationalError("SELECT 1", {}, Exception("库连不上"))

    pruned: list[int] = []

    def fake_prune(out_dir: Path, *, retention_days: int) -> int:
        pruned.append(retention_days)
        return 0

    monkeypatch.setattr(backup, "create_backup", boom)
    monkeypatch.setattr(backup, "prune_backups", fake_prune)
    monkeypatch.setattr(
        backup,
        "get_settings",
        lambda: type(
            "S",
            (),
            {
                "backup_auto_enabled": True,
                "backup_dir": str(tmp_path),
                "backup_retention_days": 3,
                "app_env": "test",
            },
        )(),
    )

    await backup.backup_loop()  # 不该抛
    assert pruned == [3]
