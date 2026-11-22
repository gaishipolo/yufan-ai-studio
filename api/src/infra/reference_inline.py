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

from infra import get_storage

__all__ = [
    "AUDIO_MIME_BY_SUFFIX",
    "IMAGE_MIME_BY_SUFFIX",
    "MIME_BY_SUFFIX",
    "inline_data_uris",
]

#: 图片后缀 -> MIME。供应商只认这几种（与生图链的白名单一致）。
IMAGE_MIME_BY_SUFFIX: Mapping[str, str] = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
}
#: 音频后缀 -> MIME。视频参考素材（音轨）用。
AUDIO_MIME_BY_SUFFIX: Mapping[str, str] = {
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
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

    读失败就抛：参考素材是提交输入，静默退化成纯文生会让用户拿到一段
    与参考素材毫无关系的成片，而记录里写着「用过参考图」。
    """
    if not keys:
        return ()
    storage = get_storage()
    uris: list[str] = []
    for key in keys:
        data = await storage.get(key)
        if not data:
            raise ValueError(f"参考图为空文件：{key}")
        if len(data) > max_bytes:
            raise ValueError(f"参考图超过 {max_bytes // (1024 * 1024)}MB：{key}")
        mime = mime_by_suffix.get(Path(key).suffix.lower(), "")
        if not mime:
            allowed = " / ".join(sorted(mime_by_suffix))
            raise ValueError(f"参考图格式不支持（只认 {allowed}）：{key}")
        uris.append(f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}")
    return tuple(uris)
