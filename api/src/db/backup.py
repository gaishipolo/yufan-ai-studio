"""备份与恢复：把「有没有备份」变成「备份能不能恢复」。

## 为什么不能只写一条 `cp app.db`

1. **sqlite 在 WAL 模式下不是单文件**：正在写的时候直接 `cp` 会拿到半份数据
   （页面已经写进 `-wal`，主文件还没 checkpoint）。必须用 sqlite 的 backup API，
   它保证复制出的是一致快照。
2. **备份没有清单就没有意义**：恢复的人需要知道「这份备份是哪个版本的模式、
   有多少行、什么时候、什么环境」。没有 `manifest.json`，你只能靠目录名猜。
3. **恢复是破坏性操作**：必须先留一份现场（`.pre-restore-*`），否则「恢复了
   一个更旧的库」这件事本身无法回退。

## 边界（写出来免得被当成万能工具）

- **对象存储里的字节不在备份范围内**。归档盘 / S3 桶的备份靠存储侧策略
  （版本化、跨区复制），本命令只负责「元数据与业务行」。恢复后如果对象也丢了，
  `app verify-blobs` 会告诉你哪些 key 不存在。
- **postgres 依赖 `pg_dump` / `pg_restore` 在 PATH 里**。没装就明确报错，
  不做「退化成 SQL 文本导出」这种半成品——那种备份在恢复时才发现不完整。
- **不自动重启服务**。恢复完请重启（连接池里还有指向旧文件的句柄）。
"""

from __future__ import annotations

import asyncio
import json
import shutil
import sqlite3
import subprocess
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import text

from core.config import get_settings
from core.logging import get_logger
from core.scheduler import periodic

logger = get_logger(__name__)

MANIFEST_NAME = "manifest.json"
DB_FILE_NAME = "database.dump"


class BackupError(RuntimeError):
    """备份/恢复无法继续，消息面向使用者（CLI 直接打印）。"""


@dataclass
class BackupManifest:
    created_at: str
    app_env: str
    dialect: str
    file: str
    note: str = ""
    schema_head: str = ""
    tables: dict[str, int] = field(default_factory=dict)

    def summary(self) -> str:
        tables = "、".join(f"{name}={count}" for name, count in sorted(self.tables.items()))
        return (
            f"{self.created_at}（env={self.app_env}，{self.dialect}，"
            f"schema={self.schema_head or '未知'}）\n  表行数：{tables or '无'}"
        )


def _sqlite_path() -> Path:
    url = get_settings().db_url
    if ":memory:" in url:
        raise BackupError("内存库无法备份（DB_URL 指向 :memory:）")
    if "///" not in url:
        raise BackupError(f"无法从 DB_URL 解析 sqlite 文件路径：{url}")
    raw = url.split("///", 1)[1]
    path = Path(raw)
    return path if path.is_absolute() else Path.cwd() / path


def _pg_url() -> str:
    """把 SQLAlchemy 的异步 URL 换成 libpq 认识的 URL。"""
    url = get_settings().db_url
    for prefix in ("postgresql+asyncpg://", "postgres+asyncpg://"):
        if url.startswith(prefix):
            return "postgresql://" + url[len(prefix) :]
    if url.startswith("postgresql://"):
        return url
    raise BackupError(f"不支持的数据库 URL：{url.split('://')[0]}")


def _dump_sqlite(target: Path) -> None:
    source = _sqlite_path()
    if not source.exists():
        raise BackupError(f"数据库文件不存在：{source}")
    # sqlite3 的 backup API：在源库上加共享锁复制，得到一致快照；
    # 直接 shutil.copy 在 WAL 模式下可能漏掉尚未 checkpoint 的页。
    with sqlite3.connect(source) as src, sqlite3.connect(target) as dst:
        src.backup(dst)


def _restore_sqlite(source: Path) -> None:
    target = _sqlite_path()
    if target.exists():
        stamp = datetime.now(tz=UTC).strftime("%Y%m%d-%H%M%S")
        safety = target.with_name(f"{target.name}.pre-restore-{stamp}")
        shutil.copy2(target, safety)
        logger.info("恢复前现场已保留：%s", safety)
    target.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(source) as src, sqlite3.connect(target) as dst:
        src.backup(dst)


def _run(command: list[str], *, what: str) -> None:
    try:
        completed = subprocess.run(command, capture_output=True, text=True, check=False)
    except FileNotFoundError as exc:
        raise BackupError(f"{what} 需要命令 `{command[0]}`，但 PATH 里没有") from exc
    if completed.returncode != 0:
        raise BackupError(f"{what} 失败（exit={completed.returncode}）：{completed.stderr.strip()}")


async def _collect_metadata() -> tuple[str, dict[str, int]]:
    """读 schema 版本与各表行数。读不到的部分留空，不因此让备份失败。"""
    from db import registry
    from db.base import Base
    from db.session import get_session_factory

    registry.load_all_models()
    head = ""
    counts: dict[str, int] = {}
    async with get_session_factory()() as session:
        try:
            head = str(await session.scalar(text("SELECT version_num FROM alembic_version")) or "")
        except Exception:  # noqa: BLE001 - 用 create_all 建的库没有这张表
            head = ""
        for table in Base.metadata.sorted_tables:
            try:
                count = await session.scalar(text(f'SELECT count(*) FROM "{table.name}"'))
            except Exception:  # noqa: BLE001 - 表可能还没建
                continue
            counts[table.name] = int(count or 0)
    return head, counts


async def create_backup(out_dir: Path, *, note: str = "") -> tuple[Path, BackupManifest]:
    """生成一份备份，返回备份目录与清单。"""
    stamp = datetime.now(tz=UTC).strftime("%Y%m%d-%H%M%S")
    target_dir = out_dir / f"backup-{stamp}"
    target_dir.mkdir(parents=True, exist_ok=True)
    dump = target_dir / DB_FILE_NAME

    settings = get_settings()
    dialect = "sqlite" if settings.is_sqlite else "postgresql"
    if settings.is_sqlite:
        # 走线程：backup API 会持读锁并逐页复制，库稍大就是几百毫秒到数秒的阻塞。
        # 在 CLI 里无所谓，但同一个函数现在也由周期任务调用——阻塞的是事件循环。
        await asyncio.to_thread(_dump_sqlite, dump)
    else:
        await asyncio.to_thread(
            _run,
            ["pg_dump", "--format=custom", "--no-owner", "--file", str(dump), _pg_url()],
            what="pg_dump",
        )

    head, tables = await _collect_metadata()
    manifest = BackupManifest(
        created_at=datetime.now(tz=UTC).isoformat(timespec="seconds"),
        app_env=settings.app_env,
        dialect=dialect,
        file=DB_FILE_NAME,
        note=note,
        schema_head=head,
        tables=tables,
    )
    (target_dir / MANIFEST_NAME).write_text(
        json.dumps(asdict(manifest), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    logger.info("备份完成：%s", target_dir)
    return target_dir, manifest


def read_manifest(backup_dir: Path) -> BackupManifest:
    path = backup_dir / MANIFEST_NAME
    if not path.exists():
        raise BackupError(f"不是有效的备份目录（缺 {MANIFEST_NAME}）：{backup_dir}")
    raw = json.loads(path.read_text(encoding="utf-8"))
    return BackupManifest(
        created_at=raw.get("created_at", ""),
        app_env=raw.get("app_env", ""),
        dialect=raw.get("dialect", ""),
        file=raw.get("file", DB_FILE_NAME),
        note=raw.get("note", ""),
        schema_head=raw.get("schema_head", ""),
        tables={str(k): int(v) for k, v in (raw.get("tables") or {}).items()},
    )


def restore_backup(backup_dir: Path, *, force: bool = False) -> BackupManifest:
    """用备份覆盖当前库。

    `force=False` 时只允许恢复到「当前还没有库」的环境（新建部署）。
    覆盖已有数据必须显式 `--force`：这个参数存在的意义是让执行者读一遍这句话。
    """
    manifest = read_manifest(backup_dir)
    settings = get_settings()
    current = "sqlite" if settings.is_sqlite else "postgresql"
    if manifest.dialect != current:
        raise BackupError(
            f"备份来自 {manifest.dialect}，当前是 {current}：跨方言恢复不受支持"
        )

    dump = backup_dir / manifest.file
    if not dump.exists():
        raise BackupError(f"备份文件缺失：{dump}")

    if current == "sqlite":
        target = _sqlite_path()
        if target.exists() and not force:
            raise BackupError(
                f"目标库已存在：{target}\n"
                "覆盖会丢数据。确认后加 --force（会先把现场另存为 .pre-restore-*）"
            )
        _restore_sqlite(dump)
    else:
        if not force:
            raise BackupError("postgres 恢复会清空现有对象，确认后加 --force")
        _run(
            [
                "pg_restore",
                "--clean",
                "--if-exists",
                "--no-owner",
                "--dbname",
                _pg_url(),
                str(dump),
            ],
            what="pg_restore",
        )

    logger.info("恢复完成：%s", backup_dir)
    return manifest


# --------------------------------------------------------------------------- #
# 自动备份与保留期
# --------------------------------------------------------------------------- #
def prune_backups(out_dir: Path, *, retention_days: int) -> int:
    """删掉超过保留期的备份目录，返回删除数量。

    三条自我约束：

    1. **只认带 `manifest.json` 的 `backup-*` 目录**。别的目录一律不碰——
       把 `BACKUP_DIR` 指到 `var/` 这种大目录是很容易犯的错，靠通配符删会出人命。
    2. **按清单里的 `created_at` 判断，不看目录 mtime**。恢复演练时会把备份
       `cp` 到别处、mtime 变成今天，按 mtime 排序会把最老的那份当成最新的留着、
       把别的都删掉——正好删反。
    3. **`retention_days <= 0` 直接返回**，表示"永不清理"。宁可磁盘涨，也不做
       默认删除：删备份是不可逆的，只有显式配了天数才动手。
    """
    if retention_days <= 0 or not out_dir.is_dir():
        return 0

    cutoff = datetime.now(tz=UTC) - timedelta(days=retention_days)
    removed = 0
    for entry in sorted(out_dir.iterdir()):
        if not entry.is_dir() or not entry.name.startswith("backup-"):
            continue
        if not (entry / MANIFEST_NAME).exists():
            continue
        try:
            created = datetime.fromisoformat(read_manifest(entry).created_at)
        except (BackupError, ValueError):
            # 清单坏了就不删：这可能正是唯一一份还能看出问题的现场
            logger.warning("备份清单无法解析，跳过清理：%s", entry)
            continue
        if created.tzinfo is None:
            created = created.replace(tzinfo=UTC)
        if created >= cutoff:
            continue
        try:
            shutil.rmtree(entry)
        except OSError as exc:
            logger.warning("清理备份失败：%s（%s）", entry, exc)
            continue
        removed += 1
    if removed:
        logger.info("清理过期备份 %d 份（保留 %d 天）：%s", removed, retention_days, out_dir)
    return removed


@periodic(
    "core.backup",
    every=max(3600.0, get_settings().backup_interval_hours * 3600),
    immediate=False,
    # 多副本各备一份纯浪费磁盘与读锁，且不会换来确定性；抢不到租约这一轮跳过即可。
    # 与 `core.outbox` 的取舍相反——那边重复投递只是冗余，这边重复备份是实打实的成本。
    leader_only=True,
)
async def backup_loop() -> None:
    """周期入口：自动备份 + 清理过期备份。默认关闭，`BACKUP_AUTO_ENABLED=true` 打开。"""
    settings = get_settings()
    if not settings.backup_auto_enabled:
        return
    out_dir = Path(settings.backup_dir)
    try:
        _, manifest = await create_backup(out_dir, note=f"auto:{settings.app_env}")
    except (BackupError, OSError) as exc:
        # 不让异常冒出去：调度器会记一笔，但这一轮的清理不该被备份失败拖住
        logger.error("自动备份失败：%s", exc)
    else:
        logger.info(
            "自动备份完成：%s（schema=%s，%d 张表）",
            manifest.created_at,
            manifest.schema_head or "未知",
            len(manifest.tables),
        )
    await asyncio.to_thread(
        prune_backups, out_dir, retention_days=settings.backup_retention_days
    )


__all__ = [
    "BackupError",
    "BackupManifest",
    "backup_loop",
    "create_backup",
    "prune_backups",
    "read_manifest",
    "restore_backup",
]
