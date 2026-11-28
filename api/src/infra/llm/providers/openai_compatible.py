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
from dataclasses import dataclass
from typing import ClassVar

from core.exceptions import BadRequest
from infra.driver_config import DriverSettings, driver_config
from infra.env_settings import vendor_settings
from infra.llm.providers.source import (
    ProviderConfig,
    normalize_model_id,
    parse_model_entries,
    provider_config,
)


class LLMSettings(DriverSettings):
    """任意一个 OpenAI 兼容端点的接入参数。"""

    model_config = driver_config("LLM")

    base_url: str = ""  # 空 → 400，不猜默认网关
    api_key: str = ""
    timeout_sec: float = 120.0
    # 上游模型名，如 qwen-plus / deepseek-v3。调用方用平台 id，这里翻译。
    model: str = ""
    # 当前供应商的「平台 id → 上游模型名」映射（JSON 数组）。
    models: str = ""
    # 全局模型优先/降级链（JSON 数组，第一项为默认模型）。只有 `llm/default`
    # 走这条链；链项形如 `primary/qwen-plus` 或 `minimax/flash`。
    model_chain: str = ""
    # 页面保存网关时允许的私网/本机主机（逗号分隔）。空 = 只允许公网。
    allowed_hosts: str = ""

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
        """解析当前供应商的 models 字段。坏 JSON 直接抛 ``ValueError``。"""
        raw = self.models.strip()
        if not raw:
            return {}
        return dict(parse_model_entries(raw))

    def model_chain_ids(self) -> tuple[str, ...]:
        """解析 ``LLM_MODEL_CHAIN``；坏值抛 ``ValueError``（由调用方转 400）。"""
        return parse_model_chain(self.model_chain)

    def allowed_host_set(self) -> frozenset[str]:
        """``LLM_ALLOWED_HOSTS``：页面允许保存的私网/本机主机（可含端口）。"""
        raw = self.allowed_hosts.replace(";", ",")
        return frozenset(part.strip().lower() for part in raw.split(",") if part.strip())


get_llm_settings = vendor_settings(LLMSettings, env_prefix="LLM")

#: 平台 id → 上游 model 名。保留给将来的代码内映射；页面配置优先。
MODELS: dict[str, str] = {}

#: 降级链最多多少个模型。太长的链意味着一次业务调用可能打穿多家供应商。
MAX_CHAIN = 20


def parse_model_chain(raw: str) -> tuple[str, ...]:
    """解析模型优先链（JSON 数组，第一项为默认模型）。

    链项是**平台模型 id**（`primary/qwen-plus` / `minimax/flash`），不是上游名。
    `llm/default` 不能出现在链里——它表示整条链本身，放进去会自我递归；
    旧 `llm/*` 前缀也已停用。
    """
    text = raw.strip()
    if not text:
        return ()
    try:
        parsed = json.loads(text)
    except ValueError as exc:
        raise ValueError("LLM_MODEL_CHAIN 不是合法 JSON") from exc
    if not isinstance(parsed, list):
        raise ValueError("LLM_MODEL_CHAIN 必须是 JSON 数组")
    chain: list[str] = []
    seen: set[str] = set()
    for item in parsed:
        if not isinstance(item, str) or not item.strip():
            raise ValueError("LLM_MODEL_CHAIN 只能放非空字符串")
        model_id = item.strip()
        if model_id == "llm/default":
            raise ValueError("LLM_MODEL_CHAIN 不能包含 llm/default")
        if model_id.startswith("llm/"):
            raise ValueError(f"LLM_MODEL_CHAIN 里的旧 llm/* 模型 id 已停用：{model_id}")
        if model_id in seen:
            raise ValueError(f"LLM_MODEL_CHAIN 里模型重复：{model_id}")
        seen.add(model_id)
        chain.append(model_id)
    if len(chain) > MAX_CHAIN:
        raise ValueError(f"LLM_MODEL_CHAIN 最多 {MAX_CHAIN} 个模型")
    return tuple(chain)


@dataclass(frozen=True, slots=True)
class ProviderRuntime:
    """一次调用需要的供应商运行时配置。"""

    name: str
    settings: LLMSettings
    model_map: dict[str, str]
    api_key_env: str
    label: str


def load_provider(name: str) -> ProviderRuntime:
    """读某个供应商的配置；缺网关等配置错误一律是 4xx。"""
    try:
        config: ProviderConfig = provider_config(name)
    except ValueError as exc:
        raise BadRequest(f"LLM 供应商配置有误：{exc}") from exc
    if not config.base_url.strip():
        raise BadRequest(
            f"LLM 供应商 {config.name} 未配置：请设置 {config.base_url_env}（OpenAI 兼容端点）"
        )
    try:
        entries = parse_model_entries(config.models)
    except ValueError as exc:
        raise BadRequest(
            f"LLM 供应商 {config.name} 的模型映射（{config.models_env}）有误：{exc}"
        ) from exc
    settings = LLMSettings(
        base_url=config.base_url,
        api_key=config.api_key,
        timeout_sec=config.timeout_sec,
        model=config.model,
        models=config.models,
    )
    try:
        table: dict[str, str] = {}
        for model_id, upstream in entries:
            normalized = normalize_model_id(config.name, model_id)
            if normalized in table:
                raise ValueError(f"模型 id 重复：{normalized}")
            table[normalized] = upstream
    except ValueError as exc:
        raise BadRequest(
            f"LLM 供应商 {config.name} 的模型映射（{config.models_env}）有误：{exc}"
        ) from exc
    return ProviderRuntime(
        name=config.name,
        settings=settings,
        model_map=table,
        api_key_env=config.api_key_env,
        label=f"LLM {config.name}",
    )


def resolve_model(
    platform_id: str,
    settings: LLMSettings | None = None,
    model_map: dict[str, str] | None = None,
) -> str:
    """把平台 id 翻成上游 model 名；未知 id 抛 `ValueError`（由客户端转 400）。

    优先级：显式 `model_map`（连通测试指定）> `settings.models`（当前供应商
    页面存的映射）> 模块常量 `MODELS`。

    - 映射表里有 → 用映射值；
    - 其它 id 必须在该供应商的映射表里，不猜。
    """
    from_settings = settings.model_table() if settings else {}
    table = {**MODELS, **from_settings, **(model_map or {})}
    if platform_id in table:
        return table[platform_id]
    if platform_id == "llm/default":
        raise ValueError("llm/default 只能由 complete() 走模型链解析")
    raise ValueError(f"未知的 LLM 模型 id：{platform_id}（未在供应商模型映射里登记）")


__all__ = [
    "LLMSettings",
    "MAX_CHAIN",
    "MODELS",
    "ProviderRuntime",
    "get_llm_settings",
    "load_provider",
    "parse_model_chain",
    "resolve_model",
]
