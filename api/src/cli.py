"""命令行入口：`app <command>`。

app serve                          # 启动开发服务器
app init-db                        # 按 ORM 元数据建表
app seed [--reset]                 # 灌演示数据
app create-admin --username admin  # 创建/修复超级管理员（生产首启必跑一次）
app modules                        # 列出已发现的模块与权限点
app models                         # 列出可用模型（供应商路由结果）
app export-openapi                 # 导出接口契约（docs/ + 前端 TS 类型）
app import-archive-index           # 归档目录的 JSON 索引 → assets 表（幂等）
app migrate-storage                # 把本地 storage 根目录的对象搬进当前驱动（local → s3）
app migrate-blobs                  # 归档素材改内容寻址布局（key = sha256，幂等）
app migrate-archive                # 把本地归档根的内容搬进当前归档驱动（local → s3）
app prune-blobs                    # 回收没人引用的孤儿素材字节（默认只报告）
app runs-status                    # 运行工作区现状：多少档案只在本机可见
app new-module voice --label 配音   # 生成新模块脚手架
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # 只用于注解：命令行入口保持「用到才 import」，别把启动链拖长
    from infra.archive.base import ArchiveStorage
    from infra.storage.base import ObjectStorage
    from modules.asset.models import Asset


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
    """写入演示数据。

    `seed` 里的账号口令是**固定字符串**（`admin@12345` 等），一旦进生产就是
    一个已知口令的超级管理员。而部署脚本里「顺手跑一次 seed 初始化数据」很常见，
    所以这里必须按环境硬拒，不能只在注释里写「生产用 create-admin」（R6-06）。
    """
    from core.config import get_settings

    if get_settings().app_env == "prod" and not args.force:
        print(
            "生产环境拒绝写入演示数据：seed 会创建口令固定的账号。确需写入请显式加 --force",
            file=sys.stderr,
        )
        raise SystemExit(1)

    from seed import seed

    counts = asyncio.run(seed(reset=args.reset))
    print("演示数据已写入：", ", ".join(f"{k}={v}" for k, v in counts.items()))


def _cmd_prune_logins(args: argparse.Namespace) -> None:
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


def _cmd_prune_sessions(args: argparse.Namespace) -> None:
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


def _cmd_sync_roles(args: argparse.Namespace) -> None:
    """把新声明的权限点补进已有内置角色（只加不减）。

    上线一个带新权限点的模块时跑一次。没有这一步，老库里的 editor/viewer
    仍是旧权限集：超管自测一切正常，非超管点新功能的按钮直接 403。
    只打印差异、不删任何权限，可以安全地放进部署脚本重复执行。
    """
    from db.session import get_session_factory
    from modules.identity.service import IdentityService

    async def run() -> dict[str, list[str]]:
        async with get_session_factory()() as session:
            return await IdentityService(session).sync_builtin_role_permissions()

    added = asyncio.run(run())
    if not added:
        print("内置角色已是最新，无需补齐")
        return
    for code, codes in added.items():
        print(f"{code}: +{' +'.join(codes)}")


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
    from core.scheduler import periodic_jobs
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
    print(f"周期任务：{', '.join(f'{j.name}({j.every:g}s)' for j in periodic_jobs()) or '-'}")
    print(f"权限点合计：{len(catalog)}")

    # 注入口是「可选依赖」：没接上不报错，但必须能从命令行看出来，
    # 否则「为什么离职的人还在跑任务」只能靠读代码发现（A3）
    from api.ports import port_status

    print()
    print("跨模块注入口：")
    for port, registered in port_status():
        state = "已注册" if registered else f"未注册（{port.when_unregistered}）"
        print(f"  {port.name:<20}{state:<12}{port.protocol:<24}{port.implementer}")


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
    from openapi_export import DEFAULT_JSON, DEFAULT_MD, DEFAULT_PERMS, DEFAULT_TS, export

    targets = {
        "json": Path(args.out_json) if args.out_json else DEFAULT_JSON,
        "md": Path(args.out_md) if args.out_md else DEFAULT_MD,
        "ts": Path(args.out_ts) if args.out_ts else DEFAULT_TS,
        "perms": Path(args.out_perms) if getattr(args, "out_perms", "") else DEFAULT_PERMS,
    }
    try:
        status = export(
            json_path=targets["json"],
            md_path=targets["md"],
            ts_path=targets["ts"],
            perms_path=targets["perms"],
            check=args.check,
        )
    except SystemExit as exc:
        print(str(exc), file=sys.stderr)
        raise

    verb = {"written": "已写入", "unchanged": "无变化", "stale": "已过期", "missing": "缺失"}
    for kind, path in targets.items():
        print(f"{verb.get(status[str(path)], status[str(path)]):<8}{kind:<5}{path}")


def _cmd_import_archive_index(args: argparse.Namespace) -> None:
    """把归档盘的 JSON 索引补进 `assets` 表。

    设计成可反复执行：主键沿用 JSON 的 id，第二次跑就是 `updated=0`。
    不删 JSON —— 替换模板在 worker 里仍按文件读素材，删了那边就断。
    """
    from core.exceptions import AppError
    from db.session import get_session_factory
    from infra.archive import get_archive_storage
    from modules.asset.archive_import import import_archive_index

    try:
        root = Path(args.root).resolve() if args.root else get_archive_storage().root()
    except AppError as exc:
        print(exc.message, file=sys.stderr)
        raise SystemExit(1) from exc

    if not (root / "catalog" / "assets").is_dir():
        print(f"没有找到归档索引目录：{root / 'catalog' / 'assets'}", file=sys.stderr)
        raise SystemExit(1)

    async def run():
        async with get_session_factory()() as session:
            return await import_archive_index(session, root, dry_run=args.dry_run)

    report = asyncio.run(run())
    for line in report.lines():
        print(line)


def _cmd_migrate_storage(args: argparse.Namespace) -> None:
    """把本地存储目录里的对象按 key 搬进当前 `STORAGE_DRIVER`，并修正库里的旧地址。

    切驱动要做两件事，缺一件就露馅：

    1. **搬对象**。DB 里的 `storage_key` 不变，桶里却没有对象 —— 素材列表还在，
       点开 404。
    2. **把落库的旧地址剥成 key**。`image_jobs.output_url` 存的是生成那一刻
       `url_for()` 的产物（本地驱动下是 `/static/images/xxx.png`）。`/static` 只在
       本地驱动挂载（`app._mount_static`），切到 S3 之后这些历史地址直接没人应答。
       现在读路径只认 key（出参时按当前驱动现签），所以这一步是「把数据搬回
       驱动无关的形态」，跑完再换多少次驱动都不会失联。
    """
    from core.config import settings
    from infra import get_storage
    from infra.storage.migration import migrate_local_objects

    if settings.storage_driver == "local":
        print(
            "当前 STORAGE_DRIVER=local，目标就是源目录本身。"
            "先把 STORAGE_DRIVER 配成 s3（连同 S3_* 四项）再执行本命令",
            file=sys.stderr,
        )
        raise SystemExit(1)

    root = Path(args.root).resolve() if args.root else Path(settings.storage_local_root)
    if not root.is_dir():
        print(f"源目录不存在：{root}", file=sys.stderr)
        raise SystemExit(1)

    storage = get_storage()
    report = asyncio.run(
        migrate_local_objects(storage, root, dry_run=args.dry_run, delete_source=args.delete_source)
    )
    print(f"源目录：{root}")
    print(f"目标驱动：{settings.storage_driver}")
    for line in report.lines():
        print(line)

    if not args.dry_run and not args.skip_urls:
        rewritten, scanned = asyncio.run(_strip_legacy_urls(storage))
        print(f"  已把 {rewritten} 处落库地址剥成对象 key（扫描 {scanned} 行）")

    if report.failed:
        raise SystemExit(1)


async def _strip_legacy_urls(storage: ObjectStorage) -> tuple[int, int]:
    """把落库的旧绝对地址剥成对象 key；返回（改写数，扫描行数）。

    `image_jobs.output_url` / `cover_url` 以前存的是生成那一刻 `url_for()` 的产物
    （`/static/images/xxx.png`），现在只存 key，地址在出参时按当前驱动现签。
    老值留着就等于把「生成那一刻的驱动」固化进数据：换一次驱动失联一次。

    只剥「本服务拼得出来的」前缀（当前驱动的 `url_for("")` 与静态前缀），
    供应商直链与 `data:` 原样保留 —— 那些本来就不是我们的对象。
    """
    from sqlalchemy import select

    from core.config import settings
    from db.session import get_session_factory
    from modules.image.models import ImageJob

    prefixes = tuple(
        {
            settings.storage_public_base.rstrip("/") + "/",
            storage.url_for(""),
        }
    )
    rewritten = 0

    async with get_session_factory()() as session:
        rows = (await session.execute(select(ImageJob))).scalars().all()
        for row in rows:
            for column in ("cover_url", "output_url"):
                value = getattr(row, column)
                key = _strip_prefix(value, prefixes)
                if key is None:
                    continue
                setattr(row, column, key)
                rewritten += 1
        if rewritten:
            await session.commit()

    return rewritten, len(rows)


def _strip_prefix(value: str | None, prefixes: tuple[str, ...]) -> str | None:
    """命中前缀就返回剩余部分（对象 key），否则 `None`。"""
    if not value:
        return None
    for prefix in prefixes:
        if prefix and value.startswith(prefix):
            key = value[len(prefix) :]
            # `//host/...` 这类协议相对地址不是我们的对象
            return key if key and not key.startswith("/") else None
    return None


def _cmd_migrate_blobs(args: argparse.Namespace) -> None:
    """把归档素材搬到「按 sha256 内容寻址」的布局（`blobs/ab/abcdef…`）。

    旧布局是「分类 + 年月 + 随机 id」，于是同一份字节可以有多个副本、也可以用
    同名覆盖 —— "这份字节还是不是当初那份"只能靠每次读出来重算 sha256 来猜。
    搬到内容寻址之后 key 就是摘要，发布校验与运行快照都不再需要读全量。

    幂等：已经是 blob 布局的行直接跳过，跑第二遍 `moved=0`。
    """
    moved, already, failed = asyncio.run(
        _migrate_archive_blobs(dry_run=args.dry_run, delete_source=args.delete_source)
    )
    print(f"已迁移 {moved} 条，已是新布局 {already} 条，缺字节 {failed} 条")
    if args.dry_run:
        print("（dry-run：未写库、未搬文件）")
    if failed:
        raise SystemExit(1)


async def _migrate_archive_blobs(*, dry_run: bool, delete_source: bool) -> tuple[int, int, int]:
    from sqlalchemy import select

    from db.session import get_session_factory
    from infra.archive import (
        BlobStore,
        clip_key,
        get_archive_storage,
        is_blob_key,
        preview_key,
    )
    from modules.asset.models import Asset
    from modules.asset.schema import AssetSource

    storage = get_archive_storage()
    blobs = BlobStore(storage)
    moved = already = failed = 0

    async with get_session_factory()() as session:
        rows = (
            (await session.execute(select(Asset).where(Asset.source == AssetSource.ARCHIVE)))
            .scalars()
            .all()
        )
        for row in rows:
            if is_blob_key(row.storage_key):
                already += 1
                continue
            old_key = row.storage_key
            if not storage.exists(old_key):
                failed += 1
                print(f"  缺字节 {row.id} ← {old_key}", file=sys.stderr)
                continue
            if not row.sha256:
                # 内容寻址靠摘要命名，没有摘要就只能是"位置寻址的孤儿"，跳过并报出来
                failed += 1
                print(f"  缺摘要 {row.id} ← {old_key}", file=sys.stderr)
                continue
            if dry_run:
                moved += 1
                continue

            key, _ = blobs.adopt(old_key, row.sha256, Path(old_key).suffix)
            old_cover = row.cover_key
            row.storage_key = key
            if old_cover and not old_cover.startswith("previews/"):
                target = (
                    clip_key(row.sha256) if old_cover.endswith(".mp4") else preview_key(row.sha256)
                )
                if storage.exists(old_cover):
                    with storage.materialize(old_cover) as source:
                        storage.copy(source, target, None)
                    row.cover_key = target
            await session.commit()
            _rewrite_asset_mirror(storage, row)
            if delete_source:
                storage.remove(old_key)
                if old_cover and old_cover != row.cover_key:
                    storage.remove(old_cover)
            moved += 1

    return moved, already, failed


def _cmd_migrate_archive(args: argparse.Namespace) -> None:
    """把本地归档根的内容搬进当前归档驱动（`local → s3`），幂等。

    只搬**服务会读的对象**：`catalog/`（素材镜像、封装草稿与发布版本）、
    `blobs/`（素材字节）、`previews/`（缩略图与转码流）、`library/`（换内容寻址
    之前的旧 key，历史记录仍指向它）。

    `runs/` 不搬：那是执行器的工作区，必须留在本地（`REPLACEMENT_RUNS_ROOT`）。
    `inbox/`、`incoming/`、`trash/` 是人手投放/临时上传/回收目录，也不属于内容。
    """
    from core.config import settings
    from infra.archive import get_archive_storage, local_storage

    if settings.archive_driver == "local":
        print("当前归档驱动就是本地目录，没有可搬的目标；先把 ARCHIVE_DRIVER 切到 s3")
        raise SystemExit(2)
    target = get_archive_storage()
    source = local_storage()

    copied = skipped = failed = 0
    for prefix in ("catalog", "blobs", "previews", "library"):
        for key in source.list_keys(prefix):
            if target.exists(key):
                skipped += 1
                continue
            if args.dry_run:
                copied += 1
                continue
            try:
                if key.startswith("catalog/") and key.endswith(".json"):
                    target.write(key, source.read(key))
                else:
                    with source.materialize(key) as path:
                        target.copy(path, key)
            except Exception as exc:  # noqa: BLE001 - 单个对象失败不该中断整次迁移
                failed += 1
                print(f"  失败 {key}：{exc}", file=sys.stderr)
                continue
            copied += 1

    print(f"已复制 {copied} 个对象，已存在 {skipped} 个，失败 {failed} 个")
    if args.dry_run:
        print("（dry-run：未写入目标驱动）")
    if failed:
        raise SystemExit(1)


def _cmd_prune_blobs(args: argparse.Namespace) -> None:
    """回收内容寻址下没人引用的字节。

    孤儿从哪来：登记是「先落字节、再落库行」，中间失败（校验不过、连接断、
    进程被杀）时字节已经在 `blobs/` 里。失败路径**有意不删**它——同一个 key
    可能正被并发登记的另一条记录共享，硬删会打断别人（T18）。代价就是这些
    没人引用的字节需要一次独立的、可审计的回收。

    默认只报告；`--execute` 才删。`--min-age-hours` 不是保守而是必要：
    没有它，一次正好撞上上传的回收会删掉「字节已写、行还没提交」的那一份。
    """
    orphans, deleted, protected = asyncio.run(
        _prune_archive_blobs(execute=args.execute, min_age_hours=args.min_age_hours)
    )
    print(
        f"孤儿字节 {orphans} 个，已删除 {deleted} 个，"
        f"处于保护期 {protected} 个（{args.min_age_hours:g} 小时内）"
    )
    if not args.execute:
        print("（dry-run：未删除任何对象；确认后加 --execute）")
    elif protected:
        print("保护期内的对象下轮再扫：它们可能正在被登记流程写入")


async def _prune_archive_blobs(*, execute: bool, min_age_hours: float) -> tuple[int, int, int]:
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import select

    from db.session import get_session_factory
    from infra.archive import get_archive_storage
    from modules.asset.models import Asset

    storage = get_archive_storage()
    async with get_session_factory()() as session:
        rows = (await session.execute(select(Asset))).scalars().all()

    # 引用集合 = 库行的 storage_key/cover_key + 镜像 JSON 的同名字段。
    # 镜像必须算进来：模板 worker 直接读 `catalog/assets/*.json`，库里删掉的行
    # 只要镜像还在，就仍可能被人读，那份字节不能当垃圾。
    referenced: set[str] = set()
    for row in rows:
        referenced.update(key for key in (row.storage_key, row.cover_key) if key)
    for key in storage.list_keys("catalog/assets", suffix=".json"):
        value = storage.read(key)
        if not isinstance(value, dict):
            continue
        for field in ("path", "previewPath"):
            mirror_key = value.get(field)
            if isinstance(mirror_key, str) and mirror_key:
                referenced.add(mirror_key)

    cutoff = datetime.now(UTC) - timedelta(hours=min_age_hours)
    orphans = deleted = protected = 0
    for key in storage.list_keys("blobs"):
        if key in referenced:
            continue
        modified = storage.modified_at(key)
        if modified is None or modified > cutoff:
            protected += 1
            continue
        orphans += 1
        print(f"  孤儿 {key}")
        if execute:
            storage.remove(key)
            deleted += 1
    return orphans, deleted, protected


def _rewrite_asset_mirror(storage: ArchiveStorage, row: Asset) -> None:
    """镜像 JSON 的 `path` 也要跟着搬：替换模板读的是它，不是 DB。"""
    key = f"catalog/assets/{row.id}.json"
    if not storage.exists(key):
        return
    payload = storage.read(key)
    if not isinstance(payload, dict):
        return
    payload["path"] = row.storage_key
    payload["previewPath"] = row.cover_key or None
    storage.write(key, payload)


def _cmd_runs_status(_: argparse.Namespace) -> None:
    """运行工作区的部署形态，以及有多少历史档案在这台机器上打不开。

    T21 的落地：切到远端归档之后，索引指针在桶里、`run.json` 与成片在本地。
    任务列表照样能看到档案，点开却是「调用档案不存在」——不知道这个前提的人
    会当成数据丢了。这条命令把「多少档案只在本机可见」变成一句可查的话。
    """
    from infra.archive.runs import (
        orphan_pointers,
        pointer_ids,
        runs_root,
        workspace_needs_attention,
        workspace_note,
    )

    print(workspace_note())
    print(f"工作区根：{runs_root()}")
    if workspace_needs_attention():
        print("  这是默认的单机形态；多实例或换机器要共享，请设置 REPLACEMENT_RUNS_ROOT")

    total = len(pointer_ids())
    missing = orphan_pointers()
    print(f"归档登记 {total} 个档案，本机可见 {total - len(missing)} 个")
    if missing:
        print(f"以下 {len(missing)} 个只有指针、没有工作区（换机器后就是这个表现）：")
        for run_id in missing[:20]:
            print(f"  {run_id}")
        if len(missing) > 20:
            print(f"  ……另有 {len(missing) - 20} 个")


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
    parser = argparse.ArgumentParser(prog="app", description="雨范 AI 短视频工作台后端")
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
    p_seed.add_argument(
        "--force",
        action="store_true",
        help="允许在 APP_ENV=prod 下写入（默认拒绝：演示账号口令是固定的）",
    )
    p_seed.set_defaults(func=_cmd_seed)

    p_admin = sub.add_parser("create-admin", help="创建/修复超级管理员（生产首启必跑）")
    p_admin.add_argument("--username", required=True, help="登录名")
    p_admin.add_argument("--password", default="", help="不传则交互式输入")
    p_admin.add_argument("--display-name", default="", help="昵称，默认与用户名相同")
    p_admin.set_defaults(func=_cmd_create_admin)

    p_prune = sub.add_parser("prune-logins", help="清理过期的登录审计")
    p_prune.add_argument(
        "--days", type=int, default=0, help="保留天数，默认取 LOGIN_EVENT_RETENTION_DAYS"
    )
    p_prune.set_defaults(func=_cmd_prune_logins)

    p_prune_sessions = sub.add_parser("prune-sessions", help="清理已过期的登录会话")
    p_prune_sessions.add_argument(
        "--days",
        type=int,
        default=7,
        help="清理「过期超过多少天」的会话行，默认 7（refresh 有效期即 7 天）",
    )
    p_prune_sessions.set_defaults(func=_cmd_prune_sessions)

    p_sync_roles = sub.add_parser(
        "sync-roles", help="把新声明的权限点补进已有内置角色（只加不减，可重复执行）"
    )
    p_sync_roles.set_defaults(func=_cmd_sync_roles)

    p_runs = sub.add_parser(
        "runs-status",
        help="运行工作区现状：位置、是否为跨机器共享、多少档案只在本机可见",
    )
    p_runs.set_defaults(func=_cmd_runs_status)

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
    p_export.add_argument("--out-perms", default="", help="前端权限点常量输出路径")
    p_export.add_argument(
        "--check", action="store_true", help="只比对不写入，不一致时以非 0 退出（CI 用）"
    )
    p_export.set_defaults(func=_cmd_export_openapi)

    p_import = sub.add_parser(
        "import-archive-index",
        help="把归档目录的 JSON 索引导入 assets 表（幂等，不删 JSON）",
    )
    p_import.add_argument("--root", default="", help="归档根目录，默认取 MEDIA_ARCHIVE_ROOT")
    p_import.add_argument("--dry-run", action="store_true", help="只统计将要发生的变更，不写库")
    p_import.set_defaults(func=_cmd_import_archive_index)

    p_migrate = sub.add_parser(
        "migrate-storage",
        help="把本地 storage 目录的对象按 key 搬进当前驱动（切 s3 前跑一次）",
    )
    p_migrate.add_argument("--root", default="", help="源目录，默认取 STORAGE_LOCAL_ROOT")
    p_migrate.add_argument("--dry-run", action="store_true", help="只统计文件数，不写入")
    p_migrate.add_argument(
        "--delete-source",
        action="store_true",
        help="复制成功后删除源文件（默认保留：本地目录是回滚依据）",
    )
    p_migrate.add_argument(
        "--skip-urls",
        action="store_true",
        help="不改写落库的 /static 旧地址（只在明确要保留本地驱动可读时使用）",
    )
    p_migrate.set_defaults(func=_cmd_migrate_storage)

    p_blobs = sub.add_parser(
        "migrate-blobs",
        help="归档素材改内容寻址布局（key = sha256，幂等；发布校验不再读全量）",
    )
    p_blobs.add_argument("--dry-run", action="store_true", help="只统计，不写库不搬文件")
    p_blobs.add_argument(
        "--delete-source",
        action="store_true",
        help="搬成功后删除旧位置的文件（默认保留：旧文件是回滚依据）",
    )
    p_blobs.set_defaults(func=_cmd_migrate_blobs)

    p_archive = sub.add_parser(
        "migrate-archive",
        help="把本地归档根的内容（catalog/blobs/previews/library）搬进当前归档驱动，幂等",
    )
    p_archive.add_argument("--dry-run", action="store_true", help="只统计，不写入")
    p_archive.set_defaults(func=_cmd_migrate_archive)

    p_prune_blobs = sub.add_parser(
        "prune-blobs",
        help="回收内容寻址下没人引用的孤儿字节（默认只报告，--execute 才删）",
    )
    p_prune_blobs.add_argument("--execute", action="store_true", help="实际删除孤儿字节")
    p_prune_blobs.add_argument(
        "--min-age-hours",
        type=float,
        default=24.0,
        help="只回收早于此时长的对象（默认 24 小时，避开正在进行的登记）",
    )
    p_prune_blobs.set_defaults(func=_cmd_prune_blobs)

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
