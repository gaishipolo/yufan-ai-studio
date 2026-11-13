"""模板库的运维命令：把归档盘上的老封装与老调用档案导入数据库。

**为什么需要它**：数据从「`catalog/*.json` + `runs/<id>/run.json`」搬到六张表之后，
存量数据不会自己跟过来。没有这一步，「升级」等于「模板库清空」。

三条设计约束：

1. **幂等**：主键沿用老 JSON 里的 id，第二次跑只会更新、不会复制。运维可以先
   `--dry-run` 看清单再执行，执行一半中断也能重跑。
2. **不删老文件**：JSON 留在归档盘上是回滚依据，也是 `doctor` 的对比基准。
   真要清理，等新实现稳定一个版本之后单独做。
3. **不猜**：老 JSON 缺字段就按默认值补，而不是拒绝整条记录——拒绝会让一份
   脏数据挡住其余 99 条。补了什么会打在报告里，人能一眼看到。
"""

from __future__ import annotations

import argparse
import asyncio
import sys


def cmd_import_legacy(args: argparse.Namespace) -> None:
    from core.exceptions import AppError
    from db.session import get_session_factory
    from infra.archive import get_archive_storage

    try:
        storage = get_archive_storage()
    except AppError as exc:
        print(exc.message, file=sys.stderr)
        raise SystemExit(1) from exc

    async def run():
        from modules.template.import_legacy import import_legacy

        async with get_session_factory()() as session:
            return await import_legacy(session, storage, dry_run=args.dry_run)

    try:
        report = asyncio.run(run())
    except AppError as exc:
        # 部署配置问题（归档根不可用等）要给一句人话并以非零退出，别甩 traceback：
        # 这条命令是升级时必须跑的一次，运维看不懂栈就等于没提示。
        print(exc.message, file=sys.stderr)
        raise SystemExit(1) from exc
    for line in report.lines():
        print(line)
    if args.dry_run:
        print("（--dry-run：以上变更未写库）")


def cmd_import_video_templates(args: argparse.Namespace) -> None:
    """把老的 `catalog.json` 视频模板目录导进 `video_templates` 表（幂等）。"""
    from pathlib import Path

    from core.exceptions import AppError
    from db.session import get_session_factory
    from modules.template.video_template_import import import_catalog, legacy_catalog_path

    path = Path(args.path).expanduser() if args.path else legacy_catalog_path()

    async def run():
        async with get_session_factory()() as session:
            return await import_catalog(session, path, dry_run=args.dry_run)

    try:
        report = asyncio.run(run())
    except AppError as exc:
        print(exc.message, file=sys.stderr)
        raise SystemExit(1) from exc
    for line in report.lines():
        print(line)
    if args.dry_run:
        print("（--dry-run：以上变更未写库）")


def cmd_workspaces(args: argparse.Namespace) -> None:
    """盘点运行工作区：占多少、最老最新、哪些已经没有档案认领。

    **只读**。工作区里 `inputs/` 是验收依据、`records/` 是证据、`code/runtime/`
    是复现依据，删了没有第二份，所以「留多久」得先有数才能定（§24.23 候选 B）。
    """
    from pathlib import Path

    from sqlalchemy import select

    from db.session import get_session_factory
    from infra.archive.runs import runs_root
    from modules.template.models import ReplacementRun
    from modules.template.workspace_inventory import inspect

    root = Path(args.root).expanduser() if args.root else runs_root()

    async def known_ids() -> set[str]:
        async with get_session_factory()() as session:
            rows = await session.execute(select(ReplacementRun.id))
            return {row for (row,) in rows}

    # 数据库连不上时不能整个命令失败：盘上占了多少是运维此刻最想知道的事。
    # 但**不能**退化成「空集合」——那会把每个目录都显示成孤儿，等于递给人一份
    # 「全都删得」的清单。改成 None，输出里如实写「归属未判断」。
    try:
        ids: set[str] | None = asyncio.run(known_ids())
    except Exception as exc:  # noqa: BLE001 - 运维命令，任何驱动错误都只降级不中断
        print(f"读不到档案表（{exc}），本次不判断哪些目录是孤儿。", file=sys.stderr)
        print("（要判断归属请先确保 DB_URL 指向正确且已迁移）", file=sys.stderr)
        ids = None

    report = inspect(root, ids, measure=not args.no_size)
    for line in report.lines(top=args.top, orphans_only=args.orphans_only):
        print(line)


def register(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """把本模块的运维命令挂到 `app` 上（框架组装解析器时调用一次）。"""
    parser = sub.add_parser(
        "import-template-legacy",
        help="把归档盘上的封装与替换档案 JSON 导入数据库（幂等，不删 JSON）",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="只统计将要发生的变更，不写库"
    )
    parser.set_defaults(func=cmd_import_legacy)

    catalog = sub.add_parser(
        "import-video-templates",
        help="把老的 video_templates/catalog.json 导入 video_templates 表（幂等，不删 JSON）",
    )
    catalog.add_argument(
        "--path", default="", help="老目录文件路径（默认按 VIDEO_TEMPLATE_CATALOG 或 api/var）"
    )
    catalog.add_argument(
        "--dry-run", action="store_true", help="只统计将要发生的变更，不写库"
    )
    catalog.set_defaults(func=cmd_import_video_templates)

    spaces = sub.add_parser(
        "template-workspaces",
        help="盘点运行工作区占用（只读：占多少、最老最新、哪些档案已删）",
    )
    spaces.add_argument("--root", default="", help="工作区根（默认取 REPLACEMENT_RUNS_ROOT）")
    spaces.add_argument("--top", type=int, default=5, help="列出最大的前几个（默认 5）")
    spaces.add_argument(
        "--orphans-only", action="store_true", help="只列档案已删的目录（清理候选）"
    )
    spaces.add_argument(
        "--no-size",
        action="store_true",
        help="不统计体积（共享盘上逐个 stat 很慢，只想看清单时用它）",
    )
    spaces.set_defaults(func=cmd_workspaces)
