"""应用工厂：所有装配都在这里，`main.py` 只引用它。

这样测试可以 `create_app()` 出独立实例，不依赖全局状态。

本文件**不应该因为新增业务功能而修改**：
- 业务模块 → `api/discovery.py` 自动发现
- 权限点     → 各模块在 `ModuleSpec.permissions` 声明
- job handler → 各模块 `@register_handler` 注册
- 基础设施   → `infra/loader.py` 按配置名动态加载
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from api.discovery import active_modules
from api.errors import register_exception_handlers
from api.middleware import (
    DEFAULT_RATE_LIMIT_EXCLUDE,
    GlobalRateLimitMiddleware,
    RequestContextMiddleware,
)
from api.ops import build_ops_router
from api.v1.router import build_v1_router
from core.config import get_settings
from core.logging import get_logger, setup_logging
from core.permissions import catalog
from core.scheduler import Scheduler, periodic_jobs
from core.security import shutdown_hash_pool
from db.session import dispose_engine, init_db
from infra import get_queue
from workers import Worker, handler_names

logger = get_logger(__name__)

#: 停机时留给调度器跑完当前一轮的宽限期（秒）。任务本身可能更长，
#: 到点仍未退出就强制取消——不可逆任务宁可留日志，也不能让进程永远关不掉。
_SCHEDULER_GRACE_SEC = 30.0

if TYPE_CHECKING:
    from api.module import ModuleSpec

DESCRIPTION = """
雨范 AI 短视频工作台后端。

- 统一响应：`{code, message, data}`，`code=0` 为成功
- 字段命名：出入参均为 camelCase，与前端 TypeScript 类型一一对应
- 生成任务：提交与轮询走队列，业务侧只调用 `runner.dispatch()`
"""


async def _shutdown_step(name: str, action: Callable[[], Awaitable[None]]) -> None:
    """执行一步收尾，失败只记账不外抛。

    收尾步骤之间是**并列关系**：任何一步失败都不该让后面的步骤被跳过
    （跳过 `dispose_engine()` = 每次 `--reload` 漏一批连接池）。所以每步
    各自兜住，把异常变成一条 error 日志——停机过程没人盯着日志，但
    「端口没释放」「连接池泄漏」这类问题会被运维从日志里翻出来。
    """
    try:
        await action()
    except Exception:  # noqa: BLE001 - 收尾失败不能中断其它收尾
        logger.exception("收尾步骤失败：%s", name)


def load_extension_points() -> None:
    """导入期注册类扩展点（job handler 等）。

    模块被自动发现时会顺带 import，所以业务模块的 handler 无需在此登记；
    这里只加载框架自带的 handler。
    """
    # 审计写入端：core 只定义协议，具体落库在 db 层注册
    from db import audit_sink as _audit_sink  # noqa: F401

    # 自动备份：import 即注册 `@periodic("core.backup")`。
    # 同样不能靠 `app backup` 那条 CLI 路径——手敲命令 == 没有人跑 == 没有备份。
    from db import backup as _backup  # noqa: F401

    # outbox 投递：import 即注册 `@periodic("core.outbox")`。
    # 不能靠 `init_db()` 顺带加载——那只在非生产环境执行，生产用 Alembic 建表，
    # 结果是「事件写进去了，但永远没有投递器」这种只在生产出现的静默故障。
    from db import outbox as _outbox  # noqa: F401
    from generation import worker as _generation_worker  # noqa: F401


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    setup_logging(settings.app_env, settings.debug)
    logger.info("启动 %s（env=%s）", settings.app_name, settings.app_env)

    load_extension_points()

    # ---- 配置自检：必须在任何副作用之前（R13-03）----
    # 以前这两道断言在 lifespan 末尾：启动失败前已经跑完全部恢复（可能判死几条
    # 记录、发几条失败通知）、起完后 worker，而 `try: yield` 在那之后，
    # `finally` 根本进不去——worker 后台任务与 engine 都不会被回收。
    # 运维补上配置重启后，上一轮「失败的启动」留下的状态改写不会回滚。
    from core.audit import ensure_audit_sink
    from infra.driver_config import ensure_prod_driver_settings, precheck_submit_budgets

    # 审计写入端是唯一「没接上会静默丢数据」的注入口：record() 按设计吞掉一切，
    # 不抛异常也不打日志。生产环境必须在启动期炸掉，不能等到对账时才发现全是空的。
    ensure_audit_sink(settings.app_env)
    # 成图下载校验没配白名单时会静默回落到黑名单兜底（挡不住 DNS 解析到内网）
    ensure_prod_driver_settings(settings.app_env)
    # 阈值声明坏了会让周期巡检永久停摆（`Scheduler.run_once` 吞异常后 continue），
    # 在启动期先算一遍，坏配置立刻失败而不是每 5 分钟刷一条错（R13-06）。
    logger.info("提交预算预检：%s", precheck_submit_budgets() or "无驱动声明")

    if settings.app_env != "prod":
        await init_db()

    # 启动恢复的唯一入口：模块自带的 startup 与框架恢复都在里面执行。
    # `worker_enabled` 是动作级约束（本实例不消费队列 → 不做恢复），
    # 贴在入口上才能同时覆盖两类实现（R13-01）。
    from generation.recovery import run_startup_recovery

    modules = active_modules()
    await run_startup_recovery(modules, worker_enabled=settings.worker_enabled)

    worker = Worker(get_queue(), concurrency=settings.worker_concurrency)
    if settings.worker_enabled:
        await worker.start()
    app.state.worker = worker

    logger.info(
        "扩展点就绪：模块 %d 个 [%s] | 权限点 %d 个 | job handler %s",
        len(modules),
        ", ".join(s.name for s in modules),
        len(catalog),
        handler_names(),
    )
    # 注入口未接上不报错（可选依赖），但要在日志里留痕：`app modules` 同样能看到
    from api.ports import port_status

    for port, registered in port_status():
        if not registered:
            logger.info(
                "注入口未注册：%s（%s）→ %s",
                port.name,
                port.protocol,
                port.when_unregistered,
            )

    # 运行工作区是本机还是共享，决定了「换台机器能不能打开历史任务」。默认配置下
    # 这个事实只存在于源码注释里，部署的人看不到，于是把「档案不存在」当数据丢失（T21）
    from infra.archive.runs import workspace_needs_attention, workspace_note

    if workspace_needs_attention():
        logger.warning("运行工作区：%s", workspace_note())
    else:
        logger.info("运行工作区：%s", workspace_note())

    # 周期任务：调度器本身不认识任何具体任务，任务由模块在 import 期注册。
    # 不可逆任务（GC）标了 leader_only，租约策略按 LEADER_ELECTOR 注入。
    from core.coordination import get_leader_elector

    stop_scheduler = asyncio.Event()
    scheduler_task: asyncio.Task[None] | None = None
    if settings.scheduler_enabled and periodic_jobs():
        scheduler_task = asyncio.create_task(
            Scheduler(elector=get_leader_elector()).serve(stop_scheduler)
        )

    if settings.anonymous_allowed:
        # 匿名回退给的是演示账号（含 `*`）。单独看合理，和「监听 0.0.0.0」叠在一起
        # 就是内网里的免登录超管接口——这种事写在文档里没人看，打进日志才有人问（R4-16）
        logger.warning(
            "匿名访问已开启：未携带令牌的请求将以演示账号执行（权限 `*`）。"
            "仅限本机开发；生产环境请设置 APP_ENV=prod 或显式 ALLOW_ANONYMOUS=false"
        )

    if settings.metrics_enabled and settings.app_env == "prod" and not settings.metrics_token:
        # 不拦启动：监控没配好不该让服务起不来。但必须留痕——否则表现是
        # 「面板全空」，而没人会想到去看 /metrics 返回的 403
        logger.warning(
            "METRICS_ENABLED 已开但未设置 METRICS_TOKEN：/metrics 将返回 403，抓不到任何指标"
        )

    try:
        yield
    finally:
        # 停机必须**按顺序全部走完**：调度器 → 队列 worker → 出站连接 → 数据库
        # 引擎 → 线程池。任何一步抛异常都不能让后面的步骤被跳过——
        # 跳过 `dispose_engine()` 会留下未关闭的连接池（`--reload` 每轮漏一批，
        # 端口占用），跳过 `worker.stop()` 会让队列里的任务停在半途。
        #
        # 之前这里是「一串 await」：调度器那一步抛出非 TimeoutError 的异常
        # （比如任务内部抛了 RuntimeError）时，`finally` 直接中断，
        # 后面四个收尾动作**一个都不会执行**。现在每一段各自兜住并记账，
        # 收尾动作一个都不少。
        stop_scheduler.set()
        if scheduler_task is not None:
            # **先给宽限期，再强杀。** `serve()` 只在两轮之间检查 stop，
            # 正在跑的那一轮是在 `asyncio.wait_for(job.handler())` 里等着的。
            # 立刻 cancel 会把不可逆任务（回收站 GC / 备份）掐在半途，而
            # `finally` 又会马上归还集群租约——别的实例随后接管同一件事，
            # 留下「行删了、字节还在」这类需要人肉对账的现场。
            try:
                await asyncio.wait_for(scheduler_task, timeout=_SCHEDULER_GRACE_SEC)
            except TimeoutError:
                logger.warning(
                    "调度器 %.0f 秒内未退出（正在跑的任务可能仍在执行），已强制取消",
                    _SCHEDULER_GRACE_SEC,
                )
            except asyncio.CancelledError:
                pass
            except Exception:  # noqa: BLE001 - 调度器自身崩了不能拖累收尾
                logger.exception("调度器退出时异常，继续收尾")

        # 出站连接池：不显式关闭的话，`--reload` 每轮都会把一批 keep-alive 连接
        # 留给内核，几分钟后开始出现 TIME_WAIT 堆积与「连接被重置」
        from infra.http import close_http

        await _shutdown_step("队列 worker", worker.stop)
        await _shutdown_step("出站连接池", close_http)
        await _shutdown_step("数据库引擎", dispose_engine)
        # 口令线程池不随进程退出立刻回收（靠 atexit join），`--reload` 时每轮都留一组线程
        shutdown_hash_pool()
        logger.info("已关闭")


def _mount_static(app: FastAPI, public_base: str) -> None:
    """本地存储驱动下，把对象目录挂到 HTTP 上供预览。"""
    settings = get_settings()
    if settings.storage_driver != "local":
        return
    root = Path(settings.storage_local_root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    app.mount(public_base, StaticFiles(directory=root), name="storage")


def create_app() -> FastAPI:
    settings = get_settings()
    load_extension_points()

    app = FastAPI(
        title=settings.app_name,
        version="0.2.0",
        description=DESCRIPTION,
        docs_url="/docs" if settings.app_env != "prod" else None,
        redoc_url="/redoc" if settings.app_env != "prod" else None,
        openapi_url="/openapi.json" if settings.app_env != "prod" else None,
        lifespan=lifespan,
    )

    # 顺序：后添加的在外层。CORS 必须在最外层，异常响应才能带上跨域头。
    # 全局限流放在 RequestContext **内侧**：429 也要有 request id 和访问日志，
    # 否则「谁被限了」只能靠猜。默认关闭（额度 0 时整层透传，零开销）。
    app.add_middleware(
        GlobalRateLimitMiddleware,
        limit_per_min=settings.rate_limit_global_per_min,
        exclude=DEFAULT_RATE_LIMIT_EXCLUDE + (settings.api_prefix + "/health",),
    )
    app.add_middleware(RequestContextMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=settings.cors_allow_credentials,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Request-Id", "X-Process-Time-Ms"],
    )

    register_exception_handlers(app)
    app.include_router(build_v1_router(), prefix=settings.api_prefix)
    # 运维端点挂根路径、不进 OpenAPI：抓取方是 Prometheus，不是登录用户，
    # 凭据是独立的 METRICS_TOKEN（见 api/ops.py）
    app.include_router(build_ops_router())
    _mount_static(app, settings.storage_public_base)

    @app.get("/", include_in_schema=False)
    async def root() -> dict[str, str]:
        return {"service": settings.app_name, "docs": "/docs", "api": settings.api_prefix}

    # 放在 include_router 之后：模块导入时才会登记 scope，早于这里就是空集
    _check_rate_limit_overrides()
    # 同理必须在 include_router 之后：模块导入才会注册 handler / 周期任务。
    # 声明了却没注册 = 队列会被永远堵住，所以这一项直接拒绝启动。
    # 权限码拼错只有普通用户会遇到（超管有 `*`），一并在这里拦。
    _check_assembly()
    return app


def collect_assembly() -> tuple[
    tuple[ModuleSpec, ...], dict[str, str], dict[str, str]
]:
    """运行时装配快照：模块声明 + 实际注册的 handler / 周期任务。

    返回 `(specs, {handler 名: 所在模块}, {周期任务名: 所在模块})`。
    `app doctor` 与 `app modules` 用同一份数据做对账，避免「三处各实现一遍、
    结果各不相同」——CLI 曾经因为少调一次 `load_extension_points()`，
    打印出的 handler 清单比线上少两个，而命令本身看起来完全正常。
    """
    from api.discovery import discover_modules
    from workers import HANDLERS

    load_extension_points()  # 幂等：框架自带的 handler 也要在快照里
    specs = tuple(discover_modules())
    jobs = {name: fn.__module__ for name, fn in HANDLERS.items()}
    periodic = {job.name: job.handler.__module__ for job in periodic_jobs()}
    return specs, jobs, periodic


def _check_assembly() -> None:
    """启动自检：模块声明了 job / 周期任务，就必须真的注册。

    为什么不等第一个任务：漏了副作用 import 的模块，表现是「任务永远 queued」，
    而队列里堆着的那些任务没有任何一处日志会说「handler 不存在」。
    参考 `api/assembly.py` 的模块文档。
    """
    from api.assembly import assert_assembly, permission_gaps_for_modules
    from core.exceptions import RegistryError

    specs, jobs, periodic = collect_assembly()
    assert_assembly(specs, jobs=jobs, periodic=periodic)

    # 权限码：拼错一个字母时，超级管理员（`*`）一切正常，别人永远 403。
    # 遍历**模块 router** 而不是 `build_v1_router()` 的产物——后者在当前
    # FastAPI 版本里是 `_IncludedRouter` 占位（延迟到建 OpenAPI 时才展开），
    # 里面的 `.routes` 数等于模块数而不是接口数，照着遍历会得到「零个问题」。
    gaps = permission_gaps_for_modules(specs, set(catalog.codes()))
    if gaps:
        raise RegistryError(
            "路由用了没有任何模块声明的权限码"
            "（拼错时表现为「超管能用、别人永远 403」）：\n  " + "\n  ".join(gaps)
        )


def _check_rate_limit_overrides() -> None:
    """启动自检：额度覆盖配置写错就起不来。

    为什么不等第一个请求：`resolve_budget()` 是在 async 依赖里调的，那里抛异常
    会变成一次 500，而且只影响**被限流的那些接口**——表现是「有的接口 500、
    有的正常」，比直接启动失败难查十倍。

    未知 scope 只记 error 不阻断：`create_app()` 的导入顺序决定了这里未必已经
    见过所有模块，误判成「配置写错了」不该让服务起不来。真正的对账在
    `tests/test_ratelimit.py::test_every_override_names_a_real_scope`。
    """
    from core.ratelimit import declared_scopes, parsed_overrides, unknown_overrides

    overrides = parsed_overrides()  # 非法 JSON / 类型错 → 直接抛
    if not overrides:
        return
    unknown = unknown_overrides(declared_scopes())
    if unknown:
        logger.error(
            "RATE_LIMIT_OVERRIDES 里有代码中不存在的 scope：%s（已按原样忽略）",
            ", ".join(unknown),
        )
