"""生成产物命名的口径：`用户名-年-月-日-时-分-秒`。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

from core.naming import generated_file_name

CST = timezone(timedelta(hours=8))


def test_formats_author_and_time_in_the_given_zone() -> None:
    """显式传 `tz` 时按该时区格式化（生产路径不传，见文件末尾那条）。"""
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


def test_default_timezone_is_utc_not_the_server_local_one() -> None:
    """不传 `tz` 时必须按 UTC 命名，与实例所在时区无关。

    以前默认 `astimezone(None)` = 服务器本地时区：容器是 UTC、开发机是 +08，
    同一个任务在两处名字差 8 小时；多副本部署时哪台先领到任务，名字就跟着那台
    的 `TZ` 走。前端下载名也按 UTC 算（`fileStamp`），两处必须同一个基准。
    """
    when = datetime(2026, 11, 14, 17, 27, 19, tzinfo=CST)  # 东八区 17:27 = UTC 09:27
    assert generated_file_name("老大", when) == "老大-2026-11-14-09-27-19"
