"""任务 ORM 实体。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base, TimestampMixin, UUIDMixin
from modules.task.enums import AspectRatio, TaskStatus


class VideoTask(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "video_tasks"

    title: Mapped[str] = mapped_column(String(200), nullable=False)
    prompt: Mapped[str] = mapped_column(Text, nullable=False)

    status: Mapped[str] = mapped_column(
        String(20), default=TaskStatus.QUEUED.value, nullable=False, index=True
    )
    # 可读名（展示 / 搜索用），与 model_id 成对写入
    model: Mapped[str] = mapped_column(String(64), nullable=False)
    # 模型目录 id：与 `GET /models` 的 id 对齐，供聚合与供应商路由使用。
    # 历史数据可能为空，读出时由 ModelRouter 反查补齐。
    model_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    duration_sec: Mapped[int] = mapped_column(Integer, default=5, nullable=False)
    aspect_ratio: Mapped[str] = mapped_column(
        String(8), default=AspectRatio.PORTRAIT.value, nullable=False
    )
    # 生成风格预设与参考素材：契约里声明了就必须落库，否则客户端会「以为生效了」
    style_preset: Mapped[str | None] = mapped_column(String(64), nullable=True)
    reference_asset_ids: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)

    progress: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    cover_url: Mapped[str] = mapped_column(String(512), default="", nullable=False)

    author_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    author_name: Mapped[str] = mapped_column(String(64), default="", nullable=False)

    favorited: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    downloadable: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    error_message: Mapped[str | None] = mapped_column(String(512), nullable=True)
    # 供应商侧任务标识，用于轮询外部进度
    external_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    output_url: Mapped[str | None] = mapped_column(String(512), nullable=True)

    # 「已尝试提交」次数：>0 且没有 external_id 说明进程在「供应商已受理、尚未落库」
    # 的窗口里中断，重启时绝不能自动重提（可能已经计费）
    submit_attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        # 列表默认按「作者 + 创建时间倒序」翻页，走联合索引
        Index("ix_video_tasks_author_created", "author_id", "created_at"),
        Index("ix_video_tasks_status_created", "status", "created_at"),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<VideoTask {self.id} {self.status} {self.title[:16]!r}>"
