"""LLM 供应商配置的持久化：解析 / 写回 `api/.env` 里的扁平键。

形状（每个供应商一组，名字由用户在页面上定，必须是 `[a-z][a-z0-9_]*`）：

```
LLM_<NAME>_BASE_URL=https://.../v1
LLM_<NAME>_API_KEY=...
LLM_<NAME>_MODELS=[{"id":"minimax/flash","upstream":"MiniMax-M3.1-Flash"}]
```

**`default` 是特例：它的键就是 `LLM_BASE_URL` / `LLM_API_KEY` / `LLM_MODELS`。**
这不是"为了兼容旧形状"的权宜，而是必须如此 —— 运行时 `infra.llm.complete()`
的 `LLMSettings` 前缀就是 `LLM_`。如果页面写 `LLM_DEFAULT_*` 而运行时读
`LLM_*`，页面配好了调用却读空（2026-11-27 审查抓到的断链）。让 default
直接写运行时读的键，两份真相就不会漂移。

平台模型 id 带供应商前缀：default 用 `llm/*`，具名供应商用 `<name>/*`。
运行时按前缀路由到 `LLM_<NAME>_*`，页面建的供应商才真的能调用。

另有一个**全局**键 `LLM_MODEL_CHAIN`：平台模型 id 的有序数组，第一项为默认
模型，`llm/default` 调用失败时按顺序降级。它不是供应商私有的，所以不套
`LLM_<NAME>_` 前缀。

为什么不用一张 JSON 大键：`runtime_config.apply_updates` 的密钥脱敏、
`.env.example` 对齐、运维页字段发现全按「一个键一个值」工作。一个供应商
拆成三个标准键，就能继续用这套纪律；密钥不会混进模型表里被日志打出来。

读取逻辑在 `infra/llm/providers/source.py`：页面与运行时必须读同一份解析，
否则就是又一次断链。本文件只负责**写**与页面侧校验。
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections.abc import Collection, Sequence
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path

from core.config import ENV_FILE
from infra.llm.providers import source as provider_source
from infra.llm.providers.openai_compatible import MAX_CHAIN, parse_model_chain
from infra.llm.providers.source import (
    MAX_MODELS,
    discover_provider_names,
    effective,
    keys_of,
    normalize_model_id,
    normalize_provider_name,
    parse_model_entries,
    provider_keys,
    provider_of,
    read_file_values,
    read_lines,
)

_ENV_PATH = Path(ENV_FILE)
_LINE_RE = provider_source.LINE_RE
_MAX_VALUE_LEN = 2048
#: 模型表允许更长：100 个映射的 JSON 很容易超过 2048，不能一边说最多 100 个
#: 一边在 20 个时因行长拒绝。
_MAX_MODELS_VALUE_LEN = 32 * 1024
_MASK_EDGE = 4

#: 默认模型 + 降级链的全局键。
MODEL_CHAIN_KEY = "LLM_MODEL_CHAIN"


def normalize_name(raw: str) -> str:
    return normalize_provider_name(raw)


def _fingerprint(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:8]


def _mask(value: str) -> str:
    if not value:
        return ""
    if len(value) <= _MASK_EDGE * 2:
        return "*" * len(value)
    return f"{value[:_MASK_EDGE]}***{value[-_MASK_EDGE:]}"


@dataclass(frozen=True, slots=True)
class ModelMapping:
    """平台 id → 上游 model 名。"""

    id: str
    upstream: str


@dataclass(frozen=True, slots=True)
class ProviderOut:
    name: str
    base_url: str
    api_key_configured: bool
    api_key_masked: str
    api_key_fingerprint: str
    models: tuple[ModelMapping, ...]
    source: str  # environment / env_file / default
    #: 该供应商自己的配置错误（目前只有模型列表解析）。页面展示它，
    #: 但其它供应商照常返回——一个坏键不能把整个配置页变成 500。
    config_error: str = ""


@dataclass(frozen=True, slots=True)
class ProviderListOut:
    providers: tuple[ProviderOut, ...]


def _parse_models(raw: str, provider_name: str) -> tuple[ModelMapping, ...]:
    if not raw.strip():
        return ()
    if not raw.lstrip().startswith("["):
        # V1 旧形状：LLM_MODEL 是单个上游模型名。只有 default 有这种形状。
        if provider_name != "default":
            raise ValueError(f"供应商 {provider_name} 的模型列表必须是 JSON 数组")
        return (ModelMapping("llm/default", raw.strip()),)
    entries = parse_model_entries(raw)
    result: list[ModelMapping] = []
    seen: set[str] = set()
    for raw_id, upstream in entries:
        model_id = normalize_model_id(provider_name, raw_id)
        if model_id in seen:
            raise ValueError(f"模型 id 重复：{model_id}")
        seen.add(model_id)
        result.append(ModelMapping(model_id, upstream))
    return tuple(result)


def _normalize_models(
    provider_name: str, models: Sequence[ModelMapping]
) -> tuple[ModelMapping, ...]:
    result: list[ModelMapping] = []
    seen: set[str] = set()
    for model in models:
        model_id = normalize_model_id(provider_name, model.id)
        upstream = model.upstream.strip()
        if not upstream:
            raise ValueError(f"模型 {model_id} 的上游名不能为空")
        if model_id in seen:
            raise ValueError(f"模型 id 重复：{model_id}")
        seen.add(model_id)
        result.append(ModelMapping(model_id, upstream))
    return tuple(result)


def credentials_of(name: str) -> tuple[str, str]:
    """取某个供应商的 (base_url, api_key) 明文，仅供服务端内部调用。

    明文不进任何出参 —— 页面拿到的只有掩码与指纹。
    """
    return provider_source.credentials_of(normalize_name(name))


def list_providers() -> ProviderListOut:
    values = read_file_values()
    providers: list[ProviderOut] = []
    for name in discover_provider_names(values):
        url_key, key_key, models_key = provider_keys(name, values)
        base_url, source = effective(url_key, values)
        api_key, _ = effective(key_key, values)
        models_raw, _ = effective(models_key, values)
        try:
            models = _parse_models(models_raw, name)
            config_error = ""
        except ValueError as exc:
            # 一个供应商的坏模型列表不能把整个页面变成 500；运行时加载
            # 该供应商时会明确 400（见 load_provider）。
            models = ()
            config_error = f"{models_key}：{exc}"
        providers.append(
            ProviderOut(
                name=name,
                base_url=base_url,
                api_key_configured=bool(api_key),
                api_key_masked=_mask(api_key),
                api_key_fingerprint=_fingerprint(api_key) if api_key else "",
                models=models,
                source=source,
                config_error=config_error,
            )
        )
    return ProviderListOut(tuple(providers))


def read_model_chain() -> tuple[str, ...]:
    """读取全局默认模型/降级链；坏 JSON 抛 `ValueError`。"""
    raw, _ = effective(MODEL_CHAIN_KEY)
    return parse_model_chain(raw)


def _safe_read_chain() -> tuple[str, ...]:
    """写供应商时剪链用：链坏了不该阻断本次供应商保存。"""
    try:
        return read_model_chain()
    except ValueError:
        return ()


def _encode_chain(chain: Sequence[str]) -> str:
    return json.dumps(list(chain), ensure_ascii=False, separators=(",", ":"))


def _encode_models(models: Sequence[ModelMapping]) -> str:
    return json.dumps(
        [{"id": model.id, "upstream": model.upstream} for model in models],
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _validate_value(label: str, value: str, *, max_len: int = _MAX_VALUE_LEN) -> str:
    if len(value) > max_len:
        raise ValueError(f"{label} 超过 {max_len} 字符")
    if any(ch in value for ch in ("\n", "\r", "\x00")):
        raise ValueError(f"{label} 不能包含换行或空字符")
    return value.strip()


def _validate_base_url(url: str) -> str:
    """必须公网 http(s) 地址，带版本路径。

    复用 `infra.http.validate_public_url`（与运维页网关字段同一口径），
    不在这里另写一份 SSRF 判断 —— 两处规则漂移过一次就够。
    """
    from urllib.parse import urlparse

    from infra.http import validate_public_url

    parts = urlparse(url)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise ValueError("网关地址必须是完整的 http(s) URL")
    if parts.username or parts.password or parts.query or parts.fragment:
        raise ValueError("网关地址不能带账号、查询串或片段")
    validate_public_url(url)
    return url.rstrip("/")


def _prune_chain(changes: dict[str, str], name: str, models: Sequence[ModelMapping]) -> None:
    """供应商的模型变了/被删了，链里不能再留着它。

    环境变量提供的链改不了，跳过；链本身坏了也不阻断本次供应商保存。
    """
    if MODEL_CHAIN_KEY in os.environ:
        return
    chain = _safe_read_chain()
    if not chain:
        return
    available = {model.id for model in models}
    remaining = tuple(
        model_id
        for model_id in chain
        if not (provider_of(model_id) == name and model_id not in available)
    )
    if remaining != chain:
        changes[MODEL_CHAIN_KEY] = _encode_chain(remaining)


def save_provider(
    name: str,
    *,
    base_url: str,
    api_key: str,
    models: tuple[ModelMapping, ...],
) -> ProviderOut:
    """新增或更新一个供应商。api_key 留空 = 不改已有的密钥。"""
    name = normalize_name(name)
    if len(models) > MAX_MODELS:
        raise ValueError(f"模型最多 {MAX_MODELS} 个")
    models = _normalize_models(name, models)

    lines = read_lines()
    before = read_file_values()
    # `default` 直接写 LLM_* —— 与运行时 LLMSettings 前缀一致，不再"迁移"到
    # LLM_DEFAULT_*（那正是断链的根源）。保存时顺手清掉早期版本留下的
    # LLM_DEFAULT_*，避免两处都在、改一处不生效。
    url_key, key_key, models_key = keys_of(name)
    # 早期形状：LLM_DEFAULT_* / LLM_MODEL（单值）
    stale_keys = (
        ("LLM_DEFAULT_BASE_URL", "LLM_DEFAULT_API_KEY", "LLM_DEFAULT_MODELS", "LLM_MODEL")
        if name == "default"
        else ()
    )

    clean_url = _validate_base_url(_validate_value("网关地址", base_url))
    changes: dict[str, str] = {url_key: clean_url}

    if api_key.strip():
        changes[key_key] = _validate_value("密钥", api_key)
    elif name == "default" and key_key not in before and "LLM_DEFAULT_API_KEY" in before:
        # 早期 LLM_DEFAULT_* 形状：留空保存也要把旧密钥带过来，不能弄丢
        changes[key_key] = before["LLM_DEFAULT_API_KEY"]
    elif key_key in before and not os.environ.get(key_key):
        # 留空 = 不改；但只有 .env 里有值、且不被环境变量覆盖时才成立
        pass

    changes[models_key] = _validate_value(
        "模型列表", _encode_models(models), max_len=_MAX_MODELS_VALUE_LEN
    )
    _prune_chain(changes, name, models)

    # 环境变量提供的项写了也不生效，明确拒绝，不骗用户。
    overridden = [k for k in changes if k in os.environ]
    if overridden:
        raise ValueError("这些项由进程环境变量提供，不能在页面改：" + "、".join(sorted(overridden)))

    # 清掉早期形状，避免两处都在、将来改一处不生效。
    to_remove = set(stale_keys)
    kept_lines = [
        line
        for line in lines
        if not ((match := _LINE_RE.match(line)) is not None and match.group(1) in to_remove)
    ]
    _atomic_write(_replace_lines(kept_lines, changes))
    from infra.env_settings import clear_cache

    clear_cache()

    final_key = changes.get(key_key, before.get(key_key, ""))
    return ProviderOut(
        name=name,
        base_url=clean_url,
        api_key_configured=bool(final_key),
        api_key_masked=_mask(final_key),
        api_key_fingerprint=_fingerprint(final_key) if final_key else "",
        models=models,
        source="env_file",
        config_error="",
    )


def _normalize_chain(models: Sequence[str]) -> tuple[str, ...]:
    chain: list[str] = []
    seen: set[str] = set()
    for raw in models:
        model_id = str(raw).strip()
        if not model_id:
            raise ValueError("模型优先链里的 id 不能为空")
        if model_id == "llm/default":
            raise ValueError("模型优先链不能包含 llm/default：它表示整条链本身")
        if model_id in seen:
            raise ValueError(f"模型优先链里模型重复：{model_id}")
        seen.add(model_id)
        chain.append(model_id)
    if len(chain) > MAX_CHAIN:
        raise ValueError(f"模型优先链最多 {MAX_CHAIN} 个")
    return tuple(chain)


def save_model_chain(models: Sequence[str], *, known: Collection[str]) -> tuple[str, ...]:
    """保存默认模型/降级顺序；每个 id 必须已存在于某个供应商的模型映射里。"""
    chain = _normalize_chain(models)
    unknown = [model_id for model_id in chain if model_id not in known]
    if unknown:
        raise ValueError("这些模型不在任何供应商的模型映射里：" + "、".join(unknown))
    if MODEL_CHAIN_KEY in os.environ:
        raise ValueError(f"这些项由进程环境变量提供，不能在页面改：{MODEL_CHAIN_KEY}")

    lines = read_lines()
    changes = {MODEL_CHAIN_KEY: _validate_value("模型优先链", _encode_chain(chain))}
    _atomic_write(_replace_lines(lines, changes))
    from infra.env_settings import clear_cache

    clear_cache()
    return chain


def delete_provider(name: str) -> None:
    name = normalize_name(name)
    keys = set(keys_of(name))
    if name == "default":
        # 早期形状一起清掉，否则删完页面刷新又"发现"一个 default。
        keys.update(
            ("LLM_DEFAULT_BASE_URL", "LLM_DEFAULT_API_KEY", "LLM_DEFAULT_MODELS", "LLM_MODEL")
        )
    lines = read_lines()
    values = read_file_values()
    overridden = [k for k in keys if k in os.environ]
    if overridden:
        raise ValueError("这些项由进程环境变量提供，不能删除：" + "、".join(sorted(overridden)))
    if not any(k in values for k in keys):
        raise ValueError(f"供应商 {name} 不存在")
    kept = [
        line
        for line in lines
        if not ((match := _LINE_RE.match(line)) is not None and match.group(1) in keys)
    ]
    changes: dict[str, str] = {}
    _prune_chain(changes, name, ())
    _atomic_write(_replace_lines(kept, changes) if changes else kept)
    from infra.env_settings import clear_cache

    clear_cache()


def _replace_lines(lines: list[str], changes: dict[str, str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for line in lines:
        match = _LINE_RE.match(line)
        if match is None or match.group(1) not in changes:
            result.append(line)
            continue
        key = match.group(1)
        seen.add(key)
        prefix = "export " if line.lstrip().startswith("export ") else ""
        result.append(f"{prefix}{key}={changes[key]}")
    for key, value in changes.items():
        if key not in seen:
            result.append(f"{key}={value}")
    return result


def _atomic_write(lines: list[str]) -> None:
    _ENV_PATH.parent.mkdir(parents=True, exist_ok=True)
    text = "\n".join(lines) + "\n"
    fd, tmp_name = tempfile.mkstemp(prefix=".env-llm-", dir=str(_ENV_PATH.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, _ENV_PATH)
    except BaseException:
        with suppress(OSError):
            os.unlink(tmp_name)
        raise


__all__ = [
    "MAX_CHAIN",
    "MAX_MODELS",
    "MODEL_CHAIN_KEY",
    "ModelMapping",
    "ProviderListOut",
    "ProviderOut",
    "credentials_of",
    "delete_provider",
    "keys_of",
    "list_providers",
    "normalize_model_id",
    "normalize_name",
    "read_model_chain",
    "save_model_chain",
    "save_provider",
]