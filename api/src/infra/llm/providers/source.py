"""LLM 供应商配置的**读取**（进程环境变量 + `.env`）。

配置页（`modules/llm_config`）负责写，运行时（`infra.llm`）负责读；两边按同一组
键工作。读取逻辑放在 infra 的原因很具体：

- 运行时在 infra，按依赖方向不能 import 业务模块；
- 业务模块 import infra 是允许的（见 `tests/test_architecture.py`）。

页面与运行时各写一份解析，必然出现「页面能读、运行时读空」的断链——2026-11-27
`LLM_DEFAULT_*` 那次事故就是同一类问题。所以这里只留一份。

## 形状

```
LLM_<NAME>_BASE_URL / _API_KEY / _MODELS / _MODEL / _TIMEOUT_SEC
```

旧 `default` 供应商（`LLM_BASE_URL` 等无名字键）已停用；历史配置通过
`app llm-migrate-default --name primary` 一次性迁移。

`LLM_MODEL_CHAIN` 是**全局**键，不属于任何供应商；它由 `openai_compatible`
按「平台模型 id 的有序列表」解析，第一项即默认模型。
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

from core.config import ENV_FILE

_ENV_PATH = Path(ENV_FILE)

#: `.env` 一行一个键；允许 `export KEY=...`，与 Python dotenv 的常见写法一致。
LINE_RE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$")
_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,29}$")
_DEFAULT_TIMEOUT_SEC = 120.0

#: 一个供应商最多挂多少模型。页面写入与运行时加载共用这个上限。
MAX_MODELS = 100


def normalize_provider_name(raw: str) -> str:
    name = raw.strip().lower()
    if name in ("default", "llm"):
        raise ValueError(f"{name} 是保留名：请使用其它供应商名")
    if not _NAME_RE.match(name):
        raise ValueError("供应商名只能用小写字母、数字、下划线，且以字母开头（最长 30 字）")
    return name


def keys_of(name: str) -> tuple[str, str, str]:
    """(base_url, api_key, models) 三个 env 键。所有供应商都带名字。"""
    prefix = f"LLM_{normalize_provider_name(name).upper()}"
    return f"{prefix}_BASE_URL", f"{prefix}_API_KEY", f"{prefix}_MODELS"


def parse_model_entries(raw: str) -> tuple[tuple[str, str], ...]:
    """解析 ``LLM_*_MODELS`` 的 JSON 数组，返回 ``(平台 id, 上游名)``。

    页面读取、保存和运行时加载共用这一处：坏 JSON 不再出现"页面 500、
    运行时静默忽略"两套行为。空串表示没配置，返回空元组。
    """
    text = raw.strip()
    if not text:
        return ()
    try:
        parsed = json.loads(text)
    except ValueError as exc:
        raise ValueError("不是合法 JSON") from exc
    if not isinstance(parsed, list):
        raise ValueError("必须是 JSON 数组")
    if len(parsed) > MAX_MODELS:
        raise ValueError(f"最多 {MAX_MODELS} 个模型")
    entries: list[tuple[str, str]] = []
    for item in parsed:
        if not isinstance(item, dict):
            raise ValueError("每个模型项必须是对象")
        model_id = item.get("id")
        upstream = item.get("upstream")
        if not isinstance(model_id, str) or not model_id.strip():
            raise ValueError("模型的 id 不能为空")
        if not isinstance(upstream, str) or not upstream.strip():
            raise ValueError(f"模型 {model_id.strip()} 的上游名不能为空")
        entries.append((model_id.strip(), upstream.strip()))
    return tuple(entries)


def provider_keys(name: str, file_values: dict[str, str]) -> tuple[str, str, str]:
    """供应商的三个 env 键。已不再兼容 default / LLM_MODEL 旧形状。"""
    return keys_of(name)


def read_lines() -> list[str]:
    try:
        return _ENV_PATH.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return []


def read_file_values() -> dict[str, str]:
    values: dict[str, str] = {}
    for line in read_lines():
        match = LINE_RE.match(line)
        if match is None:
            continue
        raw = match.group(2).strip()
        if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in ("'", '"'):
            raw = raw[1:-1]
        values[match.group(1)] = raw
    return values


def effective(key: str, file_values: dict[str, str] | None = None) -> tuple[str, str]:
    """取生效值：进程环境变量 > `.env` > 空。"""
    if key in os.environ:
        return os.environ[key], "environment"
    values = read_file_values() if file_values is None else file_values
    if key in values:
        return values[key], "env_file"
    return "", "none"


def _env_keys() -> set[str]:
    return {key.upper() for key in os.environ if key.upper().startswith("LLM_")}


def discover_provider_names(file_values: dict[str, str] | None = None) -> tuple[str, ...]:
    """反推供应商名：凡是有 `LLM_*_BASE_URL` 的就认为存在。

    必须同时看进程环境变量：容器部署常把 `LLM_<NAME>_*` 注入环境，只扫 `.env`
    会让页面显示"还没有供应商"，而运行时其实是好的。

    只有具名供应商（`LLM_<NAME>_BASE_URL`）会被发现；default 旧形状不再列出，
    历史配置由迁移命令处理。
    """
    values = read_file_values() if file_values is None else file_values
    file_keys = {key.upper() for key in values}
    keys = file_keys | _env_keys()

    names: set[str] = set()
    for key in keys:
        match = re.match(r"^LLM_(.+)_BASE_URL$", key)
        if match is None:
            continue
        candidate = match.group(1).lower()
        if candidate != "default" and _NAME_RE.match(candidate):
            names.add(candidate)
    return tuple(sorted(names))


def provider_of(platform_id: str) -> str:
    """平台模型 id → 供应商名。旧 `llm/*` 与无前缀 id 一律失效。"""
    if platform_id == "llm/default":
        raise ValueError("llm/default 是默认模型别名，不是供应商模型")
    if "/" not in platform_id:
        raise ValueError(f"模型 id 必须带供应商前缀：{platform_id}")
    prefix = platform_id.split("/", 1)[0].strip().lower()
    if prefix == "llm":
        raise ValueError(f"旧 llm/* 模型 id 已停用：{platform_id}")
    if not _NAME_RE.match(prefix):
        raise ValueError(f"模型 id 的供应商前缀不合法：{platform_id}")
    return normalize_provider_name(prefix)


def model_prefix(provider_name: str) -> str:
    return normalize_provider_name(provider_name)


def normalize_model_id(provider_name: str, model_id: str) -> str:
    """模型 id 必须是 ``<供应商名>/<名字>``。旧 `llm/*` 不再兼容。"""
    mid = model_id.strip()
    prefix = model_prefix(provider_name)
    if mid.startswith("llm/"):
        raise ValueError(
            f"旧 llm/* 模型 id 已停用：{mid}；请迁移成 {prefix}/... 的形式"
        )
    if not mid.startswith(prefix + "/"):
        raise ValueError(f"供应商 {provider_name} 的模型 id 必须以 {prefix}/ 开头：{mid}")
    if not mid[len(prefix) + 1 :].strip():
        raise ValueError(f"模型 id 不能只有前缀：{mid}")
    return mid


def _timeout(raw: str, env_name: str) -> float:
    if not raw.strip():
        return _DEFAULT_TIMEOUT_SEC
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError(f"{env_name} 不是数字：{raw!r}") from exc
    if value <= 0:
        raise ValueError(f"{env_name} 必须大于 0")
    return value


@dataclass(frozen=True, slots=True)
class ProviderConfig:
    """某个供应商的原始配置（明文只在服务端内部流转）。"""

    name: str
    base_url: str
    api_key: str
    models: str
    model: str
    timeout_sec: float
    base_url_env: str
    api_key_env: str
    models_env: str


def provider_config(name: str) -> ProviderConfig:
    name = normalize_provider_name(name)
    values = read_file_values()
    url_key, key_key, models_key = provider_keys(name, values)
    base_url, _ = effective(url_key, values)
    api_key, _ = effective(key_key, values)
    models, _ = effective(models_key, values)
    prefix = f"LLM_{name.upper()}"
    model, _ = effective(f"{prefix}_MODEL", values)
    timeout_raw, _ = effective(f"{prefix}_TIMEOUT_SEC", values)
    return ProviderConfig(
        name=name,
        base_url=base_url,
        api_key=api_key,
        models=models,
        model=model,
        timeout_sec=_timeout(timeout_raw, f"{prefix}_TIMEOUT_SEC"),
        base_url_env=url_key,
        api_key_env=key_key,
        models_env=models_key,
    )


def credentials_of(name: str) -> tuple[str, str]:
    """服务端内部取明文 (base_url, api_key)；不进任何出参。"""
    config = provider_config(name)
    return config.base_url, config.api_key


__all__ = [
    "LINE_RE",
    "MAX_MODELS",
    "ProviderConfig",
    "credentials_of",
    "discover_provider_names",
    "effective",
    "keys_of",
    "model_prefix",
    "normalize_model_id",
    "normalize_provider_name",
    "parse_model_entries",
    "provider_config",
    "provider_keys",
    "provider_of",
    "read_file_values",
    "read_lines",
]