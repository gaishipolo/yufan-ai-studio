"""命令行入口：`app <command>`。

    app serve                          # 启动开发服务器
    app init-db                        # 按 ORM 元数据建表
    app seed [--reset]                 # 灌演示数据
    app create-admin --username admin  # 创建/修复超级管理员（生产首启必跑一次）
    app modules                        # 列出已发现的模块与权限点
    app models                         # 列出可用模型（供应商路由结果）
    app export-openapi                 # 导出接口契约（docs/ + 前端 TS 类型）
    app new-module voice --label 配音   # 生成新模块脚手架
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import sys
from pathlib import Path


def _cmd_serve(args: argparse.Namespace) -> None:
    import uvicorn

    uvicorn.run(
        "main:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
    )


def _cmd_init_db(_: argparse.Namespace) -> None:
    from db.session import init_db

    asyncio.run(init_db())
    print("数据表已创建")


def _cmd_seed(args: argparse.Namespace) -> None:
    from seed import seed

    counts = asyncio.run(seed(reset=args.reset))
    print("演示数据已写入：", ", ".join(f"{k}={v}" for k, v in counts.items()))


def _cmd_create_admin(args: argparse.Namespace) -> None:
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


def _cmd_modules(_: argparse.Namespace) -> None:
    from api.discovery import discover_modules
    from app import load_extension_points
    from core.permissions import catalog
    from generation import pipeline_kinds
    from workers import handler_names

    load_extension_points()  # 注册框架自带的 job handler

    specs = discover_modules()
    width = max((len(",".join(sorted(s.permission_codes())) or "-") for s in specs), default=8)
    width = max(width, 8) + 2

    print(f"{'模块':<12}{'路由':<6}{'权限点':<{width}}说明")
    print("-" * (12 + 6 + width + 20))
    for spec in specs:
        perms = ",".join(sorted(spec.permission_codes())) or "-"
        print(f"{spec.name:<12}{len(spec.router.routes):<6}{perms:<{width}}{spec.description}")

    print()
    print(f"生成流水线：{pipeline_kinds() or '-'}")
    print(f"Job handler：{handler_names() or '-'}")
    print(f"权限点合计：{len(catalog)}")


def _cmd_models(_: argparse.Namespace) -> None:
    from infra import get_model_router

    router = get_model_router()
    summary = router.summary()
    print("供应商：", ", ".join(summary["providers"]) or "-")
    print()
    print(f"{'模型 ID':<18}{'能力':<10}{'供应商':<14}{'展示名'}")
    print("-" * 60)
    for model in summary["models"]:
        print(
            f"{model['id']:<18}{str(model['capability']):<10}"
            f"{model['provider']:<14}{model['label']}"
        )


def _cmd_export_openapi(args: argparse.Namespace) -> None:
    """把 OpenAPI 固化成给前端用的三份文件（json / md / ts）。"""
    from openapi_export import DEFAULT_JSON, DEFAULT_MD, DEFAULT_TS, export

    targets = {
        "json": Path(args.out_json) if args.out_json else DEFAULT_JSON,
        "md": Path(args.out_md) if args.out_md else DEFAULT_MD,
        "ts": Path(args.out_ts) if args.out_ts else DEFAULT_TS,
    }
    try:
        status = export(
            json_path=targets["json"],
            md_path=targets["md"],
            ts_path=targets["ts"],
            check=args.check,
        )
    except SystemExit as exc:
        print(str(exc), file=sys.stderr)
        raise

    verb = {"written": "已写入", "unchanged": "无变化", "stale": "已过期", "missing": "缺失"}
    for kind, path in targets.items():
        print(f"{verb.get(status[str(path)], status[str(path)]):<8}{kind:<5}{path}")


def _cmd_new_module(args: argparse.Namespace) -> None:
    from scaffold import ModulePlan, ScaffoldError, create_module

    plan = ModulePlan(
        name=args.name,
        label=args.label,
        perm=args.perm or args.name,
        slot=args.slot,
        group=args.group,
        with_generation=args.generation,
    )
    try:
        written = create_module(plan, Path(args.root).resolve())
    except ScaffoldError as exc:
        print(f"生成失败：{exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    print(f"已创建模块 {plan.name}：")
    for path in written:
        print(f"  + {path.relative_to(Path(args.root).resolve())}")
    print()
    print("下一步：")
    print("  1. 补全 service.py 的业务逻辑（当前接口返回 501）")
    print(f"  2. 执行 alembic revision --autogenerate -m '{plan.name} init'")
    print("  3. 重启服务，`app modules` 应能看到该模块")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="app", description="天火 AI 短视频工作台后端")
    sub = parser.add_subparsers(dest="command", required=True)

    p_serve = sub.add_parser("serve", help="启动 API 服务")
    p_serve.add_argument("--host", default="0.0.0.0")
    p_serve.add_argument("--port", type=int, default=8080)
    p_serve.add_argument("--reload", action="store_true", help="代码变更自动重启")
    p_serve.set_defaults(func=_cmd_serve)

    p_init = sub.add_parser("init-db", help="按 ORM 元数据建表")
    p_init.set_defaults(func=_cmd_init_db)

    p_seed = sub.add_parser("seed", help="写入演示数据")
    p_seed.add_argument("--reset", action="store_true", help="先清空现有数据")
    p_seed.set_defaults(func=_cmd_seed)

    p_admin = sub.add_parser("create-admin", help="创建/修复超级管理员（生产首启必跑）")
    p_admin.add_argument("--username", required=True, help="登录名")
    p_admin.add_argument("--password", default="", help="不传则交互式输入")
    p_admin.add_argument("--display-name", default="", help="昵称，默认与用户名相同")
    p_admin.set_defaults(func=_cmd_create_admin)

    p_modules = sub.add_parser("modules", help="列出已发现的模块与扩展点")
    p_modules.set_defaults(func=_cmd_modules)

    p_models = sub.add_parser("models", help="列出模型目录")
    p_models.set_defaults(func=_cmd_models)

    p_export = sub.add_parser(
        "export-openapi", help="导出接口契约（docs/openapi.json + docs/API.md + 前端 schema.ts）"
    )
    p_export.add_argument("--out-json", default="", help="OpenAPI JSON 输出路径")
    p_export.add_argument("--out-md", default="", help="Markdown 文档输出路径")
    p_export.add_argument("--out-ts", default="", help="前端 TS 类型输出路径")
    p_export.add_argument(
        "--check", action="store_true", help="只比对不写入，不一致时以非 0 退出（CI 用）"
    )
    p_export.set_defaults(func=_cmd_export_openapi)

    p_new = sub.add_parser("new-module", help="生成新模块脚手架")
    p_new.add_argument("name", help="模块名（snake_case），同时作为目录名与 pipeline kind")
    p_new.add_argument("--label", required=True, help="中文名，用于路由标签与权限名")
    p_new.add_argument("--perm", default="", help="权限前缀，默认与模块名相同")
    p_new.add_argument(
        "--slot",
        type=int,
        default=0,
        help="错误码号段 1-9，必填（0 为框架保留）；见 docs/EXTENDING.md 第 7 节",
    )
    p_new.add_argument("--group", default="业务", help="权限分组（用于角色配置界面）")
    p_new.add_argument("--generation", action="store_true", help="同时生成生成流水线骨架")
    p_new.add_argument("--root", default=".", help="项目根目录")
    p_new.set_defaults(func=_cmd_new_module)

    args = parser.parse_args(argv)
    args.func(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
