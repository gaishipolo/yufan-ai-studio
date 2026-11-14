"""`core.template_spec` 的纯函数：编号派生与引用展开。

这些用例**不碰数据库**——编译是纯函数，能离开 session 测才说明它真的没在算
之外偷偷查东西（§12 不变量 4）。
"""

from __future__ import annotations

import pytest

from core.exceptions import BadRequest
from core.template_spec import (
    FilledAsset,
    FilledSlot,
    InputSlot,
    PromptParts,
    TemplateSpec,
    compile_spec,
    validate_spec,
)

VIDEO = FilledAsset("v-src", "video", name="原片.mp4")
HERO = FilledAsset("i-hero", "image", name="主图.png")
DETAIL_1 = FilledAsset("i-d1", "image", role="detail", name="细节1.png")
DETAIL_2 = FilledAsset("i-d2", "image", role="detail", name="细节2.png")
BGM = FilledAsset("a-bgm", "audio", name="配乐.mp3")
NOTE = FilledAsset("t-note", "text", name="文案.md")


def _spec(*slots: InputSlot, locked: str = "", editable: str = "x {{slot:hero}}") -> TemplateSpec:
    return TemplateSpec(
        key="demo",
        name="演示",
        model="libtv-wan3-replacement",
        slots=tuple(slots),
        prompt=PromptParts(locked=locked, editable=editable),
    )


def _source(**over: object) -> InputSlot:
    base: dict[str, object] = {
        "key": "source",
        "type": "video",
        "binding": "fixed",
        "asset_id": "v-src",
        "position": 0,
        "label": "原片",
    }
    base.update(over)
    return InputSlot(**base)  # type: ignore[arg-type]


def _user(key: str, position: int, **over: object) -> InputSlot:
    base: dict[str, object] = {
        "key": key,
        "type": "image",
        "binding": "user",
        "position": position,
        "label": key,
    }
    base.update(over)
    return InputSlot(**base)  # type: ignore[arg-type]


def _fills(**slots: tuple[FilledAsset, ...]) -> dict[str, FilledSlot]:
    return {key: FilledSlot(slot_key=key, assets=assets) for key, assets in slots.items()}


def test_numbers_follow_slot_order_and_skip_attachments() -> None:
    """编号只由 position + 实际填充量决定；音频不占号。"""
    spec = _spec(
        _source(),
        _user("hero", 1),
        _user("detail", 2, min_count=0, max_count=2),
        InputSlot(
            key="bgm", type="audio", binding="user",
            ref_mode="attachment", position=3, label="配乐",
        ),
        editable=(
            "{{slot:hero}} 与 {{slot:detail*}}；原片 {{slot:source}}"
        ),
    )
    out = compile_spec(
        spec,
        _fills(source=(VIDEO,), hero=(HERO,), detail=(DETAIL_1, DETAIL_2), bgm=(BGM,)),
    )

    assert out.prompt == "{{Mixed 2}} 与 {{Mixed 3}}、{{Mixed 4}}；原片 {{Mixed 1}}"
    # 视觉参考按编号顺序，附加输入追加在后 —— 音频永远不插到图中间。
    assert out.reference_ids == ("v-src", "i-hero", "i-d1", "i-d2", "a-bgm")
    assert out.slot_tokens == {
        "source": ("{{Mixed 1}}",),
        "hero": ("{{Mixed 2}}",),
        "detail": ("{{Mixed 3}}", "{{Mixed 4}}"),
    }


def test_short_fill_shifts_later_numbers() -> None:
    """少填一张细节图，后面的编号自动前移 —— 这正是「编号不落库」的原因。"""
    spec = _spec(
        _source(),
        _user("hero", 1),
        _user("detail", 2, min_count=0, max_count=2),
        _user("look", 3, min_count=0, max_count=1),
        editable="{{slot:hero}} {{slot:detail*}} {{slot:look}}",
    )
    out = compile_spec(
        spec,
        _fills(source=(VIDEO,), hero=(HERO,), detail=(), look=(FilledAsset("i-look", "image"),)),
    )
    assert out.prompt == "{{Mixed 2}}  {{Mixed 3}}"


def test_pack_slot_keeps_library_order_and_never_reorders() -> None:
    """封装成员顺序由资产库的 position 给定，编译不得重排。"""
    spec = _spec(
        _source(),
        InputSlot(
            key="detail",
            type="image",
            source="pack",
            binding="user",
            position=1,
            label="细节封装",
        ),
        editable="{{slot:detail*}}",
    )
    # 故意给一个"倒序 id"：若编译按 id 排序就会失败。
    fills = {
        "source": FilledSlot("source", (VIDEO,)),
        "detail": FilledSlot(
            "detail",
            (FilledAsset("z-third", "image"), FilledAsset("a-first", "image")),
            pack_id="pack-1",
            pack_name="6036 三视图",
        ),
    }
    out = compile_spec(spec, fills)
    assert out.prompt == "{{Mixed 2}}、{{Mixed 3}}"
    assert out.reference_ids == ("v-src", "z-third", "a-first")


def test_fixed_pack_cannot_be_swapped() -> None:
    spec = _spec(
        InputSlot(
            key="detail", type="image", source="pack", binding="fixed",
            pack_id="pack-1", position=0, label="细节封装",
        ),
        editable="{{slot:detail*}}",
    )
    with pytest.raises(BadRequest, match="已固定为封装"):
        compile_spec(spec, {"detail": FilledSlot("detail", (DETAIL_1,), pack_id="pack-2")})


def test_text_slot_goes_to_text_ids_and_never_takes_a_number() -> None:
    spec = _spec(
        _source(),
        _user("hero", 1),
        InputSlot(
            key="brief", type="text", binding="user", ref_mode="attachment",
            position=2, label="文案",
        ),
        editable="{{slot:hero}}",
    )
    out = compile_spec(
        spec, _fills(source=(VIDEO,), hero=(HERO,), brief=(NOTE,))
    )
    assert out.text_ids == ("t-note",)
    assert out.reference_ids == ("v-src", "i-hero")
    assert "brief" not in out.slot_tokens


@pytest.mark.parametrize(
    ("editable", "message"),
    [
        ("{{slot:missing}}", "不存在的槽位"),
        ("{{slot:bgm}}", "附加输入"),
        ("{{slot:hero.2}}", "第 2 张不存在"),
        ("{{slot:hero", "无法解析"),
    ],
)
def test_bad_references_are_rejected(editable: str, message: str) -> None:
    spec = _spec(
        _source(),
        _user("hero", 1),
        InputSlot(
            key="bgm", type="audio", binding="user", ref_mode="attachment",
            position=2, min_count=0, label="配乐",
        ),
        editable=editable,
    )
    with pytest.raises(BadRequest, match=message):
        compile_spec(spec, _fills(source=(VIDEO,), hero=(HERO,), bgm=(BGM,)))


def test_count_bounds_and_type_are_enforced() -> None:
    spec = _spec(_source(), _user("hero", 1, min_count=1, max_count=2), editable="{{slot:hero*}}")
    with pytest.raises(BadRequest, match="至少需要"):
        compile_spec(spec, _fills(source=(VIDEO,), hero=()))
    with pytest.raises(BadRequest, match="需要 1~2 个素材"):
        compile_spec(
            spec,
            _fills(source=(VIDEO,), hero=(HERO, DETAIL_1, DETAIL_2)),
        )
    with pytest.raises(BadRequest, match="填进来的是 video"):
        compile_spec(spec, _fills(source=(VIDEO,), hero=(VIDEO,)))


def test_too_many_numbered_refs_is_rejected() -> None:
    many = tuple(FilledAsset(f"i-{index}", "image") for index in range(11))
    spec = _spec(
        _source(),
        _user("hero", 1, min_count=0, max_count=11),
        editable="{{slot:hero*}}",
    )
    with pytest.raises(BadRequest, match="超过上限"):
        compile_spec(spec, _fills(source=(VIDEO,), hero=many))


def test_spec_must_not_hardcode_mixed_numbers() -> None:
    """写死 `{{Mixed N}}` 会在少填时错位，预检当场拒。"""
    spec = _spec(_source(), _user("hero", 1), editable="{{Mixed 2}}")
    with pytest.raises(BadRequest, match="不要直接写"):
        validate_spec(spec)


def test_locked_segment_is_expanded_too_and_joins_without_blank_line() -> None:
    spec = _spec(
        _source(),
        _user("hero", 1),
        locked="8 秒成片，参考 {{slot:hero}}",
        editable="{{slot:source}} 换成参考图",
    )
    out = compile_spec(spec, _fills(source=(VIDEO,), hero=(HERO,)))
    assert out.prompt == "8 秒成片，参考 {{Mixed 2}}\n\n{{Mixed 1}} 换成参考图"

    only_editable = _spec(
        _source(), _user("hero", 1), locked="  ", editable="{{slot:hero}}"
    )
    out2 = compile_spec(only_editable, _fills(source=(VIDEO,), hero=(HERO,)))
    assert out2.prompt == "{{Mixed 2}}"


def test_compile_is_pure() -> None:
    """同输入两次结果必须一致（§12 不变量 4）。"""
    spec = _spec(_source(), _user("hero", 1), editable="{{slot:hero}} / {{slot:source}}")
    fills = _fills(source=(VIDEO,), hero=(HERO,))
    first = compile_spec(spec, fills)
    second = compile_spec(spec, fills)
    assert first == second


def test_unknown_fill_key_is_rejected() -> None:
    spec = _spec(_source(), _user("hero", 1), editable="{{slot:hero}}")
    with pytest.raises(BadRequest, match="未知槽位"):
        compile_spec(spec, {"other": FilledSlot("other", (HERO,))})
