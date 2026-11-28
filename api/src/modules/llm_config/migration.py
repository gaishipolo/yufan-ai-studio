"""供应商重命名 / 旧 default 配置迁移。

两种动作共用一套约束：

- 迁移 `.env` 里的 `LLM_<OLD>_*` 键；
- 把模型 id 从 `<old>/...` 改成 `<new>/...`；
- 同步 `LLM_MODEL_CHAIN` 里的模型 id；
- 返回 `model_ids` 映射，供调用方同步运行期引用（`app_meta.llm.random_rewrite`）。

旧 `llm/*` 模型 id 只允许在「default → 具名供应商」迁移里出现，迁移后立即失效。
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from core.random_rewrite import RANDOM_REWRITE_META_KEY, renamed_model_reference
from db.app_meta import get_json, set_json
from infra.env_lock import env_write_lock
from infra.llm.providers.openai_compatible import provider_config
from infra.llm.providers.source import (
    normalize_provider_name,
    parse_model_entries,
    read_file_values,
    read_lines,
)
from modules.llm_config.store import (
    _LINE_RE,
    MODEL_CHAIN_KEY,
    ModelMapping,
    ProviderOut,
    _atomic_write,
    _encode_chain,
    _encode_models,
    _fingerprint,
    _mask,
    _parse_models,
    _replace_lines,
    _validate_base_url,
    _validate_value,
    read_model_chain,
)


@dataclass(frozen=True, slots=True)
class RenameResult:
    """改名/迁移后的结果。`model_ids` 用于同步运行期引用。"""

    provider: ProviderOut
    model_ids: dict[str, str]
    chain: tuple[str, ...]


def _renamed_models(
    old_name: str, new_name: str, models: Sequence[ModelMapping]
) -> tuple[tuple[ModelMapping, ...], dict[str, str]]:
    old_prefix = f"{old_name}/"
    new_prefix = f"{new_name}/"
    renamed: list[ModelMapping] = []
    mapping: dict[str, str] = {}
    for model in models:
        if not model.id.startswith(old_prefix):
            raise ValueError(f"模型 id 不属于供应商 {old_name}：{model.id}")
        new_id = new_prefix + model.id[len(old_prefix) :]
        mapping[model.id] = new_id
        renamed.append(ModelMapping(new_id, model.upstream))
    return tuple(renamed), mapping


def _renamed_chain(
    old_name: str, new_name: str, mapping: dict[str, str]
) -> tuple[str, ...]:
    chain = read_model_chain()
    old_prefix = f"{old_name}/"
    new_prefix = f"{new_name}/"
    renamed: list[str] = []
    for model_id in chain:
        if model_id in mapping:
            renamed.append(mapping[model_id])
        elif model_id.startswith(old_prefix):
            renamed.append(new_prefix + model_id[len(old_prefix) :])
        else:
            renamed.append(model_id)
    return tuple(renamed)


def _rewrite_chain_for_migration(
    raw: str, target: str, models: Sequence[ModelMapping]
) -> tuple[str, ...]:
    text = (raw or "").strip()
    if not text:
        return (models[0].id,) if models else ()
    try:
        parsed = json.loads(text)
    except ValueError as exc:
        raise ValueError("LLM_MODEL_CHAIN 不是合法 JSON") from exc
    if not isinstance(parsed, list):
        raise ValueError("LLM_MODEL_CHAIN 必须是 JSON 数组")
    result: list[str] = []
    seen: set[str] = set()
    for item in parsed:
        if not isinstance(item, str) or not item.strip():
            raise ValueError("LLM_MODEL_CHAIN 只能放非空字符串")
        model_id = item.strip()
        if model_id == "llm/default":
            raise ValueError("LLM_MODEL_CHAIN 不能包含 llm/default")
        if model_id.startswith("llm/"):
            model_id = f"{target}/{model_id[4:]}"
        elif "/" not in model_id:
            model_id = f"{target}/{model_id}"
        if model_id in seen:
            raise ValueError(f"LLM_MODEL_CHAIN 里模型重复：{model_id}")
        seen.add(model_id)
        result.append(model_id)
    # 旧配置写了空数组 = 没设过默认档；与「没写这个键」同口径，取第一项。
    if not result and models:
        return (models[0].id,)
    return tuple(result)


def _slug_upstream(upstream: str) -> str:
    """把上游模型名派生成平台 id 的一段：MiniMax-M3.1-Flash → minimax-m3.1-flash。"""
    slug = re.sub(r"[^a-z0-9.]+", "-", upstream.lower()).strip("-")
    return slug or "model"


def _env_owned(keys: Sequence[str]) -> list[str]:
    return [key for key in keys if key in os.environ]


def rename_provider(old_name: str, new_name: str) -> RenameResult:
    """给具名供应商改名，并迁移 env 键、模型前缀和降级链。"""
    old_name = normalize_provider_name(old_name)
    new_name = normalize_provider_name(new_name)
    if old_name == new_name:
        raise ValueError("新供应商名与原名相同")
    values = read_file_values()
    old_prefix = f"LLM_{old_name.upper()}_"
    new_prefix = f"LLM_{new_name.upper()}_"
    old_keys = [key for key in values if key.startswith(old_prefix)]
    if not old_keys:
        raise ValueError(f"供应商 {old_name} 不存在")
    env_old = [key for key in os.environ if key.startswith(old_prefix)]
    if env_old:
        raise ValueError("这些项由进程环境变量提供，不能改名：" + "、".join(sorted(env_old)))
    conflicts = [key for key in (*values, *os.environ) if key.startswith(new_prefix)]
    if conflicts:
        raise ValueError("目标供应商名已存在：" + new_name)

    config = provider_config(old_name)
    new_models, mapping = _renamed_models(
        old_name, new_name, _parse_models(config.models, old_name)
    )
    if MODEL_CHAIN_KEY in os.environ:
        raise ValueError(f"模型优先链由进程环境变量提供，无法随供应商改名：{MODEL_CHAIN_KEY}")
    chain = _renamed_chain(old_name, new_name, mapping)

    changes: dict[str, str] = {}
    for key in old_keys:
        suffix = key[len(old_prefix) :]
        value = values[key]
        if suffix == "MODELS":
            value = _encode_models(new_models)
        changes[f"{new_prefix}{suffix}"] = value
    changes[MODEL_CHAIN_KEY] = _encode_chain(chain)
    overridden = _env_owned(tuple(changes))
    if overridden:
        raise ValueError("这些项由进程环境变量提供，不能改名：" + "、".join(sorted(overridden)))

    with env_write_lock():
        kept = [
            line
            for line in read_lines()
            if not (
                (match := _LINE_RE.match(line)) is not None
                and match.group(1).startswith(old_prefix)
            )
        ]
        _atomic_write(_replace_lines(kept, changes))
    from infra.env_settings import clear_cache

    clear_cache()
    return RenameResult(
        provider=ProviderOut(
            name=new_name,
            base_url=config.base_url,
            api_key_configured=bool(config.api_key),
            api_key_masked=_mask(config.api_key),
            api_key_fingerprint=_fingerprint(config.api_key) if config.api_key else "",
            models=new_models,
            source="env_file",
            config_error="",
        ),
        model_ids=mapping,
        chain=chain,
    )


async def sync_random_settings_after_rename(
    session: AsyncSession,
    old_name: str,
    result: RenameResult,
    *,
    actor: str,
) -> str:
    """把 `app_meta.llm.random_rewrite.model` 里的旧前缀改到新供应商。"""
    entry = await get_json(session, RANDOM_REWRITE_META_KEY)
    if not isinstance(entry, dict):
        return ""
    model = entry.get("model")
    if not isinstance(model, str):
        return ""
    renamed = renamed_model_reference(model, old_name, result.provider.name)
    if renamed is None:
        return ""
    await set_json(
        session,
        RANDOM_REWRITE_META_KEY,
        {**entry, "model": renamed},
        actor=actor,
        detail=f"供应商 {old_name} 改名为 {result.provider.name}",
    )
    await session.commit()
    return renamed


def migrate_legacy_default(target_name: str = "primary") -> RenameResult:
    """把旧 default 的 LLM_* / llm/* 迁到具名供应商。"""
    target = normalize_provider_name(target_name)
    values = read_file_values()
    new_prefix = f"LLM_{target.upper()}_"
    conflicts = [key for key in (*values, *os.environ) if key.startswith(new_prefix)]
    if conflicts:
        raise ValueError("目标供应商名已存在：" + target)

    legacy_base = "LLM_DEFAULT_BASE_URL" if "LLM_DEFAULT_BASE_URL" in values else "LLM_BASE_URL"
    legacy_prefix = "LLM_DEFAULT_" if legacy_base == "LLM_DEFAULT_BASE_URL" else "LLM_"
    legacy_keys = (
        f"{legacy_prefix}BASE_URL",
        f"{legacy_prefix}API_KEY",
        f"{legacy_prefix}MODELS",
        f"{legacy_prefix}MODEL",
        f"{legacy_prefix}TIMEOUT_SEC",
    )
    if not any(key in values for key in legacy_keys):
        raise ValueError("没有可迁移的 default 配置")
    env_legacy = [key for key in legacy_keys if key in os.environ]
    if env_legacy:
        raise ValueError("这些项由进程环境变量提供，不能迁移：" + "、".join(sorted(env_legacy)))
    if MODEL_CHAIN_KEY in os.environ:
        raise ValueError(f"模型优先链由进程环境变量提供，不能迁移：{MODEL_CHAIN_KEY}")

    base_url = _validate_base_url(_validate_value("网关地址", values.get(legacy_keys[0], "")))
    api_key = values.get(legacy_keys[1], "")
    entries = parse_model_entries(values.get(legacy_keys[2], ""))
    if not entries and values.get(legacy_keys[3], "").strip():
        # V1 只配了单值 LLM_MODEL：用一个由上游名派生的 id 建一条映射。
        upstream = values[legacy_keys[3]].strip()
        slug = re.sub(r"[^a-z0-9]+", "-", upstream.lower()).strip("-") or "default"
        entries = ((f"llm/{slug}", upstream),)
    new_models: list[ModelMapping] = []
    mapping: dict[str, str] = {}
    seen_ids: set[str] = set()
    for source_id, upstream in entries:
        model_id = source_id
        if model_id == "llm/default":
            # 旧默认档把「默认模型」本身也写进了映射表。迁移后没有叫
            # default 的普通模型，按上游名派生一个稳定 id。
            model_id = f"llm/{_slug_upstream(upstream)}"
        if model_id.startswith("llm/"):
            new_id = f"{target}/{model_id[4:]}"
        elif "/" not in model_id:
            new_id = f"{target}/{model_id}"
        else:
            raise ValueError(f"default 配置里出现其它供应商的模型：{model_id}")
        if new_id in seen_ids:
            raise ValueError(f"迁移后模型 id 重复：{new_id}")
        seen_ids.add(new_id)
        if source_id != "llm/default":
            # llm/default 是「走整条链」的别名，不能收窄成某个具体模型：
            # 运行期引用保持原样，只同步具体的 llm/* id。
            mapping[source_id] = new_id
        new_models.append(ModelMapping(new_id, upstream))
    if not new_models:
        raise ValueError("default 配置没有模型，无法确定默认模型")

    chain = _rewrite_chain_for_migration(values.get(MODEL_CHAIN_KEY, ""), target, new_models)
    changes: dict[str, str] = {
        f"{new_prefix}BASE_URL": base_url,
        f"{new_prefix}MODELS": _encode_models(tuple(new_models)),
        MODEL_CHAIN_KEY: _encode_chain(chain),
    }
    if api_key.strip():
        changes[f"{new_prefix}API_KEY"] = _validate_value("密钥", api_key)
    if values.get(legacy_keys[3], "").strip():
        changes[f"{new_prefix}MODEL"] = values[legacy_keys[3]]
    if values.get(legacy_keys[4], "").strip():
        changes[f"{new_prefix}TIMEOUT_SEC"] = values[legacy_keys[4]]
    overridden = _env_owned(tuple(changes))
    if overridden:
        raise ValueError("这些项由进程环境变量提供，不能迁移：" + "、".join(sorted(overridden)))

    remove = set(legacy_keys) | {"LLM_MODEL"}
    with env_write_lock():
        kept = [
            line
            for line in read_lines()
            if not ((match := _LINE_RE.match(line)) is not None and match.group(1) in remove)
        ]
        _atomic_write(_replace_lines(kept, changes))
    from infra.env_settings import clear_cache

    clear_cache()
    return RenameResult(
        provider=ProviderOut(
            name=target,
            base_url=base_url,
            api_key_configured=bool(api_key),
            api_key_masked=_mask(api_key),
            api_key_fingerprint=_fingerprint(api_key) if api_key else "",
            models=tuple(new_models),
            source="env_file",
            config_error="",
        ),
        model_ids=mapping,
        chain=chain,
    )


__all__ = [
    "RenameResult",
    "migrate_legacy_default",
    "rename_provider",
    "sync_random_settings_after_rename",
]