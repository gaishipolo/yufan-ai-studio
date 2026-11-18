"""模型驱动运维命令：`app drivers list` / `app drivers check` / `app libtv-reconcile`。

`check` 有退出码，给 CI 与部署脚本用：驱动文件写错、模型 id 撞车都会 exit 1，
而不是等第一次生成才报「未知模型」。

`libtv-reconcile` 是「提交回执未知」那类任务的唯一出口：归档进入
`submission_unknown` 后，桥接层拒绝一切重提（可能已扣费），界面上点多少次
「重新生成」都是同一句错。所以由人看一眼上游记录，再用它告诉系统
「确认没提交」（解封）或「已经在跑，id 是这个」（接回）。
"""

from __future__ import annotations

import argparse
import asyncio
import sys


def cmd_list(args: argparse.Namespace) -> None:
    from modules.model_driver.registry import driver_summaries, load_errors

    for info in driver_summaries():
        state = "OK" if info.loaded else f"FAIL {info.error}"
        print(
            f"{info.name:<20} {info.version:<8} "
            f"{','.join(info.capabilities):<20} models={info.model_count:<3} "
            f"poll={str(info.supports_poll):<5} {state}"
        )
    for error in load_errors():
        print(f"[error] {error}")


def cmd_check(args: argparse.Namespace) -> None:
    from modules.model_driver.registry import driver_summaries, load_errors

    infos = driver_summaries()
    errors = load_errors()
    ok = [item for item in infos if item.loaded]
    print(f"驱动 {len(ok)} 个可用，问题 {len(errors)} 处")
    for error in errors:
        print(f"[error] {error}")
    if errors or not ok:
        sys.exit(1)
    print("PASS")


async def _auto_verdict(path, before):  # noqa: ANN001, ANN202 - 只在命令里用
    """拉最近几页消费明细，交给 `auto_verdict` 判断该接回还是解封。"""
    from modules.model_driver.libtv import account as libtv_account
    from modules.model_driver.libtv.reconcile import auto_verdict

    since = int(before.created_at.timestamp())
    prompt = (path / "inputs/prompt.txt").read_text(encoding="utf-8")
    spends = []
    for page in range(1, 4):
        data = await libtv_account.consumption(page, 20, before.browser_url or "")
        spends.extend(data.items)
        if data.items and min(item.seconds for item in data.items) < since:
            break
    return auto_verdict(spends, prompt=prompt, since=since)


def cmd_libtv_reconcile(args: argparse.Namespace) -> None:
    """人工核对上游之后，把「提交回执未知」的 LibTV 档案解封或接回。"""
    import uuid

    from core.exceptions import AppError
    from infra.archive import get_archive_storage
    from modules.model_driver.libtv import executor, process
    from modules.model_driver.libtv.reconcile import reconcile

    try:
        path = executor.run_path(args.run_id)
        before = executor.state(path)
        # 非法组合由 reconcile 抛 BadRequest：原样报给用户、不打 traceback ——
        # 这条命令是核对完之后用的，报错要能直接照着做。
        busy = process.executor_busy(path)
        not_submitted = args.not_submitted
        adopt_task = args.adopt_task or None
        if args.auto:
            # 用消费明细替人做那次「去 LibTV 看一眼」：命中原样接回，没命中就解封。
            verdict = asyncio.run(_auto_verdict(path, before))
            print(f"自动对账：{verdict.describe()}")
            if verdict.action == "adopt":
                adopt_task = verdict.task_id
            elif verdict.action == "release":
                not_submitted = True
            else:
                print(
                    "无法自动判断，请人工核对后用 --not-submitted / --adopt-task",
                    file=sys.stderr,
                )
                raise SystemExit(1)

        after = reconcile(
            before,
            not_submitted=not_submitted,
            adopt_task=adopt_task,
            # 执行器还在跑时不许改账：桥接层随后写回 task_id 会和这里改过的状态打架
            executor_busy=busy,
        )
        storage = get_archive_storage()
        storage.write(path / "run.json", after.model_dump(mode="json"))
    except AppError as exc:
        print(f"未改动档案：{exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    # 上一轮的错误回执留着会让人以为这次又失败了（与 `process.py` 同一做法）。
    stale = path / "records/executor-error.json"
    if stale.exists():
        stale.rename(path / "records" / f"executor-error-{uuid.uuid4().hex}.json")

    print(f"档案 {args.run_id}：{before.status} → {after.status}")
    if after.task_id:
        print(f"上游 task_id：{after.task_id}（账号 {after.browser_url or '默认'}）")
    if after.task_id:
        print("下一步：任务页点「重新生成」——复用同一个上游任务继续查询，不会重新下单")
    else:
        print("下一步：任务页点「重新生成」——会重新上传素材并提交")


def cmd_libtv_account(args: argparse.Namespace) -> None:
    """打印 LibTV 账号现状：额度、0 积分权益、可选消费明细与任务排队位次。

    这是「浏览器里能看到、任务中心看不到」的那半边：任务卡只有百分比，
    而「排第几」「还能提交几个」「这笔扣了多少」都在账号侧。
    """
    import asyncio
    from datetime import datetime

    from core.exceptions import AppError
    from modules.model_driver.libtv import account as libtv

    async def run() -> None:
        snapshot = await libtv.snapshot(args.account)
        asset = snapshot.get("asset")
        print(f"账号 {snapshot['account']}")
        if asset is not None:
            print(
                f"  {asset.name}（到期 {asset.expire_date}）"
                f" 可用 {asset.usable_power}（赠送 {asset.free_usable_power}）"
                f" 已用 {asset.used_power} | 还能提交 {asset.task_can_submit} 个任务"
            )
        else:
            print("  额度读取失败（其余项照常显示）")
        free = snapshot.get("freePower") or 0
        print(f"  LibTV 免费额度 {free}，到期 {snapshot.get('freePowerExpireAt')}")
        for benefit in snapshot.get("zeroPowerBenefits") or []:
            tags = benefit["tags"]
            print(f"  0 积分畅用：{benefit['targetName']} 至 {benefit['endTime']} {tags}")

        if args.tasks:
            for progress in await libtv.progress(args.tasks, args.account):
                print(f"  {progress.task_id}  {progress.describe()}")

        if args.consumption:
            total = await libtv.consumption_total(args.account)
            print(
                f"  累计消耗 {total.count} 笔 / {total.amount} 积分，"
                f"最近 {args.consumption} 条："
            )
            page = await libtv.consumption(1, args.consumption, args.account)
            for spend in page.items:
                when = datetime.fromtimestamp(spend.seconds).strftime("%m-%d %H:%M:%S")
                print(
                    f"    {when}  {spend.amount:>4}  {spend.source_name:<10} "
                    f"{spend.task_id:<22} {spend.summary[:30]}"
                )

    try:
        asyncio.run(run())
    except AppError as exc:
        print(f"查询失败：{exc}", file=sys.stderr)
        raise SystemExit(1) from exc


def register(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = sub.add_parser("drivers", help="模型驱动自检与清单")
    actions = parser.add_subparsers(dest="drivers_action", required=True)
    actions.add_parser("list", help="列出自动载入的驱动与模型数").set_defaults(func=cmd_list)
    actions.add_parser("check", help="校验驱动契约，有问题 exit 1").set_defaults(func=cmd_check)

    rescue = sub.add_parser(
        "libtv-reconcile",
        help="LibTV「提交回执未知」的解封/接回（先人工核对上游记录）",
    )
    rescue.add_argument("run_id", help="归档工作区 id（通用链里就是任务 id）")
    kind = rescue.add_mutually_exclusive_group(required=True)
    kind.add_argument(
        "--not-submitted",
        action="store_true",
        help="核对后确认上游没有这笔任务：解封，允许重新上传提交",
    )
    kind.add_argument(
        "--adopt-task",
        default="",
        help="核对后确认上游已在跑：填它的 task_id，之后重试只查询下载、不重新下单",
    )
    kind.add_argument(
        "--auto",
        action="store_true",
        help="不用人工核对：用消费明细（含上游 task_id 与提示词）自动判断该接回还是解封",
    )
    rescue.set_defaults(func=cmd_libtv_reconcile)

    status = sub.add_parser(
        "libtv-account",
        help="LibTV 账号现状：积分额度、0 积分权益、排队位次、消费明细",
    )
    status.add_argument(
        "--account",
        default="",
        help="CDP 地址（9411 免费队列 / 9412 积分队列），留空按规则挑",
    )
    status.add_argument(
        "--consumption",
        type=int,
        default=0,
        metavar="N",
        help="附上最近 N 条积分消耗（含上游 task_id，对账用）",
    )
    status.add_argument(
        "--tasks",
        nargs="*",
        default=[],
        metavar="TASK_ID",
        help="附上这些上游任务的进度与排队位次",
    )
    status.set_defaults(func=cmd_libtv_account)
