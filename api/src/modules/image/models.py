"""生图任务记录 ORM 实体。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base, TimestampMixin, UUIDMixin
from generation.spec import STATUS_QUEUED


class ImageJob(Base, TimestampMixin, UUIDMixin):
    """与 `video_tasks` 保持同一套状态字段，好在 `generation` 框架里互换。

    字段命名刻意对齐 `modules/task/models.py`：框架只认 `GenerationRecord`，
    两张表的差异全部由各自的 Store 吸收。
    """

    __tablename__ = "image_jobs"

    name: Mapped[str] = mapped_column(String(120), nullable=False)
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    size: Mapped[str] = mapped_column(String(32), default="1024x1024", nullable=False)
    count: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    status: Mapped[str] = mapped_column(
        String(20), default=STATUS_QUEUED, nullable=False, index=True
    )
    # 可读名（展示用）；model_id 是与模型目录对齐的 id，供路由与聚合使用
    model: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    model_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)

    progress: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    cover_url: Mapped[str] = mapped_column(String(512), default="", nullable=False)
    output_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(512), nullable=True)

    owner_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)

    # 供应商侧任务标识；同步供应商（Agnes）也会写，便于对账
    external_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    # 上一次提交的供应商任务号。重试必须清 external_id 才能重投，清掉之后
    # 对账凭证就没了；"可能已计费"的那次提交要留在这里（R12-01）。
    previous_external_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # 提交时供应商形态快照 "sync" / "async"：启动恢复按记录自身分类，
    # 不按当前模型目录重新解析（R12-05）。迁移前的老记录为 NULL。
    submit_mode: Mapped[str | None] = mapped_column(String(8), nullable=True)
    # >0 且无 external_id = 提交结果未知，重启不得自动重提（可能已计费）
    submit_attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
