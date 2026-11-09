"""提示词封装的工作台参数；素材包不定义固定提示词的参数。"""
from typing import Any

from core.exceptions import BadRequest

SCOPE_TOKEN = "{{ReplacementScope}}"


def scope_options(rules: dict[str, Any]) -> list[str]:
    raw = rules.get("scopeOptions", [])
    if not isinstance(raw, list) or any(not isinstance(item, str) for item in raw):
        raise BadRequest("替换范围选项必须是文字列表")
    options = [item.strip() for item in raw]
    if (len(options) > 30 or len("、".join(options)) > 1000 or len(set(options)) != len(options)
            or any(not item or len(item) > 100 or "、" in item
                   or "{{" in item or "}}" in item for item in options)):
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
