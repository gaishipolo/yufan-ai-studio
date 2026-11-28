"""引用**带修饰符**的写法不再支持（2026-11-27）。

一槽一素材（契约里 `max_count` 恒为 1），所以「整组引用」（`{{slot:x*}}`）与
「第 N 张引用」（`{{slot:x.2}}`）这两种写法**不存在**：

- 整组引用会**绕过「选中时文案」**（填了素材时直接插编号，作者写的那句措辞被无视）；
- 「第 N 张」在没填素材时**必然报错**（一个槽位只有一张，哪来的第 2 张）。

界面上本来就给不出这两种候选（`selfRefChips` 只在 `maxCount > 1` 时才给），
但**粘贴**能塞进来 → 这里当场拒。

**判据落在「引用 token 的修饰符」上**，不是「正文里出现 `*` 或 `.N`」——
正文里普通的乘号与小数点是正常文字（"价格 * 3"、"2.5 倍"），不能被误伤。
"""
from __future__ import annotations

from dataclasses import replace

import pytest

from core.exceptions import BadRequest
from core.template_spec import (
    InputSlot,
    TemplateSpec,
    validate_spec,
)


def _spec(prompt: str, *, filled_text: str = "", empty_text: str = "") -> TemplateSpec:
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
                filled_text=filled_text, empty_text=empty_text,
            ),
        ),
    )


def test_正文里的整组引用被拒() -> None:
    with pytest.raises(BadRequest) as exc:
        validate_spec(_spec("主图 {{Mixed 1}} 场景 {{Mixed 2*}}"))
    assert "整组引用" in str(exc.value)


def test_正文里的第N张引用被拒() -> None:
    with pytest.raises(BadRequest) as exc:
        validate_spec(_spec("主图 {{Mixed 1}} 场景 {{Mixed 2.2}}"))
    assert "第 2 张引用" in str(exc.value)


def test_随机档提示词里的修饰符不再校验() -> None:
    """random_prompt 已是死字段，旧写法不该继续挡住保存。"""
    spec = replace(_spec("主图 {{Mixed 1}}"), random_prompt="夜景 {{Mixed 2*}}")
    validate_spec(spec)


def test_槽位文案里的整组引用被拒() -> None:
    with pytest.raises(BadRequest) as exc:
        validate_spec(_spec("主图 {{Mixed 1}}", filled_text="人物参考 {{slot:scene*}}"))
    assert "整组引用" in str(exc.value)


def test_正文里普通的乘号与小数点不受影响() -> None:
    """判据是**引用的修饰符**，不是正文里出现 * 或 .N。

    "价格 * 3"、"2.5 倍"这类正常文字不能被误伤 —— 误伤了作者就没法写提示词。
    """
    validate_spec(_spec("主图 {{Mixed 1}}，价格 * 3，倍率 2.5 倍"))


def test_单个引用照旧放行() -> None:
    validate_spec(_spec("主图 {{Mixed 1}} 场景 {{Mixed 2}}"))
