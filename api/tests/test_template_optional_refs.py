"""可选槽位**只能被框选**（2026-11-27）。

两条规则：

1. 正文里**单个引用**某个可选槽位的地方只能有一处 —— 那一处是它的插入点，
   也就是框选出来的那一段。多出来的那处是「普通引用」（粘贴进来的），库里两处
   长得一模一样，打开草稿时分辨不出哪一处才是那段默认描述 → 作者原话被顶掉。
2. 可选槽位的「未选时文案」必须**来自正文里的框选段**
   （`empty_text_from_anchor`）。老数据没有这个标记 → 预检要求作者重新框选。

**整组引用不在这条规则里**：它的语义是「列出这一组的编号」，不是插入点，
也不碰兜底文案（发布闸同样不要求整组引用写兜底文案）。
"""
from __future__ import annotations

from dataclasses import replace

import pytest

from core.exceptions import BadRequest
from core.template_spec import InputSlot, TemplateSpec, validate_optional_slot_refs


def spec_of(
    prompt: str,
    *,
    empty_text: str = "",
    from_anchor: bool = False,
    optional_refs: int = 1,
) -> TemplateSpec:
    """一个必填槽位 + 一个可选槽位的规格。

    `optional_refs` = 正文里对可选槽位的**单个引用**次数。
    """
    # 普通字符串（不是 f-string）：{{slot:scene}} 的花括号就是字面量。
    refs = " ".join("{{slot:scene}}" for _ in range(optional_refs))
    return TemplateSpec(
        key="demo",
        name="演示",
        model="image/x",
        prompt=f"主图 {{{{slot:hero}}}} 场景 {refs}",
        slots=(
            InputSlot(
                key="hero", type="image", binding="user", ref_mode="mixed",
                min_count=1, max_count=1, label="正面图", position=0,
            ),
            InputSlot(
                key="scene", type="image", binding="user", ref_mode="mixed",
                min_count=0, max_count=1, label="角色", position=1,
                empty_text=empty_text, empty_text_from_anchor=from_anchor,
            ),
        ),
    )


def test_可选槽位被单个引用两处_被拦() -> None:
    """两处插入点 → 库里分不出哪一处才是那段默认描述。"""
    with pytest.raises(BadRequest) as exc:
        validate_optional_slot_refs(spec_of("", optional_refs=2))
    assert "只能被引用一处" in str(exc.value)


def test_单个引用一处且有标记_通过() -> None:
    validate_optional_slot_refs(
        spec_of("", empty_text="一个模特", from_anchor=True, optional_refs=1)
    )


def test_未选时文案没有标记_被拦() -> None:
    """老数据：未选时文案是当年在老界面手填的，正文里没有对应的那一段。"""
    with pytest.raises(BadRequest) as exc:
        validate_optional_slot_refs(spec_of("", empty_text="一个模特", from_anchor=False))
    assert "没有对应的正文段落" in str(exc.value)
    # 消息里带上原来那句，作者可以照抄回正文。
    assert "一个模特" in str(exc.value)


def test_随机档提示词里多处引用也拦() -> None:
    """两段提示词各算各的 —— 随机档里同一句兜底文案也不该出现多次。"""
    spec = spec_of("", optional_refs=1)
    spec = replace(spec, random_prompt="场景 {{slot:scene}} {{slot:scene}}")
    with pytest.raises(BadRequest) as exc:
        validate_optional_slot_refs(spec)
    assert "随机档提示词" in str(exc.value)


def test_随机档提示词里一处引用放行() -> None:
    """随机档是另一段话，它的那一处引用与主提示词里的互不相干。"""
    spec = spec_of("", optional_refs=1)
    spec = replace(spec, random_prompt="场景 {{slot:scene}}")
    validate_optional_slot_refs(spec)


def test_整组引用不受这条规则约束() -> None:
    """整组引用的语义是「列出这组的编号」，不是插入点 → 不拦。"""
    spec = TemplateSpec(
        key="demo",
        name="演示",
        model="image/x",
        prompt="主图 {{slot:hero}} 场景 {{slot:scene*}}",
        slots=(
            InputSlot(
                key="hero", type="image", binding="user", ref_mode="mixed",
                min_count=1, max_count=1, label="正面图", position=0,
            ),
            InputSlot(
                key="scene", type="image", binding="user", ref_mode="mixed",
                min_count=0, max_count=1, label="角色", position=1,
            ),
        ),
    )
    validate_optional_slot_refs(spec)


def test_可选槽位既没被引用也没有未选时文案_通过() -> None:
    validate_optional_slot_refs(spec_of("", optional_refs=0))


def test_必填槽位不在这条规则的范围内() -> None:
    """必填槽位没有「没选」这个状态，它不是插入点，也没有兜底文案。"""
    spec = TemplateSpec(
        key="demo",
        name="演示",
        model="image/x",
        prompt="主图 {{slot:hero}}",
        slots=(
            InputSlot(
                key="hero", type="image", binding="user", ref_mode="mixed",
                min_count=1, max_count=1, label="正面图", position=0,
            ),
        ),
    )
    validate_optional_slot_refs(spec)
