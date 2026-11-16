"""规格的结构校验：不碰数据库、不碰资产库，只回答"这份规格本身合不合法"。

分成独立文件而不是塞进 `compile.py`：封装页在用户点"预检"时要能只校验结构
（还没填过任何素材），而 `compile` 需要一份完整填充。两者的入参不同，混在一起
会让"没有填充时的校验"变成一堆可选参数。
"""

from __future__ import annotations

import re

from core.exceptions import BadRequest
from core.template_spec.models import (
    MAX_ALLOWED_MODELS,
    MAX_PROMPT,
    MAX_SLOT_TAGS,
    MAX_SLOTS,
    SLOT_TYPES,
    InputSlot,
    TemplateSpec,
)

#: 槽位 key：会出现在 `{{slot:key}}` 里，只允许下划线（连字符会让引用语法更难读）。
_KEY = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
#: 规格 key：URL 段 + 运营可读，允许连字符（`product-swap-6036`）。
_SPEC_KEY = re.compile(r"^[a-z][a-z0-9_-]{0,39}$")
#: 规格里**不许**出现编号。编号是编译产物（见 `compile.py` 的说明）。
#: `{{Text N}}` 同理：文本槽位在第几份由槽位顺序决定，写死会在少选一份时错位。
_MIXED_LITERAL = re.compile(r"\{\{\s*Mixed\s+\d+\s*\}\}")
_TEXT_LITERAL = re.compile(r"\{\{\s*Text\s+\d+\s*\}\}")
#: 提示词里的槽位引用：`{{slot:key}}` / `{{slot:key*}}` / `{{slot:key.N}}`。
#: 抓 key 是为了核对它有没有对应槽位 —— 引用一个已删掉的槽位，`compile` 才会报
#: 「引用了不存在的槽位」，而预检那一步会放行（它只管结构）。槽位可以删到 0，
#: 这条就更容易踩到，所以把判据提到结构校验里。
_SLOT_REF = re.compile(r"\{\{\s*slot:([a-z0-9_]+)(?:\*|\.\d+)?\s*\}\}")


def validate_spec(spec: TemplateSpec) -> None:
    """校验槽位声明与提示词形状。任一不合法就抛 `BadRequest`（带可读原因）。"""
    if not _SPEC_KEY.match(spec.key):
        raise BadRequest(
            "模板 key 只能是小写字母开头、含小写字母/数字/下划线/连字符的 1~40 字"
        )
    if not spec.name.strip():
        raise BadRequest("模板名称不能为空")
    if not spec.model.strip():
        raise BadRequest("必须选择模型")
    _validate_allowed_models(spec.allowed_models)
    if len(spec.slots) > MAX_SLOTS:
        raise BadRequest(f"槽位不能超过 {MAX_SLOTS} 个")

    keys: set[str] = set()
    positions: set[int] = set()
    for slot in spec.slots:
        if not _KEY.match(slot.key):
            raise BadRequest(f"槽位 key 不合法：{slot.key}")
        if slot.key in keys:
            raise BadRequest(f"槽位 key 重复：{slot.key}")
        keys.add(slot.key)
        if slot.position in positions:
            raise BadRequest(f"槽位位置重复：{slot.position}")
        positions.add(slot.position)
        _validate_slot(slot)

    for ref in sorted(set(_SLOT_REF.findall(spec.prompt))):
        if ref not in keys:
            raise BadRequest(
                f"提示词引用了不存在的槽位「{ref}」：删槽位时要把它的引用一并删掉"
            )

    if _MIXED_LITERAL.search(spec.prompt):
        raise BadRequest(
            "提示词不要直接写 {{Mixed N}}：编号由槽位顺序与填充数量派生，"
            "写死会在少填一张时全部错位"
        )
    if _TEXT_LITERAL.search(spec.prompt):
        raise BadRequest(
            "提示词不要直接写 {{Text N}}：文本槽位排第几由槽位顺序决定，"
            "写死会在少选一份时错位"
        )
    if len(spec.prompt) > MAX_PROMPT:
        raise BadRequest(f"提示词不能超过 {MAX_PROMPT} 字（现在 {len(spec.prompt)}）")
    if not spec.prompt.strip():
        raise BadRequest("提示词不能为空")


def _validate_allowed_models(models: tuple[str, ...]) -> None:
    """「允许选用的模型清单」的结构校验。

    只管**形状**（不碰目录）：模型 id 是否真的存在由 `_check_model` 在发布前查
    目录 —— 结构校验是纯函数，不碰 session。

    重复项要挡：同一个模型写两遍不会让使用者多一个选项，只会让清单看起来像
    「作者手滑」，而清单是要给人读的。
    """
    if len(models) > MAX_ALLOWED_MODELS:
        raise BadRequest(f"允许选用的模型不能超过 {MAX_ALLOWED_MODELS} 个")
    seen: set[str] = set()
    for model_id in models:
        if not model_id.strip():
            raise BadRequest("允许选用的模型 id 不能为空")
        if len(model_id) > 80:
            raise BadRequest(f"允许选用的模型 id 不合法：{model_id}")
        if model_id in seen:
            raise BadRequest(f"允许选用的模型重复：{model_id}")
        seen.add(model_id)


def _validate_slot(slot: InputSlot) -> None:
    if slot.type not in SLOT_TYPES:
        raise BadRequest(f"槽位「{slot.key}」的类型无效：{slot.type}")
    # 音频与文本都不该占视觉编号：执行器按位置取图/视频，塞进去会让编号整体偏移。
    if slot.type == "audio" and slot.ref_mode != "attachment":
        raise BadRequest(f"音频槽位「{slot.key}」必须是附加输入（refMode=attachment）")
    if slot.type == "text" and slot.ref_mode != "attachment":
        raise BadRequest(f"文本槽位「{slot.key}」必须是附加输入（refMode=attachment）")

    if slot.binding == "fixed" and not slot.asset_id:
        raise BadRequest(f"槽位「{slot.key}」固定素材时必须提供 assetId")
    if slot.binding == "user" and slot.asset_id:
        raise BadRequest(f"槽位「{slot.key}」由用户选择时不能预置 assetId")
    if slot.min_count < 0 or slot.max_count < 1:
        raise BadRequest(f"槽位「{slot.key}」的数量范围无效")
    if slot.min_count > slot.max_count:
        raise BadRequest(f"槽位「{slot.key}」的最小数量不能大于最大数量")
    if slot.type == "audio" and slot.binding == "user" and slot.max_count > 3:
        raise BadRequest(f"音频槽位「{slot.key}」最多 3 段")
    # 标签只约束候选集，不参与编译；这里只拦"明显不是 id 的字符串"，
    # 不查库（结构校验是纯函数，不碰 session）。
    if len(slot.tag_ids) > MAX_SLOT_TAGS:
        raise BadRequest(f"槽位「{slot.key}」最多圈 {MAX_SLOT_TAGS} 个标签")
    for tag_id in slot.tag_ids:
        if not tag_id or len(tag_id) > 32 or any(c.isspace() for c in tag_id):
            raise BadRequest(f"槽位「{slot.key}」的标签 id 不合法")
