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
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from core.exceptions import BadRequest

# 素材类型 → 供应商侧的类型名。键同时也是「档案里允许出现的 type」。
REFERENCE_TYPES = {
    "video": "reference_video",
    "image": "reference_image",
    "audio": "reference_audio",
}
REFERENCE_LABELS = {"video": "视频", "image": "图", "audio": "音频"}
#: 素材类型 → 提示词里的指代模板（`{n}` 是**该类型内**的序号）。
#:
#: 用模板而不是固定词：不同渠道对「第几张图」的写法不一样 —— 生图与
#: 替换链用「图1」，Agnes 视频要的是 `<Picture 1>`（上游只认这个写法）。
#: 编号规则只有一份（见 `translate_prompt_with`），两种写法不会各数各的。
REFERENCE_LABEL_TEMPLATES = {
    "video": "视频{n}",
    "image": "图{n}",
    "audio": "音频{n}",
}

# 只认模板语法 `{{Mixed N}}`。裸 `Mixed 1` 是**普通文本**，不翻译也不报错 ——
# 它可能是作者写在提示词里的自然语言（"参考 Mixed 1 这个型号"），此前把它
# 当引用会在"恰好有对应编号"时被悄悄改写、在"编号越界"时突然 400，同一个
# 提示词两种坏结局，且都跟用户的输入毫无关系。
_REFERENCE = re.compile(r"\{\{\s*Mixed\s+(\d+)\s*\}\}")


def translate_or_original(
    prompt: str,
    entries: Sequence[Mapping[str, Any]],
    translate: Callable[[str, Sequence[Mapping[str, Any]]], str | None] | None,
) -> str:
    """按驱动提供的翻译方法转换占位符；没有或没返回就用原样提示词。

    这是"每个驱动决定自己怎么翻译"的**统一调用约定**：驱动把自己的
    `translate_prompt`（可能没有）传进来，这里负责兜底 —— 缺方法、或方法判断
    无需翻译而返回 `None`，都保留 `prompt` 原文。这样新接一个纯文生驱动时，
    它什么都不做就是正确行为，不必为了接口补一个空实现。

    注意 `translate` 抛出的错误原样透出（例如引用了不存在的编号）：那是真实
    的配置错，吞掉会变成"提示词悄悄不对"。
    """
    if translate is None:
        return prompt
    converted = translate(prompt, entries)
    return prompt if converted is None else converted


def translate_prompt_with(
    prompt: str,
    entries: Sequence[Mapping[str, Any]],
    labels: Mapping[str, str],
) -> str:
    """按调用方给的模板把 `{{Mixed N}}` 翻成供应商认得的指代。

    `labels` 是「素材类型 → 模板」，模板里的 `{n}` 是**该类型内**的序号：
    `{"image": "<Picture {n}>"}` 会把第一张图写成 `<Picture 1>`。

    编号规则与 `translate_prompt` 完全一致（按类型各自从 1 开始）：上游把
    图片、视频、音频分三个数组收，说「第 2 张图」指的是**图片数组**的第 2 个，
    不是素材列表的第 2 个。两边各写一份编号规则，迟早出现「档案里写 图1、
    实际发出去的是 图2」这种对不上的情况 —— 出片正常，只是内容不对。

    幂等：翻译后的文本里不再有 `{{Mixed N}}`，再调一次原样返回。裸写的
    `Mixed N` 不参与翻译（当普通文本），越界报错。
    """
    counts: dict[str, int] = {}
    references: dict[str, str] = {}
    for index, entry in enumerate(entries, 1):
        kind = str(entry.get("type") or "")
        if kind not in labels:
            raise BadRequest(f"参考素材类型不受支持：{kind or '未知'}")
        counts[kind] = counts.get(kind, 0) + 1
        references[f"Mixed {index}"] = labels[kind].format(n=counts[kind])

    def replace(match: re.Match[str]) -> str:
        token = f"Mixed {int(match[1])}"
        if token not in references:
            raise BadRequest("提示词引用了不存在的 Mixed 素材")
        return references[token]

    return _REFERENCE.sub(replace, prompt)


def translate_prompt(prompt: str, entries: Sequence[Mapping[str, Any]]) -> str:
    """把第 N 个素材的引用换成「视频1 / 图2 / 音频1」。

    编号**按类型各自从 1 开始**：上游把图片、视频、音频分三个数组收，
    提示词里说「图2」指的是图片数组的第 2 个，不是素材列表的第 2 个。
    所以 `entries` 的顺序必须与上传顺序一致，否则指代整体错位。

    幂等：翻译后的文本里不再有 `{{Mixed N}}`，再调一次原样返回——档案里已经
    是编译后的文本，驱动不该因为「又翻了一遍」而报「引用了不存在的素材」。
    裸写的 `Mixed N` 不参与翻译（当普通文本），越界也不报错。
    """
    for entry in entries:
        if str(entry.get("type") or "") not in REFERENCE_TYPES:
            raise BadRequest("参考素材只支持视频、图片和音频")
    return translate_prompt_with(prompt, entries, REFERENCE_LABEL_TEMPLATES)
