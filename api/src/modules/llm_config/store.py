"""LLM 供应商配置的持久化：解析 / 写回 `api/.env` 里的扁平键。

形状（每个供应商一组，名字由用户在页面上定，必须是 `[a-z][a-z0-9_]*`）：

```
LLM_<NAME>_BASE_URL=https://.../v1
LLM_<NAME>_API_KEY=...
LLM_<NAME>_MODELS=[{"id":"llm/qwen-plus","upstream":"qwen-plus"}]
```

**`default` 是特例：它的键就是 `LLM_BASE_URL` / `LLM_API_KEY` / `LLM_MODELS`。**
这不是"为了兼容旧形状"的权宜，而是必须如此 —— 运行时 `infra.llm.complete()`
的 `LLMSettings` 前缀就是 `LLM_`。如果页面写 `LLM_DEFAULT_*` 而运行时读
`LLM_*`，页面配好了调用却读空（2026-11-27 审查抓到的断链）。让 default
直接写运行时读的键，两份真相就不会漂移。

为什么不用一张 JSON 大键：`runtime_config.apply_updates` 的密钥脱敏、
`.env.example` 对齐、运维页字段发现全按「一个键一个值」工作。一个供应商
拆成三个标准键，就能继续用这套纪律；密钥不会混进模型表里被日志打出来。

与 `modules/model_driver/runtime_config.py` 不互相 import（业务模块禁止横向
依赖）—— 原子写、脱敏这些**做法**照抄，但各自持有自己的实现。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path

from core.config import ENV_FILE

_ENV_PATH = Path(ENV_FILE)
_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,29}$")
_LINE_RE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$")
_MAX_VALUE_LEN = 2048
_MASK_EDGE = 4

#: 一个供应商最多挂多少模型，防止误粘贴把 .env 撑爆。
MAX_MODELS = 100


def normalize_name(raw: str) -> str:
    name = raw.strip().lower()
    if not _NAME_RE.match(name):
        raise ValueError("供应商名只能用小写字母、数字、下划线，且以字母开头（最长 30 字）")
    return name


def keys_of(name: str) -> tuple[str, str, str]:
    """(base_url, api_key, models) 三个 env 键。

    `default` 直接用 `LLM_*`（与运行时 LLMSettings 前缀一致），其余供应商
    用 `LLM_<NAME>_*`。这样「页面保存」和「运行时调用」读的是同一组键。
    """
    prefix = "LLM" if name == "default" else f"LLM_{name.upper()}"
    return f"{prefix}_BASE_URL", f"{prefix}_API_KEY", f"{prefix}_MODELS"


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


@dataclass(frozen=True, slots=True)
class ProviderListOut:
    providers: tuple[ProviderOut, ...]


def _read_lines() -> list[str]:
    try:
        return _ENV_PATH.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return []
    except OSError:
        raise


def _file_values(lines: list[str]) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in lines:
        match = _LINE_RE.match(line)
        if match is None:
            continue
        raw = match.group(2).strip()
        if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in ("'", '"'):
            raw = raw[1:-1]
        values[match.group(1)] = raw
    return values


def _effective(key: str, file_values: dict[str, str]) -> tuple[str, str]:
    if key in os.environ:
        return os.environ[key], "environment"
    if key in file_values:
        return file_values[key], "env_file"
    return "", "default"


def _discover_names(values: dict[str, str]) -> tuple[str, ...]:
    """从文件键反推供应商名：凡是有 `LLM_*_BASE_URL` 的就认为存在。

    只认 base_url 而不认 api_key：运维先填网关、后填密钥是常见顺序，
    缺密钥的条目要显示出来让人补，而不是凭空消失。

    兼容 V1 的旧形状 `LLM_BASE_URL`/`LLM_API_KEY`/`LLM_MODEL`：显示为
    `default` 供应商，让人看得见、能迁到具名供应商，不静默丢配置。
    """
    names: set[str] = set()
    pattern = re.compile(r"^LLM_(.+)_BASE_URL$")
    for key in values:
        match = pattern.match(key)
        if match is None:
            continue
        candidate = match.group(1).lower()
        # `LLM_DEFAULT_*` 是早期版本写出的形状：归于 default，不另立门户。
        if _NAME_RE.match(candidate) and candidate != "default":
            names.add(candidate)
    # `LLM_BASE_URL`（default 的键）与旧形状共用；有它就有 default。
    if "LLM_BASE_URL" in values or "LLM_DEFAULT_BASE_URL" in values:
        names.add("default")
    return tuple(sorted(names))


def _keys_of_effective(name: str, values: dict[str, str] | None = None) -> tuple[str, str, str]:
    """default 兼容早期两种形状，避免升级后页面把已有配置读空：

    - `LLM_DEFAULT_BASE_URL/API_KEY/MODELS`（第一版多供应商页写的）；
    - `LLM_MODEL`（V1 单值模型名）。页面保存一次即统一到 `LLM_*`。
    """
    keys = keys_of(name)
    if name != "default":
        return keys
    if values is None:
        return keys
    if keys[0] in values:
        # 已是当前形状；模型键仍可能停在早期单值 LLM_MODEL
        if keys[2] not in values and "LLM_MODEL" in values:
            return keys[0], keys[1], "LLM_MODEL"
        return keys
    if "LLM_DEFAULT_BASE_URL" in values:
        return "LLM_DEFAULT_BASE_URL", "LLM_DEFAULT_API_KEY", "LLM_DEFAULT_MODELS"
    return keys


def _parse_models(raw: str) -> tuple[ModelMapping, ...]:
    if not raw.strip():
        return ()
    # V1 旧形状：LLM_MODEL 是单个上游模型名。转成一条 llm/default 映射，
    # 让旧配置在页面上可见、可编辑（保存时迁到新键）。
    if not raw.lstrip().startswith("["):
        return (ModelMapping("llm/default", raw.strip()),)
    try:
        parsed = json.loads(raw)
    except ValueError as exc:
        raise ValueError("模型列表不是合法 JSON") from exc
    if not isinstance(parsed, list):
        raise ValueError("模型列表必须是数组")
    if len(parsed) > MAX_MODELS:
        raise ValueError(f"模型最多 {MAX_MODELS} 个")
    result: list[ModelMapping] = []
    seen: set[str] = set()
    for item in parsed:
        if not isinstance(item, dict):
            raise ValueError("模型项必须是对象")
        model_id = str(item.get("id") or "").strip()
        upstream = str(item.get("upstream") or "").strip()
        if not model_id or not upstream:
            raise ValueError("模型的 id 和 upstream 都不能为空")
        if not model_id.startswith("llm/"):
            raise ValueError(f"模型 id 必须以 llm/ 开头：{model_id}")
        if model_id in seen:
            raise ValueError(f"模型 id 重复：{model_id}")
        seen.add(model_id)
        result.append(ModelMapping(model_id, upstream))
    return tuple(result)


def credentials_of(name: str) -> tuple[str, str]:
    """取某个供应商的 (base_url, api_key) 明文，仅供服务端内部调用。

    明文不进任何出参 —— 页面拿到的只有掩码与指纹。
    """
    name = normalize_name(name)
    values = _file_values(_read_lines())
    url_key, key_key, _ = _keys_of_effective(name, values)
    base_url, _ = _effective(url_key, values)
    api_key, _ = _effective(key_key, values)
    return base_url, api_key


def list_providers() -> ProviderListOut:
    lines = _read_lines()
    values = _file_values(lines)
    providers: list[ProviderOut] = []
    for name in _discover_names(values):
        url_key, key_key, models_key = _keys_of_effective(name, values)
        base_url, source = _effective(url_key, values)
        api_key, _ = _effective(key_key, values)
        models_raw, _ = _effective(models_key, values)
        providers.append(
            ProviderOut(
                name=name,
                base_url=base_url,
                api_key_configured=bool(api_key),
                api_key_masked=_mask(api_key),
                api_key_fingerprint=_fingerprint(api_key) if api_key else "",
                models=_parse_models(models_raw),
                source=source,
            )
        )
    return ProviderListOut(tuple(providers))


def _validate_value(label: str, value: str) -> str:
    if len(value) > _MAX_VALUE_LEN:
        raise ValueError(f"{label} 超过 {_MAX_VALUE_LEN} 字符")
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


def _validate_models(models: tuple[ModelMapping, ...]) -> None:
    """页面传进来的模型映射同样要过 id 前缀/重复校验，不只解析 .env 时查。"""
    seen: set[str] = set()
    for model in models:
        if not model.id.startswith("llm/"):
            raise ValueError(f"模型 id 必须以 llm/ 开头：{model.id}")
        if not model.upstream.strip():
            raise ValueError(f"模型 {model.id} 的上游名不能为空")
        if model.id in seen:
            raise ValueError(f"模型 id 重复：{model.id}")
        seen.add(model.id)


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
    _validate_models(models)

    lines = _read_lines()
    before = _file_values(lines)
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

    encoded = json.dumps(
        [{"id": m.id, "upstream": m.upstream} for m in models],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    changes[models_key] = _validate_value("模型列表", encoded)

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
    )


def delete_provider(name: str) -> None:
    name = normalize_name(name)
    keys = set(keys_of(name))
    if name == "default":
        # 早期形状一起清掉，否则删完页面刷新又"发现"一个 default。
        keys.update(
            ("LLM_DEFAULT_BASE_URL", "LLM_DEFAULT_API_KEY", "LLM_DEFAULT_MODELS", "LLM_MODEL")
        )
    lines = _read_lines()
    values = _file_values(lines)
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
    _atomic_write(kept)
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
    "MAX_MODELS",
    "ModelMapping",
    "ProviderListOut",
    "credentials_of",
    "ProviderOut",
    "delete_provider",
    "keys_of",
    "list_providers",
    "normalize_name",
    "save_provider",
]
