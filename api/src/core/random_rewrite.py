"""随机改写运行配置的**共享键**（不含业务实现）。

配置本体在 `modules/template_pack.random_settings`；LLM 配置模块在供应商重命名/
旧 default 迁移时也要改里面的模型引用，所以键与模型前缀改写规则放在 core，
避免两个业务模块互相 import。
"""

from __future__ import annotations

RANDOM_REWRITE_META_KEY = "llm.random_rewrite"


def renamed_model_reference(
    model: str, old_name: str, new_name: str
) -> str | None:
    """把 `old_name/...` 改成 `new_name/...`；不匹配或 `llm/default` 返回 None。"""
    if not model or model == "llm/default":
        return None
    prefix = f"{old_name}/"
    if not model.startswith(prefix):
        return None
    return f"{new_name}/{model[len(prefix):]}"


__all__ = ["RANDOM_REWRITE_META_KEY", "renamed_model_reference"]
