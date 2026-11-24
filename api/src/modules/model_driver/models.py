"""统一生成任务记录。

驱动模块自己持有一张表，下游模块调注入口时**不需要建表**：它们只拿
`task_id`，记录、恢复、审计都在这里。字段刻意对齐 `generation.spec.GenerationRecord`，
差异由 `store.py` 的吸收层吃掉。
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Integer, String, Text, UniqueConstraint
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
    # 重试会清掉 `external_id` 才能重新提交，而那个号是「上一次可能已扣费」的
    # 唯一凭证 —— 清之前先搬到这里（与 image / task 同一口径）。
    previous_external_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
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


class SubmissionReceipt(Base, UUIDMixin, TimestampMixin):
    """一次提交的唯一回执（2026-11-23 落库，替代工作区文件）。

    `(provider, run_id)` 唯一约束替代原来的 `O_EXCL` 文件抢占：有事务、有备份、
    多实例一致。工作区误删后恢复仍能读到回执 —— 不会把同一个 request_id
    重新提交给上游（二次计费）。

    `channel_used` 记录当时走的素材通道（Base64 / OSS / 公网 URL），
    重试必须复用原通道，否则同一素材两次重试走不同通道，行为不可预期。

    状态沿用 bailian 既有语义：
    - `submitting`          抢占成功、尚未拿到上游 task_id
    - `submitted`           拿到 task_id，恢复时直接复用 external_id
    - `rejected`            上游明确拒绝（4xx），允许人工重提
    - `submission_unknown`  超时 / 5xx / 响应畸形，**禁止自动重提**（可能已受理）

    `run_id` 的两种语义（2026-11-24，D5.2）—— 台账推广到所有驱动后，一个键
    装不下两种东西，所以拆成「键 + 反查列」：

    - `bailian` 的 `run_id` 是**归档 run id** = **幂等键**：一个逻辑提交一个 run，
      重放时必须复用既有 external_id，绝不能再发一次付费 POST。
    - 其余驱动没有归档 run，只能用业务记录 id，而**同一份记录会被提交多次**
      （4xx 后框架自动重试 3 次；人工重提 `image/service.py::retry` 复用同一
      `job_id`）→ 必须**按轮次分行**，轮次键的形状是 `{record_id}#{轮次}`。

    `record_id` 就是那条业务记录 id（`ImageJob.id` / `VideoTask.id` 等）：让
    「按业务记录反查台账」成为可能 —— 恢复器只认 `record.external_id`，它得能
    反查回执才知道这条记录提交过什么、走的哪条通道。
    """

    __tablename__ = "submission_receipts"
    __table_args__ = (
        UniqueConstraint("provider", "run_id", name="uq_submission_receipts_provider_run"),
    )

    provider: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    run_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    record_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)

    task_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    external_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    channel_used: Mapped[str | None] = mapped_column(String(32), nullable=True)
    error: Mapped[str | None] = mapped_column(String(512), nullable=True)

    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
