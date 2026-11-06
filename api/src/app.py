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
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from api.discovery import active_modules
from api.errors import register_exception_handlers
from api.middleware import RequestContextMiddleware
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

DESCRIPTION = """
雨范 AI 短视频工作台后端。

- 统一响应：`{code, message, data}`，`code=0` 为成功
- 字段命名：出入参均为 camelCase，与前端 TypeScript 类型一一对应
- 生成任务：提交与轮询走队列，业务侧只调用 `runner.dispatch()`
"""


def load_extension_points() -> None:
    """导入期注册类扩展点（job handler 等）。

    模块被自动发现时会顺带 import，所以业务模块的 handler 无需在此登记；
    这里只加载框架自带的 handler。
    """
    # 审计写入端：core 只定义协议，具体落库在 db 层注册
    from db import audit_sink as _audit_sink  # noqa: F401
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

    # 周期任务：调度器本身不认识任何具体任务，任务由模块在 import 期注册
    stop_scheduler = asyncio.Event()
    scheduler_task: asyncio.Task[None] | None = None
    if settings.scheduler_enabled and periodic_jobs():
        scheduler_task = asyncio.create_task(Scheduler().serve(stop_scheduler))

    if settings.anonymous_allowed:
        # 匿名回退给的是演示账号（含 `*`）。单独看合理，和「监听 0.0.0.0」叠在一起
        # 就是内网里的免登录超管接口——这种事写在文档里没人看，打进日志才有人问（R4-16）
        logger.warning(
            "匿名访问已开启：未携带令牌的请求将以演示账号执行（权限 `*`）。"
            "仅限本机开发；生产环境请设置 APP_ENV=prod 或显式 ALLOW_ANONYMOUS=false"
        )

    try:
        yield
    finally:
        stop_scheduler.set()
        if scheduler_task is not None:
            with suppress(asyncio.CancelledError):
                scheduler_task.cancel()
                await scheduler_task
        await worker.stop()
        await dispose_engine()
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
    _mount_static(app, settings.storage_public_base)

    @app.get("/", include_in_schema=False)
    async def root() -> dict[str, str]:
        return {"service": settings.app_name, "docs": "/docs", "api": settings.api_prefix}

    return app
