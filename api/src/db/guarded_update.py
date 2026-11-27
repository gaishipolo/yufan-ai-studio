"""带终态谓词的 UPDATE。

终态保护不能写成「读出来 → 在 Python 里判 → 再写回去」：判与写之间隔着
`await`，另一个会话在这段空隙里提交终态，后写者就会把终态覆盖掉。正确形状
是把谓词放进同一条 SQL，由数据库保证「读到终态就不再写」。

本模块只放这一条原语，三个生成存储与导出巡检共用同一份实现，避免形状漂移。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any, cast

from sqlalchemy import update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession


async def update_unless_terminal(
    session: AsyncSession,
    model: type[Any],
    record_id: str,
    terminal_statuses: Iterable[str],
    values: Mapping[str, Any],
) -> bool:
    """`UPDATE ... WHERE id = ? AND status NOT IN (终态)`，返回是否真的写到。

    `values` 里可以放 SQL 表达式（如 `col + 1`、`case(...)`），让自增/取最大值
    也在同一条 SQL 里完成，不回到 Python 读-改-写。
    """
    result = await session.execute(
        update(model)
        .where(model.id == record_id, model.status.not_in(tuple(terminal_statuses)))
        .values(**dict(values))
    )
    return (cast(CursorResult[Any], result).rowcount or 0) > 0