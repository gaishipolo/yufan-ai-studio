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
素材事实（是哪些素材、什么类型、什么顺序）由调用方查好传进来，这里只管编号。

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
from core.template_spec.rules import _LEFTOVER, normalize_refs, validate_spec

#: `{{slot:key}}` / `{{slot:key*}}` / `{{slot:key.2}}`，**两边允许空格**。
#: 只匹配合法的 key 形状，写错的部分留在原地，由下面的"残留引用"检查兜住。
#:
#: 空格这一条必须与 `rules._SLOT_REF`（结构校验）一致：那边容空格、这边不容，
#: 结果就是「预检与发布都过、用户在模板库点生成才 400」。同一个形状两处两种口径，
#: 迟早踩到（2026-11-15 审查 3.4 的同源问题）。
#:
#: 与 `rules._SLOT_REF` 的差别是**这里要抓修饰符**（`*` / `.N`），所以形状
#: 不能直接共用一份 —— 但残留检查（`_LEFTOVER`）是同一件事，只在 `rules`
#: 定义一次，两边导入，别各抄一份（这个形状已经漂移过三次）。
_SLOT_REF = re.compile(r"\{\{\s*slot:([a-z][a-z0-9_]{0,39})(\*|\.\d{1,3})?\s*\}\}")


def compile_spec(
    spec: TemplateSpec, fills: Mapping[str, FilledSlot]
) -> CompiledRequest:
    """编译成执行请求。任何不合法都抛 `BadRequest`，消息里点明是哪个槽位。"""
    # 作者写的 `{{Mixed N}}` → `{{slot:key}}`：后面只处理一种写法。
    # 这里必须**自己**归一化，不能只靠 `validate_spec` —— 它内部的副本不外传，
    # `spec` 仍是 Mixed 形态，`_SLOT_REF` 会一个都匹配不到（提示词里的引用
    # 就会原样进供应商请求）。
    spec = normalize_refs(spec)
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

        if slot.binding == "user":
            count = len(assets)
            if count < slot.min_count or count > slot.max_count:
                raise BadRequest(
                    f"槽位「{_label(slot)}」需要 {slot.min_count}~{slot.max_count} 个素材，"
                    f"现在 {count} 个"
                )

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
            "请减少参考素材数量，或改用附加输入"
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
        got = [asset.asset_id for asset in assets]
        if got != [slot.asset_id]:
            raise BadRequest(
                f"槽位「{_label(slot)}」在封装时已固定为 {slot.asset_id}，"
                f"填充给的是 {got or '空'}"
            )
    return assets


def _expander(
    by_key: Mapping[str, InputSlot], tokens: Mapping[str, tuple[str, ...]]
) -> Callable[[str, str], str]:
    """造一个"把 `{{slot:…}}` 换成编号"的替换函数。

    用 `re.sub` 单遍替换而不是逐个 `str.replace`：替换结果里含 `{{Mixed N}}`，
    多遍替换会把新生成的东西再扫一遍。

    ## 三种引用，三种语义

    | 写法 | 填了素材 | 没填素材（可选槽位） |
    |---|---|---|
    | `{{slot:x}}` | **段落**：插入「选中时文案」，空则插编号 | 插入「未选时文案」，空则报错 |
    | `{{slot:x*}}` | 这组的编号列表（`、{{Mixed 2}}、{{Mixed 3}}`） | 降级为空（既有语义） |
    | `{{slot:x.N}}` | 第 N 张的编号 | 报错 |

    **必填槽位（`min_count >= 1`）只有「填了素材」这一种情况**：
    它的素材一定在（不填在编译期就报错），所以单张引用**直接插编号** ——
    两段文案对它没有意义，结构校验也不允许写（见 `rules._validate_slot_texts`）。
    「选中时 / 未选时」两种说法是**可选槽位**才有的两种状态。

    三种写法**语义不同**，所以兜底规则也不同：

    - 只有**单张引用**走「段落」：`{{slot:x*}}` 的语义是"这组的编号"，
      套一层段落反而会让人以为整组都进去了（作者写「人物参考 {{slot:x}}」
      而正文用 `{{slot:x*}}` 时，段落里只会有第一张 —— 另一张悄悄消失）。
      要在段落里表达整组，就在**段落内部**写 `{{slot:x*}}`。
    - 整组引用没填时**降级为空**（不报错）：它的语义是「列出这组」，
      没有就列出空。这是既有行为，`test_short_fill_shifts_later_numbers` 钉着。
    - 「第 N 张」没填时**报错**：没有「第 N 张」这回事，兜底文案是整段话、
      不是某个编号，套不上去。

    ## 为什么「没填」要有兜底而不是静默留空

    静默留空的后果是：提示词里**少了一整段**，模型照跑、结果不对，
    而日志里什么线索都没有（用户只会觉得"这模板效果不好"）。
    所以单张引用没填且没有兜底文案 = 报错，并说清要写什么。
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
                # **整组引用** = 这组的编号列表（`、{{Mixed 2}}、{{Mixed 3}}`）。
                # 没填时**降级为空**：它的语义是「列出这组」，没有就列出空 ——
                # 这是既有行为（`test_short_fill_shifts_later_numbers` 钉着）。
                # 作者写了「未选时文案」就用它接管这一块。
                if owned:
                    return "、".join(owned)
                return _expand_slot_text(slot.empty_text, slot, where, by_key, tokens)
            if modifier.startswith("."):
                # **第 N 张**：没有「第 N 张」这回事，兜底文案也不适用
                # （兜底是整段话，不是某个编号）。所以只有一句实话：不存在。
                index = int(modifier[1:])
                if index < 1 or index > len(owned):
                    raise BadRequest(
                        f"{where}引用的「{_label(slot)}」第 {index} 张不存在"
                        f"（这次填了 {len(owned)} 张）"
                    )
                return owned[index - 1]
            # **单张引用** = 段落插入点：
            # - 填了：作者写了「选中时文案」就用它（内部可再引用自己/整组），
            #   没写就插编号（= 旧行为，现有模板一字不改也能继续用）。
            # - 没填：作者写了「未选时文案」就用它；没写就报错
            #   （静默留空会让提示词缺一整段，模型照跑、结果不对且不留痕迹）。
            #
            # **必填槽位（`min_count >= 1`）走不到这里**：它一定有素材，
            # 「选中时文案」对它没有意义（结构校验已经拦掉）—— 必填就是
            # **直接插编号**，只有可选槽位才有「填了 / 没填」两种说法。
            if owned:
                if slot.filled_text.strip():
                    return _expand_slot_text(
                        slot.filled_text, slot, where, by_key, tokens
                    )
                return owned[0]
            return _fallback_or_raise(slot, where, by_key, tokens)

        result = _SLOT_REF.sub(repl, text)
        if _LEFTOVER.search(result):
            raise BadRequest(f"{where}里有无法解析的槽位引用（检查括号与槽位 key）")
        return result.strip()

    return expand


def _fallback_or_raise(
    slot: InputSlot,
    where: str,
    by_key: Mapping[str, InputSlot],
    tokens: Mapping[str, tuple[str, ...]],
) -> str:
    """没填素材时的兜底：作者写了「未选时文案」就用它，没写就报错。

    **报错而不是静默留空**：留空的后果是提示词里少了一整段，模型照跑、
    结果不对，而日志里什么线索都没有 —— 用户只会觉得"这模板效果不好"。
    报错时要说清**要做什么**，不然作者不知道该去哪儿改。

    兜底文案里**可以有引用**（见 `_expand_slot_text`）：它替的是这一格，
    但里面提到的**别的**必填 / 已固定槽位，编号照样成立。
    """
    fallback = slot.empty_text.strip()
    if fallback:
        return _expand_slot_text(fallback, slot, where, by_key, tokens)
    raise BadRequest(
        f"{where}引用了未填素材的槽位「{_label(slot)}」"
        + (
            "：它是可选槽位，请在模板封装里写「未选时文案」"
            "（没素材时这一段怎么说），或者把它改成必填"
            if slot.min_count == 0
            else ""
        )
    )


def _expand_slot_text(
    text: str,
    owner: InputSlot,
    where: str,
    by_key: Mapping[str, InputSlot],
    tokens: Mapping[str, tuple[str, ...]],
) -> str:
    """展开一段**槽位文案**（选中时 / 未选时文案）里的引用。

    文案里的引用指**那个槽位的编号**（它排在第几号），不是把那个槽位的
    文案整段抄进来 —— 那是「段落插入点」的语义，在文案里嵌段落会递归，
    也没人能推理得清。

    **只允许引用必定有素材的槽位**（必填 / 已固定，见
    `rules.is_always_filled`）：那种槽位每次编译都有素材、都有编号。
    引用可选槽位是没意义的 —— 它可能没人填，那时没有编号可指。
    结构校验（`rules._validate_slot_texts`）已经按这条拦过，
    这里再判一次是**兜底**：编译期报错比出提示词里一句悬空的编号强。

    引用自己（`{{slot:自己的key}}`）在**选中时文案**里成立：
    那一格有素材，编号存在（这也是「人物参考 {{slot:hero}}」的写法）。
    在**未选时文案**里不成立 —— 那一格恰恰没有素材，规则层已经拒了。
    """
    def repl(match: re.Match[str]) -> str:
        key, modifier = match.group(1), match.group(2) or ""
        target = by_key.get(key)
        if target is None:
            raise BadRequest(f"{where}引用了不存在的槽位：{key}")
        if target.ref_mode == "attachment" and target.type != "text":
            raise BadRequest(
                f"{where}不能引用「{_label(target)}」：它是附加输入，不参与编号"
            )
        owned = tokens.get(key, ())
        if not owned:
            # 目标槽位这次没有素材 → 没有编号可指。
            raise BadRequest(
                f"{where}引用了「{_label(target)}」，但它这次没有素材："
                "没有编号可指"
            )
        if modifier == "*":
            return "、".join(owned)
        if modifier.startswith("."):
            index = int(modifier[1:])
            if index < 1 or index > len(owned):
                raise BadRequest(
                    f"{where}引用的「{_label(target)}」第 {index} 张不存在"
                    f"（这次填了 {len(owned)} 张）"
                )
            return owned[index - 1]
        return owned[0]

    result = _SLOT_REF.sub(repl, text)
    if _LEFTOVER.search(result):
        raise BadRequest(f"{where}里有无法解析的槽位引用（检查括号与槽位 key）")
    return result


def _label(slot: InputSlot) -> str:
    return slot.label.strip() or slot.key
