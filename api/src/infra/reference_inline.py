"""把存储里的参考素材按**声明的通道**展开成内联 `data:` URI。

生图链（`modules/image/pipeline.py`）与视频链（`modules/task/pipeline.py`）
都要这一步，但业务模块之间不许互相 import（架构守卫），所以实现放在
`infra/`：两边共用同一份，后缀白名单与体积上限不会各写各的。

为什么不把取件地址直接交给供应商：那是**私签**地址，几分钟就过期，
而且本机的存储供应商（MinIO / OSS 内网端点）外部根本访问不到。
供应商能收的形态只有两种 —— 公网直链（我们给不出）或 `data:` URI。

两条通道分工（`infra/media_channel.py` 负责选路，这里负责落地）：

- **Base64**：框架读字节、编码成 `data:` URI，直接塞进请求
- **OSS / 工作区**：框架**不读**字节，素材留在归档侧，由物化器落工作区
  （OSS 通道由驱动自己上传，工作区通道由本地执行器读文件）

只读一次字节是硬要求：素材已经在存储里，选一次路就多下一次下载的话，
大文件的双倍流量会直接体现在成本上。
"""

from __future__ import annotations

import base64
from collections.abc import Mapping, Sequence
from pathlib import Path

from core.exceptions import BadRequest
from infra import get_storage
from infra.media_channel import ChannelPlan, MediaChannel, plan_channel
from infra.media_probe import EXTENSIONS

__all__ = [
    "AUDIO_MIME_BY_SUFFIX",
    "IMAGE_MIME_BY_SUFFIX",
    "MIME_BY_SUFFIX",
    "inline_data_uris",
    "prepare_inline_payloads",
]

#: **从上传白名单派生**，不另抄一份后缀表：`media_probe.EXTENSIONS` 是「允许进库的
#: 文件类型」的唯一真相，这里只按 MIME 前缀挑出能内联的两类。各写一份的后果是
#: 上传侧加了新后缀、内联侧不知道 —— 用户把图传进库了，提交时才报「格式不支持」。
#: 视频与文本不进这张表：参考素材只收图与音轨（视频走归档工作区那条路）。
IMAGE_MIME_BY_SUFFIX: Mapping[str, str] = {
    suffix: mime
    for suffix, mime in EXTENSIONS.items()
    if mime.startswith("image/")
}
#: 音频后缀 -> MIME。视频参考素材（音轨）用。
AUDIO_MIME_BY_SUFFIX: Mapping[str, str] = {
    suffix: mime for suffix, mime in EXTENSIONS.items() if mime.startswith("audio/")
}
#: 图片 + 音频。视频链一次可能带图与音轨两种素材。
MIME_BY_SUFFIX: Mapping[str, str] = {**IMAGE_MIME_BY_SUFFIX, **AUDIO_MIME_BY_SUFFIX}


async def inline_data_uris(
    keys: Sequence[str],
    *,
    mime_by_suffix: Mapping[str, str],
    max_bytes: int,
) -> tuple[str, ...]:
    """把存储 key 读成 `data:<mime>;base64,...`。

    **顺序必须与入参一致**：提示词里的「第一张 / 第二张」指的就是这个顺序，
    重排会让提示词与素材整体错位，而且错得很隐蔽（照常出片，只是内容不对）。

    读失败就抛，而且抛的是 `BadRequest` 不是 `ValueError`：这几种失败
    （空文件 / 超限 / 格式不支持）都是**永久错误**，重试三次只会让用户多等
    两次退避、并且用最后一次的同一句话覆盖真正的错误。runner 的
    `_retryable()` 只对 `AppError` 看 `retryable`，裸 `ValueError` 一律当
    「可能自愈」重试 —— 素材坏了不会因为重试而变好。
    """
    if not keys:
        return ()
    storage = get_storage()
    allowed = " / ".join(suffix.lstrip(".") for suffix in sorted(mime_by_suffix))
    uris: list[str] = []
    for key in keys:
        data = await storage.get(key)
        if not data:
            raise BadRequest(f"参考素材是空文件，请重新上传：{key}")
        if len(data) > max_bytes:
            raise BadRequest(
                f"参考素材超过 {max_bytes // (1024 * 1024)}MB，请压缩后再传：{key}"
            )
        mime = mime_by_suffix.get(Path(key).suffix.lower(), "")
        if not mime:
            raise BadRequest(f"参考素材格式不支持（只认 {allowed}）：{key}")
        uris.append(f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}")
    return tuple(uris)


async def prepare_inline_payloads(
    keys: Sequence[str],
    *,
    accepted: Mapping[str, frozenset[MediaChannel]],
    preferred: Mapping[str, MediaChannel] | None = None,
    supported: frozenset[MediaChannel],
    mime_by_suffix: Mapping[str, str],
    max_bytes: int,
    base64_max_bytes: int = 12 * 1024 * 1024,
) -> tuple[tuple[str, ...], dict[str, ChannelPlan]]:
    """按驱动声明的通道准备参考素材，返回（内联的 `data:` URI，逐素材的选路结果）。

    `supported` 是**这条链路**的落地能力（框架侧真有哪些通道的实现），
    `accepted` 是**驱动**声明的通道。两者取交集才是这条链路上真能走的通道。

    ## 三条纪律

    1. **不读用不上的字节**：只走 OSS / 工作区的素材，框架不碰它 —— 物化器
       （或驱动）自己会去取。读一遍只为判大小，等于把大文件的流量翻倍。
    2. **读一次就够**：需要判大小的素材（声明了多通道）读一次，字节留作编码，
       不再读第二遍。
    3. **同一素材类型的通道必须一致**：通道回退是**按素材**发生的（一张图超限、
       另一张没超限），但提示词里的「第一张 / 第二张」是**按顺序**的。同一类型
       里一半内联一半不内联，驱动收到的顺序就错位了 —— 照常出片，只是内容不对。
       所以同类型内出现分歧时统一走保守的那一档，并留痕。
    """
    if not keys:
        return (), {}

    storage = get_storage()
    allowed = " / ".join(suffix.lstrip(".") for suffix in sorted(mime_by_suffix))
    blobs: dict[str, bytes] = {}
    plans: dict[str, ChannelPlan] = {}
    kinds: dict[str, str] = {}

    for key in keys:
        mime = mime_by_suffix.get(Path(key).suffix.lower(), "")
        if not mime:
            # 认不出的后缀：现状里「内联驱动」会报错、「落盘驱动」根本不读。
            # 保持这个分寸，但驱动压根没声明通道时要明确报错（不能静默丢素材）。
            if not accepted:
                raise BadRequest(f"该驱动没有声明素材通道，无法提交参考素材：{key}")
            declared = {c for channels in accepted.values() for c in channels}
            if declared & set(supported) == {MediaChannel.BASE64}:
                raise BadRequest(f"参考素材格式不支持（只认 {allowed}）：{key}")
            continue

        kind = mime.split("/", 1)[0]
        channels = set(accepted.get(kind) or ())
        if not channels:
            raise BadRequest(f"没有为 {kind} 声明任何素材通道：{key}")
        usable = channels & set(supported)
        if not usable:
            raise BadRequest(
                f"{kind} 素材通道 "
                f"{'/'.join(sorted(c.value for c in channels))} 在这条链路上没有落地实现：{key}"
            )
        kinds[key] = kind

        if MediaChannel.BASE64 not in usable:
            # 整条路都不是 Base64 → 不读字节，素材留给物化器那条路
            plans[key] = plan_channel(
                kind=kind,
                accepted={kind: usable},
                preferred=preferred,
                size_bytes=0,
                base64_max_bytes=base64_max_bytes,
            )
            continue

        data = await storage.get(key)
        if not data:
            raise BadRequest(f"参考素材是空文件，请重新上传：{key}")
        if len(data) > max_bytes:
            raise BadRequest(
                f"参考素材超过 {max_bytes // (1024 * 1024)}MB，请压缩后再传：{key}"
            )
        blobs[key] = data

        if len(usable) == 1:
            # 只声明了 Base64 → 没有别的档可回退，选路是多余的
            plans[key] = ChannelPlan(
                kind=kind, channel=MediaChannel.BASE64, preferred=MediaChannel.BASE64
            )
            continue
        plans[key] = plan_channel(
            kind=kind,
            accepted={kind: usable},
            preferred=preferred,
            size_bytes=len(data),
            base64_max_bytes=base64_max_bytes,
        )

    _unify_kind_channels(plans, kinds)

    uris: list[str] = []
    for key in keys:
        plan = plans.get(key)
        if plan is None or plan.channel is not MediaChannel.BASE64:
            continue
        blob = blobs.get(key)
        if blob is None:
            continue
        mime = mime_by_suffix.get(Path(key).suffix.lower(), "")
        if not mime:
            continue
        uris.append(f"data:{mime};base64,{base64.b64encode(blob).decode('ascii')}")
    return tuple(uris), plans


def _unify_kind_channels(
    plans: dict[str, ChannelPlan], kinds: dict[str, str]
) -> None:
    """同一素材类型的通道统一成同一档（保守优先）。

    分歧只可能来自「部分素材超限回退」。此时统一到**非 Base64**的那一档：
    少内联一份素材只是供应商少看到一张图，而顺序错位会让提示词与素材
    整体错位（照常出片，只是内容不对）—— 后者更难排查。
    """
    by_kind: dict[str, list[str]] = {}
    for key, kind in kinds.items():
        by_kind.setdefault(kind, []).append(key)

    for kind, keys in by_kind.items():
        channels = {plans[key].channel for key in keys if key in plans}
        if len(channels) <= 1:
            continue
        fallback = next(
            (
                c
                for c in (MediaChannel.OSS, MediaChannel.PUBLIC_URL, MediaChannel.WORKSPACE)
                if c in channels
            ),
            None,
        )
        if fallback is None:
            continue
        for key in keys:
            plan = plans.get(key)
            if plan is None or plan.channel is fallback:
                continue
            plans[key] = ChannelPlan(
                kind=kind,
                channel=fallback,
                preferred=plan.preferred,
                fallback_reason=plan.fallback_reason
                or f"同一 {kind} 素材的通道不一致，统一走 {fallback.value}",
            )
