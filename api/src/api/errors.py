"""全局异常处理器：把任何异常收敛成 `{code, message, data}`。

路由与 service 层因此可以放心抛异常，不必写 try/except。
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.exc import TimeoutError as PoolTimeoutError
from sqlalchemy.orm.exc import StaleDataError
from starlette.exceptions import HTTPException as StarletteHTTPException

from core.exceptions import AppError, ErrorCode
from core.logging import get_logger

logger = get_logger(__name__)


def _payload(code: int, message: str, details: Any = None) -> dict[str, Any]:
    body: dict[str, Any] = {"code": code, "message": message, "data": None}
    if details is not None:
        body["details"] = details
    return body


def _format_validation_errors(exc: RequestValidationError) -> list[dict[str, str]]:
    fields: list[dict[str, str]] = []
    for err in exc.errors():
        loc = [str(part) for part in err.get("loc", []) if part not in ("body", "query", "path")]
        fields.append({"field": ".".join(loc) or "-", "message": err.get("msg", "参数不合法")})
    return fields


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(_: Request, exc: AppError) -> JSONResponse:
        log = logger.warning if exc.http_status < 500 else logger.error
        log("业务异常 [%s] %s", exc.code, exc.message)
        return JSONResponse(status_code=exc.http_status, content=exc.to_payload())

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        details = _format_validation_errors(exc)
        message = details[0]["message"] if details else "参数校验失败"
        return JSONResponse(
            status_code=422,
            content=_payload(ErrorCode.VALIDATION_FAILED, message, details),
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        # 404 / 405 等框架级错误也保持同样的响应结构
        code = exc.status_code * 100
        return JSONResponse(
            status_code=exc.status_code,
            content=_payload(code, str(exc.detail) or "请求失败"),
        )

    @app.exception_handler(IntegrityError)
    async def _integrity_error(_: Request, exc: IntegrityError) -> JSONResponse:
        logger.warning("数据约束冲突: %s", exc)
        return JSONResponse(
            status_code=409,
            content=_payload(ErrorCode.CONFLICT, "数据冲突，请检查唯一性或关联引用"),
        )

    @app.exception_handler(PoolTimeoutError)
    async def _pool_exhausted(_: Request, exc: PoolTimeoutError) -> JSONResponse:
        """连接池借不到连接 → 503，不是 500。

        这两者的区别不是文案：500 意味着「我们写错了」，会触发告警与人工介入；
        503 意味着「现在过载，等会再来」，调用方应该退避重试。归到 500 的后果是
        运维半夜被叫起来查一段根本没有 bug 的代码，而真正该做的是扩容或找出占着
        连接不放的那个慢查询。

        不打 `logger.exception`：堆栈全是池的内部调用，对定位没有帮助；
        有用的是异常消息里那句 `QueuePool limit of size N overflow M reached`。
        """
        logger.warning("连接池耗尽，本次请求快速失败: %s", exc)
        return JSONResponse(
            status_code=503,
            content=_payload(
                ErrorCode.UNAVAILABLE,
                "服务繁忙，请稍后重试",
                {"retryAfter": 5, "reason": "db_pool_exhausted"},
            ),
        )

    @app.exception_handler(StaleDataError)
    async def _stale_write(_: Request, exc: StaleDataError) -> JSONResponse:
        """乐观锁落败 → 409，不是 500。

        各业务表（`video_tasks` / `image_jobs` / `template_specs`）的 `state_version`
        或 `revision` 是乐观锁列：任何 `UPDATE` 都带
        `WHERE state_version = <读到的值>`，输的一方抛 `StaleDataError`。它继承
        `SQLAlchemyError`，落到下面那个通用处理器就会变成 500「数据库操作失败」
        ——一次「用户点删除时轮询正好在写进度」被报成服务端故障，用户不知道该
        重试，值班被叫起来翻一段没有 bug 的代码。

        这**不是**数据库异常，是并发写入的正常结局：动作没生效、数据没坏、
        重读一次就能继续。所以 409 + 明确的下一步，且不打 `logger.exception`。
        """
        logger.info("并发写入落败（乐观锁），已要求重试: %s", exc)
        return JSONResponse(
            status_code=409,
            content=_payload(
                ErrorCode.CONFLICT,
                "任务状态已变化，请刷新后重试",
                {"reason": "state_changed"},
            ),
        )

    @app.exception_handler(SQLAlchemyError)
    async def _db_error(_: Request, exc: SQLAlchemyError) -> JSONResponse:
        logger.exception("数据库异常: %s", exc)
        return JSONResponse(
            status_code=500,
            content=_payload(ErrorCode.DATABASE_ERROR, "数据库操作失败"),
        )

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception) -> JSONResponse:
        logger.exception("未捕获异常: %s", exc)
        return JSONResponse(
            status_code=500,
            content=_payload(ErrorCode.INTERNAL_ERROR, "服务器内部错误"),
        )
