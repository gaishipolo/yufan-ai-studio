"""日志：标准库 logging + 结构化输出。

- 每条日志自动带上 request_id
- dev 输出可读文本，prod 输出单行 JSON（便于采集到 ELK / Loki）
- 不引入第三方依赖，替换为 structlog 时只需改本文件
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime

from core.context import get_request_id


class RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = get_request_id()
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "request_id": getattr(record, "request_id", "-"),
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        for key, value in getattr(record, "extra_fields", {}).items():
            payload[key] = value
        return json.dumps(payload, ensure_ascii=False)


class DevFormatter(logging.Formatter):
    COLORS = {
        "DEBUG": "\033[38;5;245m",
        "INFO": "\033[38;5;39m",
        "WARNING": "\033[38;5;214m",
        "ERROR": "\033[38;5;203m",
        "CRITICAL": "\033[1;38;5;196m",
    }
    RESET = "\033[0m"

    def format(self, record: logging.LogRecord) -> str:
        color = self.COLORS.get(record.levelname, "")
        ts = datetime.fromtimestamp(record.created).strftime("%H:%M:%S")
        rid = getattr(record, "request_id", "-")
        rid_part = f"\033[38;5;243m[{rid[:8]}]\033[0m " if rid != "-" else ""
        text = (
            f"\033[38;5;243m{ts}\033[0m {color}{record.levelname:<7}{self.RESET} "
            f"{rid_part}{record.getMessage()}"
        )
        # 异常堆栈必须带上。`logger.exception(...)` 的全部价值就是那几行 traceback：
        # 只打 message 的话，日志里只剩「任务执行失败 name=xxx」，和
        # 「日志说了失败了」差不多——排查时还得回头复现一次才知道抛的是什么。
        # JsonFormatter 有这一段，dev 用的这个反而没有，属于两边没对齐。
        if record.exc_info:
            text += "\n" + self.formatException(record.exc_info)
        return text


# 项目自己的命名空间。`DEBUG=true` 只放开这些，第三方库仍由下面那份降噪名单管。
# 早先这里只写了 `app`——而 `app` 其实是访问日志那一支，`core.*` / `infra.*` /
# `db.*` 的 logger 名以各自的包开头，于是「打开了 debug 却一条 debug 日志都看不到」，
# 排查时很容易以为是那些代码里没写日志。
PROJECT_NAMESPACES = (
    "app",
    "api",
    "core",
    "db",
    "infra",
    "modules",
    "generation",
    "workers",
    "scaffold",
)


def setup_logging(env: str = "dev", debug: bool = False) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.addFilter(RequestIdFilter())
    handler.setFormatter(DevFormatter() if env == "dev" else JsonFormatter())

    root = logging.getLogger()
    root.handlers.clear()
    # 第三方库一律 INFO 起步，DEBUG 只对项目自身命名空间开放
    root.setLevel(logging.INFO)
    root.addHandler(handler)

    project_level = logging.DEBUG if debug else logging.INFO
    for namespace in PROJECT_NAMESPACES:
        logging.getLogger(namespace).setLevel(project_level)

    # 降噪：访问日志由自己的中间件接管；sqlite 驱动与 SQL echo 无需默认开启
    for noisy in (
        "uvicorn.access",
        "uvicorn.error",
        "sqlalchemy.engine.Engine",
        "aiosqlite",
        "httpx",
        "httpcore",
    ):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
