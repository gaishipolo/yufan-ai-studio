"""演示数据的小工具（仅开发环境）。

放 `core/` 而不是某个模块里：这些函数和任何业务模型都无关，只是「造一条看起来
像几天前发生的演示行」这件事被六个模块各写了一遍。它们是 `str`/`int` 进、
`str`/`datetime` 出，所以不至于让框架认识业务。
"""

from __future__ import annotations

from datetime import datetime, timedelta

from db.base import utcnow


def minutes_ago(minutes: int) -> datetime:
    """相对当前时刻的过去时间。演示数据靠它拉开先后，列表页才不会看起来全是同一秒。"""
    return utcnow() - timedelta(minutes=minutes)


def placeholder_image(seed: str, width: int, height: int) -> str:
    """占位图地址（picsum）。演示数据不落真实字节：`app seed` 不该产生存储写入。"""
    return f"https://picsum.photos/seed/{seed}/{width}/{height}"
