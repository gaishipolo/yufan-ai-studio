"""示例驱动 B：同步形态，submit 直接返回终态。

有些图片厂商的 API 本身就是「请求里等图」。这类驱动 `submit` 返回
`GenerationProgress` 而不是任务号，`poll` 不会被调用，`supports_poll=False`。
框架拿到终态快照会直接结算、不再排轮询。
"""

from __future__ import annotations

import hashlib

from infra.ai.base import (
    Capability,
    GenerationProgress,
    GenerationRequest,
    GenerationState,
    ModelSpec,
)
from modules.model_driver.spec import DriverMeta

META = DriverMeta(label="驱动示例·同步", description="离线模拟：提交即出结果", version="1.0.0")

MODELS = (
    ModelSpec(
        id="md-sync-image",
        label="示例同步生图",
        capability=Capability.IMAGE,
        provider="md_sync",
    ),
)


class MockSyncDriver:
    name = "md_sync"
    capabilities = frozenset({Capability.IMAGE})
    models = MODELS
    supports_poll = False

    async def submit(self, request: GenerationRequest) -> GenerationProgress:
        digest = hashlib.sha1(request.prompt.encode()).hexdigest()[:12]
        external_id = f"md-sync-{digest}"
        return GenerationProgress(
            external_id=external_id,
            state=GenerationState.SUCCEEDED,
            progress=100,
            output_url=f"https://example.com/mock/{external_id}.png",
        )

    async def poll(self, external_id: str, *, model: str = "") -> GenerationProgress:
        return GenerationProgress(
            external_id=external_id,
            state=GenerationState.FAILED,
            error="同步驱动不提供续查",
        )

    async def cancel(self, external_id: str) -> None:
        return None


Driver = MockSyncDriver
