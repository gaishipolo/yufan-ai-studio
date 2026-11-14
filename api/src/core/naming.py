"""生成产物的文件名：`用户名-年-月-日-时-分-秒`。

**为什么不用标题**：标题是给人读的一句话（「一只狸花猫在草地上奔跑」），同一句话
可以生成十次；落在磁盘上就是十个同名文件，下载下来谁也分不清哪个是新的。用户要
的是「谁、什么时候生成的那一个」，所以名字里放作者与时间。

**时间按本地时区格式化**（`astimezone()`，可用 `tz` 覆盖）：库里存的是 UTC，直接
`strftime` 会让东八区用户看到早八小时的日期 —— 下午生成的片子被叫成「早上」。

显示名可能带空格或 `/`（Windows 文件名禁字符），这里统一剥掉：名字是给下载用的，
不该因为某人的昵称里有个斜杠就写不出来。
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, tzinfo

#: Windows / POSIX 都不接受（或很容易踩坑）的字符，外加空格与连续空白。
_UNSAFE = re.compile(r'[\\/:*?"<>|\s]+')

#: 名字太长会让「下载」写出超长路径，`用户名` 部分限长，留足时间戳的位置。
_MAX_AUTHOR = 32


def generated_file_name(
    author: str,
    when: datetime | None = None,
    *,
    suffix: str = "",
    tz: tzinfo | None = None,
) -> str:
    """`老大-2026-11-14-17-27-19.mp4`。

    `suffix` 由调用方给（真实产物的扩展名），这里不猜：视频是 mp4、图是 png，
    猜错会让文件双击打不开。
    """
    stamp = (when or datetime.now(tz=UTC))
    if stamp.tzinfo is None:  # 库里可能是 naive（按 UTC 解释），不能当本地时间直接格式化
        stamp = stamp.replace(tzinfo=UTC)
    stamp = stamp.astimezone(tz)

    who = _UNSAFE.sub("", (author or "").strip())[:_MAX_AUTHOR]
    return f"{who or '用户'}-{stamp:%Y-%m-%d-%H-%M-%S}{suffix}"


__all__ = ["generated_file_name"]
