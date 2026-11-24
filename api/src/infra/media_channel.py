"""素材通道的**决策层**：按素材类型选通道、启动期校验可行性、回退留痕。

## 边界（重要）

本模块**只回答「这条素材该走哪条通道」**，不编码也不上传。落地实现另有归属：
- `infra/reference_inline.py::inline_data_uris()` —— Base64 data URI
- `infra/oss_channel.py::upload_media_bytes()` —— OSS 直传，拿到 `oss://` 直链

两个落地模块都是**供应商中立**的：供应商特有的部分只有「怎么签发上传凭据」
（`OssUploadTargetProvider` 协议），由驱动实现。驱动里因此不再有第二份直传
代码 —— 换个供应商声明 OSS 通道，直接复用同一套落地。

## 三种通道不是对等选项

| 通道 | 可用性 | 原因 |
|---|---|---|
| `BASE64` | 通用 | 我们从 S3 读字节编码，不依赖上游环境 |
| `OSS` | 有条件 | 只有供应商**签发上传凭据**时可用（百炼有） |
| `PUBLIC_URL` | **当前不可用** | 素材取件链接私签、几分钟过期，上游访问不到
  （见 `image/pipeline.py:330-332`） |
| `WORKSPACE` | **本地执行器专有** | 素材落盘到归档工作区，由**本地执行器**读文件
  （`libtv`）。不经过网络，**执行器只认工作区里的真实文件** —— 改成 Base64
  或 OSS 它就读不到了 |

因此驱动**不能自由三选一**：声明 `PUBLIC_URL` 但没配公网域名 → 启动失败，
而不是运行时拿到上游 400「图片无法访问」（根因在我们这侧，极难排查）。

> **`WORKSPACE` 为什么必须单列成一档**（设计文档 A6）：
> 「libtv 的工作区需求与通道选择正交」这句话的意思是 —— libtv 需要的不是
> 「把素材送到上游」，而是「把素材落到**本地执行器**能读到的地方」。它与
> Base64/OSS/公网 URL 是**不同维度**的东西，不是同一维度里的备选项。
> 如果不单列，接线时很容易为了「统一」把 libtv 也改成 Base64 —— 那样
> 执行器读不到文件，任务会在提交阶段才炸（且报的是与用户输入无关的 ENOENT）。

## 驱动声明什么

```python
accepted_media_formats = {
    "image": frozenset({MediaChannel.BASE64, MediaChannel.OSS}),
    "audio": frozenset({MediaChannel.OSS}),  # 百炼 audio_url 不支持 Base64
}
preferred_channel = {"image": MediaChannel.BASE64}  # 可选偏好，需理由
```

框架**按素材类型分别选**（同一驱动 image 走 Base64、audio 走 OSS 共存），
这是 `inline_references: bool` 表达不了的粒度。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

from core.exceptions import BadRequest
from infra.ai.base import InlineReferencesCapable

__all__ = [
    "ChannelPlan",
    "MediaChannel",
    "MediaChannelPreferenceDeclared",
    "MediaChannelsDeclared",
    "MediaKind",
    "OssUploadDeclared",
    "choose_channel",
    "declared_channels",
    "plan_channel",
    "preferred_channels",
    "record_channel_fallbacks",
    "summarize_channels",
    "validate_declaration",
]


class MediaChannel(StrEnum):
    BASE64 = "base64"
    OSS = "oss"
    PUBLIC_URL = "public_url"
    #: 落盘到归档工作区，由**本地执行器**读文件（`libtv`）。不走网络。
    WORKSPACE = "workspace"


class MediaKind(StrEnum):
    IMAGE = "image"
    AUDIO = "audio"
    VIDEO = "video"


#: 通道的优先级（能走 Base64 就不走 OSS：少一轮上传往返）。
#: `WORKSPACE` 排最后 —— 它是「本地执行器专有」的形态，只在前面的档都不可用
#: （或驱动只声明了它）时才是答案。
PREFERRED_ORDER = (
    MediaChannel.BASE64,
    MediaChannel.OSS,
    MediaChannel.PUBLIC_URL,
    MediaChannel.WORKSPACE,
)


def validate_declaration(
    *,
    provider: str,
    accepted: Mapping[str, frozenset[MediaChannel] | set[MediaChannel]],
    public_base_url: str = "",
    oss_upload_supported: bool = False,
) -> None:
    """启动期可行性校验。失败即抛 BadRequest（调用方应转成启动失败）。

    - 声明 PUBLIC_URL 但没配公网域名 → 拒绝（当前私签 S3 给不出公网 URL）
    - 声明 OSS 但供应商无上传凭据端点 → 拒绝（驱动无从上传）
    - 声明空集合 → 拒绝（该素材类型根本没法提交）
    - `WORKSPACE` 与其它通道并列 → 拒绝（见下）
    """
    public_ok = bool(public_base_url.strip())
    for kind, channels in accepted.items():
        if not channels:
            raise BadRequest(f"{provider} 的 {kind} 没有声明任何可用通道")
        if MediaChannel.WORKSPACE in channels and len(channels) > 1:
            # WORKSPACE 说的是「素材落到本地执行器能读到的地方」，其余几档说的是
            # 「上游收什么格式」—— 两者正交，并列声明会让选路静默挑一个，
            # 而执行器只认工作区里的真实文件 → 提交阶段才炸（报错还与用户输入无关）。
            raise BadRequest(
                f"{provider} 的 {kind} 同时声明了 WORKSPACE 与其它通道 —— "
                "WORKSPACE 专供本地执行器读工作区文件，不能与「上游收什么格式」并列"
            )
        if MediaChannel.PUBLIC_URL in channels and not public_ok:
            raise BadRequest(
                f"{provider} 的 {kind} 声明了公网 URL 通道，但未配置公网域名 —— "
                "素材取件链接是私签的，供应商访问不到"
            )
        if MediaChannel.OSS in channels and not oss_upload_supported:
            raise BadRequest(
                f"{provider} 的 {kind} 声明了 OSS 通道，但供应商不提供上传凭据端点"
            )


def choose_channel(
    *,
    kind: str,
    accepted: Mapping[str, frozenset[MediaChannel] | set[MediaChannel]],
    preferred: Mapping[str, MediaChannel] | None = None,
    size_bytes: int = 0,
    base64_max_bytes: int = 12 * 1024 * 1024,
) -> MediaChannel:
    """按素材类型选通道。

    优先级：显式偏好（若可用且装得下）> BASE64（装得下）> OSS > PUBLIC_URL。
    超限自动回退到下一档 —— 调用方**必须记审计事件**（含原通道/实际通道/原因），
    否则「素材突然从 Base64 变 OSS」无法排查。
    """
    allowed = set(accepted.get(kind) or ())
    if not allowed:
        raise BadRequest(f"没有为 {kind} 声明任何素材通道")

    # 显式偏好优先，但必须可用
    if preferred:
        want = preferred.get(kind)
        if want in allowed and _fits(want, size_bytes, base64_max_bytes):
            return want

    for channel in PREFERRED_ORDER:
        if channel in allowed and _fits(channel, size_bytes, base64_max_bytes):
            return channel
    # 所有通道都装不下（目前只有 BASE64 有大小上限）—— 明确报错，不静默截断
    raise BadRequest(f"{kind} 素材 {size_bytes} 字节超出所有可用通道的上限")


def _fits(channel: MediaChannel, size_bytes: int, base64_max_bytes: int) -> bool:
    if channel is MediaChannel.BASE64:
        return size_bytes <= base64_max_bytes
    return True


@runtime_checkable
class MediaChannelsDeclared(Protocol):
    """驱动按素材类型声明可用的传输通道（结构化类型，驱动无需继承）。

    键是素材类型（`image` / `audio` / `video`），值是该类型可走的通道集合。

    ```python
    accepted_media_formats = {
        "image": frozenset({MediaChannel.BASE64}),
        "audio": frozenset({MediaChannel.OSS}),
    }
    ```

    这是**声明**，不是实现：框架只读它来决定「这个驱动该走哪条通道」，
    通道本身的落地（编码、上传、落工作区）由框架与驱动各自负责。
    """

    accepted_media_formats: Mapping[str, frozenset[MediaChannel]]


@runtime_checkable
class MediaChannelPreferenceDeclared(Protocol):
    """可选：声明偏好通道（必须给理由，否则声明会被忽略）。"""

    preferred_media_channels: Mapping[str, MediaChannel]


@runtime_checkable
class OssUploadDeclared(Protocol):
    """可选：声明本驱动能拿到供应商签发的 OSS 上传凭据端点。

    声明了 OSS 通道的驱动必须同时声明它 —— 否则框架没法判断「这档通道
    是不是真的能走」，只能等提交时才发现没有上传端点。
    """

    oss_upload_supported: bool


def declared_channels(provider: Any) -> Mapping[str, frozenset[MediaChannel]]:
    """读出驱动声明的通道（按素材类型）。

    优先读 `accepted_media_formats`；没声明就按旧的 `inline_references` 兼容推导
    （`True` = 全部素材类型走 Base64；`False` 或没声明 = 没有声明）。

    ## 为什么要兼容

    `inline_references` 是上一版口径：**所有**素材类型一个开关，不分类型。
    第三方驱动可能只写了它 —— 直接不认会让它们在启动期被拒绝（声明为空），
    升级就断。兼容推导把它翻译成「全部类型走 Base64」，与它原来的行为一致。

    ## 为什么不反过来（让新声明覆盖旧开关的一半）

    `inline_references=True` 但 `accepted_media_formats` 只声明了 image：
    这是**自相矛盾**的声明（说了全都内联，又说 audio 不内联）。
    这里以更具体的新声明为准，但旧开关的 `True` 不会被自动降级成「只内联 image」
    —— 那种「部分内联」的语义旧开关表达不了，硬翻译会静默改变行为。
    """
    raw = provider.accepted_media_formats if isinstance(provider, MediaChannelsDeclared) else None
    if isinstance(raw, Mapping):
        out: dict[str, frozenset[MediaChannel]] = {}
        for kind, channels in raw.items():
            key = str(kind).strip().lower()
            if not key:
                continue
            try:
                out[key] = frozenset(MediaChannel(c) for c in channels)
            except ValueError as exc:
                raise BadRequest(
                    f"{type(provider).__name__} 的 {kind} 声明了未知通道：{channels!r}"
                ) from exc
        return out

    if isinstance(provider, InlineReferencesCapable) and provider.inline_references:
        return {kind.value: frozenset({MediaChannel.BASE64}) for kind in MediaKind}
    return {}


def preferred_channels(provider: Any) -> Mapping[str, MediaChannel]:
    """读出驱动声明的偏好通道；没声明就是空的（选路走默认优先级）。"""
    raw = (
        provider.preferred_media_channels
        if isinstance(provider, MediaChannelPreferenceDeclared)
        else None
    )
    if not isinstance(raw, Mapping):
        return {}
    out: dict[str, MediaChannel] = {}
    for kind, channel in raw.items():
        key = str(kind).strip().lower()
        if not key:
            continue
        try:
            out[key] = MediaChannel(channel)
        except ValueError as exc:
            raise BadRequest(
                f"{type(provider).__name__} 的 {key} 声明了未知偏好通道：{channel!r}"
            ) from exc
    return out


@dataclass(frozen=True, slots=True)
class ChannelPlan:
    """一次选路的结果。

    - `channel`：实际要走的通道
    - `preferred`：本来该走的通道（声明里的第一档）
    - `fallback_reason`：非空表示**发生了回退**（首选装不下 / 不可用）
    """

    kind: str
    channel: MediaChannel
    preferred: MediaChannel | None = None
    fallback_reason: str = ""

    @property
    def fell_back(self) -> bool:
        return bool(self.fallback_reason)


def plan_channel(
    *,
    kind: str,
    accepted: Mapping[str, frozenset[MediaChannel] | set[MediaChannel]],
    preferred: Mapping[str, MediaChannel] | None = None,
    size_bytes: int = 0,
    base64_max_bytes: int = 12 * 1024 * 1024,
) -> ChannelPlan:
    """选路，并说清「有没有回退」。

    与 `choose_channel` 的区别：后者只给结果，这里额外给出**本应首选的那一档**
    与**回退原因** —— 没有这两样，「素材突然从 Base64 变 OSS」就只能靠猜。

    判定回退的方式：把声明里「本应首选」的档（PREFERRED_ORDER 中第一个可用的）
    与实际选中的档比。两者相同 = 没回退（正常路径，不产生噪音留痕）。
    """
    allowed = set(accepted.get(kind) or ())
    if not allowed:
        raise BadRequest(f"没有为 {kind} 声明任何素材通道")

    baseline: MediaChannel | None = None
    if preferred:
        want = preferred.get(kind)
        if want in allowed:
            baseline = want
    if baseline is None:
        baseline = next((c for c in PREFERRED_ORDER if c in allowed), None)

    channel = choose_channel(
        kind=kind,
        accepted=accepted,
        preferred=preferred,
        size_bytes=size_bytes,
        base64_max_bytes=base64_max_bytes,
    )
    if baseline is None or channel is baseline:
        return ChannelPlan(kind=kind, channel=channel, preferred=baseline)

    if baseline is MediaChannel.BASE64:
        reason = (
            f"素材 {size_bytes} 字节超过 Base64 上限 {base64_max_bytes} 字节，"
            f"回退到 {channel.value}"
        )
    else:
        reason = f"首选通道 {baseline.value} 不可用，回退到 {channel.value}"
    return ChannelPlan(
        kind=kind, channel=channel, preferred=baseline, fallback_reason=reason
    )


async def record_channel_fallbacks(
    plans: Mapping[str, ChannelPlan],
    *,
    target_id: str,
    actor_id: str | None = None,
) -> None:
    """把「通道回退」记成审计事件。

    回退必须是**留痕**的：素材从 Base64 变成 OSS（或反过来）会改变请求体大小、
    失败点与排查方向，没留痕就只能靠对比上游日志反推。
    """
    fell_back = [plan for plan in plans.values() if plan.fell_back]
    if not fell_back:
        return
    from core.audit import record  # 局部导入：审计是可选依赖，不能拖垮选路

    for plan in fell_back:
        await record(
            "media.channel_fallback",
            actor_id=actor_id,
            target_type="generation",
            target_id=target_id,
            detail={
                "kind": plan.kind,
                "preferred": plan.preferred.value if plan.preferred else "",
                "actual": plan.channel.value,
                "reason": plan.fallback_reason,
            },
        )


def summarize_channels(plans: Mapping[str, ChannelPlan]) -> str:
    """把逐素材的选路结果汇总成一个字符串，落进提交台账的 `channel_used`。

    为什么是「汇总」而不是单值：一条任务可以带多份素材（image + audio），
    各自走各自的通道；而台账只有一列 `channel_used` → 记**去重后的通道集合**
    （逗号分隔，如 `base64` / `base64,oss`）。排序后拼接，保证同一批通道
    每次汇总出的字符串一致（可直接用于比对）。

    ⚠️ 纯文本生图（没有素材）没有通道 → 返回空串。台账里就留空，**不要编一个
    值** —— 编出来的通道会让「重试复用原通道」按着一条不存在的通道走。
    """
    return ",".join(sorted({plan.channel.value for plan in plans.values()}))
