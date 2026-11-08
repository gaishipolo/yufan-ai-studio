"""把替换运行摘要提供给任务中心注入点。

读端是 async（见 `core/feed.py` 的注释）：上一版读归档盘 JSON 是同步 IO，
现在读数据库，同步签名会逼调用方进线程池、在线程里拿到另一个事件循环。
"""

from __future__ import annotations

from core.feed import ReplacementFeedRecord
from db.session import get_session_factory
from modules.template.replacement_service import ReplacementService


async def read_feed(owner: str) -> list[ReplacementFeedRecord]:
    async with get_session_factory()() as session:
        return await ReplacementService(session).feed_records(owner)
