"""身份模块的运维命令：`app create-admin` / `prune-logins` / `prune-sessions` / `sync-roles`。

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
import sys


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
