"""随机改写的运行配置：存在 `app_meta`，编译时读，LLM 调试台保存。

为什么不放 `.env`：system prompt 是多行文本，而 `.env` 写入器明确拒绝换行；
它也不是供应商配置，放 `app_meta` 能顺带拿到版本 CAS 与写入历史。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from core.random_rewrite import RANDOM_REWRITE_META_KEY
from db.app_meta import get_json, set_json
from modules.template_pack.randomness import (
    DEFAULT_LLM_MODEL,
    DEFAULT_MAX_TOKENS,
    DEFAULT_SYSTEM_PROMPT,
    DEFAULT_TEMPERATURE,
    RandomRewriteSettings,
)

#: 键带域名前缀，与 `permissions.catalog` 同一约定。
RANDOM_REWRITE_KEY = RANDOM_REWRITE_META_KEY


@dataclass(frozen=True, slots=True)
class RandomSettingsState:
    settings: RandomRewriteSettings
    #: 是否有保存过的覆盖配置（没有就是代码默认值）。
    customized: bool


def _parse(raw: Any) -> RandomSettingsState:
    if not isinstance(raw, dict):
        return RandomSettingsState(RandomRewriteSettings(), customized=False)
    try:
        temperature = float(raw.get("temperature", DEFAULT_TEMPERATURE))
    except (TypeError, ValueError):
        temperature = DEFAULT_TEMPERATURE
    max_tokens = raw.get("max_tokens")
    if not isinstance(max_tokens, int) or max_tokens < 1:
        max_tokens = DEFAULT_MAX_TOKENS
    extra = raw.get("extra_params")
    system_prompt = raw.get("system_prompt")
    model = raw.get("model")
    return RandomSettingsState(
        RandomRewriteSettings(
            model=model.strip() if isinstance(model, str) and model.strip() else DEFAULT_LLM_MODEL,
            system_prompt=(
                system_prompt
                if isinstance(system_prompt, str) and system_prompt.strip()
                else DEFAULT_SYSTEM_PROMPT
            ),
            temperature=temperature,
            max_tokens=max_tokens,
            extra_params=dict(extra) if isinstance(extra, dict) else {},
        ),
        customized=True,
    )


async def read_random_settings(session: AsyncSession) -> RandomSettingsState:
    return _parse(await get_json(session, RANDOM_REWRITE_KEY))


async def save_random_settings(
    session: AsyncSession,
    *,
    model: str,
    system_prompt: str,
    temperature: float,
    max_tokens: int | None,
    extra_params: dict[str, Any],
    actor: str,
) -> RandomSettingsState:
    settings = RandomRewriteSettings(
        model=model.strip() or DEFAULT_LLM_MODEL,
        system_prompt=system_prompt,
        temperature=temperature,
        max_tokens=max_tokens,
        extra_params=dict(extra_params),
    )
    await set_json(
        session,
        RANDOM_REWRITE_KEY,
        {
            "model": settings.model,
            "system_prompt": settings.system_prompt,
            "temperature": settings.temperature,
            "max_tokens": settings.max_tokens,
            "extra_params": dict(settings.extra_params),
        },
        actor=actor,
        detail="随机改写配置",
    )
    await session.commit()
    return RandomSettingsState(settings, customized=True)


__all__ = [
    "RANDOM_REWRITE_KEY",
    "RandomSettingsState",
    "read_random_settings",
    "save_random_settings",
]