"""模板声明的媒体槽位；未声明的包内图片仍全部附带。"""

from __future__ import annotations

import re
from typing import Any

from core.exceptions import BadRequest
from modules.template.pack_roles import PACK_ROLES
from modules.template.video_template_options import is_scene_text


def reference_roles(rules: dict[str, Any], prompt: str, kind: str) -> list[str] | None:
    bindings = rules.get("referenceBindings")
    if is_scene_text(rules):
        if kind != "scene" or bindings not in (None, {"{{Mixed 1}}": "source_video"}):
            raise BadRequest("文字换场景仅支持场景类型和一个原视频输入")
        return []
    if bindings is None:
        return None  # 旧版模板仍按完整 mediaOrder 排序。
    if not isinstance(bindings, dict) or not 2 <= len(bindings) <= 11:
        raise BadRequest("bindings 必须声明视频及至少一个图片槽位")
    tokens = [f"{{{{Mixed {index}}}}}" for index in range(1, len(bindings) + 1)]
    if set(bindings) != set(tokens) or list(bindings.values()).count("source_video") != 1:
        raise BadRequest("bindings 必须包含一个视频槽位，编号从 Mixed 1 连续排列")
    roles = [bindings[token] for token in tokens if bindings[token] != "source_video"]
    if any(not isinstance(role, str) or role not in PACK_ROLES.get(kind, {}) for role in roles):
        raise BadRequest("bindings 图片用途与素材包类型不匹配")
    cited = {
        f"{{{{{token}}}}}"
        for token in re.findall(r"\{\{([^{}]+)\}\}", prompt)
        if token != "ReplacementScope"
    }
    fixed_tokens = {ref["token"] for ref in rules.get("fixedReferences", [])}
    if not cited <= set(tokens) | fixed_tokens:
        raise BadRequest("提示词中的每个 Mixed 引用都必须在 bindings 中声明")
    if kind == "character" and roles.count("front") > 1 and set(roles) != {"front"}:
        raise BadRequest("多人换脸请为每位人物声明一个 front 槽位，其余图片自动作为辅助参考附带")
    if kind == "character" and roles.count("front") > 6:
        raise BadRequest("当前多人换脸最多支持6个人物角色卡")
    return roles


def allows_partial_slots(required: list[str], kind: str = "character") -> bool:
    """多人换脸的默认样例包只填第一位，其余身份留待分别选卡。

    典型是「3 个正面身份图 = 视频里 3 位人物」：人物包一人一包、各一张正面图，
    一个包天生填不满 3 个槽位，剩下的由用户在替换配置里逐个选人补上。
    每种用途只声明一个槽位时**不**放宽——那说明包选错了，必须当场报缺项。
    """
    return kind == "character" and len(required) > 1 and set(required) == {"front"}


def source_position(rules: dict[str, Any]) -> int:
    bindings = rules.get("referenceBindings")
    if not bindings:
        return 0
    return next(
        index
        for index in range(len(bindings))
        if bindings.get(f"{{{{Mixed {index + 1}}}}}") == "source_video"
    )


def place_source(source: Any, images: list[Any], rules: dict[str, Any]) -> list[Any]:
    index = source_position(rules)
    return [*images[:index], source, *images[index:]]


def character_slots(template: Any) -> int:
    if template is None or template.default_kind != "character":
        return 1
    roles = reference_roles(template.rules, template.prompt, "character")
    if roles is not None:
        return max(1, roles.count("front"))
    return max(
        1,
        sum(
            item.type == "image" and item.role == "front" and item.in_prompt
            for item in template.media_order
        ),
    )


def ensure_distinct_roles(roles: dict[str, str], required: list[str], kind: str) -> None:
    """多槽位模板的底线：每种**用到的**用途至少得有一张图，否则运行时必然失败。"""
    provided = set(roles.values())
    labels = PACK_ROLES.get(kind, {})
    missing = [role for role in dict.fromkeys(required) if role not in provided]
    if missing:
        names = "、".join(labels.get(role, role) for role in missing)
        raise BadRequest(f"当前素材包缺少「{names}」，无法填充提示词槽位")


def order_reference_ids(
    ids: list[str], roles: dict[str, str], required: list[str], kind: str
) -> list[str]:
    remaining = list(ids)
    ordered = []
    for role in required:
        picked = next((asset_id for asset_id in remaining if roles.get(asset_id) == role), None)
        if picked is None:
            label = PACK_ROLES.get(kind, {}).get(role, role)
            raise BadRequest(f"当前素材包缺少足够的「{label}」，无法填充提示词槽位")
        remaining.remove(picked)
        ordered.append(picked)
    return [*ordered, *remaining]
