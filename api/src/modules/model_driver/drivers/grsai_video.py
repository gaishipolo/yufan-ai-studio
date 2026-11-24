"""Grsai 视频驱动（多模型聚合平台）。

上游文档：Grsai 图片/视频生成接口（apifox 工程 8212034）里的 wan-video 一节。
入口与生图完全相同（`POST /v1/api/generate`，按请求体的 `model` 分流），
异步结果查询同样是 `GET /v1/api/result?id=<任务号>`，所以公共层（配置、HTTP、
错误映射、素材拆分）直接复用 `grsai.py`。

为什么单独一个文件、而不是并进 `grsai.py`：

- `reference_limits` 是**驱动级**的，而生图收 4 张图、视频收 10 张图 + 5 段音频；
  目录接口（`GET /models`）会把驱动级的张数上限直接透给前端 —— 混在一个驱动里，
  前端看到的数字必然有一处是错的。
- 凭据是同一把（同一个 `GRSAI_` 前缀），但**能力不同**（生图 vs 视频），
  目录与前端要按能力分开展示。

三处与上游文档不一样、这里按实际情况处理的地方：

1. **只用 `replyType=async`**。上游的 json 模式会阻塞到出片（可达数分钟），
   HTTP 长连接挂在中间，一断就变成「提交结果未知」——钱可能花了、片丢了、
   还不许重试。async 下提交只是一次短往返，进度靠轮询收。
2. **`durationSec` → 上游的 `duration`**。平台的顶层字段叫 `durationSec`
   （避免与别的模型参数重名），上游叫 `duration`，这里做一次改名。
3. **不接上游的 `videos`（参考视频）与 `firstFrame` / `lastFrame`**：
   - 参考视频：框架的参考素材只内联**图片与音频**（`infra/reference_inline.py`
     的白名单里没有视频），视频参考走归档工作区那条路，这里拿不到可提交的
     素材；
   - 首尾帧：与 `images` / `audios` 互斥，属于另一套用法（首尾帧续写），
     当前平台的视频链路没有这个入口，半吊子支持比不支持更容易出错。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import httpx

from core.exceptions import BadRequest, UpstreamError
from core.logging import get_logger
from core.prompt_refs import translate_or_original, translate_prompt
from infra.ai.base import (
    Capability,
    GenerationProgress,
    GenerationRequest,
    GenerationState,
    ModelSpec,
    ReferenceLimit,
)
from infra.media_channel import MediaChannel
from modules.model_driver.drivers.grsai import (
    GENERATE_PATH,
    RESULT_PATH,
    GrsaiSettings,
    get_grsai_settings,
    grsai_first_url,
    grsai_headers,
    grsai_http_error,
    grsai_json,
    grsai_reference_media,
    grsai_root,
)
from modules.model_driver.spec import DriverMeta

logger = get_logger(__name__)

#: 驱动名 = 模型 id 前缀。配置前缀仍是 `GRSAI_`（同一把 key）。
PROVIDER = "grsai_video"

#: 上游模型名。
_UPSTREAM_MODEL = "wan-3.0"

#: 上游支持的比例（比平台顶层的三种多：21:9 / 4:3 / 3:4）。平台顶层字段
#: 只有竖屏 / 横屏 / 方形三种，所以这三档用户暂时选不到，但驱动侧按上游的
#: 全量校验 —— 万一将来平台放宽了枚举，这里不用再改。
_UPSTREAM_ASPECTS: frozenset[str] = frozenset(
    {"16:9", "9:16", "21:9", "4:3", "1:1", "3:4"}
)

#: 上游的分辨率档位。
_RESOLUTIONS: tuple[str, ...] = ("480p", "720p", "1080p")

#: 参考素材上限（上游文档原文）：图最多 10 张，音频最多 5 段、每段最长 15 秒。
_MAX_IMAGE_REFERENCES = 10
_MAX_AUDIO_REFERENCES = 5
_MAX_AUDIO_SECONDS = 15
#: 图 + 音频 = 模型级的参考素材总数上限（平台侧 `reference_library_ids`
#: 最多 20 个，这里 15 留了余量）。
_MAX_REFERENCES = _MAX_IMAGE_REFERENCES + _MAX_AUDIO_REFERENCES

#: 视频时长（秒）。上游支持 2~30 秒。
_MIN_DURATION_SEC = 2
_MAX_DURATION_SEC = 30


MODELS: tuple[ModelSpec, ...] = (
    ModelSpec(
        id=f"{PROVIDER}-{_UPSTREAM_MODEL}",
        label="Wan 3.0",
        capability=Capability.VIDEO,
        provider=PROVIDER,
        description=(
            "Grsai · wan-3.0。2~30 秒，480p / 720p / 1080p 三档分辨率；"
            f"最多 {_MAX_IMAGE_REFERENCES} 张参考图 + {_MAX_AUDIO_REFERENCES} 段参考音频"
            f"（每段最长 {_MAX_AUDIO_SECONDS} 秒）。"
            "画幅目前只有竖屏 / 横屏 / 方形三种（平台顶层的限制）。"
        ),
        param_schema={
            # 只声明**模型特有**参数（走 params）。`durationSec` 与 `aspectRatio`
            # 是平台的顶层字段，受理期单独成列：把它们写进 schema 会让前端多
            # 渲染一对控件（用户填了反而被受理期以「顶层字段不要放进 params」
            # 拒掉），还会让 schema 收敛出来的值盖掉用户在顶层选的那份。
            # 上游的时长/画幅限制因此放在驱动侧校验（提交前挡，比出片后失败早）。
            "resolution": {"type": "enum", "options": list(_RESOLUTIONS), "default": "720p"},
            "seed": {"type": "int"},
        },
        max_references=_MAX_REFERENCES,
        # 参考音频：上游收 mp3 等音频链接或 base64。
        reference_kinds=("image", "audio"),
    ),
)


class GrsaiVideoDriver:
    """Grsai 视频（wan-3.0）。异步形态：提交拿任务号，轮询拿产物地址。"""

    name = PROVIDER
    capabilities = frozenset({Capability.VIDEO})
    models = MODELS
    supports_poll = True
    # 视频动辄几分钟，5 秒一跳 poll 足够；再密只是白白打上游。
    poll_interval_sec = 5.0
    submit_timeout_sec = 30.0
    # 参考素材直接以 base64 提交，不必落盘物化。
    #
    # 通道声明（`infra/media_channel.py`）取代旧的 `inline_references` 开关：
    # 旧开关是「所有素材类型一个开关」，分不了类型。
    accepted_media_formats = {
        "image": frozenset({MediaChannel.BASE64}),
        "audio": frozenset({MediaChannel.BASE64}),
    }
    reference_limits = {
        "image": ReferenceLimit(max_count=_MAX_IMAGE_REFERENCES),
        "audio": ReferenceLimit(
            max_count=_MAX_AUDIO_REFERENCES, max_seconds=_MAX_AUDIO_SECONDS
        ),
    }

    def __init__(self, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.settings: GrsaiSettings = get_grsai_settings()
        self.transport = transport

    # ------------------------------------------------------------------ 提交
    async def submit(self, request: GenerationRequest) -> Any:
        if request.capability is not Capability.VIDEO:
            raise BadRequest("grsai_video 只负责视频生成；图片请走 grsai 驱动")

        media = grsai_reference_media(request.reference_urls)
        images = [item for kind, item in media if kind == "image"]
        audios = [item for kind, item in media if kind == "audio"]
        # 受理期会按驱动声明的 `reference_limits` 拦超量，但历史任务的重试
        # 路径可能绕过受理期 —— 驱动再兜一层，别让上游只回一句含糊的 400。
        if len(images) > _MAX_IMAGE_REFERENCES:
            raise BadRequest(
                f"Grsai 视频最多 {_MAX_IMAGE_REFERENCES} 张参考图，当前 {len(images)} 张"
            )
        if len(audios) > _MAX_AUDIO_REFERENCES:
            raise BadRequest(
                f"Grsai 视频最多 {_MAX_AUDIO_REFERENCES} 段参考音频，当前 {len(audios)} 段"
            )
        prompt = request.prompt
        if media:
            prompt = translate_or_original(
                request.prompt,
                [{"type": kind} for kind, _ in media],
                translate_prompt,
            )

        payload = _video_payload(request.params, prompt, images, audios)
        async with self._client() as client:
            response = await client.post(
                f"{grsai_root(self.settings)}{GENERATE_PATH}",
                json=payload,
                headers=grsai_headers(self.settings),
            )
        if response.status_code >= 400:
            raise grsai_http_error(response)

        body = grsai_json(response)
        task_id = str(body.get("id") or "").strip()
        if not task_id:
            raise UpstreamError("Grsai 没返回任务号，无法续查，请到控制台核对")
        status = str(body.get("status") or "").strip().lower()
        error = str(body.get("error") or "").strip()
        if status == "violation":
            raise BadRequest(f"Grsai 判定内容违规，未生成{_suffix(error)}")
        if status == "failed":
            # 上游在 200 的响应体里明确回报 failed = 它明确拒绝了这次请求
            # （没受理、不会计费）→ 落 4xx 一侧，框架才判「确定没送达」并允许
            # 安全重试；抛 5xx 会被判成「可能已受理」，用户只能去核对上游任务。
            raise BadRequest(f"Grsai 提交失败{_suffix(error)}")
        if status == "succeeded":
            url = grsai_first_url(body)
            if url:
                return GenerationProgress(
                    external_id=task_id,
                    state=GenerationState.SUCCEEDED,
                    progress=100,
                    output_url=url,
                )
        return task_id

    # ------------------------------------------------------------------ 轮询
    async def poll(self, external_id: str, *, model: str = "") -> GenerationProgress:
        task_id = (external_id or "").strip()
        if not task_id:
            raise BadRequest("Grsai 任务号为空，无法查询")
        async with self._client() as client:
            response = await client.get(
                f"{grsai_root(self.settings)}{RESULT_PATH}",
                params={"id": task_id},
                headers=grsai_headers(self.settings),
            )
        if response.status_code == 404:
            # 上游只保留 2 小时的结果，过期后查不到 —— 这是终态，不能当成
            # 临时故障一直重试到墙钟超时。
            return GenerationProgress(
                external_id=task_id,
                state=GenerationState.FAILED,
                error="Grsai 查不到这个任务（任务号无效，或结果已超过 2 小时）",
                retryable=False,
            )
        if response.status_code >= 400:
            raise grsai_http_error(response)

        body = grsai_json(response)
        status = str(body.get("status") or "").strip().lower()
        error = str(body.get("error") or "").strip()
        progress = _int(body.get("progress"))

        if status == "running":
            return GenerationProgress(
                external_id=task_id, state=GenerationState.RUNNING, progress=progress
            )
        if status == "succeeded":
            url = grsai_first_url(body)
            if not url:
                return GenerationProgress(
                    external_id=task_id,
                    state=GenerationState.FAILED,
                    progress=100,
                    error="Grsai 报告已完成但没给出产物地址，请到控制台核对",
                    retryable=False,
                )
            return GenerationProgress(
                external_id=task_id,
                state=GenerationState.SUCCEEDED,
                progress=100,
                output_url=url,
            )
        if status == "violation":
            return GenerationProgress(
                external_id=task_id,
                state=GenerationState.FAILED,
                progress=100,
                error=f"Grsai 判定内容违规{_suffix(error)}",
                retryable=False,
            )
        if status == "failed":
            return GenerationProgress(
                external_id=task_id,
                state=GenerationState.FAILED,
                progress=100,
                error=f"Grsai 生成失败{_suffix(error)}",
                retryable=False,
            )
        return GenerationProgress(
            external_id=task_id,
            state=GenerationState.FAILED,
            progress=progress,
            error=f"Grsai 返回了无法识别的任务状态（{status or '空'}）",
            retryable=False,
        )

    # ------------------------------------------------------------------ 取消
    async def cancel(self, external_id: str) -> None:
        """上游没有取消接口。明确拒绝，而不是假装取消：

        假装成功会让本地状态变「已取消」而上游照常出片计费，用户以为停掉了。
        """
        raise BadRequest("Grsai 任务暂不支持取消，请到 Grsai 控制台核对")

    # ------------------------------------------------------------------ 内部
    def _client(self) -> httpx.AsyncClient:
        # trust_env=False：本机常驻 HTTPS_PROXY（见 HANDOFF §6），
        # 让 httpx 读环境代理会把请求打到本机代理上拿 502。
        return httpx.AsyncClient(
            transport=self.transport,
            timeout=self.settings.timeout_sec,
            trust_env=False,
        )


# --------------------------------------------------------------------------- #
# 辅助
# --------------------------------------------------------------------------- #


def _video_payload(
    params: Mapping[str, Any],
    prompt: str,
    images: list[str],
    audios: list[str],
) -> dict[str, Any]:
    """组装上游请求体。

    上游要求 `prompt` / `aspectRatio` / `resolution` / `model` / `duration` /
    `replyType` 全部必填，所以这里不传「留空让上游自己猜」。
    """
    aspect = str(params.get("aspectRatio") or "9:16").strip()
    if aspect not in _UPSTREAM_ASPECTS:
        supported = ", ".join(sorted(_UPSTREAM_ASPECTS))
        raise BadRequest(f"Grsai 不支持画幅 {aspect}（支持：{supported}）")

    duration = _int(params.get("durationSec")) or 5
    if not _MIN_DURATION_SEC <= duration <= _MAX_DURATION_SEC:
        raise BadRequest(
            f"Grsai 视频时长要在 {_MIN_DURATION_SEC}~{_MAX_DURATION_SEC} 秒之间，当前 {duration} 秒"
        )

    resolution = str(params.get("resolution") or "720p").strip()
    if resolution not in _RESOLUTIONS:
        raise BadRequest(f"Grsai 不支持分辨率 {resolution}（支持：{', '.join(_RESOLUTIONS)}）")

    payload: dict[str, Any] = {
        "model": _UPSTREAM_MODEL,
        "prompt": prompt,
        "aspectRatio": aspect,
        "resolution": resolution,
        "duration": duration,
        "replyType": "async",
    }
    if images:
        payload["images"] = images
    if audios:
        payload["audios"] = audios
    seed = params.get("seed")
    if seed is not None and str(seed).strip():
        payload["seed"] = _int(seed)
    return payload


def _int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _suffix(error: str) -> str:
    """与 grsai.py 的同名函数逐字等价 → 两份都委托到 kit 的唯一实现。"""
    from modules.model_driver.kit.errors import suffix as kit_suffix

    return kit_suffix(error)


META = DriverMeta(
    label="Grsai 视频",
    description="Grsai 多模型聚合平台的视频系列（wan-3.0）。",
    version="1.0.0",
)

Driver = GrsaiVideoDriver
