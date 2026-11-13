"""视频模板的媒体清单计算：**纯函数，无 IO**。

这三个函数原来混在 `video_template_store.py` 里，靠 `await asyncio.to_thread(...)`
把整段（读文件 + 算 + 写文件）丢进线程池才显得像业务代码。拆出来的好处很直接：
它们只是一组字符串与列表运算，可以同步单测，也不用再为它们付线程池的切换成本。

`resolve_media` 没搬过来——它要查素材包与素材名，是 IO，留在 service 里。

## 为什么 `{{Mixed N}}` 的编号必须连续

`mediaOrder` 的下标 +1 就是 `{{Mixed N}}` 的 N，而编译器按这个编号往提示词里
填图。中间少一位不会报错，只会让 `{{Mixed 3}}` 指向本该是第 4 张的图——错误
一路传到成片才看得出来。`validate_media()` 的逐项 token 校验就是钉住这件事。
"""

from __future__ import annotations

import re
from typing import Any

from core.exceptions import BadRequest
from modules.template.video_template_schema import VideoTemplateMedia

__all__ = [
    "SLOT_RE",
    "TOKEN_RE",
    "apply_pack_roles",
    "cited_slots",
    "prompt_tokens",
    "slot_number",
    "validate_media",
]

TOKEN_RE = re.compile(r"\{\{([^{}]+)\}\}")
SLOT_RE = re.compile(r"^Mixed (\d+)$")

#: 可选的替换范围占位符：运行文案里替换成用户选的范围，不算媒体槽位。
SCOPE_TOKEN = "ReplacementScope"


def slot_number(token: str) -> int:
    """`{{Mixed 3}}` → `3`。不合法直接抛，别让调用方各自 try。"""
    inner = token[2:-2] if token.startswith("{{") and token.endswith("}}") else token
    match = SLOT_RE.match(inner)
    if match is None:
        raise BadRequest(f"非法占位符 {{{{{inner}}}}}：只允许 {{{{Mixed N}}}}")
    return int(match.group(1))


def prompt_tokens(prompt: str, scene_text: bool = False) -> set[str]:
    """封装时允许出现的占位符集合。

    `{{ReplacementScope}}` 与文字换场景的 `{{ScenePrompt}}` 不是媒体槽位，
    剔除后不参与 mixedList 长度校验。
    """
    found = TOKEN_RE.findall(prompt)
    if not found and not scene_text:
        raise BadRequest("提示词缺少 {{Mixed N}} 占位符")
    tokens: set[str] = set()
    for raw in found:
        if raw == SCOPE_TOKEN or (scene_text and raw == "ScenePrompt"):
            continue
        match = SLOT_RE.match(raw)
        if not match:
            raise BadRequest(f"非法占位符 {{{{{raw}}}}}：只允许 {{{{Mixed N}}}}")
        number = int(match.group(1))
        if number < 1:
            raise BadRequest("占位符编号从 1 开始")
        tokens.add(f"{{{{Mixed {number}}}}}")
    return tokens


def cited_slots(prompt: str, *, scene_text: bool = False) -> tuple[list[int], bool]:
    """提示词点名了哪些槽位（`Mixed N` 的 N），以及是否引用可选范围参数。

    与 `prompt_tokens` 的差别只有一处：改提示词允许 `{{ReplacementScope}}`，
    它不算媒体槽位。
    """
    numbers: list[int] = []
    scope = False
    for raw in TOKEN_RE.findall(prompt):
        if scene_text and raw == "ScenePrompt":
            continue
        if raw == SCOPE_TOKEN:
            scope = True
            continue
        match = SLOT_RE.match(raw)
        if not match:
            raise BadRequest(
                f"非法占位符 {{{{{raw}}}}}：只允许 {{{{Mixed N}}}} 或 {{{{ReplacementScope}}}}"
            )
        number = int(match.group(1))
        if number < 1:
            raise BadRequest("占位符编号从 1 开始")
        if number not in numbers:
            numbers.append(number)
    return numbers, scope


def apply_pack_roles(media: list[VideoTemplateMedia], refs: list[Any]) -> None:
    """用途以素材包为准：显式清单只决定顺序，不决定「这张是主图还是三视图」。

    封装界面不传 role（传了也未必与包一致）。若照抄空 role，运行时按用途取图
    会全部落空，「换一个产品包、提示词不用改」就退化成按 asset_id 死配。
    """
    by_asset = {ref.asset_id: ref.role for ref in refs}
    for item in media:
        if item.type == "image" and item.asset_id in by_asset:
            item.role = by_asset[item.asset_id]


def validate_media(
    media: list[VideoTemplateMedia],
    tokens: set[str],
    source_index: int = 0,
    video_only: bool = False,
) -> list[VideoTemplateMedia]:
    """清单自洽性检查，返回其中的图片项。

    这条链上每一步都在防同一类事故：提示词里点名的编号与真正插进接口的
    媒体顺序不一致。所以「原片唯一」「token 与下标对齐」「`inPrompt` 与提示词
    引用一致」「参考图不重复」必须一次全查，漏一条都只在成片阶段暴露。
    """
    if not media:
        raise BadRequest("媒体清单不能为空")
    source_token = f"{{{{Mixed {source_index + 1}}}}}"
    if (
        source_index >= len(media)
        or media[source_index].type != "video"
        or sum(item.type == "video" for item in media) != 1
    ):
        raise BadRequest(f"{source_token} 必须是唯一的完整原片")
    for index, item in enumerate(media, 1):
        expected = f"{{{{Mixed {index}}}}}"
        if item.token != expected:
            raise BadRequest(f"第 {index} 项的 token 应为 {expected}，实际 {item.token}")
    for token in tokens:
        if slot_number(token) > len(media):
            raise BadRequest(f"{token} 超出 mixedList 长度 {len(media)}")
    if source_token not in tokens and not video_only:
        raise BadRequest(f"提示词必须引用 {source_token} 原片")
    if not video_only and not any(item.type == "image" and item.token in tokens for item in media):
        raise BadRequest("提示词必须至少点名一张参考图")
    marked = {item.token for item in media if item.in_prompt}
    if marked != tokens:
        raise BadRequest(f"inPrompt 标记 {sorted(marked)} 与提示词引用 {sorted(tokens)} 不一致")
    images = [item for item in media if item.type == "image"]
    if not images and not video_only:
        raise BadRequest("至少要有 1 张参考图")
    # 空槽位（`assetId` 为空）是「运行时由第 N 张人物卡填」的声明，不是真图，
    # 不能参与去重：否则两个空槽会被判成「重复图片」。
    asset_ids = [item.asset_id for item in images if item.asset_id]
    if len(set(asset_ids)) != len(asset_ids):
        raise BadRequest("参考图不能重复")
    return images
