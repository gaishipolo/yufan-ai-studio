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

## 为什么过滤 `=`、`+`、`@`、`-` 开头的单元格

CSV 公式注入：某条任务的标题写成 `=cmd|'/c calc'!A1`，管理员用 Excel 打开导出文件
就会触发。前缀一个单引号让 Excel 当文本，是 OWASP 的推荐做法。

`-` 同样是公式触发字符（`-2+3+cmd|' /C calc'!A0`），但它也是**负数**的写法。
早先刻意不处理 `-`（负数太多，误伤比收益大），代价是留了一条注入口子。
现在按内容区分：`-` 后面是**纯数字**的当数值原样输出（`-2`、`-1.5`、`- 3`），
其余（`-cmd|...`、`-2+3`）当公式加前缀 —— 注入堵住了，负数没被改成文本。
"""

from __future__ import annotations

import csv
import io
import re
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
from modules.export.errors import ExportTooLarge

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


#: 纯数字（可带小数与科学计数法）——用来区分「负数」与「以 `-` 开头的公式」
_NUMERIC_RE = re.compile(r"^(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?$")


def _cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, str):
        # CSV 公式注入：让 Excel 把这一格当文本，而不是执行它
        if value[:1] in ("=", "+", "@", "\t", "\r"):
            return "'" + value
        # `-` 也是公式触发字符（`-2+3+cmd|' /C calc'!A0`），但负数是合法业务值，
        # 一律加前缀会把 "-2" 变成文本 —— 那是数据损坏，不是安全修复。
        # 判据：`-` 后面（去掉空白后）是纯数字就当数值，否则当公式。
        if value[:1] == "-" and not _NUMERIC_RE.match(value[1:].lstrip()):
            return "'" + value
        return value
    return str(value)


class _Utf8Stream:
    """`csv.writer` 要的最小文本流：每次 write 立刻编码成字节写进二进制流。

    `csv.writer` 只要求对象有 `write(str) -> int`，不要求是真正的文件对象。
    这样峰值内存是「一行 str + 已写出的字节」，而不是「整份 str + 整份字节」。
    """

    __slots__ = ("_stream",)

    def __init__(self, stream: io.BytesIO) -> None:
        self._stream = stream

    def write(self, text: str) -> int:
        self._stream.write(text.encode("utf-8"))
        return len(text)


def render_csv(
    columns: Sequence[str],
    rows: Sequence[Sequence[Any]],
    *,
    max_bytes: int | None = None,
) -> bytes:
    """渲染成可直接下载的 CSV 字节（UTF-8 BOM + CRLF）。

    **直接写二进制流**：中间不再留「整份 str」这一份全量副本。旧写法
    （`io.StringIO` → 拼 BOM → `encode`）在构造过程中同时存在 str 缓冲、
    拼接后的 str、编码后的 bytes 三份副本；10 万行的中文 CSV 能到几十 MB，
    而 worker 是共享进程（见 `core/config.py` 里 `export_max_rows` 的注释），
    多出来的那一份全量副本就够把同进程里别的 job 一起拖下水。

    `max_bytes` 是**字节熔断**：超过就抛 `ExportTooLarge`。行数上限挡不住
    「行少但每行很长」，字节上限是最后一道闸。
    """
    buffer = io.BytesIO()
    # BOM 直接写进流里：拼在前面会多一份全量副本
    buffer.write(b"\xef\xbb\xbf")
    writer = csv.writer(_Utf8Stream(buffer), lineterminator="\r\n")
    writer.writerow(list(columns))
    for row in rows:
        writer.writerow([_cell(value) for value in row])
        if max_bytes is not None and buffer.tell() > max_bytes:
            raise ExportTooLarge
    return buffer.getvalue()


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
