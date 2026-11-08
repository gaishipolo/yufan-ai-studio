"""导出源：内置源 + 运行时注册表 + CSV 渲染。

## 两类数据源

1. **业务模块的表**（`video_tasks`、`image_jobs`…）：由拥有它的模块通过
   `core.ports.export_sources` 注册，本模块只按名字查，绝不 import 对方的 models。
2. **框架表**（`audit_events`）：表就在 `db/` 里，`modules → db` 是合法方向，
   所以直接在这里实现，不必绕道注入口。

## 为什么 CSV 要加 BOM

Excel 打开 UTF-8 CSV 时默认按系统编码（简体中文 Windows 上是 GBK）解释，
中文标题会变成乱码。加 `\ufeff` 是唯一不依赖用户设置的办法；代价是
`cat` 看第一列会多一个不可见字符，可接受。

## 为什么过滤 `=`、`+`、`@` 开头的单元格

CSV 公式注入：某条任务的标题写成 `=cmd|'/c calc'!A1`，管理员用 Excel 打开导出文件
就会触发。前缀一个单引号让 Excel 当文本，是 OWASP 的推荐做法。
刻意不处理 `-`：负数太多，误伤比收益大。
"""

from __future__ import annotations

import csv
import io
from collections.abc import Sequence
from datetime import date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.context import CurrentUser
from core.exceptions import BadRequest, Forbidden
from core.ports.export_sources import ExportSource, get_export_source
from db.audit import AuditEvent
from modules.export.enums import ExportSource as SourceName

#: 参数里允许出现的时间键：从 ExportParams 归一化后只会是 ISO 字符串
_TIME_KEYS = ("since", "until")


def normalize_params(raw: dict[str, Any]) -> dict[str, Any]:
    """把入参压成「可 JSON 存、可判等」的标量字典。

    两个用途：
    1. 落库到 `exports.params`（JSON 列不能存 datetime）；
    2. 判断「同参数是否已有进行中的导出」（409 去重），必须能稳定比较。
    """
    normalized: dict[str, Any] = {}
    for key, value in raw.items():
        if value is None or value == "":
            continue
        if isinstance(value, datetime | date):
            normalized[key] = value.isoformat()
        elif isinstance(value, bool | int | float | str):
            normalized[key] = value
        else:  # pragma: no cover - schema 已挡住，防御未来加的字段
            raise BadRequest(f"参数 {key} 的类型不受支持：{type(value).__name__}")
    return normalized


def _cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, str):
        # CSV 公式注入：让 Excel 把这一格当文本，而不是执行它
        return "'" + value if value[:1] in ("=", "+", "@", "\t", "\r") else value
    return str(value)


def render_csv(columns: Sequence[str], rows: Sequence[Sequence[Any]]) -> bytes:
    """渲染成可直接下载的 CSV 字节（UTF-8 BOM + CRLF）。"""
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow(list(columns))
    for row in rows:
        writer.writerow([_cell(value) for value in row])
    return ("\ufeff" + buffer.getvalue()).encode("utf-8")


class AuditEventExportSource:
    """操作留痕。含他人行为，所以额外要求 `export:read:audit`。

    这里不做数据范围过滤是刻意的：审计本来就是「谁都能被看见」的东西，
    真正的闸门是那个权限点——它只发给运维/审计角色。
    """

    name: str = SourceName.AUDIT_EVENTS.value
    label: str = "操作留痕"
    columns: tuple[str, ...] = (
        "created_at",
        "action",
        "actor_id",
        "target_type",
        "target_id",
        "detail",
    )
    permission: str | None = "export:read:audit"

    async def fetch(
        self,
        session: AsyncSession,
        *,
        actor_id: str,
        params: dict[str, Any],
        limit: int,
    ) -> list[Sequence[Any]]:
        stmt = select(AuditEvent)

        action = params.get("action")
        if isinstance(action, str) and action:
            # 前缀匹配：`task.` 可以一次导出「任务相关的所有动作」
            stmt = stmt.where(AuditEvent.action.startswith(action))

        since = _as_datetime(params.get("since"))
        if since is not None:
            stmt = stmt.where(AuditEvent.created_at >= since)

        until = _as_datetime(params.get("until"))
        if until is not None:
            stmt = stmt.where(AuditEvent.created_at < until)

        stmt = stmt.order_by(AuditEvent.created_at.desc(), AuditEvent.id.desc())
        stmt = stmt.limit(limit)

        rows = (await session.execute(stmt)).scalars().all()
        return [
            [
                r.created_at,
                r.action,
                r.actor_id,
                r.target_type,
                r.target_id,
                r.detail,
            ]
            for r in rows
        ]


AUDIT_EXPORT_SOURCE = AuditEventExportSource()


def _as_datetime(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
    return None


def available_sources(actor: CurrentUser) -> list[dict[str, Any]]:
    """当前身份可用的数据源（`GET /exports/sources` 的载荷）。

    授权在这里判一次，创建接口还会再判一次——前置过滤只是让前端不显示
    点进去必然 403 的按钮，安全不能靠它。
    """
    from core.ports.export_sources import export_sources

    found: dict[str, ExportSource] = dict(export_sources())
    found[AUDIT_EXPORT_SOURCE.name] = AUDIT_EXPORT_SOURCE

    items: list[dict[str, Any]] = []
    for source in found.values():
        allowed = source.permission is None or actor.has(source.permission)
        if not allowed:
            continue
        items.append(
            {
                "name": source.name,
                "label": source.label,
                "columns": list(source.columns),
                "permission": source.permission,
            }
        )
    return sorted(items, key=lambda item: str(item["name"]))


def lookup_source(name: str) -> ExportSource:
    """按名字取源，**不判权限**（worker 用：权限在受理时已经判过）。

    数据源不存在是客户端传错（400），不是 404。
    """
    source = get_export_source(name)
    if source is None and name == AUDIT_EXPORT_SOURCE.name:
        source = AUDIT_EXPORT_SOURCE
    if source is None:
        raise BadRequest(f"不支持的数据源：{name}")
    return source


def resolve_source(name: str, actor: CurrentUser) -> ExportSource:
    """按名字取源并校验权限。"""
    source = lookup_source(name)
    if source.permission is not None and not actor.has(source.permission):
        raise Forbidden(f"缺少权限: {source.permission}")
    return source
