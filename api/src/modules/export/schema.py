"""导出出入参。字段用 snake_case 书写，序列化自动转 camelCase。

`params` 刻意不是自由 dict，也不是「每个数据源一个联合类型」：

- 自由 dict 在 OpenAPI 里只是一个 `object`，前端拿不到任何字段提示；
- 联合类型会让前端每加一个数据源就多一个分支。

所以把所有源用得到的筛选项平铺成一个模型，用 `description` 标明它只对哪个源生效。
不支持的字段被填了不是错误——`sources.py` 会把它当「这个条件对该源无意义」直接忽略，
并在响应里通过 `appliedParams` 回显真正生效的那几个，避免用户以为筛选生效了。
"""

from __future__ import annotations

from datetime import datetime

from pydantic import Field

from core.schema import CamelModel, UtcDateTime
from modules.export.enums import ExportSource


class ExportParams(CamelModel):
    status: str | None = Field(
        default=None, description="仅 tasks：按任务状态过滤（如 succeeded / failed）"
    )
    model_id: str | None = Field(default=None, description="仅 tasks：按模型目录 id 过滤")
    action: str | None = Field(
        default=None, description="仅 audit_events：按动作前缀过滤（如 task.）"
    )
    since: datetime | None = Field(default=None, description="起始时间（含），ISO 8601")
    until: datetime | None = Field(default=None, description="结束时间（不含），ISO 8601")


class CreateExportIn(CamelModel):
    data_source: ExportSource = Field(description="数据源，见 enums.ExportSource")
    params: ExportParams = Field(
        default_factory=ExportParams, description="筛选条件；不填表示该源的全量"
    )


class ExportOut(CamelModel):
    id: str
    data_source: str
    params: dict[str, object] = Field(default_factory=dict)
    status: str
    row_count: int = 0
    byte_size: int = 0
    error: str | None = None
    attempts: int = 0
    requested_by: str
    created_at: UtcDateTime | None = None
    started_at: UtcDateTime | None = None
    finished_at: UtcDateTime | None = None
    expires_at: UtcDateTime | None = None


class ExportDetailOut(ExportOut):
    """详情多一个 `downloadable`：让前端不必重复实现「能不能下」的判断。

    这条规则有两处（`succeeded` 且未被回收），前端各写一遍必然和回收任务
    对不上——用户会看到「按钮是亮的，点进去 410」。
    """

    downloadable: bool = False


class ExportSourceOut(CamelModel):
    """可导出的数据源。前端据此渲染下拉，不要硬编码数据源名单。

    这份列表来自运行时注册表（`core.ports.export_sources` + 内置源），
    新模块注册一个源之后这里自动多一项——前端不需要跟着发版。
    """

    name: str
    label: str
    columns: list[str] = Field(description="CSV 列，同时也是列顺序")
    permission: str | None = Field(
        default=None, description="导出该源额外需要的权限码；null 表示只需 export:read"
    )


__all__ = [
    "CreateExportIn",
    "ExportDetailOut",
    "ExportOut",
    "ExportParams",
    "ExportSourceOut",
]
