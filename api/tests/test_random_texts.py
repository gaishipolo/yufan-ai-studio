"""「随机文案」的编译守卫。

随机档不传素材，由前端抽好文案下标传给 compile：service 层把选中的文案
替换进 `empty_text`，再交给纯函数 `compile_spec`。这里钉三件事：

1. 选中项正确注入提示词（且只影响被选的槽位）；
2. 越界 / 指向无随机文案的槽位 → 400（不静默退回 empty_text）；
3. 随机文案不许引用槽位、必填槽位不许带（规则层守卫）。

判别力：把 `_apply_random_texts` 换成 `return spec`，第 1 条变红；
把越界校验删掉，第 2 条变红。
"""

from __future__ import annotations

import pytest

from core.exceptions import BadRequest
from core.template_spec import InputSlot, TemplateSpec, compile_spec
from modules.template_pack.spec_service import _apply_random_texts


def _spec(*slots: InputSlot, prompt: str = "{{slot:scene}}") -> TemplateSpec:
    return TemplateSpec(key="demo", name="演示", model="m", prompt=prompt, slots=slots)


def _optional_scene(**over: object) -> InputSlot:
    base: dict[str, object] = {
        "key": "scene",
        "type": "image",
        "binding": "user",
        "min_count": 0,
        "random_texts": ("白天阳光下的庭院", "夜晚霓虹的街道"),
    }
    base.update(over)
    return InputSlot(**base)  # type: ignore[arg-type]


def test_selected_random_text_is_injected() -> None:
    spec = _apply_random_texts(_spec(_optional_scene()), {"scene": 1})
    compiled = compile_spec(spec, {})
    assert compiled.prompt == "夜晚霓虹的街道"


def test_only_selected_slot_is_changed() -> None:
    spec = _spec(
        _optional_scene(),
        InputSlot(
            key="other", type="image", binding="user", min_count=0, position=1,
            empty_text="未选时", random_texts=("随机甲", "随机乙"),
        ),
        prompt="{{slot:scene}} + {{slot:other}}",
    )
    compiled = compile_spec(_apply_random_texts(spec, {"scene": 0}), {})
    # scene 用了随机文案，other 没传 choice → 仍走原 empty_text
    assert compiled.prompt == "白天阳光下的庭院 + 未选时"


def test_out_of_range_choice_is_rejected() -> None:
    with pytest.raises(BadRequest, match="越界"):
        _apply_random_texts(_spec(_optional_scene()), {"scene": 9})


def test_choice_for_slot_without_random_texts_is_rejected() -> None:
    spec = _spec(InputSlot(key="scene", type="image", min_count=0, empty_text="兜底"))
    with pytest.raises(BadRequest, match="没有可用的随机文案"):
        _apply_random_texts(spec, {"scene": 0})


def test_choice_for_required_slot_is_rejected() -> None:
    spec = _spec(_optional_scene(min_count=1))
    with pytest.raises(BadRequest, match="没有可用的随机文案"):
        _apply_random_texts(spec, {"scene": 0})


def test_random_text_cannot_reference_a_slot() -> None:
    from core.template_spec import validate_spec

    spec = _spec(_optional_scene(random_texts=("参考 {{slot:other}}",)))
    with pytest.raises(BadRequest, match="随机文案不能引用槽位"):
        validate_spec(spec)
