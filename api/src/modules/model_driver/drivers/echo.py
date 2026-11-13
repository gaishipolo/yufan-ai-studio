"""Echo 供应商：本地开发占位，不调用任何外部 API。

用于跑通「提交 → 轮询 → 完成」的完整链路。整体迁移后它也在 `drivers/` 里
自动载入，接真实厂商时把这个文件删掉或标 `placeholder` 即可，不需要改 `.env`。
它同时演示了接入真实供应商时要提供什么：三个方法 + 一份模型清单。

**这里声明的模型全部标了 `placeholder=True`**，所以它们**不会出现在用户的
模型选择器里**（`public_models()` 过滤掉），但 `resolve()` 仍能按 id 解析到。

为什么必须挡在选择器外面：这些 id / label 长得跟真的第三方模型一模一样
（Seedream 3 / Seedance 2.5 / 可灵 2.1），而 echo 根本不调外部 API ——
提交后按时间推算进度，最后返回 `https://example.com/mock/...` 的占位图。
用户选中「Seedream 3」会拿到一张假图，**全程没有任何报错**，看起来像接了
字节的豆包，其实什么都没接。

`placeholder` 与 `internal` 是两回事，别混用：`internal` 是「只能自己的
业务链路调」（通用入口选中会 400）；`placeholder` 是「实现是假的」。

示例驱动（`_mock_async.py` 等）靠**文件名前缀 `_`** 不自动载入；本文件不带
前缀，会被扫进来——这是故意的：生成框架的离线联调按 id 提交要能解析得到。
挡在选择器外靠 `placeholder`，不靠文件名。
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

# 本地模拟耗时（秒）：图片快、视频慢，便于观察不同能力的轮询行为
_FAKE_DURATION_SEC: dict[Capability, float] = {
    Capability.VIDEO: 8.0,
    Capability.IMAGE: 2.5,
    Capability.AUDIO: 5.0,
}

_VIDEO_SCHEMA = {
    "durationSec": {"type": "int", "min": 5, "max": 12, "default": 5},
    "aspectRatio": {"type": "enum", "options": ["9:16", "16:9", "1:1"], "default": "9:16"},
}
_IMAGE_SCHEMA = {
    "size": {
        "type": "enum",
        # 尺寸统一用**半角 x**：这是供应商接口的字面格式（Agnes 收到 `×` 直接 400），
        # 而 `GET /models` 的这份声明就是前端渲染下拉的取值来源——这里写全角，
        # 前端照着渲染出来的每个选项都会在提交时失败。
        "options": ["1024x1024", "1024x1536", "1536x1024"],
        "default": "1024x1024",
    },
    "count": {"type": "int", "min": 1, "max": 4, "default": 1},
}

MODELS: tuple[ModelSpec, ...] = (
    ModelSpec(
        id="seedance-2.5",
        label="Seedance 2.5",
        capability=Capability.VIDEO,
        provider="echo",
        param_schema=_VIDEO_SCHEMA,
        description="通用短视频生成，支持首尾帧参考",
        placeholder=True,
    ),
    ModelSpec(
        id="kling-2.1",
        label="可灵 2.1",
        capability=Capability.VIDEO,
        provider="echo",
        param_schema=_VIDEO_SCHEMA,
        description="写实风格运镜，适合商品与人物",
        placeholder=True,
    ),
    ModelSpec(
        id="seedream-3",
        label="Seedream 3",
        capability=Capability.IMAGE,
        provider="echo",
        param_schema=_IMAGE_SCHEMA,
        placeholder=True,
    ),
    ModelSpec(
        id="tts-v3",
        label="语音合成 v3",
        capability=Capability.AUDIO,
        provider="echo",
        param_schema={"voice": {"type": "enum", "options": ["female-warm", "male-calm"]}},
        placeholder=True,
    ),
)


class EchoProvider:
    """把提交时间编码进 external_id，轮询时按经过秒数推算进度。"""

    name = "echo"
    capabilities = frozenset(Capability)
    models = MODELS
    # 返回任务 ID、由框架续轮询；恢复器按缺省 False 处理，必须显式声明
    supports_poll = True

    async def submit(self, request: GenerationRequest) -> str:
        digest = hashlib.sha1(request.prompt.encode()).hexdigest()[:8]
        return f"echo-{request.capability.value}-{digest}-{int(time.time())}"

    async def poll(self, external_id: str, *, model: str = "") -> GenerationProgress:
        parts = external_id.split("-")
        if len(parts) < 4:
            return GenerationProgress(
                external_id=external_id,
                state=GenerationState.FAILED,
                error="非法的任务标识",
                retryable=False,
            )

        try:
            capability = Capability(parts[1])
            started = int(parts[-1])
        except ValueError:
            return GenerationProgress(
                external_id=external_id,
                state=GenerationState.FAILED,
                error="非法的任务标识",
            )

        elapsed = time.time() - started
        ratio = min(1.0, elapsed / _FAKE_DURATION_SEC[capability])

        if ratio >= 1.0:
            return GenerationProgress(
                external_id=external_id,
                state=GenerationState.SUCCEEDED,
                progress=100,
                output_url=f"https://example.com/mock/{capability.value}.mp4",
                cover_url=f"https://picsum.photos/seed/{parts[2]}/720/1280",
            )
        return GenerationProgress(
            external_id=external_id,
            state=GenerationState.RUNNING,
            progress=max(1, int(ratio * 100)),
        )

    async def cancel(self, external_id: str) -> None:
        return None


Driver = EchoProvider
