# -*- coding: utf-8 -*-
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


def test_ref_in_both_texts_is_accepted() -> None:
    """两段文案里都有 → 填不填素材都引用得到。"""
    spec = spec_of(
        "⟦e:scene⟧主图 {{Mixed 1}}SS⟦/e⟧",
        scene_empty="主图 {{Mixed 1}}SS",
        scene_filled="主图 {{Mixed 1}}SS",
    )
    validate_required_slot_refs(spec)


def test_ref_in_body_is_accepted() -> None:
    """引用写在正文里（框选段之外）→ 永远在。"""
    validate_required_slot_refs(spec_of("主图 {{Mixed 1}}，场景 {{Mixed 2}}"))
