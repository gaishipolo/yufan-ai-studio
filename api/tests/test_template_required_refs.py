"""必填槽位必须真的被提示词引用 —— 否则素材白传给供应商。

两条独立的错：

1. 必填槽位**完全没被引用**；
2. 它的引用**只落在会被替换掉的文案里**（作者把它框进了某个可选槽位的
   默认描述段 → 使用者一填素材，那段被「选中时文案」顶掉 → 引用消失）。
"""
from __future__ import annotations

import pytest

from core.exceptions import BadRequest
from core.template_spec import InputSlot, TemplateSpec, validate_required_slot_refs


def spec_of(prompt: str, scene_empty: str = "", scene_filled: str = "") -> TemplateSpec:
    return TemplateSpec(
        key="demo",
        name="演示",
        model="image/x",
        prompt=prompt,
        slots=(
            InputSlot(
                key="hero", type="image", binding="user", ref_mode="mixed",
                min_count=1, max_count=1, label="正面图", position=0,
            ),
            InputSlot(
                key="scene", type="image", binding="user", ref_mode="mixed",
                min_count=0, max_count=1, label="角色", position=1,
                empty_text=scene_empty, filled_text=scene_filled,
            ),
        ),
    )


def test_required_slot_never_referenced_is_rejected() -> None:
    """必填槽位一次都没被引用 → 素材白传。"""
    with pytest.raises(BadRequest) as exc:
        validate_required_slot_refs(spec_of("一个模特"))
    assert "没有引用它" in str(exc.value)


def test_ref_only_inside_a_replaceable_text_is_rejected() -> None:
    """引用只在「角色的未选时文案」里 → 角色一被填素材，引用就没了。"""
    spec = spec_of(
        "⟦e:scene⟧主图 {{Mixed 1}}SS⟦/e⟧",
        scene_empty="主图 {{Mixed 1}}SS",
    )
    with pytest.raises(BadRequest) as exc:
        validate_required_slot_refs(spec)
    assert "没人引用了" in str(exc.value)
    assert "角色" in str(exc.value)


def test_ref_in_both_texts_is_rejected() -> None:
    """两段文案里都有 → **也不行**。

    这一条是 2026-11-27 收紧的：之前这里放过「未选时文案 ∩ 选中时文案」一条
    兜底，看起来「填不填素材都引用得到」。但同一格只能二选一 ——
    使用者一改写「未选时文案」，那两段就都用不上了，引用当场消失。
    """
    spec = spec_of(
        "⟦e:scene⟧主图 {{Mixed 1}}SS⟦/e⟧",
        scene_empty="主图 {{Mixed 1}}SS",
        scene_filled="主图 {{Mixed 1}}SS",
    )
    with pytest.raises(BadRequest) as exc:
        validate_required_slot_refs(spec)
    assert "没人引用了" in str(exc.value)
    assert "角色" in str(exc.value)


def test_ref_only_in_filled_text_is_rejected() -> None:
    """引用只在「角色的选中时文案」里 → 均衡 / 随机档压根不用那一段 → 不算数。"""
    spec = spec_of(
        "⟦e:scene⟧主图SS⟦/e⟧",
        scene_empty="主图SS",
        scene_filled="主图 {{Mixed 1}}SS",
    )
    with pytest.raises(BadRequest) as exc:
        validate_required_slot_refs(spec)
    assert "没人引用了" in str(exc.value)


def test_ref_in_body_and_in_texts_is_accepted() -> None:
    """正文里有引用（哪怕文案里也有一份）→ 永远在 → 放行。"""
    spec = spec_of(
        "主图 {{Mixed 1}}，⟦e:scene⟧主图 {{Mixed 1}}SS⟦/e⟧",
        scene_empty="主图 {{Mixed 1}}SS",
        scene_filled="主图 {{Mixed 1}}SS",
    )
    validate_required_slot_refs(spec)


def test_ref_in_body_is_accepted() -> None:
    """引用写在正文里（框选段之外）→ 永远在。"""
    validate_required_slot_refs(spec_of("主图 {{Mixed 1}}，场景 {{Mixed 2}}"))


def test_使用者改写未选时文案不能引用自己() -> None:
    """三处同口径：作者侧拦自引用，使用者改写这一层也要就地拦。

    以前这里减掉了 `{key}` 放行，靠更深的 `validate_spec` 兜底 —— 报错文案
    说的是「未选时文案不能引用它自己」，定位不到是这次改写造成的。
    """
    from modules.template_pack.slot_text_overrides import apply_slot_text_overrides

    spec = spec_of("主图 {{slot:hero}}，场景 {{slot:scene}}")
    with pytest.raises(BadRequest) as exc:
        apply_slot_text_overrides(
            spec,
            {"scene": "场景参考 {{slot:scene}}"},
            {},
        )
    assert "引用了它自己" in str(exc.value)


def test_使用者改写可以引用必填槽位() -> None:
    """允许引用**别格**里必定有素材的（hero 必填），与作者侧同一套判据。"""
    from modules.template_pack.slot_text_overrides import apply_slot_text_overrides

    spec = spec_of("主图 {{slot:hero}}，场景 {{slot:scene}}")
    result = apply_slot_text_overrides(
        spec,
        {"scene": "参考主图 {{slot:hero}} 的构图"},
        {},
    )
    scene = next(slot for slot in result.slots if slot.key == "scene")
    assert scene.empty_text == "参考主图 {{slot:hero}} 的构图"
