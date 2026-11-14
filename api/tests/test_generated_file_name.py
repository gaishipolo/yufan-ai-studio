"""生成产物命名的口径：`用户名-年-月-日-时-分-秒`。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

from core.naming import generated_file_name

CST = timezone(timedelta(hours=8))


def test_formats_author_and_local_time() -> None:
    """库里存 UTC，名字按**本地**时区：否则东八区用户会看到早八小时的日期。"""
    when = datetime(2026, 11, 14, 9, 27, 19, tzinfo=UTC)
    assert generated_file_name("老大", when, suffix=".mp4", tz=CST) == (
        "老大-2026-11-14-17-27-19.mp4"
    )


def test_naive_datetime_is_read_as_utc() -> None:
    """sqlite 读回来是 naive：当本地时间格式化会整体偏一个时区。"""
    naive = datetime(2026, 11, 14, 9, 27, 19)
    assert generated_file_name("老大", naive, tz=CST) == "老大-2026-11-14-17-27-19"


def test_unsafe_characters_are_stripped() -> None:
    """昵称里的空格与 `/` 会让下载写不出文件（Windows 禁止这些字符）。"""
    when = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
    assert generated_file_name('a/b c:*?"<>|d', when, tz=CST) == "abcd-2026-01-02-11-04-05"


def test_empty_author_falls_back_to_a_generic_name() -> None:
    when = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
    assert generated_file_name("   ", when, tz=CST) == "用户-2026-01-02-11-04-05"


def test_long_author_is_truncated_but_keeps_the_timestamp() -> None:
    when = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
    name = generated_file_name("名" * 50, when, suffix=".png", tz=CST)
    assert name == f"{'名' * 32}-2026-01-02-11-04-05.png"
