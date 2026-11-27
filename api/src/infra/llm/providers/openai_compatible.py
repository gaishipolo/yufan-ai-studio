"""通用 OpenAI 兼容供应商的配置与模型表。

不绑死某一家：任意一个 OpenAI 兼容端点的 `base_url` + `api_key` 都能接。
V1 只做这一家；将来真要并存第二家，另建一个 `providers/<name>.py`，
换前缀即可，不改本文件。

配置必须是 `DriverSettings` 子类：运维页的字段自动发现
（`runtime_config._discover_vendor_fields` → `import_all_driver_settings`
的 `infra/**` rglob）与 `.env.example` 双向对齐守卫都只认这个基类。
写成模块级常量，运维页不会出现、守卫巡检不到、改 `.env` 也不热生效。
"""

from __future__ import annotations

import json
from typing import ClassVar

from infra.driver_config import DriverSettings, driver_config
from infra.env_settings import vendor_settings


class LLMSettings(DriverSettings):
    """任意一个 OpenAI 兼容端点的接入参数。"""

    model_config = driver_config("LLM")

    base_url: str = ""  # 空 → 400，不猜默认网关
    api_key: str = ""
    timeout_sec: float = 120.0
    # 上游模型名，如 qwen-plus / deepseek-v3。调用方用平台 id，这里翻译。
    model: str = ""
    # 页面配的「平台 id → 上游模型名」映射（JSON 数组）。与 LLM_BASE_URL
    # 同属 LLM_ 前缀：页面存 default 供应商时写这组键，运行时就在这里读。
    models: str = ""

    # V1 没有业务场景：生产不配 LLM 也应当能起。等真接业务再加入必填。
    # ClassVar 必须保留：写成普通字段会被 pydantic 当成 env 项，
    # config guard 会要求 .env.example 多列一个没用的键。
    prod_required_fields: ClassVar[tuple[str, ...]] = ()

    # ---- 配置翻译：client 只调这些方法，不直接碰字段。
    # 也是 config guard 要求的「字段有真实使用点」（它只在类定义文件里找 `.field`）。

    def chat_completions_url(self) -> str:
        """在 base_url 后拼上固定的 chat 路径。空串由 client 判 400。"""
        return self.base_url.strip().rstrip("/") + "/chat/completions"

    def bearer_token(self) -> str:
        return self.api_key.strip()

    def request_timeout(self) -> float:
        return self.timeout_sec

    def default_upstream_model(self) -> str:
        return self.model.strip()

    def model_table(self) -> dict[str, str]:
        """解析页面存的模型映射表。坏 JSON 视为未配置（不猜、不静默吞错）。"""
        raw = self.models.strip()
        if not raw or not raw.startswith("["):
            return {}
        try:
            parsed = json.loads(raw)
        except ValueError:
            return {}
        if not isinstance(parsed, list):
            return {}
        table: dict[str, str] = {}
        for item in parsed:
            if isinstance(item, dict) and item.get("id") and item.get("upstream"):
                table[str(item["id"])] = str(item["upstream"])
        return table


get_llm_settings = vendor_settings(LLMSettings, env_prefix="LLM")

#: 平台 id → 上游 model 名。带 `llm/` 前缀，天然全局唯一。
#: 新增模型只加一行映射；未知 id 一律 400，不静默回落。
MODELS: dict[str, str] = {}


def resolve_model(
    platform_id: str,
    settings: LLMSettings | None = None,
    model_map: dict[str, str] | None = None,
) -> str:
    """把平台 id 翻成上游 model 名；未知 id 抛 `ValueError`（由客户端转 400）。

    优先级：显式 `model_map`（连通测试指定）> `settings.models`（页面存的
    default 供应商映射）> 模块常量 `MODELS` > `llm/default` > `llm/<name>` 直连。
    映射表让同一个平台 id 指向不同家的上游模型 —— 换供应商只改配置，
    调用方永远只认平台 id。
    """
    from_settings = settings.model_table() if settings else {}
    table = {**MODELS, **from_settings, **(model_map or {})}
    if platform_id in table:
        return table[platform_id]
    if platform_id == "llm/default":
        if settings and settings.default_upstream_model():
            return settings.default_upstream_model()
        raise ValueError("LLM_MODEL 未配置，不能用 llm/default")
    if platform_id.startswith("llm/") and platform_id[4:].strip():
        return platform_id[4:]
    raise ValueError(f"未知的 LLM 模型 id：{platform_id}")


__all__ = ["LLMSettings", "MODELS", "get_llm_settings", "resolve_model"]
