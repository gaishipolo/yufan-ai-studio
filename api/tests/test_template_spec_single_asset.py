"""一个槽位只能放一张素材的契约守卫（一槽一张，无历史兼容）。

库里已无多值槽位（迁移 `4e06879f9f75` 清掉最后两个多值 e2e 模板），所以
`SpecSlotIn` 用字段级硬约束 `le=1`：传 >1 直接 422，不钳制、不静默改。

判别力：把字段的 `le=1` 放宽回 `MAX_SLOT_REFS`，这些断言会变红。
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from modules.template_pack.spec_schema import SpecSlotIn


def _slot(**overrides: object) -> dict[str, object]:
    return {
        "key": "hero",
        "type": "image",
        "minCount": 1,
        "maxCount": 1,
        **overrides,
    }


def test_max_count_above_one_is_rejected() -> None:
    """多张配置不再接受 —— 不留兼容，直接 422。"""
    with pytest.raises(ValidationError):
        SpecSlotIn.model_validate(_slot(maxCount=5))


def test_min_count_above_one_is_rejected() -> None:
    """下限同样锁死：必填就是一张，不存在"至少三张"。"""
    with pytest.raises(ValidationError):
        SpecSlotIn.model_validate(_slot(minCount=3))


def test_optional_zero_min_count_survives() -> None:
    """可选（minCount=0）保留 —— 锁的是上限，不是取消可选。"""
    slot = SpecSlotIn.model_validate(_slot(minCount=0, maxCount=1))
    assert slot.min_count == 0
    assert slot.max_count == 1


def test_legacy_snake_case_is_rejected_too() -> None:
    """历史草稿用 snake_case 存的槽位同样收紧，别漏掉另一套键名。"""
    with pytest.raises(ValidationError):
        SpecSlotIn.model_validate(
            {"key": "hero", "type": "image", "min_count": 1, "max_count": 9}
        )
