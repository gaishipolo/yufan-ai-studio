"""生成域事件名与发布辅助。

订阅方式（在自己的模块里，不需要改框架）：

    from core.events import get_event_bus
    from generation.events import EVT_GENERATION_SUCCEEDED

    @get_event_bus().subscribe(EVT_GENERATION_SUCCEEDED)
    async def notify(event):
        await send_im(event.payload["record_id"], event.payload["output_url"])
"""

from __future__ import annotations

from typing import Any

from core.events import get_event_bus

EVT_GENERATION_SUBMITTED = "generation.submitted"
EVT_GENERATION_PROGRESS = "generation.progress"
EVT_GENERATION_SUCCEEDED = "generation.succeeded"
EVT_GENERATION_FAILED = "generation.failed"

ALL_GENERATION_EVENTS = (
    EVT_GENERATION_SUBMITTED,
    EVT_GENERATION_PROGRESS,
    EVT_GENERATION_SUCCEEDED,
    EVT_GENERATION_FAILED,
)


async def emit(name: str, **payload: Any) -> None:
    await get_event_bus().publish(name, **payload)
