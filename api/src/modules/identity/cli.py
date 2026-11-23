"""身份模块的运维命令。

`app create-admin` / `prune-logins` / `prune-sessions` / `sync-roles` / `purge-users`。

## 为什么这些命令不在 `src/cli.py`

它们全部依赖本模块的实现（`IdentityService`、口令策略、权限快照键）。写在框架的
CLI 里，那个文件就必须 `from modules.identity.service import ...`——删掉身份模块，
连 `app backup` 这种救命命令都起不来；加一条运维命令，也要去改 1200 行的框架文件。

`ModuleSpec.cli` 把这件事反过来：框架只负责在组装解析器时调一次 `register(sub)`，
模块自己挂命令。和 `ModuleSpec.doctor` / `ModuleSpec.seed` 是同一个模式。
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import sys
from dataclasses import asdict
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # 只用于注解：命令执行时才 import，别把启动链拖长
    from modules.identity.purge import PurgeReport


def cmd_create_admin(args: argparse.Namespace) -> None:
    """生产首启的唯一入口：没有账号时谁也登不进去，必须由运维在服务器上创建。"""
    from db.session import get_session_factory
    from modules.identity.policy import WeakPassword, check_password_strength
    from modules.identity.service import IdentityService

    password = args.password
    if not password:
        if not sys.stdin.isatty():
            print("非交互环境请用 --password 传入密码", file=sys.stderr)
            raise SystemExit(1)
        password = getpass.getpass("密码: ")
    try:
        # 与 API 同一份策略：这个账号是权限最大的那个，不能是唯一不受约束的入口
        check_password_strength(password)
    except WeakPassword as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1) from exc

    async def run() -> tuple[str, str, bool]:
        async with get_session_factory()() as session:
            user, created = await IdentityService(session).ensure_superuser(
                args.username, password, args.display_name
            )
            return user.username, user.id, created

    username, user_id, created = asyncio.run(run())
    print(f"{'已创建' if created else '已更新'}超级管理员：{username}（{user_id}）")


def cmd_prune_logins(args: argparse.Namespace) -> None:
    """清理过期的登录审计。

    不做成「每次登录顺手删一批」：那会把一次写操作变成「写 + 扫表」，
    而且审计该留多久是运维决定，不该由某个请求的运气决定。
    """
    from core.config import get_settings
    from db.session import get_session_factory
    from modules.identity.service import IdentityService

    days = args.days or get_settings().login_event_retention_days

    async def run() -> int:
        async with get_session_factory()() as session:
            return await IdentityService(session).purge_login_events(days)

    removed = asyncio.run(run())
    print(f"已清理 {days} 天前的登录审计：{removed} 行")


def cmd_prune_sessions(args: argparse.Namespace) -> None:
    """清理已经过期的登录会话。

    与 `prune-logins` 分开：两者的保留期不是一回事——审计要留 90 天（追溯用），
    会话行一过 `expires_at` 就没人再读，留着只撑表。
    """
    from db.session import get_session_factory
    from modules.identity.service import IdentityService

    async def run() -> int:
        async with get_session_factory()() as session:
            return await IdentityService(session).purge_sessions(args.days)

    removed = asyncio.run(run())
    print(f"已清理 {args.days} 天前过期的会话：{removed} 行")


def cmd_sync_roles(args: argparse.Namespace) -> None:
    """全量补齐内置角色缺失的权限点（只加不减）。

    **日常不需要跑**：启动时的 `sync_new_permissions` 会把这一版新增的权限点
    自动并进去（靠 `app_meta.permissions.catalog` 的快照判断哪些是「新增」）。
    它的适用范围只剩两种手工场景：

    - 有人把内置角色的权限改乱了，想按 `BUILTIN_ROLES` 恢复默认；
    - 部署环境从不执行启动钩子（例如只跑 `alembic upgrade` 的离线库），
      想提前把角色准备好。

    语义仍是「只加不减」：管理员手工勾掉的不会回来；只打印差异，可重复执行。

    `--prune-snapshot` 是唯一会**删除**权限目录快照条目的入口（见
    `sync_new_permissions` 的「只增不减」说明）：自动路径不敢收缩快照，因为分不清
    「这一版少声明了」和「写的人是个旧版本」；人明确跑这一条时，两者不再是问题。
    """
    from core.permissions import catalog
    from db.app_meta import get_json
    from db.session import get_session_factory
    from modules.identity.service import PERMISSION_SNAPSHOT_KEY, IdentityService

    # 这里不再自己调 `discover_modules()`：`app` 在组装解析器时已经发现过模块
    # （`cli._register_module_commands`），所以 `catalog` 必然是填好的。
    # 以前必须手动补这一句，因为 CLI 不走 `create_app()`；漏了的话
    # `catalog.codes()` 是空集，`--prune-snapshot` 会把整张权限快照抹掉。

    async def run() -> tuple[dict[str, list[str]], list[str], list[str]]:
        async with get_session_factory()() as session:
            service = IdentityService(session)
            snapshot = set(await get_json(session, PERMISSION_SNAPSHOT_KEY) or [])
            added = await service.sync_builtin_role_permissions(dry_run=args.dry_run)
            if not args.prune_snapshot:
                return added, [], []
            if args.dry_run:
                return added, [], sorted(snapshot - set(catalog.codes()))
            # `prune=True` 会把快照改写成「当前声明的全部」并按差集重跑一次补齐
            await service.sync_new_permissions(prune=True)
            after = set(await get_json(session, PERMISSION_SNAPSHOT_KEY) or [])
            return added, sorted(set(after) - snapshot), sorted(snapshot - after)

    added, grown, pruned = asyncio.run(run())
    if args.dry_run:
        print("（dry-run：只列出会改什么，没有写库）")
    if not added:
        print("内置角色已是最新，无需补齐")
    for code, codes in added.items():
        print(f"{code}: +{' '.join(codes)}")
    for code in grown:
        print(f"快照补入：{code}")
    for code in pruned:
        print(f"快照移除：{code}（当前目录已不再声明）")
    if args.prune_snapshot and not grown and not pruned:
        print("快照与当前声明一致，无需收缩")


def cmd_purge_users(args: argparse.Namespace) -> None:
    """按用户名模式批量停用 / 删除账号。默认只预演。

    三个动作互斥：`--dry-run`（默认）/ `--deactivate` / `--delete`。
    默认只读是刻意的 —— 这条命令的输入是一个**模式**，写宽一个字符，
    删掉的就不只是冒烟账号了。
    """
    from db.session import get_session_factory
    from modules.identity.purge import Mode, purge_users

    mode: Mode = "deactivate" if args.deactivate else "delete" if args.delete else "dry-run"

    async def run() -> PurgeReport:
        async with get_session_factory()() as session:
            return await purge_users(
                session,
                pattern=args.pattern,
                mode=mode,
                allow_orphans=args.allow_orphans,
                include_admins=args.include_admins,
            )

    report = asyncio.run(run())
    _print_purge(report, as_json=args.json)
    # 预演即便有 blocker 也是成功的（它的产出就是那句「按这个参数会失败」）
    if mode != "dry-run" and report.blockers:
        raise SystemExit(1)


def _print_purge(report: PurgeReport, *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(asdict(report), ensure_ascii=False, indent=2, default=str))
        return

    if not report.accounts:
        print(f"没有用户名匹配 {report.pattern!r} 的账号")
        return

    active = sum(1 for account in report.accounts if account.is_active)
    label = {"dry-run": "预演（不改库）", "deactivate": "停用", "delete": "物理删除"}
    print(
        f"匹配 {report.pattern!r} 的账号 {report.matched} 个"
        f"（启用 {active} / 停用 {report.matched - active}）"
    )
    print(f"动作：{label[report.mode]}")
    print()
    for account in report.accounts:
        state = "已启用" if account.is_active else "已停用"
        marker = " [超管]" if account.is_superuser else ""
        seen = f"{account.last_login_at:%Y-%m-%d %H:%M}" if account.last_login_at else "-"
        print(f"  {account.username:32} {state}  最后登录 {seen}{marker}")

    print()
    # 措辞要跟「有没有真执行」对得上：预演与被拒时写「会 / 若执行」，
    # 否则「会删 12 行」后面紧跟「不会执行」，读起来自相矛盾。
    if report.mode == "deactivate":
        if report.executed:
            print(
                f"已停用 {active} 个账号、吊销 {report.revoked_sessions} 个会话"
                "（账号行保留，可再启用）"
            )
        else:
            print(f"会停用 {active} 个仍启用的账号，并吊销它们当前有效的会话")
    else:
        prefix = "已删除" if report.executed else "若执行，会删"
        detail = "、".join(f"{name} {count}" for name, count in report.cleared.items() if count)
        print(f"{prefix} {report.matched} 行 users，并清掉关联行：{detail or '无'}")
        left = "、".join(f"{name} {count}" for name, count in report.kept.items() if count)
        print(f"会留下（不删，变成悬空引用）：{left or '无'}")

    if report.blockers:
        print()
        print("不会执行，原因：")
        for reason in report.blockers:
            print(f"  - {reason}")
        return

    if report.mode == "dry-run":
        print()
        print("预演结束，没有改库。确认无误后加 --deactivate（可再启用）或 --delete（不可恢复）。")
    else:
        print()
        print("已执行，并写了一条 identity.user.purge 审计。")


def register(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """把本模块的运维命令挂到 `app` 上（框架在组装解析器时调用一次）。"""
    p_admin = sub.add_parser("create-admin", help="创建/修复超级管理员（生产首启必跑）")
    p_admin.add_argument("--username", required=True, help="登录名")
    p_admin.add_argument("--password", default="", help="不传则交互式输入")
    p_admin.add_argument("--display-name", default="", help="昵称，默认与用户名相同")
    p_admin.set_defaults(func=cmd_create_admin)

    p_prune = sub.add_parser("prune-logins", help="清理过期的登录审计")
    p_prune.add_argument(
        "--days", type=int, default=0, help="保留天数，默认取 LOGIN_EVENT_RETENTION_DAYS"
    )
    p_prune.set_defaults(func=cmd_prune_logins)

    p_prune_sessions = sub.add_parser("prune-sessions", help="清理已过期的登录会话")
    p_prune_sessions.add_argument(
        "--days",
        type=int,
        default=7,
        help="清理「过期超过多少天」的会话行，默认 7（refresh 有效期即 7 天）",
    )
    p_prune_sessions.set_defaults(func=cmd_prune_sessions)

    p_sync_roles = sub.add_parser(
        "sync-roles",
        help="全量补齐内置角色缺失的权限点（启动时只自动补「新增」的；这个命令用于手工兜底）",
    )
    p_sync_roles.add_argument(
        "--dry-run", action="store_true", help="只打印会补什么，不写库（升级前预演）"
    )
    p_sync_roles.add_argument(
        "--prune-snapshot",
        action="store_true",
        help="同时把权限目录快照收缩成「当前声明的全部」（自动路径只增不减）",
    )
    p_sync_roles.set_defaults(func=cmd_sync_roles)

    p_purge = sub.add_parser(
        "purge-users",
        help="按用户名模式批量停用或删除账号（默认只预演：冒烟测试留下的 smoke_* 用这个清）",
    )
    p_purge.add_argument(
        "--pattern",
        required=True,
        help="用户名模式：`*` 任意串、`?` 单字符，其余字符按字面（如 'smoke_*'）",
    )
    action = p_purge.add_mutually_exclusive_group()
    action.add_argument("--dry-run", action="store_true", help="只打印会动什么、不动库（默认行为）")
    action.add_argument(
        "--deactivate", action="store_true", help="停用并吊销会话（账号行保留，可再启用）"
    )
    action.add_argument("--delete", action="store_true", help="物理删除账号及其关联行（不可恢复）")
    p_purge.add_argument(
        "--allow-orphans",
        action="store_true",
        help="--delete 时允许留下内容引用（任务 / 素材等会失去作者）",
    )
    p_purge.add_argument("--include-admins", action="store_true", help="允许处理超级管理员账号")
    p_purge.add_argument("--json", action="store_true", help="以 JSON 输出（便于核对）")
    p_purge.set_defaults(func=cmd_purge_users)
