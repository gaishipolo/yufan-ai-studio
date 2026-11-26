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

`mode=reference` 的 `images` 收两种形态：**公网可达的 URL**（Agnes 服务端自己去拉）
与**内联 data URI**（`data:image/png;base64,...`）。后者是实测出来的：上游的文档
只写了 URL，但 2026-11-22 实测内联 data URI 一样受理、一样出片（轮询回包里有
`usage.input_image_count`），而我们本机的存储在内网、根本给不出公网地址。

所以这条链走**内联**：链路层把资产库里的字节读成 data URI，驱动原样透传。
本地文件、内网地址在提交前就拒绝——让上游回一句「图片下载失败」比在本地说清楚
要贵得多。

图片的边长另有硬限制：上游原话是
`input image side length must be between 256 and 5760 pixels`，所以受理期按资产库
里的宽高判一次、提交前按真实字节再判一次。
"""

from __future__ import annotations

import base64
from collections.abc import Mapping, Sequence
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
from core.prompt_refs import translate_or_original, translate_prompt_with
from core.reference_limits import check_side_lengths
from infra.ai.base import (
    Capability,
    GenerationProgress,
    GenerationRequest,
    GenerationState,
    ModelSpec,
    ReferenceLimit,
)
from infra.driver_config import DriverSettings, driver_config
from infra.media_channel import MediaChannel
from infra.media_probe import image_size
from modules.model_driver.kit.credentials import require_api_key
from modules.model_driver.kit.settings import vendor_settings
from modules.model_driver.kit.status import progress_from_status
from modules.model_driver.kit.submission_guard import guarded_submit
from modules.model_driver.spec import DriverMeta

logger = get_logger(__name__)

META = DriverMeta(
    label="Agnes 视频",
    description="Video 2.5 Flash：720P 文生视频（可带公网参考图），异步任务",
    version="1.0.0",
    author="",
)

MODEL_ID = "agnes-video-2.5-flash"
_MODEL_LABEL = "Agnes · Video 2.5 Flash"
_LEGACY_LABEL = "Agnes Video 2.5 Flash"

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

# 参考素材的上游上限（官方文档 + 2026-11-22 实测）：
#   images 最多 5 张、audios 最多 3 段，videos 不支持（传了直接 400）。
_MAX_REFERENCE_IMAGES = 5
_MAX_REFERENCE_AUDIOS = 3
#: 一条任务最多带几份参考素材（图片 + 音频）：上游两个数组各自的上限之和。
_MAX_REFERENCES = _MAX_REFERENCE_IMAGES + _MAX_REFERENCE_AUDIOS
#: 图片**单边**的像素范围。上游的原话是
#: `input image side length must be between 256 and 5760 pixels`（实测 64x64 被拒）。
#: 口径是**两条边都要**落在这个区间里：实测一张 200x3000（短边 200、长边 3000）
#: 同样被拒 —— 只判长边会把它放行。
_MIN_IMAGE_SIDE = 256
_MAX_IMAGE_SIDE = 5760
#: 参考素材的音频时长范围（官方「参考媒体限制」表：总时长 2-12 秒）。
#: 判两档：单段也要落在这个区间，且同类型**合计**不得超过 12 秒——
#: 三段各 5 秒单看都合法，合计 15 秒上游照样拒。
_MIN_AUDIO_SECONDS = 2.0
_MAX_AUDIO_SECONDS = 12.0
#: 单份参考素材的字节上限（官方：图片、音频单份均 < 15MB）。
#: base64 会把它撑大 4/3，15MB 的图进请求体约 20MB。
_MAX_REFERENCE_BYTES = 15 * 1024 * 1024
#: 同类**合计**字节上限（官方：图片整单 < 50MB、音频整单 < 64MB）。
#: 这一档不能省：五张 15MB 的图每张都合法，合计 75MB 却超过整单上限。
#: 注意我们走内联 data URI，请求体会是这里的 4/3，上游的 body 限制仍是未知数——
#: 所以按官方原始字节口径判，不按 base64 后口径放松。
_MAX_TOTAL_IMAGE_BYTES = 50 * 1024 * 1024
_MAX_TOTAL_AUDIO_BYTES = 64 * 1024 * 1024

#: Agnes 视频的素材指代写法：图片 `<Picture N>`、音频 `<Audio N>`。
#:
#: 上游的提示词里用尖括号标签指代素材（实测受理），不是「图1」这种中文说法。
#: 编号按类型各自从 1 开始（见 `core.prompt_refs.translate_prompt_with`），
#: 与 `images` / `audios` 两个数组的顺序一一对应。
_REFERENCE_LABELS = {"image": "<Picture {n}>", "audio": "<Audio {n}>"}

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
        aliases=(_LEGACY_LABEL,),
        capability=Capability.VIDEO,
        provider="agnes_video",
        param_schema=_PARAM_SCHEMA,
        description="720P 文生视频，4-12 秒；支持参考图与参考音频（当前限时免费）",
        # 参考素材走**内联 data URI**（见下面的 `inline_references`）：上游只收
        # 公网地址或 data URI，而我们的存储在内网、拿不出公网地址。
        # 声明成非 0，前端才会画出参考素材入口 —— 以前这里写 0，于是选这个
        # 模型时整块控件不画、后端受理期直接 400「模型不支持参考素材」。
        max_references=_MAX_REFERENCES,
        # 只收图片与音频：上游的 `videos` 数组传了直接 400。
        reference_kinds=("image", "audio"),
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


# 配置热生效（D3）：mtime 指纹缓存 —— 改 `.env` 下次请求即生效，
# 且不再每次 submit 都整读一遍 `.env`（原实现每次新建实例）。
get_agnes_video_settings = vendor_settings(AgnesVideoSettings, env_prefix="AGNES")


#: 上游状态词 → 平台状态。**只声明词表**，判定在 `kit/status.py`（唯一实现）。
#: 词表来源是实测：`completed` / `queued` / `running`，另收 `success` / `processing`
#: 这类同义写法（上游偶尔换词）。
_STATUS_MAP: Mapping[str, GenerationState] = {
    "completed": GenerationState.SUCCEEDED,
    "succeeded": GenerationState.SUCCEEDED,
    "success": GenerationState.SUCCEEDED,
    "failed": GenerationState.FAILED,
    "error": GenerationState.FAILED,
    "cancelled": GenerationState.FAILED,
    "canceled": GenerationState.FAILED,
    "queued": GenerationState.QUEUED,
    "pending": GenerationState.QUEUED,
    "waiting": GenerationState.QUEUED,
    "running": GenerationState.RUNNING,
    "processing": GenerationState.RUNNING,
    "in_progress": GenerationState.RUNNING,
    "generating": GenerationState.RUNNING,
}


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
    # 参考素材以**内联 data URI**交给上游（通道声明见 `infra/media_channel.py`）：
    # 上游只收公网地址或 `data:` URI，我们的存储在内网、拿不出公网地址。
    #
    # 旧的 `inline_references` 开关是「所有素材类型一个开关」，分不了类型；
    # 这里按 image / audio 分别声明走哪条通道。
    accepted_media_formats = {
        "image": frozenset({MediaChannel.BASE64}),
        "audio": frozenset({MediaChannel.BASE64}),
    }
    # 参考素材的硬性限制（见 `ReferenceLimitsCapable`）：图片最多 5 张、
    # 音频最多 3 段，图片单边 256-5760 像素、单份 15MB，音频 2-12 秒。
    # 受理期与提交前各判一次。
    reference_limits = {
        "image": ReferenceLimit(
            max_count=_MAX_REFERENCE_IMAGES,
            min_side=_MIN_IMAGE_SIDE,
            max_side=_MAX_IMAGE_SIDE,
            max_bytes=_MAX_REFERENCE_BYTES,
            max_total_bytes=_MAX_TOTAL_IMAGE_BYTES,
        ),
        "audio": ReferenceLimit(
            max_count=_MAX_REFERENCE_AUDIOS,
            max_bytes=_MAX_REFERENCE_BYTES,
            max_total_bytes=_MAX_TOTAL_AUDIO_BYTES,
            min_seconds=_MIN_AUDIO_SECONDS,
            max_seconds=_MAX_AUDIO_SECONDS,
            max_total_seconds=_MAX_AUDIO_SECONDS,
        ),
    }

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
        """提交视频任务：轮次键与三态落库交给 kit 的 `guarded_submit` 统一处理。"""
        return await guarded_submit(
            provider="agnes_video",
            record_id=request.request_id,
            model=request.model,
            channel_used=request.channel_used or None,
            call=lambda: self._submit_once(request),
        )

    async def _submit_once(self, request: GenerationRequest) -> str:
        if request.capability is not Capability.VIDEO:
            raise BadRequest("agnes_video 只负责视频生成；图片请走 agnes 驱动")
        # 缺密钥 = 确定没送达 → 4xx（kit 唯一判定口径，见 kit/credentials.py）。
        # 请求头在这里一次算好：既做缺密钥检查，也避免同一请求读两遍配置。
        headers = self._headers()

        seconds = _seconds(request.params.get("durationSec"))
        aspect = _aspect(request.params.get("aspectRatio"))
        # 参考素材：内联 data URI 或公网直链，拆成「图片」与「音频」两组。
        # 顺序保留 —— 提示词里的「第 2 张」指的就是这个顺序。
        media = _reference_media(request.reference_urls)
        images = [item for kind, item in media if kind == "image"]
        audios = [item for kind, item in media if kind == "audio"]
        # 提示词里的素材指代翻成上游认得的写法（`<Picture 1>` / `<Audio 1>`）。
        prompt = request.prompt
        if media:
            prompt = translate_or_original(
                request.prompt,
                [{"type": kind} for kind, _ in media],
                lambda text, entries: translate_prompt_with(
                    text, entries, _REFERENCE_LABELS
                ),
            )
        payload: dict[str, Any] = {
            "model": _upstream_model(request.model),
            "prompt": prompt,
            # 上游要字符串："5"，传整数会被拒
            "seconds": str(seconds),
            "mode": "text",
            # Flash 固定 720P，别的值直接 400（实测 `size 必须是 720P 之一`）
            "size": "720P",
            "aspect_ratio": aspect,
        }
        if media:
            payload["mode"] = "reference"
            if images:
                payload["images"] = images
            if audios:
                payload["audios"] = audios

        async with self._client() as client:
            # 不吞 httpx 的超时/连接异常：runner 的「超时 = 可能已受理」靠异常类型判断，
            # 自己包一层会丢掉这个语义，让一次已经计费的提交被当成没送达而重提。
            response = await client.post(
                f"{self._root()}/v1/videos",
                json=payload,
                headers=headers,
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
            len(media),
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

        # 没见过的状态：按进行中处理（墙钟超时兜底），但要留痕，
        # 否则上游加了新状态我们只能看到「一直转圈」而查不到原因。
        if status not in _STATUS_MAP:
            logger.warning("Agnes 返回未知视频状态 status=%r task=%s", status, task_id)
        return progress_from_status(
            external_id=task_id,
            raw_status=status,
            status_map=_STATUS_MAP,
            progress=progress,
            output_url=str(body.get("url") or "").strip(),
            error=_task_error(body) or "Agnes 视频生成失败（上游未给出原因）",
            missing_output_error="Agnes 任务已完成但没有返回视频地址，请到控制台核对",
            unknown_state=GenerationState.RUNNING,
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
        key = require_api_key(
            self.settings.api_key,
            vendor="Agnes",
            env_name="AGNES_API_KEY",
            note="与生图共用",
        )
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
    if not value or value in {MODEL_ID, _MODEL_LABEL, _LEGACY_LABEL}:
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


def _reference_media(urls: Sequence[str]) -> list[tuple[str, str]]:
    """把参考素材拆成「图片 / 音频」两类，保序去重。

    两种形态都收：

    - `data:image/...;base64,...` / `data:audio/...;base64,...`：**内联**的字节。
      这是我们唯一给得出的形态 —— 本机的存储在内网，供应商的取件链接又几分钟
      就过期（实测上游受理内联 data URI，并真的解了图、出了片）。
    - `http(s)://...`：公网直链，上游服务端会自己去拉。只能当图片（没有 MIME
      可判，音频直链没验证过）。

    按 MIME 分桶：上游的图片与音频分两个数组收（`images` / `audios`），
    提示词里的指代也是**分类型各自编号**（`<Picture 1>` / `<Audio 1>`），
    混在一个数组里就对不上号。

    顺序必须保留：提示词里的「第 2 张」指的就是上传顺序里的第 2 张。
    """
    seen: set[str] = set()
    media: list[tuple[str, str]] = []
    for raw in urls:
        item = str(raw or "").strip()
        if not item or item in seen:
            continue
        seen.add(item)
        if item.startswith("data:"):
            mime, data = _split_data_uri(item)
            if mime.startswith("image/"):
                _check_image_bytes(data)
                media.append(("image", item))
            elif mime.startswith("audio/"):
                if len(data) > _MAX_REFERENCE_BYTES:
                    raise BadRequest(
                        f"参考音频超过 {_MAX_REFERENCE_BYTES // (1024 * 1024)}MB"
                    )
                media.append(("audio", item))
            else:
                raise BadRequest(f"Agnes 参考素材只收图片与音频，收到 {mime}")
        elif urlsplit(item).scheme in {"http", "https"}:
            media.append(("image", item))
        else:
            raise BadRequest(
                "Agnes 参考素材必须是公网可访问的 http(s) 地址或内联 data URI，"
                f"收到 {item[:60]!r}"
            )
    images = sum(1 for kind, _ in media if kind == "image")
    audios = sum(1 for kind, _ in media if kind == "audio")
    if images > _MAX_REFERENCE_IMAGES:
        raise BadRequest(
            f"Agnes Video 2.5 Flash 最多 {_MAX_REFERENCE_IMAGES} 张参考图，收到 {images} 张"
        )
    if audios > _MAX_REFERENCE_AUDIOS:
        raise BadRequest(
            f"Agnes Video 2.5 Flash 最多 {_MAX_REFERENCE_AUDIOS} 段参考音频，收到 {audios} 段"
        )
    return media


def _split_data_uri(item: str) -> tuple[str, bytes]:
    """拆 `data:<mime>;base64,<payload>`，返回 (MIME, 字节)。"""
    head, _, payload = item.partition(",")
    if not payload or ";base64" not in head:
        raise BadRequest("参考素材的 data URI 必须是 base64 编码")
    mime = head[len("data:"):].split(";")[0].strip().lower()
    try:
        data = base64.b64decode(payload, validate=True)
    except ValueError as exc:
        raise BadRequest("参考素材的 data URI 不是合法的 base64") from exc
    return mime, data


def _check_image_bytes(data: bytes) -> None:
    """按**真实字节**判图片的体积与边长。

    受理期判过一次（资产库里的宽高），但那里可能是 0（历史行 / 未探测完），
    或者与实际文件对不上 —— 上游的判据是它自己解出来的边长。少了这道，
    用户拿到的是一句英文报错，而不是「换一张尺寸合适的图」。
    """
    if len(data) > _MAX_REFERENCE_BYTES:
        raise BadRequest(f"参考图超过 {_MAX_REFERENCE_BYTES // (1024 * 1024)}MB")
    width, height = image_size(data)
    check_side_lengths(
        label="",
        width=width,
        height=height,
        min_side=_MIN_IMAGE_SIDE,
        max_side=_MAX_IMAGE_SIDE,
    )


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
    # 未枚举的 4xx：供应商**明确拒绝**（没受理、不会计费）→ 落 4xx 侧，
    # 与生图驱动同口径。落 `UpstreamError`(502) 会被判成「可能已受理」→
    # 自动重试 3 次且把真实原因盖成「提交结果未知」。2026-11-24 行为修正。
    return BadRequest(f"Agnes 调用失败{suffix}")


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
