"""日志格式化器必须把异常堆栈打出来。

这类缺陷的症状极隐蔽：`logger.exception("任务执行失败 name=xxx")` 照常输出，
日志里也确实出现了那行 ERROR —— 只是**下面没有 traceback**。于是排查的人
看到「失败了」，却拿不到任何线索，只能回头复现一遍。

真实发生过：dev 环境用 `DevFormatter`，它只拼 `record.getMessage()`，
`JsonFormatter` 有 `exc_info` 分支、它没有。两边不对齐，且没有任何用例
覆盖 formatter，所以从加进来那天起就没被发现。生产环境走 JSON 反而正常，
越接近开发越看不见错误细节。
"""

from __future__ import annotations

import json
import logging
import sys

import pytest

from core.logging import DevFormatter, JsonFormatter, RequestIdFilter


def _record_with_exception() -> logging.LogRecord:
    """构造一条带异常信息的日志记录（模拟 `logger.exception` 的产物）。"""
    try:
        raise ValueError("产物文件不存在：/tmp/run-42/out.mp4")
    except ValueError:
        exc_info = sys.exc_info()

    record = logging.LogRecord(
        name="workers",
        level=logging.ERROR,
        pathname=__file__,
        lineno=1,
        msg="任务执行失败 name=%s id=%s",
        args=("template.replacement.prepare", "abc123"),
        exc_info=exc_info,
    )
    RequestIdFilter().filter(record)
    return record


def test_dev_formatter_keeps_the_traceback() -> None:
    text = DevFormatter().format(_record_with_exception())

    assert "任务执行失败" in text
    assert "template.replacement.prepare" in text
    # 关键：异常类型与消息必须出现，否则日志等于只说了一句「失败了」
    assert "ValueError" in text
    assert "产物文件不存在" in text
    assert "Traceback (most recent call last)" in text


def test_json_formatter_keeps_the_traceback() -> None:
    payload = json.loads(JsonFormatter().format(_record_with_exception()))

    assert payload["level"] == "ERROR"
    assert "template.replacement.prepare" in payload["message"]
    assert "ValueError" in payload["exc_info"]
    assert "产物文件不存在" in payload["exc_info"]


def test_formatters_agree_on_whether_a_stack_is_present() -> None:
    """两种形态对「有没有堆栈」的判断必须一致。

    它们服务于同一个 `setup_logging(env=...)` 分支——dev 看人眼、prod 看机器。
    一边留、一边丢，意味着同一个故障在开发环境查不到、在生产环境查得到，
    或者反过来。这条测试就是钉住这个等价关系。
    """
    record = _record_with_exception()
    dev_text = DevFormatter().format(record)
    json_payload = json.loads(JsonFormatter().format(record))

    assert ("Traceback (most recent call last)" in dev_text) is (
        "exc_info" in json_payload
    )


@pytest.mark.parametrize("formatter", [DevFormatter(), JsonFormatter()])
def test_record_without_exception_has_no_stack(formatter: logging.Formatter) -> None:
    """没有异常时不许凭空多出 traceback —— 否则每条正常日志都会变三行。"""
    record = logging.LogRecord(
        name="core.db",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="数据表已就绪（23 张）",
        args=(),
        exc_info=None,
    )
    RequestIdFilter().filter(record)
    text = formatter.format(record)

    assert "Traceback" not in text
    assert "NoneType: None" not in text
