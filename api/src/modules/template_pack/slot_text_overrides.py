"""改写「未选时文案」的唯一闸门。

使用者（自定义档）与模型（随机档）改的是同一样东西：某个可选槽位的
`empty_text`。两边的检查必须完全一致，否则会出现「作者/使用者改得、模型改不得」
这种没道理的差别。所以这个函数只留一个写点，编译与随机改写都调它。

放在独立模块而不是 `spec_service.py`：随机改写要 import 它，而编译入口
（`spec_service.compile_for`）将来要 import 随机改写。放在 service 里会成环。

与作者侧 `rules._validate_slot_texts` 的分工：那边校验**模板声明**（作者写进
版本里的文案）；这里校验**这一次改写的文案**（可能来自使用者，也可能来自模型），
还要判断「这次改写有没有生效的位置」。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace

from core.exceptions import BadRequest
from core.template_spec import (
    InputSlot,
    TemplateSpec,
    always_filled_slot_keys,
    normalize_text_refs,
    slot_ref_modes,
    slot_refs,
    strip_valid_refs,
)

#: 使用者改写「未选时文案」的长度上限。与作者那一栏同口径
#: （`SpecSlotIn.empty_text` 的 `max_length`）：同一个槽位、同一段话，
#: 只是写的人不同，不该有两套限制。
MAX_SLOT_TEXT_OVERRIDE = 2000


def apply_slot_text_overrides(
    spec: TemplateSpec,
    overrides: Mapping[str, str],
    slot_values: Mapping[str, Sequence[str]],
) -> TemplateSpec:
    """把改写的「未选时文案」换进 spec。

    放在 `compile_spec` **之前**改 spec（与随机档换 `prompt` 同一手法、同一位置
    之后）：编译核心因此保持纯函数，同一 `(spec, fills)` 必得同一结果。
    传进来的 `spec.prompt` 已是**这次真正要编的那一段**（随机档已换过），
    所以「有没有插入点」查的就是它，不会出现「插入点在另一档里、改了也不生效」。

    九道 400 都在这里，都点明是哪一格、为什么不生效。静默收下的后果是
    「用户改的话没进提示词，页面看起来一切正常」—— 那比报错难查得多：

    1. 槽位不存在（拼错 key / 页面停在旧版本）
    2. 必填或固定槽位：没有「没选」这个状态，兜底文案本来就用不上
    3. 同一格既挑了素材又改了文案：两者都在回答「这一格是什么」，
       取谁都是替使用者做决定
    4. 提示词里没有插入点：只有单张引用 `{{slot:x}}` 会用兜底文案；
       整组 / 第 N 张已由结构校验拒，不是另一种插入点
    5. 文案为空或含 `{{`：空的到编译期必报错（且报的是「作者没写文案」，
       与眼前的情况对不上）；占位符放进去会在编译期炸成一句看不懂的话
    6. 引用自己：这段话用的正是这一格没素材的时候，那时没有编号可指
    7. 引用可选槽位：它可能没人填，编号不一定存在
    8. 引用不存在的槽位：同样没有编号可指
    9. 留下认不出来的花括号：会原样进供应商提示词

    第 6–8 条只在文案含 `{{` 时才跑；不含占位符的纯文字只走前面五条。
    """
    if not overrides:
        return spec
    by_key = {slot.key: slot for slot in spec.slots}
    known = set(by_key)
    known_always_filled = always_filled_slot_keys(spec)
    # 与发布闸同一个判据（`slot_ref_modes`）：只认**单张**引用。
    modes = slot_ref_modes(spec.prompt)
    replaced: dict[str, InputSlot] = {}
    for key, raw in overrides.items():
        text = (raw or "").strip()
        slot = by_key.get(key)
        name = (slot.label.strip() or key) if slot else key
        if slot is None:
            raise BadRequest(f"改文案的槽位不存在：{key}")
        if slot.binding == "fixed" or slot.min_count >= 1:
            raise BadRequest(
                f"「{name}」是必填槽位，没有「未选」这个状态，"
                "不能改未选时文案；要改请把它改成可选槽位"
            )
        if any(item for item in slot_values.get(key, ())):
            raise BadRequest(f"「{name}」既挑了素材又改了文案：一格只有一种说法，请二选一")
        if "" not in modes.get(key, set()):
            raise BadRequest(
                f"「{name}」在这份模板的提示词里没有插入点"
                "（要写成 {{slot:" + key + "}}），改的文案不会进提示词"
            )
        if not text:
            raise BadRequest(f"「{name}」改写的提示词不能为空")
        if len(text) > MAX_SLOT_TEXT_OVERRIDE:
            raise BadRequest(
                f"「{name}」改写的提示词 {len(text)} 字，超过上限 {MAX_SLOT_TEXT_OVERRIDE} 字"
            )
        # 改写里可以引用**必定有素材**的槽位（必填 / 已固定）：
        # 那段话提到的别格素材一定在，编号一定成立。规则与作者侧同一套
        # （core/template_spec/rules.py::_validate_slot_texts）。
        #
        # 先把作者写法（{{Mixed N}}）归一成内部写法再判 —— 改写框里
        # 预填的就是作者写法，不归一就认不出它引用了谁。
        if "{{" in text:
            normalized = normalize_text_refs(text, spec, where=f"「{name}」改写的提示词")
            refs = set(slot_refs(normalized))
            # 改写的是「未选时文案」—— 用的正是这一格没素材的时候，
            # 所以它不能引用自己（那一格此时没有编号可指）。规范 §12 不变量 25
            # 要求三处同口径：作者侧 `_validate_slot_texts` 已拦自引用，
            # 这里也要就地拦，不能靠更深的 validate_spec 兜底（那样错误
            # 文案说的是「未选时文案」，定位不到是这次改写造成的）。
            if key in refs:
                raise BadRequest(
                    f"「{name}」改写的提示词里引用了它自己："
                    "这段话用的正是这一格没挑素材的时候，那时没有编号可指"
                )
            unknown = sorted(refs - known)
            if unknown:
                raise BadRequest(
                    f"「{name}」改写的提示词里引用了不存在的槽位「{'、'.join(unknown)}」："
                    "请删掉它，或改成这份模板里真实存在的槽位"
                )
            forbidden = sorted(refs - known_always_filled)
            if forbidden:
                raise BadRequest(
                    f"「{name}」改写的提示词里引用了「{'、'.join(forbidden)}」："
                    "只能引用必定有素材的槽位（必填 / 已固定）—— "
                    "可选槽位可能没人填，那时没有编号可指"
                )
            leftover = strip_valid_refs(normalized)
            if "{{" in leftover or "}}" in leftover:
                raise BadRequest(
                    f"「{name}」改写的提示词里有认不出来的占位符："
                    "这一段是纯文字，编号由提示词骨架决定"
                )
        replaced[key] = replace(slot, empty_text=text)
    if not replaced:
        return spec
    return replace(
        spec,
        slots=tuple(replaced.get(slot.key, slot) for slot in spec.slots),
    )


__all__ = ["MAX_SLOT_TEXT_OVERRIDE", "apply_slot_text_overrides"]