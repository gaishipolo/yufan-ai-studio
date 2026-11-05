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
# 少于这么多种不同字符就判「过于简单」（`aaaa1111` 只有 2 种）
PASSWORD_MIN_UNIQUE_CHARS: Final = 4


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
    "PASSWORD_MIN_LENGTH",
    "PASSWORD_MIN_UNIQUE_CHARS",
    "WeakPassword",
    "check_password_strength",
]
