"""导出 ORM 实体。

表名必须加前缀或复数，避免与其他模块撞名。
新增字段后执行：alembic revision --autogenerate -m "export init"

设计要点：

- `params` 存**受理时定下来的查询条件快照**。用户改了自己的筛选条件不影响
  已经排队的那一份，否则「我 9 点导的和 10 点导的为什么不一样」无法解释。
- `object_key` 是存储键，不是下载 URL。URL 由 `/download` 现签，
  直接存 URL 会在签名过期后变成「记录成功、点开 403」。
- `expires_at` 与状态分开：产物被回收后行还在（`expired`），
  这样「上周导过什么」查得到，只是文件没了。
- 没有 `title`：一份导出由「数据源 + 条件 + 时间」唯一描述，
  让用户取名只会得到一堆「导出」「导出(1)」。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, DateTime, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base, TimestampMixin, UUIDMixin
from modules.export.enums import ExportStatus


class Export(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "exports"

    # 数据源白名单见 enums.ExportSource；存字符串而不是 int，
    # 便于直接在 `app db` 里看懂，也方便以后加源不用改列类型。
    data_source: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    # 查询条件快照（JSON）。字段含义按数据源不同，由 parameters.py 校验
    params: Mapped[dict[str, object]] = mapped_column(
        JSON, default=dict, nullable=False
    )

    status: Mapped[str] = mapped_column(
        String(20), default=ExportStatus.QUEUED.value, nullable=False, index=True
    )
    # 产物信息：row_count 在写完 CSV 后才知道，用于前端展示「共 N 行」
    row_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    object_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    byte_size: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # 失败原因（截断到 512）：这是要给用户看的，不是给开发看堆栈的
    error: Mapped[str | None] = mapped_column(String(512), nullable=True)
    # 「已失败次数」。与 queued_jobs.attempts 语义一致，不要另造一套
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # 数据范围判定的唯一依据：列表只返回自己的导出（见 service.list_items）
    requested_by: Mapped[str] = mapped_column(String(32), nullable=False, index=True)

    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )

    __table_args__ = (
        # 列表固定按「我的 + 创建时间倒序」翻页
        Index("ix_exports_owner_created", "requested_by", "created_at"),
        # 回收任务扫「已成功的、且到期时间早于现在」
        Index("ix_exports_status_expires", "status", "expires_at"),
    )
