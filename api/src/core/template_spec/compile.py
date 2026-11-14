"""把「规格 + 本次填充」编译成一份可直接提交的生成请求。

## 为什么编号在这里算

模板库页面里，用户看到的是缩略图和 chip，不是 `{{Mixed 3}}`。编号取决于
**槽位的书写顺序**（`position`）和**这次实际填了几张**：少填一张细节图，后面
所有编号都要前移。把数字写进 spec 就等于把它固化成事实，一次少填就全错位
——旧的替换链就是这么错的（`media_order` 按位置拼）。

所以 spec 里只写 `{{slot:key}}`，编号是**派生物**。本模块是唯一算它的地方：
不许在前端算（规则会有两份），也不许在封装模块里各写一遍。

## 纯函数

入参只有「规格」与「已解析好的填充」，没有 session、没有资产库、没有网络。
`type=pack` 的成员展开由调用方先用 `LibraryLookup.expand_packs()` 做完再传进来
——那是唯一有权解释"封装里有什么"的地方，这里只管编号。

同一个 `(spec, fills)` 调两次必须得到同一结果；这是 §12 不变量 4 的判据。
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping

from core.exceptions import BadRequest
from core.template_spec.models import (
    MAX_SLOT_REFS,
    CompiledRequest,
    FilledAsset,
    FilledSlot,
    InputSlot,
    TemplateSpec,
)
from core.template_spec.rules import validate_spec

#: `{{slot:key}}` / `{{slot:key*}}` / `{{slot:key.2}}`。
#: 只匹配合法的 key 形状，写错的部分留在原地，由下面的"残留引用"检查兜住。
_SLOT_REF = re.compile(r"\{\{slot:([a-z][a-z0-9_]{0,39})(\*|\.\d{1,3})?\}\}")
#: 替换后还残留这种片段，说明引用写坏了（少括号、含空格等）。
_LEFTOVER = "{{slot:"


def compile_spec(
    spec: TemplateSpec, fills: Mapping[str, FilledSlot]
) -> CompiledRequest:
    """编译成执行请求。任何不合法都抛 `BadRequest`，消息里点明是哪个槽位。"""
    validate_spec(spec)
    ordered = sorted(spec.slots, key=lambda slot: (slot.position, slot.key))
    by_key = {slot.key: slot for slot in ordered}

    unknown = set(fills) - set(by_key)
    if unknown:
        raise BadRequest(f"填充里出现未知槽位：{'、'.join(sorted(unknown))}")

    tokens: dict[str, tuple[str, ...]] = {}
    mixed_ids: list[str] = []
    attachment_ids: list[str] = []
    text_ids: list[str] = []
    counter = 0
    text_counter = 0

    for slot in ordered:
        assets = _assets_of(slot, fills.get(slot.key))
        if not assets:
            if slot.min_count == 0:
                tokens[slot.key] = ()
                continue
            raise BadRequest(f"槽位「{_label(slot)}」至少需要 {slot.min_count} 个素材")

        if not slot.is_pack and slot.binding == "user":
            count = len(assets)
            if count < slot.min_count or count > slot.max_count:
                raise BadRequest(
                    f"槽位「{_label(slot)}」需要 {slot.min_count}~{slot.max_count} 个素材，"
                    f"现在 {count} 个"
                )

        if not slot.is_pack:
            # 封装槽位的成员由资产库决定（可能图片视频混装），类型不按槽位判；
            # 数量也由成员数决定，上面那条 min/max 校验对它没有意义。
            for asset in assets:
                if asset.kind != slot.type:
                    raise BadRequest(
                        f"槽位「{_label(slot)}」要 {slot.type}，填进来的是 {asset.kind}"
                    )

        if slot.ref_mode == "attachment":
            # 附加输入不占**视觉**编号：音频/文本塞进 Mixed 会让后面的图整体偏移。
            # 但文本另有自己的编号空间（`{{Text N}}`）：执行层按 `textLibraryIds`
            # 的顺序注入正文，所以 N 必须与本段追加进 `text_ids` 的顺序一致。
            if slot.type == "text":
                assigned = tuple(
                    f"{{{{Text {text_counter + index + 1}}}}}"
                    for index in range(len(assets))
                )
                text_counter += len(assets)
                tokens[slot.key] = assigned
                text_ids.extend(asset.asset_id for asset in assets)
            else:
                tokens[slot.key] = ()
                attachment_ids.extend(asset.asset_id for asset in assets)
            continue

        assigned = tuple(
            f"{{{{Mixed {counter + index + 1}}}}}" for index in range(len(assets))
        )
        counter += len(assets)
        tokens[slot.key] = assigned
        mixed_ids.extend(asset.asset_id for asset in assets)

    if counter > MAX_SLOT_REFS:
        raise BadRequest(
            f"参与编号的素材共 {counter} 个，超过上限 {MAX_SLOT_REFS}；"
            "请把一组图放进资产库封装，或用附加输入"
        )

    prepend = _expander(by_key, tokens)
    prompt = prepend(spec.prompt, "提示词")
    return CompiledRequest(
        model=spec.model,
        prompt=prompt,
        params=dict(spec.params),
        # 参考素材：先按编号顺序的视觉参考，再追加不占号的附加输入（音频）。
        reference_ids=tuple([*mixed_ids, *attachment_ids]),
        text_ids=tuple(text_ids),
        slot_tokens={key: value for key, value in tokens.items() if value},
    )


def _assets_of(slot: InputSlot, fill: FilledSlot | None) -> tuple[FilledAsset, ...]:
    """取本次填进该槽位的素材，并校验"固定的东西没被换掉"。"""
    assets: tuple[FilledAsset, ...] = () if fill is None else fill.assets
    if slot.binding == "fixed":
        if not slot.is_pack:
            got = [asset.asset_id for asset in assets]
            if got != [slot.asset_id]:
                raise BadRequest(
                    f"槽位「{_label(slot)}」在封装时已固定为 {slot.asset_id}，"
                    f"填充给的是 {got or '空'}"
                )
        elif fill is None or fill.pack_id != slot.pack_id:
            raise BadRequest(
                f"槽位「{_label(slot)}」在封装时已固定为封装 {slot.pack_id}，"
                f"填充给的是 {(fill.pack_id if fill else None) or '空'}"
            )
    return assets


def _expander(
    by_key: Mapping[str, InputSlot], tokens: Mapping[str, tuple[str, ...]]
) -> Callable[[str, str], str]:
    """造一个"把 `{{slot:…}}` 换成编号"的替换函数。

    用 `re.sub` 单遍替换而不是逐个 `str.replace`：替换结果里含 `{{Mixed N}}`，
    多遍替换会把新生成的东西再扫一遍。
    """

    def expand(text: str, where: str) -> str:
        def repl(match: re.Match[str]) -> str:
            key, modifier = match.group(1), match.group(2) or ""
            slot = by_key.get(key)
            if slot is None:
                raise BadRequest(f"{where}引用了不存在的槽位：{key}")
            if slot.ref_mode == "attachment" and slot.type != "text":
                # 文本槽位有 `{{Text N}}` 这个独立编号空间，可以引用；音频没有。
                raise BadRequest(
                    f"{where}不能引用「{_label(slot)}」：它是附加输入，不参与编号"
                )
            owned = tokens.get(key, ())
            if modifier == "*":
                # 整组引用：没填就当这组不存在（可选槽位的自然语义）。
                # 单张引用不能这样处理 —— `{{slot:x}}` 的语义是"这一张"，
                # 悄悄变空会让提示词缺一个必需引用而没人发现。
                return "、".join(owned)
            if not owned:
                raise BadRequest(f"{where}引用了未填素材的槽位「{_label(slot)}」")
            if modifier.startswith("."):
                index = int(modifier[1:])
                if index < 1 or index > len(owned):
                    raise BadRequest(
                        f"{where}引用的「{_label(slot)}」第 {index} 张不存在"
                        f"（这次填了 {len(owned)} 张）"
                    )
                return owned[index - 1]
            return owned[0]

        result = _SLOT_REF.sub(repl, text)
        if _LEFTOVER in result:
            raise BadRequest(f"{where}里有无法解析的槽位引用（检查括号与槽位 key）")
        return result.strip()

    return expand


def _label(slot: InputSlot) -> str:
    return slot.label.strip() or slot.key
