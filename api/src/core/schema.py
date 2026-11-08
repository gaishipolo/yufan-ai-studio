"""Pydantic 基座：统一 camelCase 契约，与前端 TypeScript 类型一一对应。"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Annotated, Any, TypeVar

from pydantic import BaseModel, ConfigDict, PlainSerializer

_CAMEL_RE = re.compile(r"_([a-z0-9])")

T = TypeVar("T", bound=BaseModel)


def _iso_utc(value: datetime) -> str:
    """统一输出带时区的 ISO8601（`...Z`）。

    sqlite 读回的 datetime 是 naive 的，这里按 UTC 补齐，
    避免前端 `new Date()` 按本地时区解析导致时间偏移。
    """
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


# 时间字段统一使用该类型，保证出入参格式一致
UtcDateTime = Annotated[datetime, PlainSerializer(_iso_utc, return_type=str)]


def to_camel(snake: str) -> str:
    """`duration_sec` → `durationSec`（保留前导下划线与已有驼峰）。"""
    return _CAMEL_RE.sub(lambda m: m.group(1).upper(), snake)


def to_snake(camel: str) -> str:
    """`durationSec` → `duration_sec`。"""
    return re.sub(r"(?<!^)(?=[A-Z])", "_", camel).lower()


class CamelModel(BaseModel):
    """所有对外出入参的基类。

    - 序列化/反序列化默认使用 camelCase（前端零适配）
    - `from_attributes` 支持直接从 ORM 实例构造
    - `populate_by_name` 允许内部代码用 snake_case 构造
    """

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        from_attributes=True,
        str_strip_whitespace=True,
    )

    def dump(self, *, exclude_none: bool = True) -> dict[str, Any]:
        return self.model_dump(by_alias=True, exclude_none=exclude_none)


class CamelORMModel(CamelModel):
    """**语义标记**：出参是从 ORM 实体读出来的（`from_attributes` 已在父类放开）。

    这里曾经写着「额外放开 lazy 属性访问开销」，但两份 `model_config` 逐字相同——
    那句话既没有实现，也做不到：`from_attributes` 只决定能不能 `model_validate(obj)`，
    「读不读 lazy 关系」取决于查询里的 `selectinload` / `noload`，不是模型基类能管的。
    保留这个类是为了让「这是 ORM 出参」在类型上可读（`UserOut` / `RoleOut` /
    `SessionOut` 用它），不要指望它比 `CamelModel` 多做什么。
    """
