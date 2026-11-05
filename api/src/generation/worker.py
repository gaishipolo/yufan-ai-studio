"""框架自带的两个 job handler。

它们只是把队列和 runner 接起来，真正的编排在 `runner.py`。
业务模块不需要注册 handler，只需要注册 pipeline。
"""

from __future__ import annotations

from generation.runner import JOB_POLL, JOB_SUBMIT, get_runner
from infra.queue.base import Job
from workers import register_handler


@register_handler(JOB_SUBMIT)
async def handle_submit(job: Job) -> None:
    await get_runner().handle_submit(job)


@register_handler(JOB_POLL)
async def handle_poll(job: Job) -> None:
    await get_runner().handle_poll(job)
