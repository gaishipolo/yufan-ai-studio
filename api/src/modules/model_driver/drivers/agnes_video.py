"""Agnes Video 2.5 Flash 驱动：异步视频，与同步生图的 `agnes` 分成两个文件。

## 为什么不能并进 `drivers/agnes.py`

`supports_poll` 是**驱动级**声明：生图那条 `submit` 自己等到出图、直接返回终态快照
（`supports_poll = False`），视频是「提交拿任务号 → 轮询 → 终态」。塞进同一个驱动
只能二选一，另一边必然出错——同步的图被当成异步去轮询，或者异步的视频永远不轮询。
所以按「一个文件 = 一种调用形态」单列，共用同一把 `AGNES_API_KEY`
（环境变量前缀仍是 `AGNES_`，`.env` 不用改）。

## 契约（2026-11-11 对 api.agnes-ai.cn 实测 + 官方文档）

    POST {base}/v1/videos
      {"model":"agnes-video-2.5-flash","prompt":"...","seconds":"5",
       "mode":"text","size":"720P","aspect_ratio":"16:9"}
    → {"id": "...", "task_id": "...", "video_id": "..."}

    GET {根地址}/agnesapi?video_id=<id>&model_name=agnes-video-2.5-flash
    → {"status":"completed","progress":100,"url":"https://.../x.mp4", ...}

## 五处与文档不一致（实测为准，别照文档改回去）

1. **错误体不是 `{"detail": ...}`**。`.cn` 网关返回
   `{"code":"invalid_request","message":"size 必须是 720P 之一","data":{"param":"size"}}`；
   查询接口又是 `{"error":{"code":404,"message":"任务不存在"}}`。三种形状都要能解析，
   否则用户只看到「参数校验失败」，不知道是哪个参数错。
2. **队列满 = 503 + `code=video_queue_full`**，但它的含义是「没建任务、容量暂时不足」，
   不是「可能已受理」。所以**不能**按普通 5xx 归到 `UpstreamError`：runner 会把
   5xx 一律视为「请求可能已送达」，于是任务直接判失败、禁止自动重试，还把原因
   盖成「提交结果未知」（这是实测踩到的）。归 `RateLimited`（429）才对：
   4xx 侧 = 确定没受理（不怕重复计费），`retryable=True` = 队列一松自动补交。
3. **查询路径在根上**：`AGNES_BASE_URL` 是 `https://api.agnes-ai.cn`（生图驱动自己拼
   `/v1/images/generations`），视频查询是 `/agnesapi`。这里统一去掉尾部 `/v1`，
   两种写法的 `.env` 都能用。
4. **查询接口限频**：2 秒一问（框架默认节奏）会连续收到
   `429 查询过于频繁`，进度条卡在个位数。所以本驱动声明
   `poll_interval_sec = 10`，由 runner 按这个节奏问。
5. **提交侧的「队列满」是常态**：免费通道容量有限，503 `video_queue_full` 意味着
   「没建任务、只是没轮到」。驱动把它归成 `RateLimited`，runner 会按
   `GENERATION_THROTTLE_DELAYS` 排队重投（任务留在 `queued`），排满表才失败。

## 参考素材

`mode=reference` 的 `images` 必须是**公网可达**的 URL（Agnes 服务端自己去拉），
所以只有调用方明确给出 `reference_urls` 时才走参考模式；本地文件、内网地址在
提交前就拒绝——让上游回一句「图片下载失败」比在本地说清楚要贵得多。
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any
from urllib.parse import urlsplit

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

META = DriverMeta(
    label="Agnes 视频",
    description="Video 2.5 Flash：720P 文生视频（可带公网参考图），异步任务",
    version="1.0.0",
    author="",
)

MODEL_ID = "agnes-video-2.5-flash"
_MODEL_LABEL = "Agnes Video 2.5 Flash"

# 上游只认 "720P" 这一个尺寸，具体像素由 aspect_ratio 决定：
# 21:9→1680x720、16:9→1280x704、4:3→960x720、1:1→720x720、3:4→720x960、9:16→720x1280。
_ASPECT_OPTIONS = ("21:9", "16:9", "4:3", "1:1", "3:4", "9:16")

# 但**目录里只能列平台枚举支持的三种**：`core.enums.AspectRatio` 只有
# 9:16 / 16:9 / 1:1，`CreateTaskIn.aspectRatio` 是那个枚举。把 21:9 写进
# param_schema 会让前端画出这个选项、提交时被 422 拒掉（「能选不能交」）。
# 要放开得同时改 core.enums、前端 `AspectRatio` 联合类型与四份契约生成物，
# 属于契约变更，留待拍板；驱动侧的 `_aspect` 仍按上游全量校验，
# 以便将来经 `model_driver` 直连时不用再改这里。
_SCHEMA_ASPECTS = ("9:16", "16:9", "1:1")
_DEFAULT_ASPECT = "16:9"

# 实测与文档一致：seconds 只收 "4"-"12" 的字符串（传整数或 20 都会被 400）。
_MIN_SECONDS = 4
_MAX_SECONDS = 12
_DEFAULT_SECONDS = 5

# Flash 的参考图上限（音频 3 段、视频不支持，本期没有对应输入通道）。
_MAX_REFERENCE_IMAGES = 5

_PARAM_SCHEMA = {
    # 键名跟 `modules/task/params.py` 的校验口径对齐（durationSec / aspectRatio），
    # 前端 CreateTaskModal 直接按这份 schema 渲染下拉，加模型不需要改前端。
    "durationSec": {
        "type": "int",
        "min": _MIN_SECONDS,
        "max": _MAX_SECONDS,
        # 必须显式声明 step：前端的档位展开在缺 step 时按 5 秒跳（5/10/15 的历史约定），
        # 不写的话 4-12 只会渲染出「4 / 9 / 12」三档，中间的秒数全都选不到。
        "step": 1,
        "default": _DEFAULT_SECONDS,
    },
    "aspectRatio": {
        "type": "enum",
        "options": list(_SCHEMA_ASPECTS),
        "default": _DEFAULT_ASPECT,
    },
}

MODELS: tuple[ModelSpec, ...] = (
    ModelSpec(
        id=MODEL_ID,
        label=_MODEL_LABEL,
        capability=Capability.VIDEO,
        provider="agnes_video",
        param_schema=_PARAM_SCHEMA,
        description="720P 文生视频，4-12 秒；当前限时免费（刊例 $0.025/秒）",
        # 参考图要走公网 URL，而现役视频任务没有解析 reference_asset_ids 的通道
        # （`task/pipeline.py` 不传 reference_urls），所以这里声明 0：
        # 声明成 5 会让前端画出一个「传了也不会被用」的上传框。
        max_references=0,
    ),
)


class AgnesVideoSettings(DriverSettings):
    """与生图共用同一个 `AGNES_` 前缀：同一把密钥、同一个网关。

    单独建类而不是复用 `AgnesSettings`：那边 `submit_budget_for` 声明的是
    「生图同步提交耗时」，视频提交只是一次 HTTP 往返，混在一起会把预算阈值拖歪。
    """

    model_config = driver_config("AGNES")

    base_url: str = "https://api.agnes-ai.cn"
    api_key: str = ""
    # 提交与查询都是一次短往返；真正等视频的是轮询，不占这里。
    timeout_sec: float = 30.0


def get_agnes_video_settings() -> AgnesVideoSettings:
    return AgnesVideoSettings()


class AgnesVideoDriver:
    """异步视频：`submit` 只拿任务号，结果由框架按节奏调 `poll` 收。"""

    name = "agnes_video"
    capabilities = frozenset({Capability.VIDEO})
    models = MODELS
    # 异步供应商：必须显式声明，缺省按 False 处理（恢复器会把在途任务判死）。
    supports_poll = True
    # 查询接口对频率敏感：2 秒一问（框架默认）会被 429「查询过于频繁」挡回来，
    # 退避到 30 秒才勉强过。视频本来就要跑几分钟，10 秒一问足够灵敏也不挨限流。
    poll_interval_sec = 10.0
    # 声明这个供应商是视频能力的默认首选。
    #
    # 之前刻意不声明，因为默认视频模型是 echo 的 `seedance-2.5`——**那是占位
    # 模型**（`placeholder=True`，实现是假的，不调外部 API）。现在占位模型
    # 已经被挡在公开目录外、也不再当默认；三个 `*-replacement` 虽然从
    # 2026-11-13 起也公开了，却要收参考素材、还要跑长耗时的上传准备，
    # 不适合「用户不传 model 时替他选的那个」。
    #
    # 结果就是：默认必须落在这个开箱即用的文生视频模型上，由它接任默认。
    preferred_for = frozenset({Capability.VIDEO})

    def __init__(
        self,
        *,
        settings: AgnesVideoSettings | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.settings = settings or get_agnes_video_settings()
        self.transport = transport

    # ------------------------------------------------------------------ 提交
    async def submit(self, request: GenerationRequest) -> str:
        if request.capability is not Capability.VIDEO:
            raise BadRequest("agnes_video 只负责视频生成；图片请走 agnes 驱动")
        config = self.settings
        if not config.api_key.strip():
            raise UpstreamError(
                "Agnes 未配置：请设置 AGNES_API_KEY（与生图共用），当前无法提交视频"
            )

        seconds = _seconds(request.params.get("durationSec"))
        aspect = _aspect(request.params.get("aspectRatio"))
        payload: dict[str, Any] = {
            "model": _upstream_model(request.model),
            "prompt": request.prompt,
            # 上游要字符串："5"，传整数会被拒
            "seconds": str(seconds),
            "mode": "text",
            # Flash 固定 720P，别的值直接 400（实测 `size 必须是 720P 之一`）
            "size": "720P",
            "aspect_ratio": aspect,
        }
        references = _reference_images(request.reference_urls)
        if references:
            payload["mode"] = "reference"
            payload["images"] = references

        async with self._client() as client:
            # 不吞 httpx 的超时/连接异常：runner 的「超时 = 可能已受理」靠异常类型判断，
            # 自己包一层会丢掉这个语义，让一次已经计费的提交被当成没送达而重提。
            response = await client.post(
                f"{self._root()}/v1/videos",
                json=payload,
                headers=self._headers(),
            )
        if response.status_code >= 400:
            raise _http_error(response)

        body = _json_body(response)
        video_id = _video_id(body)
        if not video_id:
            raise UpstreamError(f"Agnes 未返回视频任务号：{str(body)[:200]}")
        logger.info(
            "Agnes 视频已提交 model=%s seconds=%s aspect=%s refs=%d task=%s",
            payload["model"],
            seconds,
            aspect,
            len(references),
            video_id,
        )
        return video_id

    # ------------------------------------------------------------------ 轮询
    async def poll(self, external_id: str, *, model: str = "") -> GenerationProgress:
        task_id = (external_id or "").strip()
        if not task_id:
            raise BadRequest("Agnes 视频任务号为空，无法查询")
        # 带 model_name 的查询对 text / keyframe / reference 全部适用；
        # 不带 model_name 的写法只对 text 任务有效，所以这里始终带上。
        params = {"video_id": task_id, "model_name": _upstream_model(model)}

        async with self._client() as client:
            response = await client.get(
                f"{self._root()}/agnesapi", params=params, headers=self._headers()
            )
        if response.status_code == 404:
            # 任务号不存在/已过期是终态，不能当成临时故障一直重试到 30 分钟超时
            return GenerationProgress(
                external_id=task_id,
                state=GenerationState.FAILED,
                error="Agnes 查不到这个视频任务（任务号无效或已过期）",
                retryable=False,
            )
        if response.status_code >= 400:
            raise _http_error(response)

        body = _json_body(response)
        status = str(body.get("status") or "").strip().lower()
        progress = _progress(body)

        if status in {"completed", "succeeded", "success"}:
            url = str(body.get("url") or "").strip()
            if not url:
                return GenerationProgress(
                    external_id=task_id,
                    state=GenerationState.FAILED,
                    progress=100,
                    error="Agnes 任务已完成但没有返回视频地址，请到控制台核对",
                    retryable=False,
                )
            return GenerationProgress(
                external_id=task_id,
                state=GenerationState.SUCCEEDED,
                progress=100,
                output_url=url,
            )
        if status in {"failed", "error", "cancelled", "canceled"}:
            return GenerationProgress(
                external_id=task_id,
                state=GenerationState.FAILED,
                progress=progress,
                error=_task_error(body) or "Agnes 视频生成失败（上游未给出原因）",
                retryable=False,
            )
        if status in {"queued", "pending", "waiting"}:
            return GenerationProgress(
                external_id=task_id, state=GenerationState.QUEUED, progress=progress
            )
        if status in {"running", "processing", "in_progress", "generating"}:
            return GenerationProgress(
                external_id=task_id, state=GenerationState.RUNNING, progress=progress
            )
        # 没见过的状态：按进行中处理（墙钟超时兜底），但要留痕，
        # 否则上游加了新状态我们只能看到「一直转圈」而查不到原因。
        logger.warning("Agnes 返回未知视频状态 status=%r task=%s", status, task_id)
        return GenerationProgress(
            external_id=task_id, state=GenerationState.RUNNING, progress=progress
        )

    async def cancel(self, external_id: str) -> None:
        """上游没有取消接口。明确拒绝，而不是假装取消：

        假装成功会让本地状态变「已取消」而上游照常出片计费，用户以为停掉了。
        """
        raise BadRequest("Agnes 视频任务暂不支持取消，请到 Agnes 控制台核对")

    # ------------------------------------------------------------------ 内部
    def _client(self) -> httpx.AsyncClient:
        # trust_env=False：本机常驻 HTTPS_PROXY（见 HANDOFF §6），
        # 让 httpx 读环境代理会把请求打到本机代理上拿 502。
        return httpx.AsyncClient(
            transport=self.transport,
            timeout=self.settings.timeout_sec,
            trust_env=False,
        )

    def _headers(self) -> dict[str, str]:
        key = self.settings.api_key.strip()
        if not key:
            raise UpstreamError("Agnes 未配置：请设置 AGNES_API_KEY（与生图共用）")
        return {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}

    def _root(self) -> str:
        """网关根地址：`AGNES_BASE_URL` 写成带 `/v1` 或不带都能用。"""
        root = self.settings.base_url.rstrip("/")
        if root.endswith("/v1"):
            root = root[: -len("/v1")]
        return root


def _upstream_model(model: str) -> str:
    """目录 id / 展示名 → 上游模型名。

    轮询拿到的 `model` 来自任务记录：新数据存目录 id，历史数据可能只剩展示名
    （`ModelRouter` 两种都认）。两种都映射到同一个上游名，别让恢复链因为
    记录里存的是 label 而查不到任务。
    """
    value = (model or "").strip()
    if not value or value in {MODEL_ID, _MODEL_LABEL}:
        return MODEL_ID
    raise BadRequest(f"agnes_video 驱动不认识模型 {model!r}，可用：{MODEL_ID}")


def _seconds(value: Any) -> int:
    """时长校验：4-12 秒的整数。

    通用门面（`model_driver` 的提交路径）不校验 `param_schema`，所以驱动自己要拦：
    少了这道，`durationSec=30` 会被上游 400，而错误信息是 `seconds 必须在 [4, 12] 范围内`——
    用户看到的是英文参数名，不知道该改哪个控件。
    """
    try:
        seconds = int(value if value is not None else _DEFAULT_SECONDS)
    except (TypeError, ValueError) as exc:
        raise BadRequest(f"Agnes 视频时长必须是整数秒，收到 {value!r}") from exc
    if not _MIN_SECONDS <= seconds <= _MAX_SECONDS:
        raise BadRequest(
            f"Agnes Video 2.5 Flash 只支持 {_MIN_SECONDS}-{_MAX_SECONDS} 秒（收到 {seconds}s）",
            details={"field": "durationSec", "min": _MIN_SECONDS, "max": _MAX_SECONDS},
        )
    return seconds


def _aspect(value: Any) -> str:
    aspect = str(value or _DEFAULT_ASPECT).strip()
    if aspect not in _ASPECT_OPTIONS:
        raise BadRequest(
            f"Agnes 不支持画幅 {aspect!r}，可选：{', '.join(_ASPECT_OPTIONS)}",
            details={"field": "aspectRatio", "options": list(_ASPECT_OPTIONS)},
        )
    return aspect


def _reference_images(urls: Sequence[str]) -> list[str]:
    """参考图：只收公网 http(s) 地址，保序去重，最多 5 张。

    不把本地存储的签名地址透传进来：Agnes 服务端拉不到我们的内网对象存储，
    会在提交后回一句「图片下载失败」，而那时任务已经建了、队列也占了。
    """
    seen: set[str] = set()
    images: list[str] = []
    for raw in urls:
        item = str(raw or "").strip()
        if not item or item in seen:
            continue
        if urlsplit(item).scheme not in {"http", "https"}:
            raise BadRequest(
                "Agnes 参考图必须是公网可访问的 http(s) 地址（服务端会主动下载），"
                f"收到 {item[:60]!r}"
            )
        seen.add(item)
        images.append(item)
    if len(images) > _MAX_REFERENCE_IMAGES:
        raise BadRequest(
            f"Agnes Video 2.5 Flash 最多 {_MAX_REFERENCE_IMAGES} 张参考图，收到 {len(images)} 张"
        )
    return images


def _video_id(body: dict[str, Any]) -> str:
    """取任务号：文档说用 `video_id` 查询，但创建响应同时有 `id` / `task_id`。

    三个都试一遍，是因为这一层最怕「上游某天只回其中一个」把提交结果丢掉——
    那时提交已经发生、可能已经计费，而我们连任务号都没存下来。
    """
    candidates: list[Any] = [body.get("video_id"), body.get("id"), body.get("task_id")]
    data = body.get("data")
    if isinstance(data, dict):
        candidates += [data.get("video_id"), data.get("id"), data.get("task_id")]
    for value in candidates:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _progress(body: dict[str, Any]) -> int:
    try:
        return int(body.get("progress") or 0)
    except (TypeError, ValueError):
        return 0


def _task_error(body: dict[str, Any]) -> str:
    """失败任务的 `error` 可能是字符串、也可能是 `{code, message}`。"""
    error = body.get("error")
    if isinstance(error, dict):
        return str(error.get("message") or error.get("code") or "").strip()
    if error:
        return str(error).strip()
    return str(body.get("message") or "").strip()


def _json_body(response: httpx.Response) -> dict[str, Any]:
    try:
        body = response.json()
    except ValueError as exc:
        raise UpstreamError(f"Agnes 返回了非 JSON 响应：{response.text[:200]}") from exc
    if not isinstance(body, dict):
        raise UpstreamError(f"Agnes 返回结构异常：{str(body)[:200]}")
    return body


def _http_error(response: httpx.Response) -> Exception:
    """把上游状态码翻译成本项目的异常。

    分类依据与生图驱动一致：4xx = 供应商明确拒绝（可安全失败），
    429/5xx = 临时故障（交给框架退避重试）。

    `detail` 的取法要兼容三套形状：
    `{error:{message}}`（查询接口）、`{code, message, data}`（`.cn` 网关）、
    `{detail}`（官方文档示例）。
    """
    body: Any = None
    try:
        body = response.json()
    except ValueError:
        body = None
    detail = _error_detail(body) or response.text[:200]
    code = _error_code(body)
    suffix = f"（HTTP {response.status_code}{' ' + code if code else ''}）{detail}"

    if response.status_code == 401:
        return Unauthorized(f"Agnes 鉴权失败：请检查 AGNES_API_KEY{suffix}")
    if response.status_code == 403:
        return Forbidden(f"Agnes 拒绝访问：当前密钥无权调用该模型{suffix}")
    if response.status_code == 404:
        return BadRequest(
            f"Agnes 不认这个模型或任务：当前密钥分组可能未开通视频模型{suffix}；"
            "可用模型见 GET /api/v1/models"
        )
    if response.status_code == 429:
        return RateLimited(f"Agnes 限流，请稍后重试{suffix}")
    if response.status_code >= 500:
        if code == "video_queue_full":
            # 上游明确说「队列满、没建任务」，这是**容量不足**（429 语义），
            # 不是「可能已受理」。归 `UpstreamError`（502）会被 runner 的
            # `_possibly_delivered` 判成「请求可能已送达」→ 任务直接判失败、
            # 禁止自动重试，并且把这条真正的原因盖成「提交结果未知」（实测过）。
            # 归 429 后：确认没受理（4xx → 不担心重复计费）+ 可重试，
            # 队列一松就自动补交，用户不再看到一句误导性的「请核对上游」。
            return RateLimited(
                f"{detail or 'Agnes 视频队列已满，请稍后重试'}（HTTP 503 {code}）"
            )
        return UpstreamError(f"Agnes 服务暂时不可用{suffix}")
    if response.status_code == 400:
        return BadRequest(f"Agnes 参数校验失败{suffix}")
    return UpstreamError(f"Agnes 调用失败{suffix}")


def _error_detail(body: Any) -> str:
    if not isinstance(body, dict):
        return ""
    error = body.get("error")
    if isinstance(error, dict):
        return str(error.get("message") or error.get("code") or "").strip()
    if error:
        return str(error).strip()
    for key in ("message", "detail", "msg"):
        value = body.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _error_code(body: Any) -> str:
    if not isinstance(body, dict):
        return ""
    code = body.get("code")
    # `code` 可能是 HTTP 数字（查询接口的 error.code 已在上面取过 message），
    # 这里只要「错误的业务码」，数字码不再重复展示。
    return code if isinstance(code, str) else ""


Driver = AgnesVideoDriver
