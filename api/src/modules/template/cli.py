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

    report = asyncio.run(run())
    for line in report.lines():
        print(line)
    if args.dry_run:
        print("（--dry-run：以上变更未写库）")


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
