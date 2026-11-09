"""资产库 v2 的运维命令。

M1 只有一条：把旧素材库搬进 v2。它必须能反复跑（幂等），因为并存期会先试跑
一次、切换前一天再跑一次追平增量。

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
