"""示例驱动 A：异步形态，离线可跑。

演示「怎么调用 / 怎么查询结果 / 怎么停止」三件事的最小写法：
`submit` 返回供应商任务号，框架会按节奏回来调 `poll`。
真实厂商把这里的假时间换成 HTTP 请求即可，其余一行不用改。

**文件名以下划线开头**，所以 `registry._iter_driver_modules()` 会跳过它：
示例模型不进模型目录、不进前端选择器、不进 `app drivers list`。
需要离线联调时显式调用
`modules.model_driver.registry.register_example_drivers()`。
"""

from __future__ import annotations

import hashlib
import time

from infra.ai.base import (
    Capability,
    GenerationProgress,
    GenerationRequest,
    GenerationState,
    ModelSpec,
)
from modules.model_driver.spec import DriverMeta

META = DriverMeta(
    label="驱动示例·异步",
    description="离线模拟：提交后按经过秒数推进进度",
    version="1.0.0",
)

MODELS = (
    ModelSpec(
        id="md-async-image",
        label="示例异步生图",
        capability=Capability.IMAGE,
        provider="md_async",
        param_schema={"size": {"type": "enum", "options": ["1024x1024"], "default": "1024x1024"}},
    ),
    ModelSpec(
        id="md-async-video",
        label="示例异步视频",
        capability=Capability.VIDEO,
        provider="md_async",
    ),
)


class MockAsyncDriver:
    name = "md_async"
    capabilities = frozenset({Capability.IMAGE, Capability.VIDEO})
    models = MODELS
    supports_poll = True

    async def submit(self, request: GenerationRequest) -> str:
        digest = hashlib.sha1(request.prompt.encode()).hexdigest()[:8]
        return f"md-{request.capability.value}-{digest}-{int(time.time())}"

    async def poll(self, external_id: str, *, model: str = "") -> GenerationProgress:
        parts = external_id.split("-")
        try:
            started = int(parts[-1])
        except (ValueError, IndexError):
            return GenerationProgress(
                external_id=external_id,
                state=GenerationState.FAILED,
                error="非法的任务标识",
            )
        ratio = min(1.0, (time.time() - started) / 3.0)
        if ratio >= 1.0:
            return GenerationProgress(
                external_id=external_id,
                state=GenerationState.SUCCEEDED,
                progress=100,
                output_url=f"https://example.com/mock/{external_id}.png",
            )
        return GenerationProgress(
            external_id=external_id,
            state=GenerationState.RUNNING,
            progress=max(1, int(ratio * 100)),
        )

    async def cancel(self, external_id: str) -> None:
        return None


Driver = MockAsyncDriver
