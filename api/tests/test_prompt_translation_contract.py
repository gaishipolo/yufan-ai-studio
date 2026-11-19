"""素材占位符翻译的标准调用约定守卫。

`translate_or_original` 是"每个驱动决定自己怎么翻译"的统一入口：驱动把
`translate_prompt`（可能没有）传进来，这里兜底。钉三件事：

1. 没有翻译方法 → 原样；
2. 方法返回 None（判断无需翻译）→ 原样；
3. 方法返回字符串 → 用返回值。

判别力：把 `translate_or_original` 换成 `return prompt`，第 3 条会变红；
换成无条件调用，第 1、2 条会变红。
"""

from __future__ import annotations

from core.prompt_refs import translate_or_original, translate_prompt
from infra.ai.base import PromptTranslation

ENTRIES = [{"type": "image"}, {"type": "video"}]


def test_missing_translate_keeps_original() -> None:
    assert translate_or_original("{{Mixed 1}} 参考", ENTRIES, None) == "{{Mixed 1}} 参考"


def test_none_result_keeps_original() -> None:
    assert translate_or_original("原样", ENTRIES, lambda prompt, entries: None) == "原样"


def test_translated_result_is_used() -> None:
    def fake(prompt: str, entries: object) -> str:
        return prompt.replace("{{Mixed 2}}", "视频1")

    assert translate_or_original("参照 {{Mixed 2}}", ENTRIES, fake) == "参照 视频1"


def test_bare_mixed_is_plain_text() -> None:
    """裸 `Mixed N` 是普通文本，不翻译、越界也不报错。

    判别力：把 `_REFERENCE` 的裸写分支加回来，本用例会变红。
    """
    assert translate_prompt("参考 Mixed 1 与 Mixed 99", ENTRIES) == (
        "参考 Mixed 1 与 Mixed 99"
    )
    # 正式写法照常翻译，两者 coexist 在一句话里也互不干扰。
    assert translate_prompt("参考 {{Mixed 1}} 与 Mixed 99", ENTRIES) == (
        "参考 图1 与 Mixed 99"
    )


def test_protocol_matches_a_driver_with_the_method() -> None:
    """协议是可选的：实现了标准方法的驱动能被识别，没实现的不会被误判。"""

    class WithTranslation:
        def translate_prompt(self, prompt: str, entries: object) -> str | None:
            return prompt

    class WithoutTranslation:
        pass

    assert isinstance(WithTranslation(), PromptTranslation)
    assert not isinstance(WithoutTranslation(), PromptTranslation)
