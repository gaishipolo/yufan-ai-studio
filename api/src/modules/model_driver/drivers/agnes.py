"""Agnes 生图驱动（按 `docs/MODEL_DRIVER_STANDARD.md` 写的单文件驱动）。

一个文件 = 一个供应商：`META` + `MODELS` + 实现 `submit/poll/cancel` 的类。
放进 `drivers/` 就自动载入，**不需要**改 `.env` 的 `AI_PROVIDERS`、不需要注册。

## 契约（2026-11-10 实测，含与官方文档的出入）

    POST {AGNES_BASE_URL}/v1/images/generations
    Authorization: Bearer {AGNES_API_KEY}
    {
      "model": "agnes-image-2.5-flash",
      "prompt": "...",
      "size": "1024x1024",
      "extra_body": {
        "image": ["https://.../a.png", "https://.../b.png"],   # 图生图 / 多图合成才有
        "response_format": "url"
      }
    }

    → 200（约 10-20 秒，**同步出结果**）
      {"data": [{"url": "https://.../output.png", "b64_json": "",
                 "revised_prompt": ""}],
       "created": 1794319801, "task_id": "task_..."}

同步形态：`submit` 自己等到出图，直接返回终态 `GenerationProgress`，
`poll` 不会被调用（`supports_poll = False`）。代价是一次 `submit` 占住一个
worker 槽位十几秒——厂商 API 就是请求里等结果，不是框架的疏漏。

## 官方文档里三条「错了就报错」的约束

1. `response_format` **必须放在 `extra_body` 内**，放请求体顶层会被拒；
2. 图生图 / 多图合成把图片数组放在 `extra_body.image`，**不要**再传
   `tags: ["img2img"]`（文档明确说不需要）；
3. `n` 只能为 1，传 2 直接 400 `n 必须为 1`。所以 schema 里 `count` 的
   `max=1`，并且 `submit` 自己也拦一道（见 `_require_single_image`）。

## 实测与文档不一致、不要「照文档改回去」的两处

- 文档给的网关是 `https://apihub.agnes-ai.com`，现有密钥打过去 401
  `Invalid token`；同一把密钥打 `https://api.agnes-ai.cn` 正常。
  **两套网关是两套密钥体系**，默认基址只能留在 `.cn`，换网关只改
  `AGNES_BASE_URL`。
- 文档主推的 `agnes-image-2.0-flash` 在当前密钥分组里 404
  `model_not_found`（"当前分组不支持模型"）；可用的是 2.1 与 2.5。
  声明一个调不通的模型，等于把「选错模型」从受理时的 400 变成提交后才失败，
  所以 `MODELS` 只列这两个。分组开通 2.0 后加一条 `ModelSpec` 即可。
- 图生图与多图合成实测均 200，产物路径由 `/images/t2i/` 变成
  `/images/i2i/`，`task_id` 形状不变。
- `1024x768` / `768x1024` / `1024x1536` / `1536x1024` / `2048x2048` 均可用；
  上游对非法尺寸**不报错**（`999x999` 也返回 200），所以这份枚举是我们替
  用户收敛的，不是照抄上游校验。
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, ClassVar

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
from modules.model_driver.spec import DriverMeta

logger = get_logger(__name__)

# 可选元数据：目录里显示用，不写也能跑。
META = DriverMeta(
    label="Agnes 生图",
    description="文生图 / 图生图 / 多图合成，同步出结果",
    version="2.0.0",
    author="",
)

# 当前密钥分组里实测可用的两个型号。换默认型号改 AGNES_MODEL，不用改代码。
_MODEL_25 = "agnes-image-2.5-flash"
_MODEL_21 = "agnes-image-2.1-flash"
_DEFAULT_MODEL = _MODEL_25

# 实测均可用的档位（含 1K / 2K）。上游不校验尺寸，见模块 docstring 末段。
_SIZE_OPTIONS = (
    "1024x1024",
    "1024x768",
    "768x1024",
    "1024x1536",
    "1536x1024",
    "2048x2048",
)

_PARAM_SCHEMA = {
    "size": {
        "type": "enum",
        "options": list(_SIZE_OPTIONS),
        "default": "1024x1024",
    },
    # 供应商只接受 n=1：传 2 或 4 会被 400 拒绝（实测 `n 必须为 1`）。
    # 这里的 max 是**能力声明**，`ImageService` 按它做受理前校验——
    # 声明成 4 会让「选 4 张」先受理成功、再异步失败，用户只看到一张裂图。
    "count": {"type": "int", "min": 1, "max": 1, "default": 1},
}

MODELS: tuple[ModelSpec, ...] = (
    ModelSpec(
        id=_MODEL_25,
        label="Agnes Image 2.5 Flash",
        capability=Capability.IMAGE,
        provider="agnes",
        param_schema=_PARAM_SCHEMA,
        description="文生图 / 图生图 / 多图合成，同步返回图片地址",
    ),
    ModelSpec(
        id=_MODEL_21,
        label="Agnes Image 2.1 Flash",
        capability=Capability.IMAGE,
        provider="agnes",
        param_schema=_PARAM_SCHEMA,
        description="文生图 / 图生图 / 多图合成，与 2.5 同价",
    ),
)


class AgnesSettings(DriverSettings):
    """Agnes 部署参数。环境变量前缀 `AGNES_`（与旧版同名，`.env` 不用改）。

    `submit_budget_for` 声明「这个 timeout 是 IMAGE 的单次提交耗时」，
    巡检阈值据此推导；不声明的话任何驱动（含成图下载）的 timeout
    都可能被聚合进来，把阈值拖到与生图无关的长度（R12-03）。
    """

    model_config = driver_config("AGNES")

    submit_budget_for: ClassVar[frozenset[str]] = frozenset({"image"})

    # 官方文档的网关是 apihub.agnes-ai.com，但那套网关要另一套密钥
    # （现有密钥打过去 401）；这里保留实测可用的 .cn 域名。
    base_url: str = "https://api.agnes-ai.cn"
    api_key: str = ""
    model: str = _DEFAULT_MODEL
    # 图片是同步出结果，超时要覆盖整个生成过程（实测 10-20 秒）。
    # 官方建议客户端超时 60-360 秒，180 秒对 2K 也够。
    timeout_sec: float = 180.0


def get_agnes_settings() -> AgnesSettings:
    return AgnesSettings()


class AgnesDriver:
    """同步生图：`submit` 等到出图，直接返回终态快照。"""

    name = "agnes"
    capabilities = frozenset({Capability.IMAGE})
    models = MODELS
    # 同步供应商：submit 直接给终态，没有可续查的任务 ID。
    # generation/recovery.py 据此不调 runner.resume()，否则 poll() 会把记录判死。
    supports_poll = False
    # 声明「生图默认选我」：默认模型不再由注册顺序决定。
    # 顺序是装配细节（infra 供应商先注册、drivers 后注册），把它当默认值来源，
    # 等于「谁先 import 谁说了算」——把 agnes 从 AI_PROVIDERS 挪进 drivers 就会
    # 把默认生图模型悄悄换成 echo 的 seedream-3，而没有任何一处报错。
    preferred_for = frozenset({Capability.IMAGE})

    async def submit(self, request: GenerationRequest) -> GenerationProgress:
        config = get_agnes_settings()
        if not config.api_key.strip():
            raise UpstreamError(
                "Agnes 未配置：请设置 AGNES_API_KEY（控制台创建的密钥），当前无法提交生图"
            )

        model = request.model or config.model
        _require_single_image(request.params.get("count", 1))
        references = _reference_images(request.reference_urls)

        # response_format 必须嵌在 extra_body 里；放顶层会被上游拒绝。
        extra: dict[str, Any] = {"response_format": "url"}
        if references:
            # 图生图 / 多图合成：图片数组同样在 extra_body 内，
            # 且**不要**附带 tags: ["img2img"]（文档明确说不需要）。
            extra["image"] = references

        payload: dict[str, Any] = {
            "model": model,
            "prompt": request.prompt,
            "size": str(request.params.get("size") or "1024x1024"),
            "extra_body": extra,
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
        first = _first_item(body)
        image_url = str(first.get("url") or "").strip()
        if not image_url:
            b64 = str(first.get("b64_json") or "")
            if b64:
                # 我们显式要了 url，还只拿到 Base64：多半是网关版本忽略了
                # response_format。Base64 动辄几 MB，塞进 output_url 会把
                # 下游的落盘/入库全拖垮，所以这里明确失败而不是硬塞。
                raise UpstreamError(
                    "Agnes 只返回了 Base64 图片（约 "
                    f"{len(b64) * 3 // 4 // 1024} KB），没有可直接落盘的地址："
                    "上游忽略了 extra_body.response_format=url，请检查网关版本"
                )
            # 200 但没有图：可能是内容审核未通过，也可能字段变了
            raise UpstreamError(f"Agnes 未返回图片地址：{str(body)[:200]}")

        logger.info(
            "Agnes 出图完成 model=%s mode=%s refs=%d task=%s",
            model,
            "i2i" if references else "t2i",
            len(references),
            body.get("task_id") or "-",
        )
        return GenerationProgress(
            external_id=str(body.get("task_id") or request.request_id or "agnes"),
            state=GenerationState.SUCCEEDED,
            progress=100,
            output_url=image_url,
            cover_url=image_url,
        )

    async def poll(self, external_id: str, *, model: str = "") -> GenerationProgress:
        """同步驱动没有中间态；走到这里说明上游数据被手工改过。"""
        return GenerationProgress(
            external_id=external_id,
            state=GenerationState.FAILED,
            error="Agnes 是同步驱动，不支持轮询；请重新提交",
            retryable=False,
        )

    async def cancel(self, external_id: str) -> None:
        return None


def _require_single_image(count: Any) -> None:
    """一次只能要 1 张图，受理前就拒绝。

    `ImageService` 会按 `param_schema` 的 `count.max=1` 先校验一遍，但
    `model_driver` 那条提交路径**不校验 param_schema**（它是通用门面，不知道
    每个模型声明的参数）。少了这道检查，`params={"count": 4}` 会被上游静默
    裁成 1 张——用户以为拿到了 4 张，账单上只有 1 张，还什么都没有报错。
    """
    try:
        value = int(count or 1)
    except (TypeError, ValueError) as exc:
        raise BadRequest(f"Agnes 的张数参数必须是整数，收到 {count!r}") from exc
    if value != 1:
        raise BadRequest(
            f"Agnes 一次只能生成 1 张图（收到 count={value}）：供应商限制 n=1，请逐张提交"
        )


def _reference_images(urls: Sequence[str]) -> list[str]:
    """参考图数组：保序、去空、去重。

    **顺序必须保留**：多图合成时提示词里的「第一张 / 第二张」指的就是这个
    顺序，重排会让提示词与图片错位，而且错得很隐蔽（出图正常，只是内容不对）。
    文档也接受 `data:image/png;base64,...` 的 Data URI，这里原样透传——
    「公网不可达就转 Base64」是调用方该做的判断，驱动不做隐式下载。
    """
    seen: set[str] = set()
    images: list[str] = []
    for raw in urls:
        item = str(raw or "").strip()
        if not item or item in seen:
            continue
        seen.add(item)
        images.append(item)
    return images


def _first_item(body: Any) -> dict[str, Any]:
    """取 `data[0]`；结构不符时返回空字典，交给调用处给出可读错误。"""
    items = body.get("data") if isinstance(body, dict) else None
    if isinstance(items, list) and items and isinstance(items[0], dict):
        return items[0]
    return {}


def _http_error(response: httpx.Response) -> Exception:
    """把上游状态码翻译成本项目的异常类型。

    分类依据是 runner 的计费口径：4xx 表示供应商明确拒绝（没受理，可安全重试或直接失败），
    5xx / 429 表示临时故障（可能已受理，交给框架的重试与「结果未知」逻辑）。

    `404` 单列：Agnes 用它表示「当前密钥分组不支持这个模型」。那是最典型的
    永久错误，落到 `UpstreamError` 会被重试三次、每次都失败得一样，最后还把
    真正的提示（"当前分组不支持模型 xxx"）盖掉。归到 `BadRequest` 后
    `runner._retryable` 直接判 false，用户第一次就能看到原因。
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
    if response.status_code == 404:
        return BadRequest(
            f"Agnes 不认这个模型：当前密钥的分组可能未开通{suffix}；"
            "可用模型见 GET /api/v1/models"
        )
    if response.status_code == 429:
        return RateLimited(f"Agnes 限流，请稍后重试{suffix}")
    if response.status_code >= 500:
        return UpstreamError(f"Agnes 服务异常{suffix}")
    if response.status_code == 400:
        return BadRequest(f"Agnes 参数校验失败{suffix}")
    return UpstreamError(f"Agnes 调用失败{suffix}")


Driver = AgnesDriver
