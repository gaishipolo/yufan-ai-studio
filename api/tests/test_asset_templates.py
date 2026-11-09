"""槽位契约（M3.5-A）：模板声明的形状必须在写入前就说清。

这些用例全是**纯函数级**的，不造库：槽位声明错了的症状不是报错，而是
"按角色取不到图"——生成出一张缺角色的成片，用户看到的是"效果不对"。
要拦住它，校验只能穷举地测，而穷举需要它不依赖数据库。
"""

from __future__ import annotations

import pytest

from modules.template.pack_roles import PACK_ROLES
from modules.template.slots import MAX_IMAGES_LIMIT, SlotError, Slots


def test_default_declares_the_base_role_and_generate() -> None:
    slots = Slots.default("product")
    # 产品的基础角色是 front：缺了它就没有稳定外观可依
    assert slots.required_roles == ("front",)
    assert slots.required_caps == ("generate",)
    assert slots.min_images == 1
    assert slots.preferred_cover_role == "front"


def test_default_for_every_kind_is_valid() -> None:
    for kind in PACK_ROLES:
        slots = Slots.default(kind)
        # 默认值也要过自己的校验：默认值不合法等于"新建就报错"
        assert Slots.parse(slots.to_json(), kind) == slots


def test_parse_keeps_order_and_drops_duplicates() -> None:
    slots = Slots.parse(
        {
            "required_roles": ["front", "detail", "front"],
            "optional_roles": ["back"],
            "min_images": 2,
            "max_images": 5,
            "required_caps": ["generate", "download", "generate"],
        },
        "product",
    )
    # 顺序决定提示词里 reference-N 的编号：乱序会让同一份配置每次编出不同提示词
    assert slots.required_roles == ("front", "detail")
    assert slots.optional_roles == ("back",)
    assert slots.required_caps == ("generate", "download")


def test_parse_rejects_unknown_field() -> None:
    # `required_role`（少个 s）不报错的话，槽位会静默变成"什么都不需要"
    with pytest.raises(SlotError, match="不认识的字段"):
        Slots.parse({"required_role": ["front"]}, "product")


def test_parse_rejects_role_outside_vocabulary() -> None:
    with pytest.raises(SlotError, match="不在该类型的角色词表里"):
        Slots.parse({"required_roles": ["main"]}, "product")  # main 是 scene 的角色


def test_parse_rejects_empty_required_roles() -> None:
    with pytest.raises(SlotError, match="至少要声明一个必填角色"):
        Slots.parse({"required_roles": [], "optional_roles": ["front"]}, "product")


def test_parse_rejects_role_both_required_and_optional() -> None:
    with pytest.raises(SlotError, match="不能同时是必填和可选"):
        Slots.parse({"required_roles": ["front"], "optional_roles": ["front"]}, "product")


def test_parse_rejects_inverted_range() -> None:
    with pytest.raises(SlotError, match="不能小于最少张数"):
        Slots.parse({"required_roles": ["front"], "min_images": 5, "max_images": 2}, "product")


def test_parse_rejects_too_many_images() -> None:
    with pytest.raises(SlotError, match=f"上限是 {MAX_IMAGES_LIMIT}"):
        Slots.parse({"required_roles": ["front"], "max_images": MAX_IMAGES_LIMIT + 1}, "product")


def test_parse_requires_generate_capability() -> None:
    # 没有 generate = 永远跑不起来的模板，而失败会出现在生成流水线里
    with pytest.raises(SlotError, match="必须包含 generate"):
        Slots.parse({"required_roles": ["front"], "required_caps": ["download"]}, "product")


def test_parse_rejects_unknown_capability() -> None:
    with pytest.raises(SlotError, match="不在 generate/download/derive 里"):
        Slots.parse(
            {"required_roles": ["front"], "required_caps": ["generate", "publish"]},
            "product",
        )


def test_parse_rejects_cover_role_not_declared() -> None:
    with pytest.raises(SlotError, match="不在声明的角色里"):
        Slots.parse(
            {"required_roles": ["front"], "preferred_cover_role": "detail"}, "product"
        )


def test_parse_falls_back_to_first_required_role_as_cover() -> None:
    slots = Slots.parse({"required_roles": ["detail", "front"]}, "product")
    # 没声明封面时取第一个必填角色：第一个通常是"最能代表它"的那张
    assert slots.preferred_cover_role == "detail"


def test_parse_accepts_camel_free_shape_from_json_column() -> None:
    """存进数据库再读回来必须等价：`slots` 列是 JSON，往返是常态路径。"""
    original = Slots.parse(
        {
            "required_roles": ["front"],
            "optional_roles": ["detail", "back"],
            "min_images": 1,
            "max_images": 3,
            "required_caps": ["generate"],
            "preferred_cover_role": "front",
        },
        "product",
    )
    assert Slots.parse(original.to_json(), "product") == original


def test_unknown_kind_is_rejected() -> None:
    with pytest.raises(SlotError, match="不认识的模板类型"):
        Slots.default("person")
