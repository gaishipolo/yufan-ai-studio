"""运维页可编辑的运行时配置：声明清单 + 原子写 `.env` + 脱敏。

## 为什么放在 model_driver 模块

运维页归 `model_driver` 拥有（页面码 `page:ops` 由它声明），而这份清单里
有供应商的字段名与中文标签 —— 公共层不该认识供应商（架构守卫会拦），
业务模块之间又不许互相 import，所以清单只能放在运维页所属的模块里。
白名单的两个 key 只是字符串声明，不需要 import image / library。

写入机制（解析 env、原子替换）是本模块内部实现，不跨模块复用；
热生效缓存本身在 `infra/env_settings.py`，那里不含任何供应商名。

## 清单从哪来

- 白名单两项固定声明（安全防线，归 image / library，文案必须精确）；
- 供应商接入项**从已登记的 `DriverSettings` 自动发现** —— 新增供应商只要按约定
  声明 `api_key` / `base_url`，运维页就自动出现，不需要记得来改这里。

「哪些配置能在页面上改」只有这一处来源：前端展示、写入键校验、脱敏规则都读它。
少一处就会漂移 —— 要么页面摆了改不动的控件，要么接口收了不该收的键。

## 写入语义

- **只改声明过的键**，其它行原样保留（含注释、空行、顺序）；
- 原子写：同目录临时文件 + `os.replace()`，中途失败不留半截 `.env`；
- 写完清掉配置缓存（`infra.env_settings.clear_cache()`），下一次请求即生效；
- 审计只记「哪个键变了」，**不记值**（密钥写进审计表就收不回来）。

## 环境变量优先级与只读项

pydantic-settings 的优先级是 `os.environ > .env 文件 > 默认值`。若某个键由
进程环境变量注入（容器 secrets / systemd Environment），改 `.env` **不会生效**。
这类项在清单里标 `source=environment`、`editable=False`，页面明说原因，
而不是让用户改完一片绿、下次请求还是旧值。

## 密钥脱敏

密钥字段的**明文不出现在出参**，只给「是否已配」与哈希指纹（前 8 位）。
指纹让运维能看出「换过key」，又不能反推原文。审计同理不记值。
"""

from __future__ import annotations

import hashlib
import os
import re
import tempfile
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path

from core.config import ENV_FILE
from infra.env_settings import clear_cache

_ENV_PATH = Path(ENV_FILE)

#: 单值长度上限。白名单/URL 都不会超；挡住误粘贴整段文本把 .env 写坏。
_MAX_VALUE_LEN = 2048
#: 域名白名单单项：只认 host[:port]，不认带协议/路径的 URL（那是 SSRF 入口）。
_HOST_RE = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?(:\d{1,5})?$")
_URL_RE = re.compile(r"^https?://[^\s]+$", re.IGNORECASE)


def _validate_hosts(value: str) -> str:
    parts = [part.strip().lower() for part in value.replace(";", ",").split(",")]
    parts = [part for part in parts if part]
    if not parts:
        return ""
    bad = [part for part in parts if not _HOST_RE.match(part)]
    if bad:
        raise ValueError(
            "白名单只接受域名或 host:port（逗号分隔），不能带 http:// 或路径："
            + "、".join(bad)
        )
    return ",".join(parts)


def _validate_url(value: str) -> str:
    value = value.strip()
    if value and not _URL_RE.match(value):
        raise ValueError("地址必须是 http:// 或 https:// 开头的完整 URL")
    return value


def _validate_any(value: str) -> str:
    return value.strip()


@dataclass(frozen=True, slots=True)
class ConfigField:
    """一个可在运维页编辑的配置项。"""

    key: str
    label: str
    group: str
    secret: bool
    validate: Callable[[str], str] = _validate_any
    hint: str = ""
    required: bool = False


# 白名单是安全防线，不是某个供应商的接入参数：文案固定、必填、host 校验。
# 不写进「供应商自动发现」里，是因为它们归 image / library 模块，
# 而运维页不该 import 业务模块（跨模块 import 被架构守卫禁止）。
_ALLOWLIST_FIELDS: tuple[ConfigField, ...] = (
    ConfigField(
        "IMAGE_ASSET_ALLOWED_HOSTS",
        "成图 CDN 白名单",
        "白名单",
        secret=False,
        validate=_validate_hosts,
        required=True,
        hint="成图地址只允许这些域名（逗号分隔）；缺域名会让任务成功但存不进封面",
    ),
    ConfigField(
        "LIBRARY_INGEST_ALLOWED_HOSTS",
        "素材入库来源白名单",
        "白名单",
        secret=False,
        validate=_validate_hosts,
        required=True,
        hint="从外部链接入库时允许下载的域名（逗号分隔）；缺域名会让成片进不了草稿箱",
    ),
)

# 供应商侧只暴露「接入类」字段：密钥、网关地址、浏览器账号地址。
# 超时、并发、模型名等是调优参数，不进运维页（页面越精简越不会被误改）。
_CONNECTION_FIELD_PARTS = ("api_key", "base_url", "browser_url")
_SECRET_FIELD_PARTS = ("api_key", "secret", "token", "password")

# 字段名 → 展示文案。按字段名匹配，不含任何供应商名（公共代码不认识供应商）。
_FIELD_LABELS: tuple[tuple[str, str, str], ...] = (
    ("api_key", "密钥", "填写新值覆盖；留空表示不改。明文不会显示，也不会写进审计"),
    ("base_url", "网关地址", "供应商 API 地址，必须是完整 http(s) URL"),
    ("browser_urls", "队列账号池", "逗号分隔的浏览器调试地址；换账号不必重启"),
    ("browser_url", "队列账号", "浏览器调试地址；换账号不必重启"),
)


def _discover_vendor_fields() -> tuple[ConfigField, ...]:
    """从已登记的 `DriverSettings` 自动派生供应商接入项。

    这正是「新增供应商不用改公共文件」的既有模式：驱动一 import，它的
    Settings 就登记进 `infra.driver_config`，这里按字段名筛出接入类参数。
    新增供应商只要按约定声明 `api_key` / `base_url`，运维页就自动出现对应项
    —— 不需要记得来改这份清单。

    白名单 Settings（image / library）不带这些字段名，不会被误收进来。
    """
    import importlib
    import pkgutil

    from infra.driver_config import import_all_driver_settings

    drivers_package = importlib.import_module("modules.model_driver.drivers")
    driver_modules = tuple(
        f"{drivers_package.__name__}.{name}"
        for _, name, _ in pkgutil.iter_modules(drivers_package.__path__)
        if not name.startswith("_")
    )
    classes = import_all_driver_settings(extra_modules=driver_modules)

    found: dict[str, ConfigField] = {}
    for cls in classes:
        prefix = str(cls.model_config.get("env_prefix") or "").upper()
        for name in cls.model_fields:
            lower = name.lower()
            kind = next(
                (k for k in _CONNECTION_FIELD_PARTS if k in lower),
                None,
            )
            if kind is None:
                continue
            key = f"{prefix}{name.upper()}"
            label_extra, hint = next(
                (text, note) for part, text, note in _FIELD_LABELS if part in lower
            )
            label = f"{prefix.rstrip('_')} {label_extra}"
            found[key] = ConfigField(
                key=key,
                label=label,
                group="供应商接入",
                secret=any(part in lower for part in _SECRET_FIELD_PARTS),
                validate=_validate_url if "url" in lower else _validate_any,
                hint=hint,
            )
    return tuple(found[key] for key in sorted(found))


FIELDS: tuple[ConfigField, ...] = _ALLOWLIST_FIELDS + _discover_vendor_fields()

_FIELDS_BY_KEY = {field.key: field for field in FIELDS}
if len(_FIELDS_BY_KEY) != len(FIELDS):  # pragma: no cover - 声明写错时必须早炸
    raise RuntimeError("runtime_config.FIELDS 有重复 key")


@dataclass(frozen=True, slots=True)
class ConfigItem:
    """出参中的一项，密钥的 `value` 恒为空。"""

    key: str
    label: str
    group: str
    secret: bool
    value: str
    configured: bool
    fingerprint: str
    masked: str
    source: str  # environment / env_file / default
    editable: bool
    hint: str
    required: bool


@dataclass(frozen=True, slots=True)
class ConfigUpdate:
    """一次写入的结果。"""

    updated: tuple[str, ...]
    unchanged: tuple[str, ...]
    skipped: tuple[str, ...]


def _read_env_lines() -> list[str]:
    try:
        return _ENV_PATH.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return []
    except OSError:
        # 读不了就别装作能写：让上层报出来，不静默吞掉
        raise


def _env_file_values(lines: list[str]) -> dict[str, str]:
    """只认 `KEY=value` / `export KEY=value`，忽略注释与空行。"""
    values: dict[str, str] = {}
    pattern = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$")
    for line in lines:
        match = pattern.match(line)
        if match is None:
            continue
        raw = match.group(2).strip()
        # 去掉成对的引号（.env 常见写法），不做变量展开
        if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in ("'", '"'):
            raw = raw[1:-1]
        values[match.group(1)] = raw
    return values


def _effective(key: str, file_values: dict[str, str]) -> tuple[str, str, bool]:
    """返回 (当前生效值, 来源, 是否可编辑)。

    进程环境变量优先于 `.env` 文件：由环境变量提供时页面改文件没用，标只读。
    """
    if key in os.environ:
        return os.environ[key], "environment", False
    if key in file_values:
        return file_values[key], "env_file", True
    return "", "default", True


def _fingerprint(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:8]


#: 掩码保留的首尾位数。太短认不出是哪把 key，太长等于把 key 泄露一半。
_MASK_EDGE = 4


def _mask(value: str) -> str:
    """密钥的脱敏展示：前 4 + `***` + 后 4；短到不足以遮时全遮。"""
    if not value:
        return ""
    if len(value) <= _MASK_EDGE * 2:
        return "*" * len(value)
    return f"{value[:_MASK_EDGE]}***{value[-_MASK_EDGE:]}"


def list_items() -> tuple[ConfigItem, ...]:
    """当前生效的配置清单（密钥只有掩码与指纹，没有完整明文）。"""
    lines = _read_env_lines()
    file_values = _env_file_values(lines)
    items: list[ConfigItem] = []
    for field in FIELDS:
        value, source, editable = _effective(field.key, file_values)
        if field.secret:
            items.append(
                ConfigItem(
                    key=field.key,
                    label=field.label,
                    group=field.group,
                    secret=True,
                    value="",
                    configured=bool(value),
                    fingerprint=_fingerprint(value) if value else "",
                    masked=_mask(value),
                    source=source,
                    editable=editable,
                    hint=field.hint,
                    required=field.required,
                )
            )
        else:
            items.append(
                ConfigItem(
                    key=field.key,
                    label=field.label,
                    group=field.group,
                    secret=False,
                    value=value,
                    configured=bool(value),
                    fingerprint="",
                    masked="",
                    source=source,
                    editable=editable,
                    hint=field.hint,
                    required=field.required,
                )
            )
    return tuple(items)


def _validate_value(field: ConfigField, value: str) -> str:
    if len(value) > _MAX_VALUE_LEN:
        raise ValueError(f"{field.label} 超过 {_MAX_VALUE_LEN} 字符")
    if any(ch in value for ch in ("\n", "\r", "\x00")):
        raise ValueError(f"{field.label} 不能包含换行或空字符")
    return field.validate(value)


def apply_updates(values: dict[str, str]) -> ConfigUpdate:
    """把改动写回 `.env`，返回哪些真改了、哪些没变、哪些被跳过。

    `values` 里未声明的键直接拒绝（由路由层转成 400），避免绕过清单写任意键。
    由进程环境变量提供的项跳过并说明 —— 写了也不生效，不能骗用户。
    """
    unknown = set(values) - set(_FIELDS_BY_KEY)
    if unknown:
        raise ValueError("不支持编辑的配置项：" + "、".join(sorted(unknown)))

    lines = _read_env_lines()
    before = _env_file_values(lines)
    skipped = tuple(
        key for key in values if not _effective(key, before)[2]
    )
    changes: dict[str, str] = {}
    for key, raw in values.items():
        field = _FIELDS_BY_KEY[key]
        if key in skipped:
            continue
        # 密钥空串 = 不改：页面上的密码框留空提交不能把现有密钥清掉
        if field.secret and not raw.strip():
            continue
        value = _validate_value(field, raw)
        if before.get(key, "") == value:
            continue
        changes[key] = value

    unchanged = tuple(key for key in values if key not in skipped and key not in changes)
    if not changes:
        return ConfigUpdate(updated=(), unchanged=unchanged, skipped=skipped)

    new_lines = _replace_lines(lines, changes)
    _atomic_write(new_lines)
    # 清缓存：下一次请求/提交就读到新值，不必重启
    clear_cache()

    return ConfigUpdate(
        updated=tuple(changes),
        unchanged=unchanged,
        skipped=skipped,
    )


def _replace_lines(lines: list[str], changes: dict[str, str]) -> list[str]:
    """替换已知键那一行，保留注释、空行、顺序；没出现过的键追加到末尾。"""
    pattern = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$")
    result: list[str] = []
    seen: set[str] = set()
    for line in lines:
        match = pattern.match(line)
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
    """同目录临时文件 + 原子替换，失败不留半截文件。"""
    _ENV_PATH.parent.mkdir(parents=True, exist_ok=True)
    text = "\n".join(lines) + "\n"
    fd, tmp_name = tempfile.mkstemp(
        prefix=".env-runtime-", dir=str(_ENV_PATH.parent)
    )
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


__all__ = ["FIELDS", "ConfigField", "ConfigItem", "ConfigUpdate", "apply_updates", "list_items"]
