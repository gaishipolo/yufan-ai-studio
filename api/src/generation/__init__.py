"""生成编排框架。

职责：把「调供应商 → 轮询 → 落库 → 发事件」这套重复逻辑收口一次。

业务模块接入的完整动作只有三步：
1. 建一张自己的表（记录生成输入与结果）
2. 写一个 `GenerationStore`（5 个方法：load / mark_submitted / mark_progress /
   mark_succeeded / mark_failed）
3. 写一个 `GenerationPipeline` 并用 `@register_pipeline` 注册

之后创建任务的 service 里只需要：

    await get_runner().dispatch("video", task.id)

重试、超时、幂等、事件、队列全部由框架负责。
"""

from __future__ import annotations

from generation.events import (
    ALL_GENERATION_EVENTS,
    EVT_GENERATION_FAILED,
    EVT_GENERATION_PROGRESS,
    EVT_GENERATION_SUBMITTED,
    EVT_GENERATION_SUCCEEDED,
)
from generation.pipeline import (
    GenerationPipeline,
    get_pipeline,
    pipeline_kinds,
    pipelines,
    register_pipeline,
)
from generation.runner import JOB_POLL, JOB_SUBMIT, GenerationRunner, get_runner
from generation.spec import (
    STATUS_FAILED,
    STATUS_QUEUED,
    STATUS_RUNNING,
    STATUS_SUCCEEDED,
    TERMINAL_STATUSES,
    GenerationRecord,
)
from generation.store import GenerationStore

__all__ = [
    "ALL_GENERATION_EVENTS",
    "EVT_GENERATION_FAILED",
    "EVT_GENERATION_PROGRESS",
    "EVT_GENERATION_SUBMITTED",
    "EVT_GENERATION_SUCCEEDED",
    "JOB_POLL",
    "JOB_SUBMIT",
    "STATUS_FAILED",
    "STATUS_QUEUED",
    "STATUS_RUNNING",
    "STATUS_SUCCEEDED",
    "TERMINAL_STATUSES",
    "GenerationPipeline",
    "GenerationRecord",
    "GenerationRunner",
    "GenerationStore",
    "get_pipeline",
    "get_runner",
    "pipeline_kinds",
    "pipelines",
    "register_pipeline",
]
