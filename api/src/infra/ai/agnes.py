"""Agnes 生图供应商（OpenAI 兼容的同步接口）。

契约（2026-11-06 实测确认）：

    POST {AGNES_BASE_URL}/v1/images/generations
    Authorization: Bearer {AGNES_API_KEY}
    {"model": "agnes-image-2.5-flash", "prompt": "...", "n": 1, "size": "1024x1024"}

    → 200（约 13 秒，**同步出结果**）
      {"data": [{"url": "https://.../xxx.png", "b64_json": "", "revised_prompt": ""}],
       "created": 1793899084, "task_id": "task_..."}

因为它是同步的，`submit` 直接返回终态 `GenerationProgress`，框架不再轮询
（`ModelProvider` 协议两种形态都收，见 `infra/ai/base.py`）。

代价要说清楚：一次 `submit` 会占住一个 worker 槽位十几秒。图片任务本来就短，
`WORKER_CONCURRENCY` 默认 2 时约 7 张/分钟；量上去以后要么调并发，
要么把这里改成「先落记录再后台跑」—— 那属于业务侧的取舍，不是协议的限制。
"""

from __future__ import annotations

from typing import ClassVar

import httpx

from core.exceptions import (
    BadRequest,
    Forbidden,
    RateLimited,
    Unauthorized,
    UpstreamError,
)
from core.logging import get_logger
from infra.ai.base import (
    Capability,
    GenerationProgress,
    GenerationRequest,
    GenerationState,
    ModelSpec,
)
from infra.driver_config import DriverSettings, driver_config

logger = get_logger(__name__)

# 只声明确认可用的型号；换型号时改 AGNES_MODEL，不用改代码
_MODEL_ID = "agnes-image-2.5-flash"

_SIZE_SCHEMA = {
    "size": {
        "type": "enum",
        "options": ["1024x1024", "1024x1536", "1536x1024"],
        "default": "1024x1024",
    },
    # 供应商只接受 n=1：传 2 或 4 会被 400 拒绝（实测 `n 必须为 1`）。
    # 这里的 max 是**能力声明**，`ImageService` 按它做受理前校验——
    # 声明成 4 会让「选 4 张」先受理成功、再异步失败，用户只看到一张裂图。
    "count": {"type": "int", "min": 1, "max": 1, "default": 1},
}

MODELS: tuple[ModelSpec, ...] = (
    ModelSpec(
        id=_MODEL_ID,
        label="Agnes Image 2.5 Flash",
        capability=Capability.IMAGE,
        provider="agnes",
        param_schema=_SIZE_SCHEMA,
        description="文生图，同步返回图片地址",
    ),
)


class AgnesSettings(DriverSettings):
    """Agnes 部署参数。环境变量前缀 `AGNES_`。

    `submit_budget_for` 声明「这个 timeout 是 IMAGE 的单次提交耗时」，
    巡检阈值据此推导；不声明的话任何驱动（含成图下载）的 timeout
    都可能被聚合进来，把阈值拖到与生图无关的长度（R12-03）。
    """

    model_config = driver_config("AGNES")

    submit_budget_for: ClassVar[frozenset[str]] = frozenset({"image"})

    base_url: str = "https://api.agnes-ai.cn"
    api_key: str = ""
    model: str = _MODEL_ID
    # 图片是同步出结果，超时要覆盖整个生成过程（实测 13 秒，留足余量）
    timeout_sec: float = 180.0


def get_agnes_settings() -> AgnesSettings:
    return AgnesSettings()


class AgnesProvider:
    """同步生图：`submit` 等到出图，直接返回终态快照。"""

    name = "agnes"
    capabilities = frozenset({Capability.IMAGE})
    models = MODELS
    # 同步供应商：submit 直接给终态，没有可续查的任务 ID。
    # generation/recovery.py 据此不调 runner.resume()，否则 poll() 会把记录判死。
    supports_poll = False

    async def submit(self, request: GenerationRequest) -> GenerationProgress:
        config = get_agnes_settings()
        if not config.api_key.strip():
            raise UpstreamError(
                "Agnes 未配置：请设置 AGNES_API_KEY（控制台创建的密钥），当前无法提交生图"
            )

        payload: dict[str, object] = {
            "model": request.model or config.model,
            "prompt": request.prompt,
            "n": int(request.params.get("count", 1) or 1),
            "size": str(request.params.get("size") or "1024x1024"),
        }
        url = f"{config.base_url.rstrip('/')}/v1/images/generations"

        async with httpx.AsyncClient(timeout=config.timeout_sec) as client:
            # 不捕获 httpx 的超时/连接异常：runner 的 _possibly_delivered 按
            # 「超时 = 可能已受理」处理，捕获后自己包装会丢掉这个语义，
            # 让一次已经计费的提交被误判成「确定没送达」而重试。
            response = await client.post(
                url,
                json=payload,
                headers={
                    "Authorization": f"Bearer {config.api_key.strip()}",
                    "Content-Type": "application/json",
                },
            )

        if response.status_code >= 400:
            raise _http_error(response)

        body = response.json()
        items = body.get("data") or []
        image_url = ""
        if items and isinstance(items[0], dict):
            image_url = str(items[0].get("url") or "")
        if not image_url:
            # 200 但没有图：可能是内容审核未通过，也可能字段变了
            raise UpstreamError(
                f"Agnes 未返回图片地址：{str(body)[:200]}"
            )

        return GenerationProgress(
            external_id=str(body.get("task_id") or request.request_id or "agnes"),
            state=GenerationState.SUCCEEDED,
            progress=100,
            output_url=image_url,
            cover_url=image_url,
        )

    async def poll(self, external_id: str, *, model: str = "") -> GenerationProgress:
        """同步供应商没有中间态；走到这里说明上游数据被手工改过。"""
        return GenerationProgress(
            external_id=external_id,
            state=GenerationState.FAILED,
            error="Agnes 是同步供应商，不支持轮询；请重新提交",
            retryable=False,
        )

    async def cancel(self, external_id: str) -> None:
        return None


def _http_error(response: httpx.Response) -> Exception:
    """把上游状态码翻译成本项目的异常类型。

    分类依据是 runner 的计费口径：4xx 表示供应商明确拒绝（没受理，可安全重试或直接失败），
    5xx / 429 表示临时故障（可能已受理，交给框架的重试与「结果未知」逻辑）。
    """
    detail = ""
    try:
        body = response.json()
        error = body.get("error") if isinstance(body, dict) else None
        if isinstance(error, dict):
            detail = str(error.get("message") or error.get("code") or "")
        elif error:
            detail = str(error)
        else:
            detail = str(body)[:200]
    except Exception:  # noqa: BLE001 - 上游返回非 JSON 时只是少一段说明
        detail = response.text[:200]

    suffix = f"（{response.status_code}）{detail}"
    if response.status_code == 401:
        return Unauthorized(f"Agnes 鉴权失败：请检查 AGNES_API_KEY{suffix}")
    if response.status_code == 403:
        return Forbidden(f"Agnes 拒绝访问：当前密钥无权调用该模型{suffix}")
    if response.status_code == 429:
        return RateLimited(f"Agnes 限流，请稍后重试{suffix}")
    if response.status_code >= 500:
        return UpstreamError(f"Agnes 服务异常{suffix}")
    if response.status_code == 400:
        return BadRequest(f"Agnes 参数校验失败{suffix}")
    return UpstreamError(f"Agnes 调用失败{suffix}")


Driver = AgnesProvider
