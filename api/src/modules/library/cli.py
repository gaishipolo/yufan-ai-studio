"""资产库 v2 的运维命令。

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
- `library init-tag-tree`：把存量扁平词表按内置映射归成两级（产品 → 3030/T35、
  角色 → 菜菜，其余留一级）。迁移只加列，环境之间词表不同，归层由命令按名字
  匹配；幂等，默认试运行。

命令通过 `ModuleSpec.cli` 注册：`src/cli.py` 不需要 import 本模块，
删掉 v2 也不会让 `app backup` 起不来。
"""

from __future__ import annotations

import argparse
import asyncio


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


def cmd_tag_tree_init(args: argparse.Namespace) -> None:
    from modules.library.maintenance import run_tag_tree_init

    report = asyncio.run(run_tag_tree_init(dry_run=args.dry_run))
    for line in report.lines():
        print(line)


def register(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = sub.add_parser("library", help="资产库 v2 运维命令")
    actions = parser.add_subparsers(dest="library_action", required=True)

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

    tag_tree = actions.add_parser(
        "init-tag-tree",
        help="按内置映射把存量标签归成两级（默认试运行）",
    )
    tag_tree.add_argument(
        "--no-dry-run",
        dest="dry_run",
        action="store_false",
        help="真的写库；不传时只报告会移动哪几个",
    )
    tag_tree.set_defaults(func=cmd_tag_tree_init, dry_run=True)
