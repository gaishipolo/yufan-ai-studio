"""日志脱敏：凭证不能进日志。

`core/logging.py` 原来只有 `RequestIdFilter` 与两个 Formatter，日志内容原样输出。
而 `db/session.py` 那行「数据库引擎已创建」用的是 `settings.db_url.split("///")[0]`
—— 那个写法**只对 sqlite 有效**：PG 的 URL 是 `scheme://user:pass@host`，没有三个
斜杠，`split` 找不到分隔符就返回原串，于是启动日志里是含密码的完整 DSN。

这里守两层：`redact()` 的规则本身，以及它确实挂在 handler 上。
"""

from __future__ import annotations

import logging

import pytest
from sqlalchemy.engine import make_url

from core.logging import RedactingFilter, redact


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # DSN：密码前面是冒号，`key=value` 规则兜不住
        (
            "postgresql+asyncpg://yufan:YufanDev2026@127.0.0.1:5433/yufan_studio",
            "postgresql+asyncpg://yufan:***@127.0.0.1:5433/yufan_studio",
        ),
        # 预签名地址：值抹掉，键名保留（排查时要知道是哪个参数）
        (
            "https://b.s3.amazonaws.com/a.png?X-Amz-Signature=abc&page=2",
            "https://b.s3.amazonaws.com/a.png?X-Amz-Signature=<redacted>&page=2",
        ),
        (
            "https://api.example.com/cb?apikey=sk-live-123&page=2",
            "https://api.example.com/cb?apikey=<redacted>&page=2",
        ),
        (
            "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.payload.sig",
            "Authorization: Bearer <redacted>",
        ),
    ],
)
def test_credentials_are_redacted(raw: str, expected: str) -> None:
    assert redact(raw) == expected


@pytest.mark.parametrize(
    "harmless",
    [
        # 没有密码段的 URL 不该被动：`redis://host:port` 里那个冒号不是凭证
        "redis://cache:6379/0",
        "sqlite+aiosqlite:///var/app.db",
        # 普通日志
        "任务已提交：芳华里-精致妈妈（42）",
    ],
)
def test_harmless_text_is_left_alone(harmless: str) -> None:
    assert redact(harmless) == harmless


def test_redact_is_idempotent() -> None:
    once = redact("postgresql://u:p@h/db?token=abc")
    assert redact(once) == once


def test_filter_rewrites_the_record_message() -> None:
    """Filter 要作用在**格式化后**的消息上，否则 `logger.info("%s", url)` 漏网。"""

    record = logging.LogRecord(
        name="app.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="回调地址 %s",
        args=("https://h/cb?token=secret-value",),
        exc_info=None,
    )
    assert RedactingFilter().filter(record) is True
    assert "secret-value" not in record.getMessage()
    assert "<redacted>" in record.getMessage()


def test_session_log_line_renders_without_password() -> None:
    """`db/session.py` 那行改用 SQLAlchemy 的渲染 —— 这是它现在的行为。

    直接构造 URL 而不是连库：要验的是「渲染出来有没有密码」，与连接无关。
    """

    rendered = make_url(
        "postgresql+asyncpg://yufan:YufanDev2026@127.0.0.1:5433/yufan_studio"
    ).render_as_string(hide_password=True)
    assert "YufanDev2026" not in rendered
    assert "***" in rendered
    assert "yufan_studio" in rendered


def test_setup_logging_attaches_the_filter() -> None:
    """Filter 必须真的挂在 handler 上 —— 定义了不挂等于没定义。"""

    from core.logging import setup_logging

    setup_logging(env="dev", debug=False)
    handlers = logging.getLogger().handlers
    assert handlers, "root logger 至少要有一个 handler"
    assert any(
        isinstance(item, RedactingFilter)
        for handler in handlers
        for item in handler.filters
    ), "RedactingFilter 没有挂到任何 handler 上"
