"""审计的数据库写入端。

`core/audit.py` 只定义协议（core 不能 import db），这里注册默认实现：
一条记录 = 一张表里的一行，字段与 `db/audit.py` 一一对应。

**独立开会话**，不借用请求的 session：审计要在业务提交之后仍然落得下去
（业务回滚了，"他曾经尝试过"这件事更该留下）。
"""

from __future__ import annotations

import json

from core.audit import AuditEntry, set_audit_sink
from db.audit import AuditEvent
from db.session import get_session_factory


async def write(entry: AuditEntry) -> None:
    async with get_session_factory()() as session:
        session.add(
            AuditEvent(
                action=entry.action,
                actor_id=entry.actor_id,
                target_type=entry.target_type,
                target_id=entry.target_id,
                detail=json.dumps(entry.detail, ensure_ascii=False, default=str),
                created_at=entry.created_at,
            )
        )
        await session.commit()


set_audit_sink(write)
