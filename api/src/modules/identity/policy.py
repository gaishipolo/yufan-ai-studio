"""口令策略：长度 + 「一眼能猜到」的形态。

单独一个文件，是因为**所有会写入口令的入口都必须走同一份规则**：
API 的 Pydantic validator（`schema.py`）和运维命令 `app create-admin`（`cli.py`）。
之前 CLI 只校验长度，导致「生产环境的第一个超管」恰好是唯一绕过完整策略的账号——
而它多半是整条链路里权限最大的那个（R5-04）。

`ValueError` 的子类：Pydantic validator 里直接抛就能变成 422，
CLI 里 `except WeakPassword` 就能变成一行人话。
"""

from __future__ import annotations

from typing import Final

PASSWORD_MIN_LENGTH: Final = 10
# 上限与 schema 里 `max_length` 保持一致：太长会让 scrypt 的输入变得可以当 DoS 用
PASSWORD_MAX_LENGTH: Final = 128
# 少于这么多种不同字符就判「过于简单」（`aaaa1111` 只有 2 种）
PASSWORD_MIN_UNIQUE_CHARS: Final = 4


def describe() -> dict[str, object]:
    """给 `GET /identity/password-policy` 用的策略快照。

    **文案也在这里出**。前端原本在 `validation.ts` 里硬编码「至少 10 位」，
    后端改成 12 位之后，用户按提示输入却被 422 拒绝——这类不一致不是靠
    「记得同步」解决的，得让策略只有一个来源。

    `forbid_same_as_username` 目前是 `false`，因为**确实没有实现**。
    接口里如实返回 false，而不是返回一个前端会照做的 true：
    那会让界面拦住一个后端并不拦的输入，属于把不一致换个方向重演。
    """
    return {
        "min_length": PASSWORD_MIN_LENGTH,
        "max_length": PASSWORD_MAX_LENGTH,
        "forbid_all_numeric": True,
        "forbid_same_as_username": False,
        "message": (
            f"至少 {PASSWORD_MIN_LENGTH} 位，不能是纯数字，也不要用重复字符凑长度"
        ),
    }


class WeakPassword(ValueError):
    """口令不满足策略。"""


def check_password_strength(value: str) -> str:
    """校验并原样返回，方便在 validator 里链式使用。

    只挡三类：太短、纯数字、重复字符过多。真正衡量强度要做熵估算，
    收益还不如这三条——它们挡住的是「1234567890」「aaaaaaaaaa」这类
    字典第一位、且是最常见的运维手滑。
    """
    if len(value) < PASSWORD_MIN_LENGTH:
        raise WeakPassword(f"密码至少 {PASSWORD_MIN_LENGTH} 位")
    if value.isdigit():
        raise WeakPassword("密码不能是纯数字")
    if len(set(value)) < PASSWORD_MIN_UNIQUE_CHARS:
        raise WeakPassword("密码过于简单（重复字符过多）")
    return value


__all__ = [
    "PASSWORD_MAX_LENGTH",
    "PASSWORD_MIN_LENGTH",
    "PASSWORD_MIN_UNIQUE_CHARS",
    "WeakPassword",
    "check_password_strength",
    "describe",
]
