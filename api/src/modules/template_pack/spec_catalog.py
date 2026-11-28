"""模板规格用到的模型目录纯查询。

只被 `spec_gate` 和 `spec_service` 共用，避免两边各算一次「固定模型 + 允许清单」。
"""

from __future__ import annotations

from collections.abc import Sequence

from core.ports.model_generation import ModelSummary, get_model_generation


def effective_model_ids(
    fixed_model: str, allowed_models: Sequence[str]
) -> tuple[str, ...]:
    """固定模型 + 作者声明的允许清单（去重、保序）。"""
    ids = [fixed_model]
    for model_id in allowed_models:
        if model_id not in ids:
            ids.append(model_id)
    return tuple(ids)


def model_catalog() -> dict[str, ModelSummary]:
    """按 id 与兼容别名索引模型目录。"""
    catalog: dict[str, ModelSummary] = {}
    for item in get_model_generation().models():
        catalog[item.id] = item
        for alias in item.aliases:
            catalog.setdefault(alias, item)
    return catalog


def model_capability(model_id: str) -> str:
    model = model_catalog().get(model_id)
    return model.capability if model is not None else "video"


def capability_label(capability: str) -> str:
    return {"video": "视频", "image": "生图", "audio": "音频"}.get(capability, capability)
