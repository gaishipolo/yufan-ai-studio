"""素材模块的运维命令：归档索引导入、内容寻址迁移、孤儿字节回收、镜像重建。

这四条都直接操作 `assets` 表与归档盘布局。以前它们写在 `src/cli.py`，于是框架
必须 `from modules.asset.models import Asset`——删掉素材模块，`app backup` 起不来。

注意 `app verify-blobs` / `app migrate-archive` **不在这里**：前者只读归档盘的字节
（`infra.archive.integrity`），后者只搬「目录 → 驱动」，两者都不认识 `assets` 表，
属于归档基础设施而不是素材业务。
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from infra.archive.base import ArchiveStorage
    from modules.asset.models import Asset


def cmd_import_archive_index(args: argparse.Namespace) -> None:
    """把归档盘的 JSON 索引补进 `assets` 表。

    设计成可反复执行：主键沿用 JSON 的 id，第二次跑就是 `updated=0`。
    不删 JSON —— 替换模板在 worker 里仍按文件读素材，删了那边就断。
    """
    from core.exceptions import AppError
    from db.session import get_session_factory
    from infra.archive import get_archive_storage
    from modules.asset.archive_import import import_archive_index

    try:
        root = Path(args.root).resolve() if args.root else get_archive_storage().root()
    except AppError as exc:
        print(exc.message, file=sys.stderr)
        raise SystemExit(1) from exc

    if not (root / "catalog" / "assets").is_dir():
        print(f"没有找到归档索引目录：{root / 'catalog' / 'assets'}", file=sys.stderr)
        raise SystemExit(1)

    async def run():
        async with get_session_factory()() as session:
            return await import_archive_index(session, root, dry_run=args.dry_run)

    report = asyncio.run(run())
    for line in report.lines():
        print(line)


def cmd_migrate_blobs(args: argparse.Namespace) -> None:
    """把归档素材搬到「按 sha256 内容寻址」的布局（`blobs/ab/abcdef…`）。

    旧布局是「分类 + 年月 + 随机 id」，于是同一份字节可以有多个副本、也可以用
    同名覆盖 —— "这份字节还是不是当初那份"只能靠每次读出来重算 sha256 来猜。
    搬到内容寻址之后 key 就是摘要，发布校验与运行快照都不再需要读全量。

    幂等：已经是 blob 布局的行直接跳过，跑第二遍 `moved=0`。
    """
    moved, already, failed = asyncio.run(
        _migrate_archive_blobs(dry_run=args.dry_run, delete_source=args.delete_source)
    )
    print(f"已迁移 {moved} 条，已是新布局 {already} 条，缺字节 {failed} 条")
    if args.dry_run:
        print("（dry-run：未写库、未搬文件）")
    if failed:
        raise SystemExit(1)


async def _migrate_archive_blobs(*, dry_run: bool, delete_source: bool) -> tuple[int, int, int]:
    from sqlalchemy import select

    from db.session import get_session_factory
    from infra.archive import (
        BlobStore,
        clip_key,
        get_archive_storage,
        is_blob_key,
        preview_key,
    )
    from modules.asset.models import Asset
    from modules.asset.schema import AssetSource

    storage = get_archive_storage()
    blobs = BlobStore(storage)
    moved = already = failed = 0

    async with get_session_factory()() as session:
        rows = (
            (await session.execute(select(Asset).where(Asset.source == AssetSource.ARCHIVE)))
            .scalars()
            .all()
        )
        for row in rows:
            if is_blob_key(row.storage_key):
                already += 1
                continue
            old_key = row.storage_key
            if not storage.exists(old_key):
                failed += 1
                print(f"  缺字节 {row.id} ← {old_key}", file=sys.stderr)
                continue
            if not row.sha256:
                # 内容寻址靠摘要命名，没有摘要就只能是"位置寻址的孤儿"，跳过并报出来
                failed += 1
                print(f"  缺摘要 {row.id} ← {old_key}", file=sys.stderr)
                continue
            if dry_run:
                moved += 1
                continue

            key, _ = blobs.adopt(old_key, row.sha256, Path(old_key).suffix)
            old_cover = row.cover_key
            row.storage_key = key
            if old_cover and not old_cover.startswith("previews/"):
                target = (
                    clip_key(row.sha256) if old_cover.endswith(".mp4") else preview_key(row.sha256)
                )
                if storage.exists(old_cover):
                    with storage.materialize(old_cover) as source:
                        storage.copy(source, target, None)
                    row.cover_key = target
            await session.commit()
            _rewrite_asset_mirror(storage, row)
            if delete_source:
                storage.remove(old_key)
                if old_cover and old_cover != row.cover_key:
                    storage.remove(old_cover)
            moved += 1

    return moved, already, failed


def cmd_prune_blobs(args: argparse.Namespace) -> None:
    """回收内容寻址下没人引用的字节，以及（可选）超期软删记录。

    实现在 `modules/asset/maintenance.py`：周期任务与 CLI 必须共用一份，
    否则「一处查了引用、另一处没查」迟早出现。
    """
    from modules.asset.maintenance import run_archive_gc

    # 引用检查器在模块 import 期注册；`app` 在组装解析器时已经发现过模块，所以这里
    # 不需要再调 `discover_modules()`（以前要，因为 CLI 不走 `create_app()`）。
    report = asyncio.run(
        run_archive_gc(
            execute=args.execute,
            min_age_hours=args.min_age_hours,
            trash_days=args.trash_age_days,
            include_trash=args.include_trash,
        )
    )
    print(
        f"孤儿字节 {len(report.orphans)} 个，已删除 {len(report.deleted)} 个，"
        f"处于保护期 {report.protected} 个（{args.min_age_hours:g} 小时内）"
    )
    for key in report.orphans:
        print(f"  孤儿 {key}")
    if args.include_trash:
        print(
            f"回收站超期（{args.trash_age_days:g} 天）：清理 {len(report.purged)} 条、"
            f"仍被引用 {len(report.blocked)} 条"
        )
        for asset_id, name, _ in report.purged:
            print(f"  [清理] {asset_id} {name}")
        for asset_id, name, refs in report.blocked:
            print(f"  [保留] {asset_id} {name} 仍被引用：{refs}")
    if not args.execute:
        print("（dry-run：未删除任何对象、未删除任何记录；确认后加 --execute）")
    elif report.protected:
        print("保护期内的对象下轮再扫：它们可能正在被登记流程写入")


def cmd_rebuild_mirrors(args: argparse.Namespace) -> None:
    """按 DB 全量重建镜像：补缺、更新 stateVersion 落后的、清掉没有 DB 行的孤儿。"""
    from modules.asset.mirror import run_mirror_reconcile

    report = asyncio.run(run_mirror_reconcile(execute=not args.dry_run))
    print(("预演：" if args.dry_run else "") + report.summary())
    for asset_id, error in report.failures:
        print(f"  [失败] {asset_id}: {error}")
    if not report.healthy:
        raise SystemExit(1)


async def prune_archive_blobs(*, execute: bool, min_age_hours: float) -> tuple[int, int, int]:
    """给测试与脚本用的薄封装：返回（孤儿数，已删数，保护期内数）。

    它不是第二份实现，只是把 `run_archive_gc` 的报告压成三个数——
    断言「保护期内不删」这类行为时，比逐个字段去翻报告清楚。
    """
    from modules.asset.maintenance import run_archive_gc

    report = await run_archive_gc(
        execute=execute, min_age_hours=min_age_hours, include_trash=False
    )
    return len(report.orphans), len(report.deleted), report.protected


def _rewrite_asset_mirror(storage: ArchiveStorage, row: Asset) -> None:
    """镜像 JSON 的 `path` 也要跟着搬：替换模板读的是它，不是 DB。"""
    key = f"catalog/assets/{row.id}.json"
    if not storage.exists(key):
        return
    payload = storage.read(key)
    if not isinstance(payload, dict):
        return
    payload["path"] = row.storage_key
    payload["previewPath"] = row.cover_key or None
    storage.write(key, payload)


def cmd_backfill_blobs(args: argparse.Namespace) -> None:
    """给存量素材补 `blob_id`。

    为什么不是迁移的一部分：回填要按 sha256 分组、逐批提交，跑在部署事务里
    会让一次发布卡住好几分钟，中途失败还得靠手动清理。做成命令之后，
    「迁移完成但回填没跑」是一个**可读的中间态**——读路径回落旧列，
    功能不受影响，doctor 只记一条 WARN。
    """
    from db.session import get_session_factory
    from modules.asset.blob_repository import backfill_blobs

    async def run():
        async with get_session_factory()() as session:
            return await backfill_blobs(
                session, batch_size=args.batch_size, dry_run=args.dry_run
            )

    report = asyncio.run(run())
    for line in report.lines():
        print(line)
    if args.dry_run:
        print("（dry-run：以上为预估值，未写库）")


def register(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """把本模块的运维命令挂到 `app` 上（框架在组装解析器时调用一次）。"""
    p_import = sub.add_parser(
        "import-archive-index",
        help="把归档目录的 JSON 索引导入 assets 表（幂等，不删 JSON）",
    )
    p_import.add_argument("--root", default="", help="归档根目录，默认取 MEDIA_ARCHIVE_ROOT")
    p_import.add_argument("--dry-run", action="store_true", help="只统计将要发生的变更，不写库")
    p_import.set_defaults(func=cmd_import_archive_index)

    p_blobs = sub.add_parser(
        "migrate-blobs",
        help="归档素材改内容寻址布局（key = sha256，幂等；发布校验不再读全量）",
    )
    p_blobs.add_argument("--dry-run", action="store_true", help="只统计，不写库不搬文件")
    p_blobs.add_argument(
        "--delete-source",
        action="store_true",
        help="搬成功后删除旧位置的文件（默认保留：旧文件是回滚依据）",
    )
    p_blobs.set_defaults(func=cmd_migrate_blobs)

    p_prune_blobs = sub.add_parser(
        "prune-blobs",
        help="回收内容寻址下没人引用的孤儿字节（默认只报告，--execute 才删）",
    )
    p_prune_blobs.add_argument("--execute", action="store_true", help="实际删除孤儿字节")
    p_prune_blobs.add_argument(
        "--min-age-hours",
        type=float,
        default=24.0,
        help="只回收早于此时长的对象（默认 24 小时，避开正在进行的登记）",
    )
    p_prune_blobs.add_argument(
        "--include-trash",
        action="store_true",
        help="同时彻底清理长期留在回收站的归档记录（软删本身不释放空间）",
    )
    p_prune_blobs.add_argument(
        "--trash-age-days",
        type=float,
        default=30.0,
        help="--include-trash 的保留期（默认 30 天）",
    )
    p_prune_blobs.set_defaults(func=cmd_prune_blobs)

    p_rebuild = sub.add_parser(
        "rebuild-mirrors", help="按 DB 幂等重建 catalog/assets/*.json，并清理孤儿镜像"
    )
    p_rebuild.add_argument("--dry-run", action="store_true", help="只报告差异、不写盘")
    p_rebuild.set_defaults(func=cmd_rebuild_mirrors)

    p_backfill = sub.add_parser(
        "backfill-blobs",
        help="给存量素材补 blob 记录（幂等、分批、可中断重跑）",
    )
    p_backfill.add_argument("--batch-size", type=int, default=500, help="每批处理条数")
    p_backfill.add_argument("--dry-run", action="store_true", help="只统计，不写库")
    p_backfill.set_defaults(func=cmd_backfill_blobs)
