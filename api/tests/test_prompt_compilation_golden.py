"""编译链的 golden test：`video_scene_text` + 固定引用（步 4 的前置）。

为什么这组 spec 必须先有用例：PR 给 `compile.mjs` 的 `video_scene_text` 分支加了
`fixedReferenceTokens`，让「文字换场景」除了原片还能带封装里的固定参考图。
这条分支**整段自己拼 prompt**，不经过通用编译器，所以它有没有被跑到，只有出口
文案能证明——文案错了不会报错，只会生成一条「场景对了但产品没换」的视频。

三条边界一起锁住，因为它们各自对应一次静默失败：
- 固定引用表的长度必须等于「参考数量 − 1」：少一张图时编译器若只取前 N 个，
  绑定关系和提示词里的 `{{Mixed N}}` 就会错位，换的是别的素材；
- 提示词里的 token 只能来自固定引用表：多写一个 `{{Mixed 3}}` 而不报错，
  模型侧拿不到第 3 份素材，用户看到的是「产品没换」而不是「配置错了」；
- `{{ScenePrompt}}` 必须恰好一处：两处会让同一段场景描述插进两条约束里，
  零处则这次输入的描述根本没进提示词。

golden 部分（prompt / bindings / 两个哈希）是**逐字断言**：这几项任何一项变了，
都意味着发给模型的字节变了，应该有人来确认，而不是顺手改期望值。
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from copy import deepcopy
from typing import Any

import pytest

from core.exceptions import BadRequest
from modules.template.workspace import RUNTIME, compile_prompt

# 固定提示词原文：封装时写的那段，`{{ScenePrompt}}` 是本次输入要填的洞。
FIXED_PROMPT = (
    "把 {{Mixed 1}} 前景里的商品换成 {{Mixed 2}} 展示的款式，"
    "镜头与人物动作不变。场景：{{ScenePrompt}}"
)
SCENE_PROMPT = "雨夜的便利店门口，暖黄灯箱打亮橱窗"


def scene_spec(**overrides: Any) -> dict[str, Any]:
    """一组有代表性的 `prepare` 输出（只保留编译器读的字段）。

    键顺序照着 `replacement_service.py` 的组装处抄，但**不承诺逐字等于服务端拼出来的
    那个 dict**：`spec_sha256` 是 `JSON.stringify(spec)` 的摘要，键顺序参与哈希，
    这里锁的是「这组字节没变」，不是「服务端一定拼出这组字节」。后者要靠 prepare
    的端到端用例（`test_replacement_*`）守。
    """
    spec: dict[str, Any] = {
        "task": "scene",
        "duration": 5,
        "aspect_ratio": "9:16",
        "wardrobe_mode": "preserve",
        "references": [
            {
                "id": "clip",
                "role": "source_video",
                "type": "video",
                "file": "inputs/reference-video.mp4",
            },
            {
                "id": "front",
                "role": "target_product",
                "type": "image",
                "file": "inputs/reference-2.png",
            },
        ],
        "variables": {
            "target_scene": "雨夜便利店门口",
            "protected_foreground": "原片人物、商品、手持物与所有交互前景对象",
            "scene_prompt": SCENE_PROMPT,
        },
        "inputMode": "video_scene_text",
        "fixedReferenceTokens": ["{{Mixed 2}}"],
        "fixed_prompt": FIXED_PROMPT,
    }
    spec.update(overrides)
    return spec


def _node() -> str:
    node = shutil.which("node")
    if not node:
        pytest.skip("本机没有 node，跳过编译器 golden test")
    return node


def compile_raw(spec: dict[str, Any]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [_node(), str(RUNTIME / "workbench/compile.mjs")],
        input=json.dumps(spec, ensure_ascii=False),
        capture_output=True,
        encoding="utf-8",
        timeout=30,
    )


def test_scene_text_with_fixed_references_golden() -> None:
    """带一张固定参考图的「文字换场景」：出口文案、绑定、两个哈希逐字锁住。"""
    spec = scene_spec()
    result = compile_raw(spec)
    assert result.returncode == 0, result.stderr
    compiled = json.loads(result.stdout)

    assert compiled["prompt"] == (
        "把 {{Mixed 1}} 前景里的商品换成 {{Mixed 2}} 展示的款式，"
        "镜头与人物动作不变。场景：雨夜的便利店门口，暖黄灯箱打亮橱窗"
    )
    assert compiled["compiler_version"] == "scene-video-text-v1"
    # 场景模式走的是固定的 wanx3.0 / mixed2video，不是通用编译器的默认模型
    assert compiled["model"] == "wanx3.0"
    assert compiled["modeType"] == "mixed2video"
    assert compiled["generation_verified"] is False
    assert compiled["warnings"] == ["场景描述来自本次输入，其余约束来自封装模板"]

    # 绑定顺序 = 参考顺序，token 按位递增：固定引用图必须是第 2 位
    assert [
        (item["index"], item["id"], item["role"], item["token"])
        for item in compiled["bindings"]
    ] == [
        (0, "clip", "source_video", "{{Mixed 1}}"),
        (1, "front", "target_product", "{{Mixed 2}}"),
    ]

    # `template_sha256` 哈希的是**模板**（固定提示词原文），不是最终 prompt：
    # 这正是「档案里的哈希复核不到实际发出去的字节」那条的原始语义，别混淆。
    assert (
        compiled["template_sha256"]
        == "df6cec1ab4731ec9de03c61772f9d587b78e4e70b05b456a31665490ad3e7a9c"
    )
    assert compiled["template_sha256"] == hashlib.sha256(
        FIXED_PROMPT.encode("utf-8")
    ).hexdigest()
    assert (
        compiled["spec_sha256"]
        == "692e2287e479aecdeec46bd7c344852efb7df798e270b2e7fb5af06e1e5b4f66"
    )


def test_scene_text_goes_through_the_python_wrapper() -> None:
    """`compile_prompt()` 是业务层唯一的编译入口，它得能解析这组 spec。

    `Compilation` 只声明两个字段、`extra="allow"`：上面那堆 golden 字段是「带着
    过去」的，少声明一个不会有任何症状，所以这里从 wrapper 再走一遍。
    """
    compiled = compile_prompt(scene_spec(), node=_node())
    assert compiled.compiler_version == "scene-video-text-v1"
    assert compiled.prompt.endswith(f"场景：{SCENE_PROMPT}")
    extra = compiled.model_extra or {}
    assert extra["template_sha256"] == hashlib.sha256(
        FIXED_PROMPT.encode("utf-8")
    ).hexdigest()
    assert [b["token"] for b in extra["bindings"]] == ["{{Mixed 1}}", "{{Mixed 2}}"]


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        # 固定引用表说「还有 1 张图」，但 references 里只剩原片
        (lambda spec: spec["references"].pop(), "requires exactly one source video"),
        # 反方向：多给一张图，固定引用表却没跟上（第 3 位没人认领）
        (
            lambda spec: spec["references"].append(
                {
                    "id": "extra",
                    "role": "target_product",
                    "type": "image",
                    "file": "inputs/reference-3.png",
                }
            ),
            "requires exactly one source video",
        ),
        # 原片这一位被换成图片：位序没变，但它已经不是原片了
        (
            lambda spec: spec["references"].__setitem__(
                0,
                {
                    "id": "clip",
                    "role": "source_video",
                    "type": "image",
                    "file": "inputs/reference-video.mp4",
                },
            ),
            "requires exactly one source video",
        ),
        # 提示词点名了不存在的第 3 份素材
        (
            lambda spec: spec.update(
                fixed_prompt="把 {{Mixed 1}} 换成 {{Mixed 3}}。场景：{{ScenePrompt}}"
            ),
            "Unknown scene prompt token",
        ),
        # 固定引用表里的 token 写成了别的名字，第 2 位没人认领
        (
            lambda spec: spec.update(fixedReferenceTokens=["{{Product}}"]),
            "Invalid fixed scene reference",
        ),
        # 没有洞：本次输入的描述根本没进提示词
        (
            lambda spec: spec.update(
                fixed_prompt="把 {{Mixed 1}} 换成 {{Mixed 2}} 展示的款式。"
            ),
            "Invalid scene description or fixed scene template",
        ),
        # 两个洞：同一段描述会被插进两条约束
        (
            lambda spec: spec.update(
                fixed_prompt="{{ScenePrompt}}：{{Mixed 1}} 换成 {{Mixed 2}}。{{ScenePrompt}}"
            ),
            "Invalid scene description or fixed scene template",
        ),
        # 描述里自带花括号：会被当成未解析的 token，宁可拒绝也不猜
        (
            lambda spec: spec["variables"].update(scene_prompt="雨夜 {{Mixed 2}}"),
            "Invalid scene description or fixed scene template",
        ),
        # 只有空白：`{{ScenePrompt}}` 填进去等于没填
        (
            lambda spec: spec["variables"].update(scene_prompt="   "),
            "Invalid scene description or fixed scene template",
        ),
    ],
)
def test_scene_text_rejects_bad_shape(mutate: Any, message: str) -> None:
    spec = deepcopy(scene_spec())
    mutate(spec)
    result = compile_raw(spec)
    assert result.returncode != 0, "这一组 spec 应当编译失败，而不是产出提示词"
    assert message in result.stderr


def test_scene_text_failure_surfaces_as_bad_request() -> None:
    """编译器拒绝 → 业务层抛 `BadRequest`，不是 500，也不是「编译成功但文案不对」。"""
    spec = scene_spec()
    spec["fixedReferenceTokens"] = ["{{Product}}"]
    with pytest.raises(BadRequest):
        compile_prompt(spec, node=_node())


# --------------------------------------------------------------------------- #
# `wardrobe_mode=reference` 的「内嵌音轨」两句
#
# 原片自带音轨时没有 `source_audio` 参考，`tokens('source_audio')` 是空串，整段文案会
# 出现「；提供同片原音轨」「声音：为完整原片音轨」这种病句。以前是 Python 拿两次
# `str.replace` 去修：模板文案一改，替换静默不命中，病句照样发出去，而
# `compiler_version` 仍标着 `+embedded-audio-v1`——等于盖了个假章。
# 现在这两句由编译器按「有没有音轨参考」各写一份，用例锁的是「字节没变」。
# --------------------------------------------------------------------------- #
CHARACTER_REFERENCE_PROMPT_SHA256 = (
    "2f9c11626aedd6ba7f5b77b706234ebf41901307a155c47b3a6729265e0687f9"
)


def character_spec(*, audio: bool, fixed_prompt: str | None = None) -> dict[str, Any]:
    references: list[dict[str, Any]] = [
        {
            "id": "clip",
            "role": "source_video",
            "type": "video",
            "file": "inputs/reference-video.mp4",
        },
        {
            "id": "who",
            "role": "identity",
            "type": "image",
            "file": "inputs/reference-2.png",
        },
    ]
    if audio:
        references.append(
            {
                "id": "voice",
                "role": "source_audio",
                "type": "audio",
                "file": "inputs/reference-3.mp3",
            }
        )
    spec: dict[str, Any] = {
        "task": "character",
        "duration": 5,
        "aspect_ratio": "9:16",
        "wardrobe_mode": "reference",
        "references": references,
        "variables": {"source_subject": "原片主角"},
    }
    if fixed_prompt is not None:
        spec["fixed_prompt"] = fixed_prompt
    return spec


def _compile_ok(spec: dict[str, Any]) -> dict[str, Any]:
    result = compile_raw(spec)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_character_reference_without_audio_writes_the_sentence_in_the_compiler() -> None:
    """没有音轨参考：两句话由编译器直出，整段文案的摘要与改造前逐字相同。"""
    compiled = _compile_ok(character_spec(audio=False))
    prompt = compiled["prompt"]
    assert hashlib.sha256(prompt.encode("utf-8")).hexdigest() == (
        CHARACTER_REFERENCE_PROMPT_SHA256
    )
    assert "；原片内嵌音轨提供声音。" in prompt
    assert "声音：原片内嵌音轨为完整原片音轨，沿用原台词" in prompt
    # 病句不能再出现——它现在是「替换没命中」的唯一可见症状
    assert "；提供同片原音轨" not in prompt
    assert "声音：为完整原片音轨" not in prompt
    assert compiled["compiler_version"] == "1.0.0+embedded-audio-v1"


def test_character_reference_with_audio_keeps_the_reference_sentence() -> None:
    """有音轨参考：句子里点的是 `source_audio` 那个 token，不该被改写成「内嵌」。"""
    compiled = _compile_ok(character_spec(audio=True))
    prompt = compiled["prompt"]
    assert "；{{Mixed 3}}提供同片原音轨。" in prompt
    assert "声音：{{Mixed 3}}为完整原片音轨，沿用原台词" in prompt
    assert "内嵌音轨" not in prompt
    assert compiled["compiler_version"] == "1.0.0"


def test_embedded_audio_marker_is_not_stamped_under_a_fixed_prompt() -> None:
    """固定提示词整段覆盖文案时不许标「内嵌音轨改写」：那段文案根本没发出去。"""
    compiled = _compile_ok(
        character_spec(audio=False, fixed_prompt="对 {{Mixed 1}} 做替换。")
    )
    assert compiled["prompt"] == "对 {{Mixed 1}} 做替换。"
    assert "embedded-audio-v1" not in compiled["compiler_version"]
    assert compiled["compiler_version"].endswith("+fixed-video-template-v1")

