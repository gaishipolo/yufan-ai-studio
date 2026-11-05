"""全局异常处理器：把任何异常收敛成 `{code, message, data}`。

路由与 service 层因此可以放心抛异常，不必写 try/except。
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
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
