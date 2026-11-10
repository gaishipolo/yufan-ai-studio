"""模型驱动运维命令：`app drivers list` / `app drivers check`。

`check` 有退出码，给 CI 与部署脚本用：驱动文件写错、模型 id 撞车都会 exit 1，
而不是等第一次生成才报「未知模型」。
"""

from __future__ import annotations

import argparse
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


def register(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = sub.add_parser("drivers", help="模型驱动自检与清单")
    actions = parser.add_subparsers(dest="drivers_action", required=True)
    actions.add_parser("list", help="列出自动载入的驱动与模型数").set_defaults(func=cmd_list)
    actions.add_parser("check", help="校验驱动契约，有问题 exit 1").set_defaults(func=cmd_check)
