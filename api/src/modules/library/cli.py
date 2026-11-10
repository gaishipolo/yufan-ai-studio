"""资产库 v2 的运维命令。

- `library import-archive`：把旧素材库搬进 v2。必须能反复跑（幂等），因为并存期
  会先试跑一次、切换前一天再跑一次追平增量。
- `library reconcile`：只读对账，回答「两边还是不是同一批东西」。观察期每天跑一次：
  有缺口退出码非 0，CI / 值班脚本据此报警，不用人肉比对列表。
- `library trash-gc`：手动跑一轮回收站清理。周期任务默认关闭（删除不可逆），
  所以运维需要一个「我看过了，现在清」的入口；`--dry-run` 只列 id。
- `library download-gc`：清过期的打包 zip。它删的是我们自己生成的临时产物，
  周期任务默认开着；这个命令用于「刚改完保留期，想立刻生效」或确认积压。
- `library download-run`：手动跑一条打包任务。worker 没起 / 队列卡住时用来补跑，
  与队列里的 handler 是同一个函数（幂等：只处理 `queued`）。
- `library repreview`：重建缺失的缩略图。原图在 `blobs/`、封面在 `previews/`，
  是两套字节一条记录；封面对象丢了原图仍然完好，但界面只剩占位与裂图。
  默认试运行，只报告有几条需要重建。
- `library sync-pack-tags`：给改动前建的封装补齐「成员标签并集」。自动继承是
  建封装 / 换成员时做的，存量数据要回填一次；只增不减，可反复跑。

命令通过 `ModuleSpec.cli` 注册：`src/cli.py` 不需要 import 本模块，
删掉 v2 也不会让 `app backup` 起不来。
"""

from __future__ import annotations

import argparse
import asyncio
import sys


def cmd_import_archive(args: argparse.Namespace) -> None:
    from core.exceptions import AppError
    from db.session import get_session_factory
    from infra import get_storage
    from infra.archive import get_archive_storage
    from modules.library.import_archive import import_legacy_assets

    async def run():
        async with get_session_factory()() as session:
            return await import_legacy_assets(
                session,
                get_archive_storage(),
                get_storage(),
                dry_run=args.dry_run,
                limit=args.limit,
                owner=args.owner,
            )

    try:
        report = asyncio.run(run())
    except AppError as exc:
        print(exc.message, file=sys.stderr)
        raise SystemExit(1) from exc

    for line in report.lines():
        print(line)

    # 有缺口就以非 0 退出：CI / 运维脚本靠退出码判断「能不能切换」，
    # 不能只看最后一行有没有「失败」两个字。
    if not report.dry_run and (report.missing_bytes or report.skipped_unsupported):
        raise SystemExit(1)


def cmd_reconcile(args: argparse.Namespace) -> None:
    from sqlalchemy.exc import SQLAlchemyError

    from db.session import get_session_factory
    from modules.library.import_archive import reconcile_legacy

    async def run():
        async with get_session_factory()() as session:
            return await reconcile_legacy(session, owner=args.owner)

    try:
        report = asyncio.run(run())
    except SQLAlchemyError as exc:
        # 旧表不存在（新部署、或已彻底下线旧模块）不是错误，是「没有可对账的东西」。
        # 与 `health.py` 同口径：这条命令不该因为旧表缺席而报红。
        print(f"跳过对账：旧素材表不可读（{type(exc).__name__}）", file=sys.stderr)
        return

    for line in report.lines(limit=args.limit):
        print(line)

    # 缺口即非 0：观察期靠退出码判断「今天能不能放心继续观察 / 能不能切换」。
    if not report.in_sync:
        raise SystemExit(1)


def cmd_trash_gc(args: argparse.Namespace) -> None:
    from modules.library.maintenance import run_trash_gc

    report = asyncio.run(run_trash_gc(batch=args.batch or None, dry_run=args.dry_run))
    if report.dry_run:
        print(f"试运行：{report.scanned} 条已过保留期（加上 --no-dry-run 才会真删）")
        return
    print(f"扫描 {report.scanned}，清理 {report.purged}，跳过 {report.blocked_count}")
    if report.blocked:
        # 被引用而跳过是**预期**行为，不是失败：这些条目的解释在详情页的
        # 「使用与引用」区。退出码仍为 0，否则运维脚本会以为整轮失败了。
        print("跳过的条目（仍被引用）：" + ", ".join(report.blocked[:20]))


def cmd_download_gc(args: argparse.Namespace) -> None:
    from modules.library.downloads import run_download_gc

    report = asyncio.run(run_download_gc(dry_run=args.dry_run))
    if report.dry_run:
        print(
            f"试运行：过期 {report.expired}，超时判失败 {report.failed}，"
            f"可删历史行 {report.purged}"
            + ("；涉及 " + ", ".join(report.ids[:20]) if report.ids else "")
        )
        return
    for line in report.lines():
        print(line)


def cmd_download_run(args: argparse.Namespace) -> None:
    from modules.library.downloads import build_download

    asyncio.run(build_download(args.job_id))
    print(f"已执行打包任务 {args.job_id}（只处理 queued 状态；其余会跳过）")


def cmd_preview_repair(args: argparse.Namespace) -> None:
    from modules.library.maintenance import run_preview_repair

    report = asyncio.run(
        run_preview_repair(batch=args.batch or None, dry_run=args.dry_run, owner=args.owner)
    )
    for line in report.lines():
        print(line)
    # 有修不好的就非 0：这些条目的原图可能真的坏了，需要人去看一眼，
    # 而「修了 N 条」的成功不该把这个信号盖住。
    if report.broken:
        raise SystemExit(1)


def cmd_pack_tag_sync(args: argparse.Namespace) -> None:
    from modules.library.maintenance import run_pack_tag_sync

    report = asyncio.run(
        run_pack_tag_sync(batch=args.batch or 0, dry_run=args.dry_run, owner=args.owner)
    )
    for line in report.lines():
        print(line)


def register(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = sub.add_parser("library", help="资产库 v2 运维命令")
    actions = parser.add_subparsers(dest="library_action", required=True)

    importer = actions.add_parser(
        "import-archive", help="把旧素材库导入 v2（幂等，可先 --dry-run）"
    )
    importer.add_argument("--dry-run", action="store_true", help="只统计，不读字节、不写库")
    importer.add_argument("--limit", type=int, default=0, help="最多处理多少条（0 = 全部）")
    importer.add_argument("--owner", default="", help="只导入指定 owner_id 的素材")
    importer.set_defaults(func=cmd_import_archive)

    reconcile = actions.add_parser("reconcile", help="只读对账：旧库与 v2 的差集")
    reconcile.add_argument("--owner", default="", help="只看指定 owner_id 的素材")
    reconcile.add_argument("--limit", type=int, default=20, help="每类最多列几个 id")
    reconcile.set_defaults(func=cmd_reconcile)

    gc = actions.add_parser("trash-gc", help="清理过期的回收站条目（默认试运行）")
    gc.add_argument(
        "--no-dry-run",
        dest="dry_run",
        action="store_false",
        help="真的删除；不传时只列出会删哪些",
    )
    gc.set_defaults(func=cmd_trash_gc, dry_run=True)
    gc.add_argument("--batch", type=int, default=0, help="单轮最多处理多少条（0 = 用配置值）")

    download_gc = actions.add_parser(
        "download-gc", help="清理过期的打包 zip（默认试运行）"
    )
    download_gc.add_argument(
        "--no-dry-run",
        dest="dry_run",
        action="store_false",
        help="真的删字节；不传时只报告数量",
    )
    download_gc.set_defaults(func=cmd_download_gc, dry_run=True)

    download_run = actions.add_parser(
        "download-run", help="手动执行一条打包任务（worker 未起时补跑）"
    )
    download_run.add_argument("job_id", help="打包任务 id")
    download_run.set_defaults(func=cmd_download_run)

    previews = actions.add_parser(
        "repreview",
        help="重建缺失的缩略图（原图还在、封面对象没了时用，默认试运行）",
    )
    previews.add_argument(
        "--no-dry-run",
        dest="dry_run",
        action="store_false",
        help="真的生成并写回存储；不传时只报告有几条缺失",
    )
    previews.set_defaults(func=cmd_preview_repair, dry_run=True)
    previews.add_argument("--batch", type=int, default=0, help="最多处理多少条（0 = 全部）")
    previews.add_argument("--owner", default="", help="只修指定 owner_id 的素材")

    pack_tags = actions.add_parser(
        "sync-pack-tags",
        help="给存量封装补齐成员标签并集（默认试运行）",
    )
    pack_tags.add_argument(
        "--no-dry-run",
        dest="dry_run",
        action="store_false",
        help="真的写库；不传时只报告有几个封装需要补",
    )
    pack_tags.set_defaults(func=cmd_pack_tag_sync, dry_run=True)
    pack_tags.add_argument("--batch", type=int, default=0, help="最多处理多少个（0 = 全部）")
    pack_tags.add_argument("--owner", default="", help="只处理指定 owner_id 的封装")
