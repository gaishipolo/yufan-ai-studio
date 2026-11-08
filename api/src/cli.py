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
app verify-blobs [--all]           # 读回字节重算 sha256，报告损坏/缺失对象
app rebuild-mirrors [--dry-run]    # 按 DB 幂等重建 JSON 镜像，清孤儿（DB 是唯一真相源）
app doctor                         # 部署自检：条件写、预签名、镜像一致性、完整性抽检
app jobs [--state dead] [--json]   # 看队列：在途/死信；--redrive <id> 重投、--drop <id> 删除
app runs-status                    # 运行工作区现状：多少档案只在本机可见
app backup [--out var/backups]     # 备份数据库（sqlite 快照 / postgres pg_dump）+ 清单
app restore --from-dir <dir> --force # 用备份覆盖当前库（先另存现场）
app new-module voice --label 配音   # 生成新模块脚手架
app meta [key]                     # 查看 app_meta 的运行期状态及其写入轨迹
```

下面这些命令**不在这个文件里**，由业务模块通过 `ModuleSpec.cli` 自己挂上来：

    create-admin / prune-logins / prune-sessions / sync-roles      ← modules/identity
    import-archive-index / migrate-blobs / prune-blobs /
    rebuild-mirrors                                               ← modules/asset

判据是「这条命令认不认识业务表」：`migrate-storage` 扫 schema 里所有 `*_url` 列、
`verify-blobs` 只读归档盘字节，都不认识业务模型，所以留在框架。
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # 只用于注解：命令行入口保持「用到才 import」，别把启动链拖长
    from infra.storage.base import ObjectStorage


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


def _cmd_meta(args: argparse.Namespace) -> None:
    """看 `app_meta` 里的状态：不传键列全部，传键看它怎么被改过来的。

    这张表存的是「上一次运行时系统认定的事实」（权限目录快照、功能开关…）。
    它过去只有最后写入者胜、没有痕迹，出问题只能翻日志猜；有了 `version` /
    `updated_by` / `app_meta_history`，这里就是排查入口。
    """
    from sqlalchemy import select

    from db.app_meta import AppMeta, history, read_entry
    from db.session import get_session_factory

    async def run() -> tuple[list[Any], Any, list[Any]]:
        async with get_session_factory()() as session:
            if not args.key:
                rows = list(
                    (await session.scalars(select(AppMeta).order_by(AppMeta.key))).all()
                )
                return rows, None, []
            entry = await read_entry(session, args.key)
            rows_ = await history(session, args.key, limit=args.limit)
            return [], entry, list(rows_)

    rows, entry, trail = asyncio.run(run())
    if args.key:
        if entry is None:
            print(f"{args.key}: 不存在")
            return
        print(f"{args.key}  v{entry.version}  最后写入 {entry.updated_by or '-'}")
        print(f"  {entry.value}")
        if trail:
            print("最近写入：")
            for item in trail:
                stamp = item.created_at.strftime("%Y-%m-%d %H:%M:%S")
                print(f"  {stamp}  v{item.version}  {item.actor or '-'}  {item.detail}")
        return
    if not rows:
        print("app_meta 是空的（还没有任何需要记住的运行期事实）")
        return
    for row in rows:
        updated = row.updated_at.strftime("%Y-%m-%d %H:%M")
        preview = row.value if len(row.value) <= 60 else row.value[:57] + "…"
        print(f"{row.key}  v{row.version}  {updated}  {row.updated_by or '-'}  {preview}")


def _cmd_modules(_: argparse.Namespace) -> None:
    from api.assembly import assembly_gaps, permission_gaps_for_modules
    from app import collect_assembly
    from core.permissions import catalog
    from core.scheduler import periodic_jobs
    from generation import pipeline_kinds
    from workers import handler_names

    # 和 create_app() 共用同一份装配快照。CLI 曾经自己装配一遍，代价是
    # 「漏了副作用 import 的模块」在这里根本不出现，而命令看起来一切正常——
    # 于是这个「检查装配的命令」本身成了假阳性来源。
    specs, jobs, periodic = collect_assembly()
    gaps = assembly_gaps(specs, jobs=jobs, periodic=periodic)
    bad_permissions = permission_gaps_for_modules(specs, set(catalog.codes()))  # type: ignore[arg-type]

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

    # 先把清单打完整再判失败：这个命令的用途是「确认被识别」，
    # 装配合不上时最需要看到的恰恰是「实际有什么」，而不是一行报错。
    missing = [gap for gap in gaps if gap.fatal]
    if missing or bad_permissions:
        print(file=sys.stderr)
        if missing:
            print("装配不完整（声明了但没注册）：", file=sys.stderr)
            for gap in missing:
                print(f"  - {gap}", file=sys.stderr)
        if bad_permissions:
            print("权限码没有模块声明（超管能用、别人永远 403）：", file=sys.stderr)
            for code in bad_permissions:
                print(f"  - {code}", file=sys.stderr)
        raise SystemExit(1)


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

    以前各模块把 `url_for()` 的产物（`/static/images/xxx.png`）直接落库，现在只存
    key、地址在出参时按当前驱动现签。老值留着就等于把「生成那一刻的驱动」固化进
    数据：换一次驱动失联一次。

    **扫的是整张 schema，不是某张表**：这曾经是 `image_jobs.output_url` /
    `cover_url` 的专用修复，于是 CLI 为了它 `from modules.image.models import
    ImageJob`——删掉生图模块，`app migrate-storage` 就崩，而且下一个犯同样毛病的
    模块还得再来改一遍。判断标准很直白：**「库里存了本服务的绝对地址」是个
    跨模块的数据纪律，不是某个模块的私事**，所以按列名（`*_url`）扫全部表。

    只剥「本服务拼得出来的」前缀（当前驱动的 `url_for("")` 与静态前缀），
    供应商直链、`data:`、协议相对地址原样保留 —— 那些本来就不是我们的对象。
    """
    from sqlalchemy import select, update

    from core.config import settings
    from db.base import Base
    from db.session import get_session_factory

    prefixes = tuple(
        {
            settings.storage_public_base.rstrip("/") + "/",
            storage.url_for(""),
        }
    )
    rewritten = 0
    scanned = 0

    async with get_session_factory()() as session:
        for table in Base.metadata.sorted_tables:
            columns = [c for c in table.columns if c.name.endswith("_url")]
            if not columns:
                continue
            rows = (await session.execute(select(table))).mappings().all()
            scanned += len(rows)
            for row in rows:
                changes = {}
                for column in columns:
                    key = _strip_prefix(row[column.name], prefixes)
                    if key is not None:
                        changes[column.name] = key
                if not changes:
                    continue
                await session.execute(
                    update(table).where(table.c.id == row["id"]).values(**changes)
                )
                rewritten += len(changes)
        if rewritten:
            await session.commit()

    return rewritten, scanned


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


def _cmd_audit_events(args: argparse.Namespace) -> None:
    """查看最近的操作留痕：分享、撤回、回收站、彻底删除、封装发布。"""
    asyncio.run(_show_audit_events(args.action, args.limit, args.target))


async def _show_audit_events(action: str, limit: int, target: str) -> None:
    from sqlalchemy import desc, select

    from db.audit import AuditEvent
    from db.session import get_session_factory

    stmt = select(AuditEvent)
    if action:
        stmt = stmt.where(AuditEvent.action == action)
    if target:
        stmt = stmt.where(AuditEvent.target_id == target)
    stmt = stmt.order_by(desc(AuditEvent.created_at)).limit(limit)
    async with get_session_factory()() as session:
        rows = (await session.execute(stmt)).scalars().all()
    if not rows:
        print("没有匹配的留痕记录")
        return
    for row in rows:
        detail = row.detail[:120] + ("…" if len(row.detail) > 120 else "")
        print(
            f"{row.created_at:%Y-%m-%d %H:%M:%S}  {row.action:28} "
            f"actor={row.actor_id or '-':8} target={row.target_id}  {detail}"
        )


def _cmd_jobs(args: argparse.Namespace) -> None:
    """队列的可读入口：看、按 id 重投、按 id 删除。

    `db` 驱动把任务落进 `queued_jobs`，死信「留档给人看」——但在此之前没有地方能看：
    `app doctor` 只给一个数字，要看内容得自己写 SQL。而写 SQL 去 `DELETE` 正是这条
    命令要替掉的事：删错一行，表现是「某个任务凭空消失」，没有任何报错。

    只做三件事：看、重投一条、删除一条。**不做批量重投**：一次把几十条死信推回队列
    等于同时给供应商发几十个请求，那应该是一个人逐条确认的决定，不该是一条命令的默认。
    """
    asyncio.run(_jobs(args))


async def _jobs(args: argparse.Namespace) -> None:
    import json as jsonlib

    from sqlalchemy import desc, select

    from core.config import settings
    from db.jobs import QueuedJob
    from db.session import get_session_factory

    if settings.queue_driver != "db":
        print(
            f"QUEUE_DRIVER={settings.queue_driver}：只有 db 驱动把任务落库，这里查不到东西。\n"
            "内存驱动的「积压」只存在于进程内存里（重启即丢）——这也正是生产要用 db 的理由。"
        )
        raise SystemExit(2)

    async with get_session_factory()() as session:
        if args.drop:
            await _drop_job(session, args.drop, force=args.force)
            return
        if args.redrive:
            await _redrive_job(session, args.redrive)
            return

        limit = max(1, min(args.limit, 500))
        stmt = select(QueuedJob).order_by(desc(QueuedJob.created_at)).limit(limit)
        if args.state:
            stmt = stmt.where(QueuedJob.status == args.state)
        rows = (await session.execute(stmt)).scalars().all()

        if args.json:
            print(
                jsonlib.dumps(
                    [_job_as_dict(row) for row in rows],
                    ensure_ascii=False,
                    indent=2,
                    default=str,
                )
            )
            return
        if not rows:
            print("队列里没有匹配的记录")
            return
        for row in rows:
            lease = f"{row.lease_expires_at:%m-%d %H:%M:%S}" if row.lease_expires_at else "-"
            print(
                f"{row.status:8} {row.id}  {row.name:24} "
                f"attempts={row.attempts}/{row.max_attempts} "
                f"run_at={row.run_at:%m-%d %H:%M:%S} lease={lease}"
            )
            if row.last_error:
                print(f"         └ {row.last_error[:140]}")
        print(f"\n共 {len(rows)} 条（最多显示 {limit}）")
        print(
            "死信是留档用的：确认无害要重跑用 `app jobs --redrive <id>`，"
            "确认不要了用 `app jobs --drop <id> --force`。"
        )


def _job_as_dict(row: Any) -> dict[str, Any]:
    return {
        "id": row.id,
        "name": row.name,
        "status": row.status,
        "attempts": row.attempts,
        "max_attempts": row.max_attempts,
        "run_at": row.run_at,
        "lease_expires_at": row.lease_expires_at,
        "created_at": row.created_at,
        "last_error": row.last_error,
    }


async def _redrive_job(session: Any, job_id: str) -> None:
    """把一条死信重新排队。

    只允许死信：在途任务的重投等于让它被执行两遍，那是把人手伸进了 worker 的状态机。
    """
    from sqlalchemy import update

    from db.base import utcnow
    from db.jobs import JOB_DEAD, JOB_PENDING, QueuedJob

    row = await session.get(QueuedJob, job_id)
    if row is None:
        print(f"没有这个任务：{job_id}")
        raise SystemExit(1)
    if row.status != JOB_DEAD:
        print(
            f"{job_id} 现在是 {row.status}，不需要重投。"
            "在途任务由 worker 自己推进；重投会让同一件事被执行两遍。"
        )
        raise SystemExit(1)

    now = utcnow()
    await session.execute(
        update(QueuedJob)
        .where(QueuedJob.id == job_id)
        .values(
            status=JOB_PENDING,
            run_at=now,
            attempts=0,
            lease_expires_at=None,
            lease_token=None,
            last_error="",
            updated_at=now,
        )
    )
    await session.commit()
    print(f"已重投 {job_id}（{row.name}），attempts 归零")
    print(
        "注意：它成为死信之前可能已经产生了副作用（供应商扣费、文件已写），"
        "handler 必须自己幂等，队列不替你去重。"
    )


async def _drop_job(session: Any, job_id: str, *, force: bool) -> None:
    """删除一条队列记录。

    `running` 一律拒绝：那可能是别的实例正在执行的任务，删掉它等于让一个活着的 worker
    白跑，而它自己不会知道（回执时会发现凭据对不上，但那时活已经干完了）。
    """
    from sqlalchemy import delete

    from db.jobs import JOB_RUNNING, QueuedJob

    if not force:
        print("删除不可恢复：确认这条记录不再需要之后，加 --force 再跑一次。")
        raise SystemExit(1)
    row = await session.get(QueuedJob, job_id)
    if row is None:
        print(f"没有这个任务：{job_id}")
        raise SystemExit(1)
    if row.status == JOB_RUNNING:
        print(
            f"{job_id} 正在被某个 worker 执行（running），拒绝删除。\n"
            "崩溃留下的 running 行会在租约过期后被接手；要立刻处理请先停掉 worker。"
        )
        raise SystemExit(1)
    await session.execute(delete(QueuedJob).where(QueuedJob.id == job_id))
    await session.commit()
    print(f"已删除 {job_id}（{row.name}，状态 {row.status}）")


def _cmd_verify_blobs(args: argparse.Namespace) -> None:
    """读回字节重算摘要：key 既是命名也是校验和，不需要另存元数据。"""
    from infra.archive import get_archive_storage
    from infra.archive.integrity import verify_blobs

    report = verify_blobs(get_archive_storage(), sample=None if args.all else args.sample)
    print(report.summary())
    for key in report.missing:
        print(f"  [缺失] {key}")
    for key in report.corrupted:
        print(f"  [摘要不符] {key}")
    if not report.healthy and not args.lenient:
        raise SystemExit(1)


def _cmd_doctor(args: argparse.Namespace) -> None:
    from api.diagnostics import run_doctor

    report = asyncio.run(run_doctor(sample=args.sample))
    print(report.render())
    if report.failures:
        raise SystemExit(1)
    if args.strict and report.warnings:
        raise SystemExit(1)


def _cmd_backup(args: argparse.Namespace) -> None:
    from db.backup import BackupError, create_backup

    out_dir = Path(args.out).resolve()
    try:
        target, manifest = asyncio.run(create_backup(out_dir, note=args.note))
    except BackupError as exc:
        print(f"备份失败：{exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    print(f"备份目录：{target}")
    print(f"  {manifest.summary()}")
    print("提示：对象存储里的字节不在这份备份里，请确认归档盘自身的版本化/复制策略。")


def _cmd_restore(args: argparse.Namespace) -> None:
    from db.backup import BackupError, restore_backup

    try:
        manifest = restore_backup(Path(args.from_dir).resolve(), force=args.force)
    except BackupError as exc:
        print(f"恢复失败：{exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    print(f"已恢复：{manifest.created_at}（schema={manifest.schema_head or '未知'}）")
    print("请重启服务后再验证：连接池里可能还有指向旧文件的句柄。")


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
        capability=args.capability,
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
    print(
        f"  2. 执行 alembic revision --autogenerate -m '{plan.name} init'，"
        "然后 alembic upgrade head"
    )
    print("     （漏了这步，测试环境照样绿，生产第一条查询就 no such column）")
    print("  3. 重启服务，`app modules` 应能看到该模块")
    print("  4. 权限点会自动并入内置角色（启动时补「新增」的），不需要再手工跑 sync-roles")
    print("     例外：老库第一次升到这一版会做一次全量补齐，见 docs/OPS.md「升级」")


def _register_module_commands(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """让每个模块把自己的运维命令挂上来（`ModuleSpec.cli`）。

    两条刻意的设计：

    1. **单个模块 import 失败不拖垮整个入口**。`app backup` / `app restore` 正是
       「出事时才跑」的命令，而那时候坏掉的可能就是某个模块。所以逐模块兜异常、
       打一行 stderr 后继续——丢几条运维命令，好过整个 CLI 起不来。
    2. **撞名要红**。模块想注册一个已经存在的命令名（比如 `backup`），框架命令就被
       业务模块顶掉了，只有用到的人才会发现。这里按解析器对象身份比对，一旦被替换
       立刻报错并**把原命令还原**（`RegistryError` 由上层兜住）。
       注意这个兜底是跨版本的：Python 3.13 的 argparse 自己抛
       `conflicting subparser`，而 3.11/3.12（`pyproject` 的 target）是**静默覆盖**；
       只写「信任 argparse」等于在 3.11 上留一个静默的洞。

    这个函数顺带也**保证了「命令执行时模块已发现」**：它跑在 `parse_args` 之前，
    所以任何模块命令执行时 `core.permissions.catalog` 与各注入口都已经填好了。
    没有这条保证的话，每个「要读目录 / 要引用检查器」的命令都得自己再调一次
    `discover_modules()`——漏一次就是静默拿到空目录（`sync-roles --prune-snapshot`
    会把整张权限快照抹掉）。代价是每条命令都要 import 一遍模块，实测约 0.8s。
    """
    from api.discovery import active_modules
    from core.exceptions import RegistryError

    try:
        specs = active_modules()
    except Exception as exc:  # noqa: BLE001 - 见函数说明第 1 条
        print(f"模块发现失败（{exc}），业务模块的运维命令不可用", file=sys.stderr)
        return

    for spec in specs:
        if spec.cli is None:
            continue
        snapshot = dict(sub.choices)
        try:
            spec.cli(sub)
            for name, parser_obj in snapshot.items():
                if sub.choices.get(name) is not parser_obj:
                    # **把被顶掉的命令还回去再报错**：只检查不修复的话，框架命令
                    # （`backup` / `restore` 这类救命命令）在那之后就已经是坏的，
                    # 而报错只是打印一行——用户手里的 CLI 已经不能用了。
                    sub.choices[name] = parser_obj
                    raise RegistryError(
                        f"模块 {spec.name} 用命令名 {name!r} 覆盖了已有的命令"
                    )
        except Exception as exc:  # noqa: BLE001 - 见函数说明第 1 条
            print(
                f"模块 {spec.name} 的命令行注册失败（{exc}），它的运维命令将不可用",
                file=sys.stderr,
            )


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


    p_meta = sub.add_parser(
        "meta",
        help="查看 app_meta 里的运行期状态（不传 key 列全部；传 key 看写入轨迹）",
    )
    p_meta.add_argument("key", nargs="?", default="", help="例如 permissions.catalog")
    p_meta.add_argument("--limit", type=int, default=20, help="轨迹条数，默认 20")
    p_meta.set_defaults(func=_cmd_meta)

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


    p_archive = sub.add_parser(
        "migrate-archive",
        help="把本地归档根的内容（catalog/blobs/previews/library）搬进当前归档驱动，幂等",
    )
    p_archive.add_argument("--dry-run", action="store_true", help="只统计，不写入")
    p_archive.set_defaults(func=_cmd_migrate_archive)


    p_verify = sub.add_parser(
        "verify-blobs", help="读回字节重算 sha256，报告损坏或缺失的归档对象"
    )
    p_verify.add_argument("--all", action="store_true", help="全量校验（默认只抽样）")
    p_verify.add_argument("--sample", type=int, default=20, help="抽样个数（默认 20）")
    p_verify.add_argument("--lenient", action="store_true", help="发现损坏也不返回非零退出码")
    p_verify.set_defaults(func=_cmd_verify_blobs)


    p_doctor = sub.add_parser("doctor", help="部署自检：条件写、预签名、镜像一致性、完整性抽检")
    p_doctor.add_argument("--sample", type=int, default=10, help="完整性抽检个数（默认 10）")
    p_doctor.add_argument("--strict", action="store_true", help="有 WARN 也返回非零退出码")
    p_doctor.set_defaults(func=_cmd_doctor)

    p_audit = sub.add_parser("audit-events", help="查看最近的操作留痕（状态变更与发布）")
    p_audit.add_argument("--action", default="", help="按动作过滤，如 asset.archive.share")
    p_audit.add_argument("--target", default="", help="按对象 id 过滤")
    p_audit.add_argument("--limit", type=int, default=30, help="条数（默认 30）")
    p_audit.set_defaults(func=_cmd_audit_events)

    p_jobs = sub.add_parser(
        "jobs", help="查看队列（在途/死信），按 id 重投或删除死信"
    )
    p_jobs.add_argument(
        "--state", choices=("pending", "running", "dead"), default=None, help="只看某个状态"
    )
    p_jobs.add_argument("--limit", type=int, default=20, help="条数（默认 20）")
    p_jobs.add_argument("--json", action="store_true", help="输出 JSON（给脚本用）")
    p_jobs.add_argument("--redrive", metavar="ID", help="把一条死信重新排队（attempts 归零）")
    p_jobs.add_argument("--drop", metavar="ID", help="删除一条记录（需 --force）")
    p_jobs.add_argument("--force", action="store_true", help="确认执行 --drop")
    p_jobs.set_defaults(func=_cmd_jobs)

    p_backup = sub.add_parser(
        "backup", help="备份数据库（sqlite 一致快照 / postgres pg_dump）并写清单"
    )
    p_backup.add_argument("--out", default="var/backups", help="备份根目录（默认 var/backups）")
    p_backup.add_argument("--note", default="", help="备注，写进 manifest.json")
    p_backup.set_defaults(func=_cmd_backup)

    p_restore = sub.add_parser("restore", help="用备份覆盖当前库（会先把现场另存）")
    p_restore.add_argument("--from-dir", required=True, help="备份目录（含 manifest.json）")
    p_restore.add_argument(
        "--force", action="store_true", help="覆盖已有数据必须显式确认（postgres 下同理）"
    )
    p_restore.set_defaults(func=_cmd_restore)

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
    p_new.add_argument(
        "--capability",
        default="",
        choices=["", "image", "video", "audio"],
        help="生成能力，--generation 时必填：决定请求交给哪一类供应商",
    )
    p_new.add_argument(
        "--root",
        default=".",
        help="项目根（必须含 src/modules；在 api/ 下执行，或显式传 --root api）",
    )
    p_new.set_defaults(func=_cmd_new_module)

    _register_module_commands(sub)

    args = parser.parse_args(argv)
    args.func(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
