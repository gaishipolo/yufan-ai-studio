"""任务 ORM 实体。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from core.library_text import parse_text_refs, text_ref_bindings
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
    # 文本素材冻结快照（JSON 文本），见 `core.library_text`。重试从本行重建
    # GenerationRecord，不落库就会在用户改稿后静默换一段提示词。
    text_json: Mapped[str] = mapped_column(Text, default="", nullable=False)

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

    @property
    def text_refs(self) -> list[dict[str, Any]]:
        """冻结的文本快照（含正文），只给提交期注入用。"""
        return parse_text_refs(self.text_json)

    @property
    def text_library_ids(self) -> list[str]:
        return [str(item.get("assetId") or "") for item in self.text_refs if item.get("assetId")]

    @property
    def text_bindings(self) -> list[dict[str, Any]]:
        """出参用的精简投影，不带正文。"""
        return text_ref_bindings(self.text_refs)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<VideoTask {self.id} {self.status} {self.title[:16]!r}>"
