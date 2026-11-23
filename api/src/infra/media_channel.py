"""素材通道的**决策层**：按素材类型选通道、启动期校验可行性、回退留痕。

## 边界（重要）

本模块**不实现** Base64 编码或 OSS 上传 —— 那些落地实现已存在：
- `infra/reference_inline.py::inline_data_uris()` —— Base64 data URI
- `drivers/bailian.py::prepare_inputs()` —— OSS 临时 URL

这里只回答「这条素材该走哪条通道」。把落地实现搬进来会让 infra 反向依赖
driver（架构守卫禁止），也会让本模块变成半个框架。

## 三种通道不是对等选项

| 通道 | 可用性 | 原因 |
|---|---|---|
| `BASE64` | 通用 | 我们从 S3 读字节编码，不依赖上游环境 |
| `OSS` | 有条件 | 只有供应商**签发上传凭据**时可用（百炼有） |
| `PUBLIC_URL` | **当前不可用** | 素材取件链接私签、几分钟过期，上游访问不到
  （见 `image/pipeline.py:330-332`） |

因此驱动**不能自由三选一**：声明 `PUBLIC_URL` 但没配公网域名 → 启动失败，
而不是运行时拿到上游 400「图片无法访问」（根因在我们这侧，极难排查）。

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
from enum import StrEnum

from core.exceptions import BadRequest

__all__ = [
    "MediaChannel",
    "MediaKind",
    "choose_channel",
    "validate_declaration",
]


class MediaChannel(StrEnum):
    BASE64 = "base64"
    OSS = "oss"
    PUBLIC_URL = "public_url"


class MediaKind(StrEnum):
    IMAGE = "image"
    AUDIO = "audio"
    VIDEO = "video"


#: 通道的优先级（能走 Base64 就不走 OSS：少一轮上传往返）
PREFERRED_ORDER = (MediaChannel.BASE64, MediaChannel.OSS, MediaChannel.PUBLIC_URL)


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
    """
    public_ok = bool(public_base_url.strip())
    for kind, channels in accepted.items():
        if not channels:
            raise BadRequest(f"{provider} 的 {kind} 没有声明任何可用通道")
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
