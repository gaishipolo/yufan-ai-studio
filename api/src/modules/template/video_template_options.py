"""提示词封装的工作台参数；素材包不定义固定提示词的参数。"""

import re
from typing import Any

from core.exceptions import BadRequest

SCOPE_TOKEN = "{{ReplacementScope}}"
SCENE_TOKEN = "{{ScenePrompt}}"
SCENE_TEXT_MODE = "video_scene_text"


def is_scene_text(rules: dict[str, Any]) -> bool:
    return rules.get("inputMode") == SCENE_TEXT_MODE


def scene_description(value: Any) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 4000:
        raise BadRequest("请填写场景描述，最多4000字")
    if "{{" in value or "}}" in value:
        raise BadRequest("场景描述请使用普通文字，不要填写模板引用标记")
    return value


def scope_options(rules: dict[str, Any]) -> list[str]:
    raw = rules.get("scopeOptions", [])
    if not isinstance(raw, list) or any(not isinstance(item, str) for item in raw):
        raise BadRequest("替换范围选项必须是文字列表")
    options = [item.strip() for item in raw]
    if (
        len(options) > 30
        or len("、".join(options)) > 1000
        or len(set(options)) != len(options)
        or any(
            not item or len(item) > 100 or "、" in item or "{{" in item or "}}" in item
            for item in options
        )
    ):
        raise BadRequest("范围选项需唯一且非空，每项最多100字，合计最多1000字，不含顿号或引用标记")
    return options


def validate_options(prompt: str, rules: dict[str, Any]) -> None:
    label = rules.get("workbenchLabel", "")
    if not isinstance(label, str) or len(label) > 80:
        raise BadRequest("工作台功能名称最多80字")
    options = scope_options(rules)
    if bool(options) != (SCOPE_TOKEN in prompt):
        raise BadRequest(
            "配置范围选项时，提示词必须包含 {{ReplacementScope}}；无选项时请移除该参数"
        )
    if is_scene_text(rules):
        scene_description(rules.get("scenePromptDefault"))
        if prompt.count(SCENE_TOKEN) != 1 or options:
            raise BadRequest(
                "文字换场景模板必须且只能包含一个 {{ScenePrompt}} 参数，不配置替换范围"
            )
        tokens = re.findall(r"\{\{([^{}]+)\}\}", prompt)
        fixed = {ref["token"][2:-2] for ref in rules.get("fixedReferences", [])}
        if any(token not in {"ScenePrompt", "Mixed 1"} | fixed for token in tokens):
            raise BadRequest("文字换场景只允许 {{ScenePrompt}} 参数和 {{Mixed 1}} 原视频引用")
    elif SCENE_TOKEN in prompt:
        raise BadRequest("只有文字换场景模板可使用 {{ScenePrompt}} 参数")
