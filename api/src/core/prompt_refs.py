"""Mixed 引用重编号：把共享的 `{{Mixed N}}` 换成按素材类型独立编号的指代。

替换链的档案与视频驱动都要用同一套规则：编排期把编译结果写进
`inputs/prompt.txt`，驱动上传素材后再翻一次（幂等）并拼进请求体。两边各写
一份，迟早出现「档案里写 视频1、实际发出去是 视频2」这种对不上的情况，
而且出图正常、只是内容不对，极难发现。

纯函数、无供应商 I/O、无状态，所以放 `core/` 共用——业务模块之间不允许
互相 import（`test_modules_do_not_import_each_other`）。
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

from core.exceptions import BadRequest

# 素材类型 → 供应商侧的类型名。键同时也是「档案里允许出现的 type」。
REFERENCE_TYPES = {
    "video": "reference_video",
    "image": "reference_image",
    "audio": "reference_audio",
}
REFERENCE_LABELS = {"video": "视频", "image": "图", "audio": "音频"}

# 两种写法都认：`{{Mixed 1}}` 是模板语法，裸 `Mixed 1` 是人写的提示词。
# 前后不能紧贴字母数字，否则 `Mixed 10` 会被 `Mixed 1` 半截匹配，也会误伤
# 正常单词里出现的 `Mixed`。
_REFERENCE = re.compile(
    r"\{\{\s*Mixed\s+(\d+)\s*\}\}|(?<![A-Za-z0-9_])Mixed\s+(\d+)(?!\d)"
)


def translate_prompt(prompt: str, entries: Sequence[Mapping[str, Any]]) -> str:
    """把第 N 个素材的引用换成「视频1 / 图2 / 音频1」。

    编号**按类型各自从 1 开始**：上游把图片、视频、音频分三个数组收，
    提示词里说「图2」指的是图片数组的第 2 个，不是素材列表的第 2 个。
    所以 `entries` 的顺序必须与上传顺序一致，否则指代整体错位。

    幂等：翻译后的文本里不再有 `Mixed N`，再调一次原样返回——档案里已经是
    编译后的文本，驱动不该因为「又翻了一遍」而报「引用了不存在的素材」。
    """
    counts: dict[str, int] = dict.fromkeys(REFERENCE_TYPES, 0)
    references: dict[str, str] = {}
    for index, entry in enumerate(entries, 1):
        kind = entry.get("type")
        if kind not in REFERENCE_TYPES:
            raise BadRequest("参考素材只支持视频、图片和音频")
        counts[str(kind)] += 1
        references[f"Mixed {index}"] = f"{REFERENCE_LABELS[str(kind)]}{counts[str(kind)]}"

    def replace(match: re.Match[str]) -> str:
        token = f"Mixed {int(match[1] or match[2])}"
        if token not in references:
            raise BadRequest("提示词引用了不存在的 Mixed 素材")
        return references[token]

    return _REFERENCE.sub(replace, prompt)
