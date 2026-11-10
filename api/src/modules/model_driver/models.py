"""统一生成任务记录。

驱动模块自己持有一张表，下游模块调注入口时**不需要建表**：它们只拿
`task_id`，记录、恢复、审计都在这里。字段刻意对齐 `generation.spec.GenerationRecord`，
差异由 `store.py` 的吸收层吃掉。
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base, TimestampMixin, UUIDMixin
from generation.spec import STATUS_QUEUED

KIND_MODEL_DRIVER = "model_driver"
STATUS_CANCELLED = "cancelled"
TERMINAL_STATUSES = frozenset({"succeeded", "failed", STATUS_CANCELLED})


class DriverJob(Base, TimestampMixin, UUIDMixin):
    """一次统一生成请求。表名带模块前缀。"""

    __tablename__ = "driver_jobs"

    name: Mapped[str] = mapped_column(String(120), nullable=False)
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    model: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    capability: Mapped[str] = mapped_column(String(16), nullable=False)

    # JSON 文本：{"params": {...}, "referenceUrls": [...]}。
    # 用 Text 而不是 JSONB，是为了 sqlite / PG 同一套代码。
    params_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    source: Mapped[str] = mapped_column(String(64), default="", nullable=False, index=True)

    status: Mapped[str] = mapped_column(
        String(20), default=STATUS_QUEUED, nullable=False, index=True
    )
    progress: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    output_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    cover_url: Mapped[str] = mapped_column(String(512), default="", nullable=False)
    error_message: Mapped[str | None] = mapped_column(String(512), nullable=True)

    owner_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    external_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    submit_attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    submit_mode: Mapped[str | None] = mapped_column(String(8), nullable=True)

    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    @property
    def request_params(self) -> dict[str, Any]:
        return decode_request(self.params_json)


def decode_request(raw: str | None) -> dict[str, Any]:
    """读出 `{"params": ..., "referenceUrls": ...}`，脏数据按空处理。"""
    if not raw:
        return {"params": {}, "referenceUrls": []}
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return {"params": {}, "referenceUrls": []}
    if not isinstance(data, dict):
        return {"params": {}, "referenceUrls": []}
    params = data.get("params")
    refs = data.get("referenceUrls")
    return {
        "params": params if isinstance(params, dict) else {},
        "referenceUrls": [str(item) for item in refs] if isinstance(refs, list) else [],
    }


def encode_request(params: dict[str, Any], reference_urls: tuple[str, ...]) -> str:
    return json.dumps(
        {"params": params, "referenceUrls": list(reference_urls)},
        ensure_ascii=False,
        separators=(",", ":"),
    )
