"""日志：标准库 logging + 结构化输出。

- 每条日志自动带上 request_id
- dev 输出可读文本，prod 输出单行 JSON（便于采集到 ELK / Loki）
- 不引入第三方依赖，替换为 structlog 时只需改本文件
"""

from __future__ import annotations

import json
import logging
import re
import sys
from datetime import UTC, datetime

from core.context import get_request_id

#: 敏感参数的**名字**。值一律替换成 `<redacted>`。
#:
#: 这条清单只覆盖「会出现在 URL / 日志文本里的凭证形态」。它不追求穷尽 ——
#: 目标是「别让预签名地址、供应商回调地址、DSN 这类东西原样进日志」，
#: 而不是做一个正则防火墙。
_SECRET_NAMES = (
    r"password|passwd|pwd|secret|token|access_token|refresh_token|"
    r"apikey|api_key|api-key|signature|credential|authorization"
)

_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    # key=value：query string 与日志里的 `k=v` 都覆盖。
    # 保留键名，只抹值 —— 排查时「哪个参数被抹了」仍要能看见。
    (
        # 排除 `<` 与 `>`：值被抹成 `<redacted>` 之后，再跑一次不能再匹配
        # （否则 `token=<redacted>` 会被替换成 `token=<redacted>>>`，
        #  `redact()` 就不是幂等的了 —— 而 formatter 会对同一段文本再抹一遍）
        re.compile(rf"(?i)\b({_SECRET_NAMES})=([^&\s,;\"'<}}>]+)"),
        r"\1=<redacted>",
    ),
    # DSN：scheme://user:pass@host —— 这是 `db/session.py` 那行的形态，
    # `key=value` 规则兜不住（密码前面是冒号不是等号）。
    (
        re.compile(r"(?i)\b([a-z][a-z0-9+.\-]*://[^:/\s@]+):([^@/\s]+)@"),
        r"\1:***@",
    ),
    # Authorization: Bearer xxx
    (re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._\-]{12,}"), r"\1<redacted>"),
)


def redact(text: str) -> str:
    """抹掉文本里的凭证。幂等：对已经抹过的文本再跑一次结果不变。"""
    for pattern, replacement in _PATTERNS:
        text = pattern.sub(replacement, text)
    return text


class RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = get_request_id()
        return True


class RedactingFilter(logging.Filter):
    """把已格式化的消息脱敏后再交给 Formatter。

    为什么做在 Filter 而不是每个调用点：调用点有几万处，漏一处就漏一条；
    而 Filter 在 `getMessage()` 之后、Formatter 之前，是唯一「所有日志都要
    经过」的位置。

    代价：这里会提前把 `msg % args` 展开一次。对 `logger.info("%s", obj)` 而言
    等于提前调用了 `str(obj)` —— 项目里没有把重对象直接丢进日志的写法，
    为此换来「不会误打凭证」是值的。
    """

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        redacted = redact(message)
        if redacted != message:
            record.msg = redacted
            record.args = ()
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
        # 堆栈里也可能带着 DSN / URL（例如 SQLAlchemy 的连接异常），
        # 所以对最终字符串再抹一遍；`redact()` 是幂等的，重复调用无副作用
        return redact(json.dumps(payload, ensure_ascii=False))


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
        return redact(text)


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
    handler.addFilter(RedactingFilter())
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
