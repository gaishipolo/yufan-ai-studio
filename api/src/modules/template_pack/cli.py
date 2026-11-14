"""模板封装模块的运维命令：导入老封装 JSON、导入老视频模板目录。

**为什么这两条命令在本模块**：它们写的是本模块的表（素材包 / 版本 / 视频模板）。
调用档案那半在 `modules/template/cli.py`（`import-template-runs`）——一张表归一个
模块，业务模块之间不许互相 import。升级时按顺序跑：

    app import-template-packs && app import-template-runs

三条设计约束（与档案那半一致）：

1. **幂等**：主键沿用老 JSON 里的 id，第二次跑只跳过、不复制；可先 `--dry-run`。
2. **不删老文件**：JSON 留在归档盘上是回滚依据，也是 `doctor` 的对比基准。
3. **不猜**：缺字段按默认值补，并在报告里说清补了什么。
"""

from __future__ import annotations

import argparse
import asyncio
import sys


def cmd_import_packs(args: argparse.Namespace) -> None:
    from core.exceptions import AppError
    from db.session import get_session_factory
    from infra.archive import get_archive_storage

    try:
        storage = get_archive_storage()
    except AppError as exc:
        print(exc.message, file=sys.stderr)
        raise SystemExit(1) from exc

    async def run():
        from modules.template_pack.import_legacy import import_legacy_packs

        async with get_session_factory()() as session:
            return await import_legacy_packs(session, storage, dry_run=args.dry_run)

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
    from modules.template_pack.video_template_import import import_catalog, legacy_catalog_path

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


def register(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """把本模块的运维命令挂到 `app` 上（框架组装解析器时调用一次）。"""
    packs = sub.add_parser(
        "import-template-packs",
        help="把归档盘上的封装与版本 JSON 导入数据库（幂等，不删 JSON）",
    )
    packs.add_argument(
        "--dry-run", action="store_true", help="只统计将要发生的变更，不写库"
    )
    packs.set_defaults(func=cmd_import_packs)

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
