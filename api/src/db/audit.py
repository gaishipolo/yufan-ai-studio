"""审计事件表（跨模块留痕）。

## 为什么放在 `db/` 而不是某个模块里

「谁在什么时候对什么做了什么」是所有模块共有的需求。放在 identity 里，
生图、视频、替换模板就得各自再写一遍；放在某个业务模块里，别的模块还得 import 它
（架构上不允许）。`db/` 是共享数据层，不认识任何业务语义，正好放这张通用表。

## 与 identity 的登录审计什么关系

`login_events` 是**安全审计**：登录失败、限流、会话轮换，字段与语义都为鉴权服务。
`audit_events` 是**操作留痕**：谁提交了什么、参数是什么。两者保留期与查询方式都不同，
不合并；将来要统一检索，在查询层做视图，别把两张表塞成一张。

## 为什么 detail 存 JSON 文本而不是 JSON 列

sqlite 的 JSON 支持要开扩展、postgres 是 jsonb，两者 DDL 不同。这里只需要
「当时提交了什么参数」这种快照，不参与索引与聚合，存文本最省事。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base, UUIDMixin, utcnow


class AuditEvent(Base, UUIDMixin):
    __tablename__ = "audit_events"

    action: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    # 系统动作（定时任务、恢复流程）没有发起人，允许为空
    actor_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    target_type: Mapped[str] = mapped_column(String(32), default="", nullable=False)
    target_id: Mapped[str] = mapped_column(String(64), default="", nullable=False, index=True)
    detail: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False, index=True
    )
