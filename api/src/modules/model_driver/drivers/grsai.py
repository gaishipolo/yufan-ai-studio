"""Grsai 生图驱动（多模型聚合平台）。

上游文档：Grsai 图片/视频生成接口（apifox 工程 8212034）。两个生图系列
—— nano-banana 系与 gpt-image 系 —— 共用同一个入口 `POST /v1/api/generate`，
按请求体里的 `model` 分流；异步结果查询走 `GET /v1/api/result?id=<任务号>`。

本文件同时是 Grsai 的**公共层**（配置、HTTP、错误映射、素材拆分），视频侧在
`grsai_video.py` 里复用同一套。

四处与上游文档不一样、这里按实际情况处理的地方：

1. **一律用 `replyType=async`**。文档给了 json / stream / async 三种回复类型，
   json 会让上游一直阻塞到出图（视频可达数分钟）：HTTP 长连接挂在中间，
   一断就变成「提交结果未知」——钱可能花了、图丢了、还不许重试。async 下
   提交只是一次短往返，进度靠轮询收。
2. **模型名要剥前缀**。我们的模型 id 必须是 `grsai-<上游名>`（模型 id 全局
   唯一，不同供应商可能撞名），发给上游时还原成 `<上游名>`。
3. **生成与视频拆成两个驱动文件**。理由不是形态（两边都是异步），而是
   `reference_limits` 是**驱动级**的：生图参考图收 4 张，视频收 10 张图 +
   5 段音频，而目录接口会把驱动级的张数上限直接透给前端 —— 混在一个驱动里，
   前端看到的数字必然有一处是错的。
4. **画幅走 `aspectRatio`，不碰平台的 `size`（像素值）**。上游的画幅字段就叫
   `aspectRatio`，但两族要的东西不同：nano-banana 系只收比例（`16:9`），
   gpt-image 的 vip / flare / sunburst 只收像素值（`1024x1024`）—— 平台的
   `size` 只有一个像素值集合，给哪一族都不对，所以按型号分别声明枚举，
   不做换算（换算表漏一档，就会把用户要的画幅悄悄换成另一个）。
   顺带一提：平台的 `size` / `count` 是历史顶层字段，受理期会从参数里剔掉
   单独存列（`ImageJob.size`），**驱动根本拿不到** —— 所以画幅只能自己声明。
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

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
from infra.driver_config import DriverSettings, driver_config
from infra.media_channel import MediaChannel
from infra.reference_inline import MIME_BY_SUFFIX
from modules.model_driver.kit.credentials import require_api_key
from modules.model_driver.kit.errors import classify_http_error
from modules.model_driver.kit.errors import suffix as kit_suffix
from modules.model_driver.kit.settings import vendor_settings
from modules.model_driver.kit.submission_guard import guarded_submit
from modules.model_driver.spec import DriverMeta

logger = get_logger(__name__)

#: 驱动名 = 模型 id 前缀 = 配置前缀（`GRSAI_`）。
PROVIDER = "grsai"

#: 上游统一入口：三个系列共用，按请求体的 `model` 分流。
GENERATE_PATH = "/v1/api/generate"
#: 异步结果查询（任务出最终结果后只保留 2 小时）。
RESULT_PATH = "/v1/api/result"

#: 上游的四种任务状态。
_STATUS_RUNNING = "running"
_STATUS_SUCCEEDED = "succeeded"
_STATUS_FAILED = "failed"
_STATUS_VIOLATION = "violation"

#: 生图参考图张数上限。上游文档没给（只说「支持 base64 与 url 链接」），
#: 这里取 4 张 —— 保守值：宁可在我们这一侧拦住，也不要把上游的 400 原样透给
#: 用户（上游没说是张数问题还是格式问题）。
_MAX_IMAGE_REFERENCES = 4


# --------------------------------------------------------------------------- #
# 配置
# --------------------------------------------------------------------------- #


class GrsaiSettings(DriverSettings):
    """Grsai 部署参数，环境变量前缀 `GRSAI_`。

    `base_url` 默认走全球节点；国内节点是 `https://grsai.dakka.com.cn`
    （同一个 key 通用），需要时改环境变量即可。
    """

    model_config = driver_config("GRSAI")

    base_url: str = "https://grsaiapi.com"
    api_key: str = ""
    timeout_sec: float = 30.0


# 配置热生效（D3）：mtime 指纹缓存替代原 lru_cache，改 .env 下次请求即生效。
# getter 名字与签名不变，测试里的 monkeypatch 与调用点都无需改。
get_grsai_settings = vendor_settings(GrsaiSettings, env_prefix="GRSAI")


# --------------------------------------------------------------------------- #
# 公共层（视频侧复用）
# --------------------------------------------------------------------------- #


def grsai_root(settings: GrsaiSettings) -> str:
    """网关根地址：写成带不带尾部斜杠都能用。"""
    return settings.base_url.rstrip("/")


def grsai_headers(settings: GrsaiSettings) -> dict[str, str]:
    """组装请求头。

    缺 key 时抛 `BadRequest`（4xx）而不是 `UpstreamError`（5xx）—— 两者在框架
    里的待遇差很远：4xx 判「确定没送达」，清掉提交意图标记、允许安全重试，
    并把**真实原因**给用户；5xx 判「可能已受理」，于是改写成「提交结果未知：
    请求已发出但未确认供应商是否受理」，用户被引导去核对上游任务 —— 而请求
    压根没发出去，上游根本没有任务可核对。

    报错类型不是随便选的：`_possibly_delivered` 按 HTTP 语义分类，5xx/408
    一律按「可能已送达」处理（宁可让任务失败等人核对，也不赌对方没收到）。
    本地配置缺失属于「确定没发出」，必须落在 4xx 这一侧。
    """
    key = require_api_key(
        settings.api_key,
        vendor="Grsai",
        env_name="GRSAI_API_KEY",
        note="控制台创建的密钥",
    )
    return {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}


def grsai_http_error(response: httpx.Response) -> Exception:
    """把上游 HTTP 错误映射成框架认识的异常。

    分类**实现**在 `kit/errors.py`（唯一一份）；这里只声明 Grsai 的口径：
    4xx（含 408）一律 BadRequest、5xx 才 UpstreamError —— 与 agnes 的
    「401→Unauthorized / 429→RateLimited」不同，是业务语义差异，不抹平。
    """
    return classify_http_error(
        response,
        vendor="Grsai",
        status_map={408: BadRequest},  # kit 默认把 408 归 5xx 侧；grsai 显式覆盖
    )


def grsai_json(response: httpx.Response) -> dict[str, Any]:
    """解析响应体；不是 JSON 对象就当上游异常（不是我们的请求写错了）。"""
    try:
        body = response.json()
    except ValueError as exc:
        raise UpstreamError("Grsai 返回的不是 JSON，无法解析") from exc
    if not isinstance(body, dict):
        raise UpstreamError("Grsai 返回的 JSON 不是对象")
    return body


def grsai_media_kind(url: str) -> str:
    """参考素材的类型：`image` / `audio` / `video`，认不出来返回空串。

    data URI 看 MIME 前缀，公网直链看后缀。后缀表复用上传白名单那份
    （`infra.reference_inline.MIME_BY_SUFFIX`），不另抄一份：上传侧加了新
    后缀、内联侧不知道，就会变成「用户传进库了、提交时才报格式不支持」。
    """
    value = (url or "").strip()
    if not value:
        return ""
    if value.lower().startswith("data:"):
        mime = value[5:].split(";", 1)[0].strip().lower()
        for kind in ("image", "audio", "video"):
            if mime.startswith(f"{kind}/"):
                return kind
        return ""
    suffix = Path(urlparse(value).path).suffix.lower()
    mime = MIME_BY_SUFFIX.get(suffix, "")
    for kind in ("image", "audio"):
        if mime.startswith(f"{kind}/"):
            return kind
    return ""


def grsai_reference_media(reference_urls: tuple[str, ...]) -> list[tuple[str, str]]:
    """把参考素材按类型拆开，顺序保留。

    顺序必须与上传顺序一致 —— 提示词里的「第 2 张图」指的就是这个顺序，
    重排会让提示词与素材整体错位，而且错得很隐蔽（照常出片，只是内容不对）。

    认不出类型的素材**直接报错**而不是跳过：跳过后这份素材不会上传，而提示词
    里的 `{{Mixed N}}` 编号是按上传顺序算的 —— 少一个素材，后面每一个编号都会
    指错对象，出片照样成功，只是内容不对（最难查的一类错）。
    """
    media: list[tuple[str, str]] = []
    for url in reference_urls:
        kind = grsai_media_kind(url)
        if kind == "video":
            raise BadRequest("Grsai 暂不支持参考视频，请改用参考图或参考音频")
        if kind not in ("image", "audio"):
            raise BadRequest(
                f"Grsai 认不出参考素材的格式（只支持图片与音频）：{url[:80]}"
            )
        media.append((kind, url))
    logger.debug(
        "Grsai 参考素材拆分：%d 张图 / %d 段音频",
        sum(1 for kind, _ in media if kind == "image"),
        sum(1 for kind, _ in media if kind == "audio"),
    )
    return media


# --------------------------------------------------------------------------- #
# 模型目录
# --------------------------------------------------------------------------- #

#: nano-banana 系支持的比例（上游文档原表）。
_NANO_ASPECTS: tuple[str, ...] = (
    "auto",
    "1:1",
    "16:9",
    "9:16",
    "4:3",
    "3:4",
    "3:2",
    "2:3",
    "5:4",
    "4:5",
    "21:9",
)
#: 「nano-banana-2 系列额外支持比例」（上游文档原话）。2.1 按同系列算。
_NANO2_EXTRA_ASPECTS: tuple[str, ...] = ("1:4", "4:1", "1:8", "8:1")

#: gpt-image-2 / 2.5 / 2.6 支持「比例格式」，取值抄自上游文档的比例参考表。
_GPT_RATIO_ASPECTS: tuple[str, ...] = (
    "auto",
    "1:1",
    "16:9",
    "9:16",
    "4:3",
    "3:4",
    "3:2",
    "2:3",
    "5:4",
    "4:5",
    "21:9",
    "9:21",
    "1:2",
    "2:1",
)
#: vip / flare / sunburst **不支持比例格式**，只收 1K–4K 像素值。
#: 逐条抄自上游文档的「比例参考（1K、2K、4K）」表（含它自己就不齐的
#: 1:3 / 3:1 两档 —— 文档只给了两档，这里照抄，不替上游补第三档）。
_GPT_PIXEL_ASPECTS: tuple[str, ...] = (
    "auto",
    "1024x1024", "2048x2048", "2880x2880",
    "1280x720", "2048x1152", "3840x2160",
    "720x1280", "1152x2048", "2160x3840",
    "1152x864", "2304x1728", "3264x2448",
    "864x1152", "1728x2304", "2448x3264",
    "1536x1024", "2048x1360", "3504x2336",
    "1024x1536", "1360x2048", "2336x3504",
    "1120x896", "2240x1792", "3200x2560",
    "896x1120", "1792x2240", "2560x3200",
    "1456x624", "2912x1248", "3840x1648",
    "624x1456", "1248x2912", "1648x3840",
    "688x2048", "1280x3840",
    "2048x688", "3840x1280",
    "1536x768", "3072x1536", "3840x1920",
    "768x1536", "1536x3072", "1920x3840",
)

#: 只收像素值的 gpt-image 型号。画幅枚举取值按这张表区分，不靠字符串匹配
#: 型号名（改名就会失配）。
_PIXEL_ONLY_MODELS: frozenset[str] = frozenset(
    {
        "gpt-image-2-vip",
        "gpt-image-2.5-flare",
        "gpt-image-2.5-sunburst",
        "gpt-image-2.6-flare",
        "gpt-image-2.6-sunburst",
    }
)




def _image_spec(
    upstream: str,
    label: str,
    *,
    description: str,
    param_schema: dict[str, Any],
) -> ModelSpec:
    return ModelSpec(
        id=f"{PROVIDER}-{upstream}",
        label=label,
        capability=Capability.IMAGE,
        provider=PROVIDER,
        description=description,
        param_schema=param_schema,
        max_references=_MAX_IMAGE_REFERENCES,
        reference_kinds=("image",),
    )


def _nano_schema(
    upstream: str,
    *,
    image_sizes: tuple[str, ...],
) -> dict[str, Any]:
    """nano-banana 系的参数声明。

    画幅用 `aspectRatio`（比例），**不声明平台通用的 `size`（像素值）**：上游
    这一族只收比例，收像素值就得由我们换算，而换算表一旦漏档就会把用户的
    「1024x1536」悄悄变成另一个比例 —— 用户拿到图才发现画幅不对。
    """
    aspects = _NANO_ASPECTS
    if upstream.startswith("nano-banana-2"):
        aspects = aspects + _NANO2_EXTRA_ASPECTS
    return {
        "aspectRatio": {"type": "enum", "options": list(aspects), "default": "1:1"},
        "imageSize": {"type": "enum", "options": list(image_sizes), "default": image_sizes[0]},
        # Grsai 一次只回一张图（结果数组里就一个地址），多张要靠上游另行支持。
        "count": {"type": "int", "min": 1, "max": 1, "default": 1},
    }


def _gpt_schema(
    upstream: str,
    *,
    qualities: tuple[str, ...],
    output_formats: tuple[str, ...],
) -> dict[str, Any]:
    """gpt-image 系的参数声明。"""
    pixel_only = upstream in _PIXEL_ONLY_MODELS
    aspects = _GPT_PIXEL_ASPECTS if pixel_only else _GPT_RATIO_ASPECTS
    return {
        "aspectRatio": {
            "type": "enum",
            "options": list(aspects),
            # 只收像素值的型号默认给 1K 方图；收比例的默认给 1:1。
            "default": "1024x1024" if pixel_only else "1:1",
        },
        "quality": {"type": "enum", "options": list(qualities), "default": "auto"},
        # `background` 不给默认值：不填就不发给上游（上游只认 transparent）。
        "background": {"type": "enum", "options": ["transparent"]},
        "outputFormat": {"type": "enum", "options": list(output_formats), "default": "png"},
        "count": {"type": "int", "min": 1, "max": 1, "default": 1},
    }


#: (上游名, 展示名, 分辨率档位) —— 档位只有一个的型号，型号名里的 2k / 4k
#: 就是它的分辨率（上游文档没逐型号列 imageSize 的合法值，这里按型号名推断，
#: 宁可少给一档，也不要让用户传出「型号不接受」的组合）。
_NANO_MODELS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("nano-banana", "Nano Banana", ("1K", "2K", "4K")),
    ("nano-banana-fast", "Nano Banana Fast", ("1K", "2K", "4K")),
    ("nano-banana-2", "Nano Banana 2", ("1K", "2K", "4K")),
    ("nano-banana-2-cl", "Nano Banana 2 CL", ("1K", "2K", "4K")),
    ("nano-banana-2-2k-cl", "Nano Banana 2 2K CL", ("2K",)),
    ("nano-banana-2-4k-cl", "Nano Banana 2 4K CL", ("4K",)),
    ("nano-banana-2.1", "Nano Banana 2.1", ("1K", "2K", "4K")),
    ("nano-banana-pro", "Nano Banana Pro", ("1K", "2K", "4K")),
    ("nano-banana-pro-vt", "Nano Banana Pro VT", ("1K", "2K", "4K")),
    ("nano-banana-pro-cl", "Nano Banana Pro CL", ("1K", "2K", "4K")),
    ("nano-banana-pro-vip", "Nano Banana Pro VIP", ("1K", "2K", "4K")),
    ("nano-banana-pro-4k-vip", "Nano Banana Pro 4K VIP", ("4K",)),
)

#: (上游名, 展示名, quality 档位, outputFormat 档位) —— 两组都抄自上游文档
#: 的逐型号说明（gif 只有 2.6 系支持，svg 只有 gpt-image-2.6 支持）。
_GPT_MODELS: tuple[tuple[str, str, tuple[str, ...], tuple[str, ...]], ...] = (
    ("gpt-image-2", "GPT Image 2", ("auto",), ("png",)),
    (
        "gpt-image-2-vip",
        "GPT Image 2 VIP",
        ("auto", "low", "medium", "high"),
        ("png",),
    ),
    ("gpt-image-2.5", "GPT Image 2.5", ("auto",), ("png",)),
    (
        "gpt-image-2.5-flare",
        "GPT Image 2.5 Flare",
        ("auto", "low", "medium", "high"),
        ("png",),
    ),
    (
        "gpt-image-2.5-sunburst",
        "GPT Image 2.5 Sunburst",
        ("auto", "low", "medium", "high", "xhigh", "max"),
        ("png",),
    ),
    ("gpt-image-2.6", "GPT Image 2.6", ("auto",), ("png", "gif", "svg")),
    (
        "gpt-image-2.6-flare",
        "GPT Image 2.6 Flare",
        ("auto", "low", "medium", "high"),
        ("png", "gif"),
    ),
    (
        "gpt-image-2.6-sunburst",
        "GPT Image 2.6 Sunburst",
        ("auto", "low", "medium", "high"),
        ("png", "gif"),
    ),
)

MODELS: tuple[ModelSpec, ...] = (
    tuple(
        _image_spec(
            upstream,
            label,
            description=(
                f"Grsai · {upstream}（nano-banana 系）。"
                "画幅填比例，imageSize 选 1K / 2K / 4K 分辨率；"
                f"最多 {_MAX_IMAGE_REFERENCES} 张参考图。"
            ),
            param_schema=_nano_schema(upstream, image_sizes=image_sizes),
        )
        for upstream, label, image_sizes in _NANO_MODELS
    )
    + tuple(
        _image_spec(
            upstream,
            label,
            description=(
                f"Grsai · {upstream}（gpt-image 系）。"
                + (
                    "画幅只填像素值（如 1024x1024）。"
                    if upstream in _PIXEL_ONLY_MODELS
                    else "画幅可填比例（如 16:9）或 1K 像素值。"
                )
                + f"最多 {_MAX_IMAGE_REFERENCES} 张参考图。"
            ),
            param_schema=_gpt_schema(
                upstream, qualities=qualities, output_formats=output_formats
            ),
        )
        for upstream, label, qualities, output_formats in _GPT_MODELS
    )
)

#: 上游模型名 → 展示名。轮询时拿到的 `model` 可能是目录 id，也可能是展示名
#: （历史记录），两种都要能还原成上游名。
_LABELS: Mapping[str, str] = {
    **{upstream: label for upstream, label, _ in _NANO_MODELS},
    **{upstream: label for upstream, label, _, _ in _GPT_MODELS},
}


# --------------------------------------------------------------------------- #
# 驱动
# --------------------------------------------------------------------------- #


class GrsaiImageDriver:
    """Grsai 生图。异步形态：提交拿任务号，轮询拿产物地址。"""

    name = PROVIDER
    capabilities = frozenset({Capability.IMAGE})
    models = MODELS
    supports_poll = True
    # 上游没公开轮询限频，取 5 秒：出图普遍几十秒，5 秒一跳不至于让用户
    # 多等一分钟；真被限了会返回 4xx/5xx，交给退避重试。
    poll_interval_sec = 5.0
    # 提交只是一次短往返（replyType=async），30 秒足够。
    submit_timeout_sec = 30.0
    # Grsai 直接收 base64，不必落盘物化再传地址。
    #
    # 通道声明（`infra/media_channel.py`）取代了旧的 `inline_references` 开关：
    # 旧开关是「所有素材类型一个开关」，分不了类型；新声明按 image / audio
    # 分别说清走哪条通道。
    accepted_media_formats = {"image": frozenset({MediaChannel.BASE64})}
    reference_limits = {"image": ReferenceLimit(max_count=_MAX_IMAGE_REFERENCES)}

    def __init__(self, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.settings = get_grsai_settings()
        self.transport = transport

    # ------------------------------------------------------------------ 提交
    async def submit(self, request: GenerationRequest) -> Any:
        """提交生图任务：轮次键与三态落库交给 kit 的 `guarded_submit` 统一处理。"""
        return await guarded_submit(
            provider=PROVIDER,
            record_id=request.request_id,
            model=request.model,
            channel_used=request.channel_used or None,
            call=lambda: self._submit_once(request),
        )

    async def _submit_once(self, request: GenerationRequest) -> Any:
        if request.capability is not Capability.IMAGE:
            raise BadRequest("grsai 只负责图片生成；视频请走 grsai_video 驱动")

        media = grsai_reference_media(request.reference_urls)
        images = [item for kind, item in media if kind == "image"]
        # 生图链只收参考图：音频素材若混进来会被剔掉，而提示词里的
        # `{{Mixed N}}` 编号是按上传顺序算的 —— 剔一个，后面每个编号都指错
        # 对象（出片正常，内容不对）。宁可当场报错。
        if any(kind == "audio" for kind, _ in media):
            raise BadRequest("Grsai 生图不支持参考音频，请去掉音频素材")
        # 生图链**不消费** `reference_limits`（只有视频链用它），所以张数上限
        # 得驱动自己判 —— 否则超量会变成上游一句含义不明的 400。
        if len(images) > _MAX_IMAGE_REFERENCES:
            raise BadRequest(
                f"Grsai 生图最多 {_MAX_IMAGE_REFERENCES} 张参考图，当前 {len(images)} 张"
            )
        prompt = request.prompt
        if media:
            # 提示词里的素材指代（`{{Mixed 1}}`）翻成「图1」这类写法。Grsai
            # 没有专门的素材指代语法，但留着 `{{Mixed 1}}` 只会是乱码。
            prompt = translate_or_original(
                request.prompt,
                [{"type": kind} for kind, _ in media],
                translate_prompt,
            )

        payload = _image_payload(request.model, prompt, images, request.params)
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
        if status == _STATUS_VIOLATION:
            raise BadRequest(f"Grsai 判定内容违规，未生成{_suffix(error)}")
        if status == _STATUS_FAILED:
            # 上游在 200 的响应体里明确回报 failed = 它明确拒绝了这次请求
            # （没受理、不会计费）→ 落 4xx 一侧，框架才判「确定没送达」并允许
            # 安全重试；抛 5xx 会被判成「可能已受理」，用户只能去核对上游任务。
            raise BadRequest(f"Grsai 提交失败{_suffix(error)}")
        if status == _STATUS_SUCCEEDED:
            # 上游快到出图即返：直接把终态交给框架，别再轮询一遍。
            url = grsai_first_url(body)
            if url:
                return GenerationProgress(
                    external_id=task_id,
                    state=GenerationState.SUCCEEDED,
                    progress=100,
                    output_url=url,
                    # 成图本身就是封面（与既有生图驱动一致）。
                    cover_url=url,
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
            # 上游只保留 2 小时的结果，过期后查不到。这是终态，不能当成临时
            # 故障一直重试到墙钟超时。
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

        if status == _STATUS_RUNNING:
            return GenerationProgress(
                external_id=task_id, state=GenerationState.RUNNING, progress=progress
            )
        if status == _STATUS_SUCCEEDED:
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
                # 成图本身就是封面（与既有生图驱动一致）。
                cover_url=url,
            )
        if status == _STATUS_VIOLATION:
            return GenerationProgress(
                external_id=task_id,
                state=GenerationState.FAILED,
                progress=100,
                error=f"Grsai 判定内容违规{_suffix(error)}",
                retryable=False,
            )
        if status == _STATUS_FAILED:
            return GenerationProgress(
                external_id=task_id,
                state=GenerationState.FAILED,
                progress=100,
                error=f"Grsai 生成失败{_suffix(error)}",
                retryable=False,
            )
        # status 认不出来（含空）：多半是任务已过期、接口没认这个 id。
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


def _upstream_model(model: str) -> str:
    """目录 id / 展示名 → 上游模型名。

    轮询拿到的 `model` 来自任务记录：新数据存目录 id，历史数据可能只剩展示名
    （`ModelRouter` 两种都认）。两种都映射到同一个上游名，别让恢复链因为
    记录里存的是展示名而查不到任务。
    """
    value = (model or "").strip()
    if not value:
        raise BadRequest("Grsai 任务没有记录模型名，无法提交")
    if value in _LABELS:
        return value
    if value.startswith(f"{PROVIDER}-"):
        upstream = value[len(PROVIDER) + 1 :]
        if upstream in _LABELS:
            return upstream
    raise BadRequest(f"grsai 驱动不认识模型 {model!r}")


def _aspect_ratio(upstream: str, params: Mapping[str, Any]) -> str:
    """画幅：直接用上游的 `aspectRatio`。

    字段名与上游一致，所以这里没有命名映射；两族对取值的要求不同（比例 vs
    像素值），枚举已在模型目录里按型号分别声明，受理期就会挡掉非法值。
    """
    value = str(params.get("aspectRatio") or "").strip()
    if value:
        return value
    return "1024x1024" if upstream in _PIXEL_ONLY_MODELS else "1:1"


def _image_payload(
    model: str,
    prompt: str,
    images: list[str],
    params: Mapping[str, Any],
) -> dict[str, Any]:
    """组装上游请求体。

    只发上游认得的键：`size` / `count` 是我们自己的顶层字段，不转发
    （`size` 的含义按型号换算进 `aspectRatio`，`count` 上游只支持 1）。
    """
    upstream = _upstream_model(model)
    payload: dict[str, Any] = {
        "model": upstream,
        "prompt": prompt,
        "replyType": "async",
        "aspectRatio": _aspect_ratio(upstream, params),
    }
    if images:
        payload["images"] = images
    if upstream.startswith("nano-banana"):
        image_size = str(params.get("imageSize") or "").strip()
        if image_size:
            payload["imageSize"] = image_size
        return payload
    quality = str(params.get("quality") or "").strip()
    if quality:
        payload["quality"] = quality
    background = str(params.get("background") or "").strip()
    if background:
        payload["background"] = background
    output_format = str(params.get("outputFormat") or "").strip()
    if output_format:
        payload["outputFormat"] = output_format
    return payload


def grsai_first_url(body: Mapping[str, Any]) -> str:
    """取第一个产物地址。"""
    results = body.get("results")
    if not isinstance(results, list):
        return ""
    for item in results:
        if isinstance(item, dict):
            url = str(item.get("url") or "").strip()
            if url:
                return url
    return ""


def _int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _suffix(error: str) -> str:
    """与 grsai_video 的 `_suffix` 逐字等价 → 已抽到 kit（A1 验证唯一可合并件）。"""
    return kit_suffix(error)


META = DriverMeta(
    label="Grsai 生图",
    description="Grsai 多模型聚合平台的生图系列（nano-banana 系与 gpt-image 系）。",
    version="1.0.0",
)

Driver = GrsaiImageDriver
