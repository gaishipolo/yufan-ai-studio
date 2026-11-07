"""指标采集：把「进程里发生了什么」变成可抓取的数值。

分两类，区别很重要：

- **累积型**（counter / histogram）：在事件发生的那一刻打点，本文件只提供
  `observe_request()` 这类薄封装，具体调用点在 `api/middleware.py` 与 `workers.py`。
- **瞬时型**（gauge）：抓取时才去问一次真实来源（队列表、outbox 表），
  由 `refresh_runtime_gauges()` 负责。这样即使进程刚启动、还没跑过任何任务，
  抓到的积压量也是真的，而不是「上次打点时的旧值」。

## 标签基数

`route` 一律用**路由模板**（`/api/v1/tasks/{task_id}`），不是真实 URL。
Starlette 匹配成功后会把路由对象放回 `scope["route"]`，未匹配（404）记 `unmatched`。
把真实 URL 当标签，等于按用户输入切时间序列——这是 Prometheus 最常见的翻车方式。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from core.config import settings
from core.logging import get_logger
from core.metrics import HTTP_BUCKETS, METRICS

logger = get_logger(__name__)

REQUESTS = METRICS.counter(
    "http_requests_total",
    "HTTP 请求数",
    ("method", "route", "status"),
)
REQUEST_DURATION = METRICS.histogram(
    "http_request_duration_seconds",
    "HTTP 请求耗时（秒）",
    ("method", "route"),
    buckets=HTTP_BUCKETS,
)
IN_FLIGHT = METRICS.gauge("http_requests_in_flight", "正在处理的 HTTP 请求数")

QUEUE_JOBS = METRICS.gauge(
    "queue_jobs",
    "队列中的任务数（db 驱动按状态拆分，其它驱动只有 active）",
    ("state",),
)
OUTBOX_EVENTS = METRICS.gauge(
    "outbox_events",
    "outbox 事件数（pending 表示尚未投递，failed 表示重试用尽）",
    ("status",),
)
BUILD_INFO = METRICS.gauge(
    "app_build_info",
    "构建与驱动信息，恒为 1；用它确认「抓的是哪个进程、跑的什么驱动」",
    ("env", "queue_driver", "cache_driver", "storage_driver", "archive_driver"),
)


def route_template(scope: Mapping[str, Any]) -> str:
    """取路由模板。未匹配的请求统一记 `unmatched`，避免路径基数失控。

    为什么不直接用 `scope["route"].path`：Starlette 存的是**该路由器内部**的路径，
    `include_router(prefix=...)` 加上的前缀不在里面（实测 `/api/v1/health` 会记成
    `/health`）。前缀丢了以后，不同挂载点的同名路由会在面板上合并。
    所以拿真实路径，把**参数位**换成模板里的名字。

    为什么不是 `actual.replace(value, "{name}")`：那是子串替换，会顺手把路径里
    任何相同的片段一起改掉。`/api/v1/tasks/1` + `task_id="1"` 会变成
    `/api/v{task_id}/tasks/{task_id}`——标签变成一句无意义的话，而且两条本来
    不同的路由可能因此合并成同一条时间序列。所以按 `/` 切段、**从右对齐**
    （模板缺的只是前缀），只替换那个位置上的整段。

    结构对不上时（`{param:path}` 这类会把值里的 `/` 吃进参数、或模板段数比真实
    路径还多）不猜：直接返回路由自己的模板。宁可少一个前缀，也不产出一个错的标签。
    """
    route = scope.get("route")
    path = getattr(route, "path", None)
    actual = scope.get("path")
    if not isinstance(path, str) or not path:
        return "unmatched"
    if not isinstance(actual, str) or not actual:
        return path
    params = scope.get("path_params") or {}

    template_segments = path.split("/")
    actual_segments = actual.split("/")
    placeholders = [
        segment[1:-1]
        for segment in template_segments
        if len(segment) > 2 and segment.startswith("{") and segment.endswith("}")
    ]
    if not placeholders:
        # 纯静态路由：真实路径就是模板 + 前缀
        return actual
    if set(placeholders) != set(params):
        # 带转换器（`{p:path}`）或多段参数：段数对不齐，不猜
        return path
    offset = len(actual_segments) - len(template_segments)
    if offset < 0:
        return path
    for index, segment in enumerate(template_segments):
        if segment[1:-1] in placeholders:
            actual_segments[offset + index] = segment
    return "/".join(actual_segments)


def observe_request(*, method: str, route: str, status: int, elapsed: float) -> None:
    REQUESTS.inc(method=method, route=route, status=str(status))
    REQUEST_DURATION.observe(elapsed, method=method, route=route)


async def refresh_runtime_gauges() -> None:
    """抓取前刷新瞬时值。任何一步失败都只记 debug：指标端点不能因为依赖抖动而 500。"""
    BUILD_INFO.set(
        1,
        env=settings.app_env,
        queue_driver=settings.queue_driver,
        cache_driver=settings.cache_driver,
        storage_driver=settings.storage_driver,
        archive_driver=settings.archive_driver,
    )
    await _refresh_queue()
    await _refresh_outbox()


async def _refresh_queue() -> None:
    from infra import get_queue

    try:
        queue = get_queue()
        stats = getattr(queue, "stats", None)
        if callable(stats):
            for state, count in (await stats()).items():
                QUEUE_JOBS.set(count, state=str(state))
        else:
            QUEUE_JOBS.set(await queue.size(), state="active")
    except Exception as exc:  # noqa: BLE001 - 指标端点不承担报错职责
        logger.debug("队列指标采集失败：%s", exc)


async def _refresh_outbox() -> None:
    from sqlalchemy import func, select

    from db.outbox import OUTBOX_DELIVERED, OUTBOX_FAILED, OUTBOX_PENDING, OutboxEvent
    from db.session import get_session_factory

    try:
        async with get_session_factory()() as session:
            rows = await session.execute(
                select(OutboxEvent.status, func.count()).group_by(OutboxEvent.status)
            )
            counts = {str(status): int(count) for status, count in rows.all()}
    except Exception as exc:  # noqa: BLE001 - 表还没建（首启）也算正常
        logger.debug("outbox 指标采集失败：%s", exc)
        return
    # 三种状态都写，缺省补 0：否则面板会在「第一次失败」之前一直缺这条线
    for status in (OUTBOX_PENDING, OUTBOX_DELIVERED, OUTBOX_FAILED):
        OUTBOX_EVENTS.set(counts.get(status, 0), status=status)


__all__ = [
    "BUILD_INFO",
    "IN_FLIGHT",
    "OUTBOX_EVENTS",
    "QUEUE_JOBS",
    "REQUESTS",
    "REQUEST_DURATION",
    "observe_request",
    "refresh_runtime_gauges",
    "route_template",
]
