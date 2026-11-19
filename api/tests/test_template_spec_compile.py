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
    """`locked` / `editable` 只是这套用例的写法习惯，落到模型上是**一段**提示词。

    两段合并之后（2026-11-14）"哪段能被模板库改"这个概念不存在了，但把两段拼起来
    的语义与旧 `compile` 一致（空段不留空行），所以这里保留两个参数、拼成一段，
    用例关注的东西（编号派生、引用展开）一个字都不用改。
    """
    return TemplateSpec(
        key="demo",
        name="演示",
        model="libtv-wan3-replacement",
        slots=tuple(slots),
        prompt="\n\n".join(part for part in (locked, editable) if part),
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


def test_text_slot_gets_its_own_number_space() -> None:
    """文本槽位**不占 Mixed 编号**，但有独立的 `{{Text N}}` 编号。

    执行层按 `textLibraryIds` 的顺序把 `{{Text N}}` 换成正文（`core/library_text.py`），
    所以 N 必须与 `text_ids` 的顺序一致 —— 这正是"提示词哪里要可变就放一个文本槽位"
    的实现方式：模板库选哪份文案，第 N 份就是它。
    """
    spec = _spec(
        _source(),
        _user("hero", 1),
        InputSlot(
            key="brief", type="text", binding="user", ref_mode="attachment",
            position=2, label="文案",
        ),
        editable="{{slot:hero}}｜{{slot:brief}}",
    )
    out = compile_spec(
        spec, _fills(source=(VIDEO,), hero=(HERO,), brief=(NOTE,))
    )
    assert out.text_ids == ("t-note",)
    assert out.reference_ids == ("v-src", "i-hero")
    assert out.prompt == "{{Mixed 2}}｜{{Text 1}}"
    assert out.slot_tokens["brief"] == ("{{Text 1}}",)
    # 视觉编号不受文本槽位影响：两个 Mixed，一个 Text，各数各的
    assert out.slot_tokens["hero"] == ("{{Mixed 2}}",)


def test_two_text_slots_number_in_slot_order() -> None:
    """两份文案：`{{Text N}}` 按槽位顺序排，与 `text_ids` 的顺序必须逐项对齐。"""
    spec = _spec(
        _source(),
        InputSlot(
            key="brief", type="text", binding="user", ref_mode="attachment",
            position=1, label="文案",
        ),
        InputSlot(
            key="tone", type="text", binding="user", ref_mode="attachment",
            position=2, label="语气",
        ),
        locked="{{slot:tone}} / {{slot:brief}}",
        editable="{{slot:source}}",
    )
    out = compile_spec(
        spec,
        _fills(source=(VIDEO,), brief=(NOTE,), tone=(FilledAsset("t-tone", "text"),)),
    )
    assert out.text_ids == ("t-note", "t-tone")
    assert out.prompt.startswith("{{Text 2}} / {{Text 1}}")


@pytest.mark.parametrize(
    ("editable", "message"),
    [
        ("{{slot:missing}}", "不存在的槽位"),
        ("{{slot:bgm}}", "附加输入"),
        ("{{slot:hero.2}}", "第 2 张不存在"),
        ("{{slot:hero", "无法解析"),
        # 带空格的坏引用：`_SLOT_REF` 容空格，所以它必须同样被残留检查兜住，
        # 否则原样进入供应商提示词（用户看到的是一坨带大括号的乱码）。
        ("{{ slot:hero", "无法解析"),
        ("{{ slot:Key }}", "无法解析"),
        ("{{ slot:hero.1234 }}", "无法解析"),
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


def test_mixed_ref_points_at_the_nth_visual_slot() -> None:
    """作者写的 `{{Mixed N}}` = 第 N 个图片/成片槽位（文本/音频不占号）。

    2026-11-19 起这是**作者唯一要记的写法**：以前作者写 `{{slot:image_1}}`、
    模型看到 `{{Mixed 1}}`，同一件事两套写法。
    """
    spec = _spec(
        _source(),
        _user("hero", 1),
        editable="原片 {{Mixed 1}}，人物参考 {{Mixed 2}}",
    )
    out = compile_spec(spec, _fills(source=(VIDEO,), hero=(HERO,)))

    assert out.prompt == "原片 {{Mixed 1}}，人物参考 {{Mixed 2}}"
    assert out.slot_tokens == {"source": ("{{Mixed 1}}",), "hero": ("{{Mixed 2}}",)}


def test_mixed_ref_expands_to_the_number_this_fill_produces() -> None:
    """作者写的编号 ≠ 编译出的编号时，必须真的展开。

    第 1 个槽位填了 2 张，它占 1、2 两个号，所以第 2 个槽位拿到的是 3 号 ——
    作者写 `{{Mixed 2}}`（第 2 个槽位），编译出来是 `{{Mixed 3}}`。

    这条用例专门用来区分「展开了」和「原样留着」：每个槽位各一张时两者输出
    一模一样，只有张数不同才分得出来。
    """
    spec = _spec(
        _user("hero", 1, min_count=1, max_count=2),
        _user("detail", 2),
        editable="细节 {{Mixed 2}}",
    )
    out = compile_spec(spec, _fills(hero=(HERO, DETAIL_1), detail=(DETAIL_2,)))

    assert out.prompt == "细节 {{Mixed 3}}"
    assert out.slot_tokens["detail"] == ("{{Mixed 3}}",)


def test_mixed_ref_that_points_nowhere_is_rejected() -> None:
    """只有 2 个视觉槽位，写 `{{Mixed 3}}` 当场拒 —— 别拖到别人点生成。"""
    spec = _spec(_source(), _user("hero", 1), editable="{{Mixed 3}}")
    with pytest.raises(BadRequest, match="找不到对应槽位"):
        validate_spec(spec)


def test_reference_to_a_missing_slot_is_rejected_by_validation() -> None:
    """引用已删掉的槽位在**结构校验**就该报，而不是拖到 `compile` 才报。

    槽位可以删到 0（纯提示词模板），所以"删了槽位但提示词里还留着 `{{slot:…}}`"
    很容易出现。以前预检会放行、发布也放行，直到用户点生成才拿到一句跟操作看不出
    关系的错 —— 那种错用户不会认为是自己删槽位删出来的。
    """
    spec = _spec(_user("hero", 1), editable="{{slot:hero}} 和 {{slot:gone*}}")
    with pytest.raises(BadRequest, match="不存在的槽位「gone」"):
        validate_spec(spec)

    # 删到一个槽位都没了，提示词里也就不能再引用
    empty = _spec(editable="{{slot:hero}}")
    with pytest.raises(BadRequest, match="不存在的槽位「hero」"):
        validate_spec(empty)


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


def test_reference_with_spaces_compiles_like_the_tight_form() -> None:
    """`{{ slot:hero }}`（带空格）与 `{{slot:hero}}` 必须编译出同一个结果。

    结构校验（`rules._SLOT_REF`）一直是容空格的，编译这边曾经不容：于是带空格的
    引用**预检与发布都能过**，到用户在模板库点生成时才 400「引用写坏了」。
    同一个形状两处两种口径，迟早踩到。
    """
    spec = _spec(
        _source(),
        _user("hero", 1),
        locked="原片 {{ slot:source }}",
        editable="主图 {{slot:hero }} 与 {{ slot:hero* }}",
    )

    out = compile_spec(spec, _fills(source=(VIDEO,), hero=(HERO,)))

    assert out.prompt == "原片 {{Mixed 1}}\n\n主图 {{Mixed 2}} 与 {{Mixed 2}}"
    assert out.slot_tokens["hero"] == ("{{Mixed 2}}",)


# --------------------------------------------------------------------------- #
# 可选槽位：选中时文案 / 未选时文案
# --------------------------------------------------------------------------- #
def _optional(key: str, position: int, **over: object) -> InputSlot:
    """一个**可选**的素材槽位（`min_count=0`）：不填素材也能编译。"""
    base: dict[str, object] = {
        "key": key,
        "type": "image",
        "binding": "user",
        "min_count": 0,
        "max_count": 1,
        "position": position,
        "label": key,
    }
    base.update(over)
    return InputSlot(**base)  # type: ignore[arg-type]


def test_optional_slot_uses_filled_text_when_asset_is_chosen() -> None:
    """填了素材 → 提示词里插入「选中时文案」，其中的自引用派生成编号。

    这是需求的核心：作者写「人物参考 {{slot:hero}}」，使用者选了图，
    提示词里就是「人物参考 {{Mixed 2}}」。
    """
    spec = _spec(
        _source(),
        _optional("hero", 1, filled_text="人物参考 {{slot:hero}}"),
        editable="{{slot:hero}}",
    )
    out = compile_spec(spec, _fills(source=(VIDEO,), hero=(HERO,)))
    assert out.prompt == "人物参考 {{Mixed 2}}"
    # 编号照旧参与参考素材顺序
    assert out.reference_ids == ("v-src", "i-hero")


def test_optional_slot_uses_empty_text_when_asset_is_not_chosen() -> None:
    """没填素材 → 插入「未选时文案」，不占编号、不进参考素材。"""
    spec = _spec(
        _source(),
        _optional("hero", 1, empty_text="主角是穿红裙的年轻女性"),
        editable="{{slot:hero}}",
    )
    out = compile_spec(spec, _fills(source=(VIDEO,)))
    assert out.prompt == "主角是穿红裙的年轻女性"
    # 没素材 → 不占编号，也不进参考素材列表
    assert out.reference_ids == ("v-src",)
    assert "hero" not in out.slot_tokens


def test_optional_slot_without_empty_text_is_rejected_with_actionable_message() -> None:
    """没填素材又没写兜底文案 → 报错，并说清**要做什么**。

    静默留空会让提示词里少一整段，模型照跑、结果不对且不留痕迹 ——
    比报错难查得多。
    """
    spec = _spec(
        _source(),
        _optional("hero", 1),
        editable="{{slot:hero}}",
    )
    with pytest.raises(BadRequest, match="未选时文案"):
        compile_spec(spec, _fills(source=(VIDEO,)))


def test_filled_text_is_optional_and_keeps_the_old_behaviour() -> None:
    """没写「选中时文案」→ 插编号（旧行为）。

    现有模板一字不改也要能继续用 —— 这是这条能力**向后兼容**的前提。
    """
    spec = _spec(_source(), _optional("hero", 1), editable="{{slot:hero}}")
    out = compile_spec(spec, _fills(source=(VIDEO,), hero=(HERO,)))
    assert out.prompt == "{{Mixed 2}}"


def test_filled_text_may_reference_the_whole_group() -> None:
    """段落里可以写 `{{slot:x*}}`：多选槽位要把整组都写进去。"""
    spec = _spec(
        _source(),
        _optional(
            "hero", 1, max_count=2, filled_text="人物参考 {{slot:hero*}}"
        ),
        editable="{{slot:hero}}",
    )
    out = compile_spec(
        spec, _fills(source=(VIDEO,), hero=(HERO, DETAIL_1))
    )
    assert out.prompt == "人物参考 {{Mixed 2}}、{{Mixed 3}}"


def test_filled_text_may_reference_one_of_several() -> None:
    """段落里可以写 `{{slot:x.2}}`：只要其中一张。"""
    spec = _spec(
        _source(),
        _optional(
            "hero", 1, max_count=3, filled_text="以 {{slot:hero.2}} 为主"
        ),
        editable="{{slot:hero}}",
    )
    out = compile_spec(
        spec, _fills(source=(VIDEO,), hero=(HERO, DETAIL_1, DETAIL_2))
    )
    assert out.prompt == "以 {{Mixed 3}} 为主"


def test_filled_text_cannot_reference_another_slot() -> None:
    """「选中时文案」只能引用自己。

    跨槽位引用会让编号依赖别的槽位的填充量，而编号是按槽位顺序算的 ——
    同一段文案在不同填充下编译出不同结果，没人能推理得清。
    """
    spec = _spec(
        _source(),
        _optional("hero", 1, filled_text="参考 {{slot:source}}"),
        _user("detail", 2),
        editable="{{slot:hero}}",
    )
    with pytest.raises(BadRequest, match="只能引用自己"):
        compile_spec(spec, _fills(source=(VIDEO,), hero=(HERO,), detail=(DETAIL_1,)))


def test_empty_text_cannot_reference_any_slot() -> None:
    """「未选时文案」不许含槽位引用：没素材就没有编号可指。"""
    spec = _spec(
        _source(),
        _optional("hero", 1, empty_text="参考 {{slot:source}}"),
        editable="{{slot:hero}}",
    )
    with pytest.raises(BadRequest, match="不能引用槽位"):
        compile_spec(spec, _fills(source=(VIDEO,)))


# --------------------------------------------------------------------------- #
# 必填槽位：没有两段文案（直接插编号）
# --------------------------------------------------------------------------- #
def test_required_slot_rejects_filled_text() -> None:
    """**必填**槽位不许写「选中时文案」。

    必填 = 素材一定在 = 提示词里直接插编号。给必填槽位加自定义措辞，
    等于多出一条「哪些槽位能写文案」的记忆负担，还跟界面上说的
    「必填 = 直接插编号」对不上。
    """
    spec = _spec(
        _source(),
        _user("hero", 1, filled_text="人物参考 {{slot:hero}}"),
        editable="{{slot:hero}}",
    )
    with pytest.raises(BadRequest, match="必填"):
        compile_spec(spec, _fills(source=(VIDEO,), hero=(HERO,)))


def test_required_slot_rejects_empty_text() -> None:
    """必填槽位也不许写「未选时文案」：它永远用不上。"""
    spec = _spec(
        _source(),
        _user("hero", 1, empty_text="主角是穿红裙的年轻女性"),
        editable="{{slot:hero}}",
    )
    with pytest.raises(BadRequest, match="必填"):
        compile_spec(spec, _fills(source=(VIDEO,), hero=(HERO,)))


def test_required_slot_without_texts_still_inserts_the_number() -> None:
    """必填槽位（没写文案）照旧插编号 —— 旧模板一字不改继续用。"""
    spec = _spec(_source(), _user("hero", 1), editable="{{slot:hero}}")
    out = compile_spec(spec, _fills(source=(VIDEO,), hero=(HERO,)))
    assert out.prompt == "{{Mixed 2}}"


def test_required_slot_error_says_how_to_make_it_optional() -> None:
    """报错要说清**要做什么**：改成「可选」才能用这两段文案。

    只说「不能写」的话，作者不知道该去哪里改。
    """
    spec = _spec(
        _source(),
        _user("hero", 1, filled_text="人物参考 {{slot:hero}}"),
        editable="{{slot:hero}}",
    )
    with pytest.raises(BadRequest, match="可选"):
        compile_spec(spec, _fills(source=(VIDEO,), hero=(HERO,)))


def test_optional_slot_shifts_later_numbers_when_unfilled() -> None:
    """可选槽位不填 → 后面槽位的编号前移（编号不落库的设计照旧）。"""
    spec = _spec(
        _source(),
        _optional("hero", 1, empty_text="主角是穿红裙的年轻女性"),
        _user("detail", 2),
        editable="{{slot:hero}} {{slot:detail}}",
    )
    out = compile_spec(
        spec, _fills(source=(VIDEO,), detail=(DETAIL_1,))
    )
    assert out.prompt == "主角是穿红裙的年轻女性 {{Mixed 2}}"
    assert out.reference_ids == ("v-src", "i-d1")


def test_group_reference_degrades_to_empty_when_unfilled_and_no_fallback() -> None:
    """整组引用（`{{slot:x*}}`）没填、也没写兜底文案 → **降级为空**，不报错。

    它的语义是「列出这组」，没有就列出空 —— 这是既有行为
    （`test_short_fill_shifts_later_numbers` 钉着），不能被新能力改掉。
    """
    spec = _spec(
        _source(),
        _optional("hero", 1, max_count=2),
        editable="看 {{slot:hero*}}",
    )
    out = compile_spec(spec, _fills(source=(VIDEO,)))
    assert out.prompt == "看"


def test_group_reference_uses_empty_text_when_written() -> None:
    """整组引用写了兜底文案 → 用它（比降级成空更符合作者意图）。

    降级成空会在句子里留一个空洞：「参考 {{slot:hero*}} 的产品」变成
    「参考  的产品」—— 句子缺一块，模型照跑、结果不对。作者既然写了
    兜底文案，就该用上。
    """
    spec = _spec(
        _source(),
        _optional("hero", 1, max_count=2, empty_text="主角是穿红裙的年轻女性"),
        editable="看 {{slot:hero*}}",
    )
    out = compile_spec(spec, _fills(source=(VIDEO,)))
    assert out.prompt == "看 主角是穿红裙的年轻女性"
