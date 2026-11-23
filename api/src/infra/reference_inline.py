"""把存储里的参考素材展开成内联 `data:` URI。

生图链（`modules/image/pipeline.py`）与视频链（`modules/task/pipeline.py`）
都要这一步，但业务模块之间不许互相 import（架构守卫），所以实现放在
`infra/`：两边共用同一份，后缀白名单与体积上限不会各写各的。

为什么不把取件地址直接交给供应商：那是**私签**地址，几分钟就过期，
而且本机的存储供应商（MinIO / OSS 内网端点）外部根本访问不到。
供应商能收的形态只有两种 —— 公网直链（我们给不出）或 `data:` URI。
"""

from __future__ import annotations

import base64
from collections.abc import Mapping, Sequence
from pathlib import Path

from core.exceptions import BadRequest
from infra import get_storage
from infra.media_probe import EXTENSIONS

__all__ = [
    "AUDIO_MIME_BY_SUFFIX",
    "IMAGE_MIME_BY_SUFFIX",
    "MIME_BY_SUFFIX",
    "inline_data_uris",
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
