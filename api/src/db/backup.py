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

import json
import shutil
import sqlite3
import subprocess
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import text

from core.config import get_settings
from core.logging import get_logger

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
        _dump_sqlite(dump)
    else:
        _run(
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


__all__ = [
    "BackupError",
    "BackupManifest",
    "create_backup",
    "read_manifest",
    "restore_backup",
]
