"""LibTV 适配器的持久化执行状态，不包含模板业务。"""

from __future__ import annotations

from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator


class LibTVState(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: str
    status: str = "prepared"
    task_id: str | None = None
    submit_attempts: int = 0
    framework_status: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    progress_percent: int = 0
    estimated_credits: float | None = None
    credits: float | None = None
    last_error: str | None = None
    error_code: str | None = None
    preparation_ready: bool = False

    @field_validator("progress_percent", mode="before")
    @classmethod
    def normalize_progress(cls, value: object) -> object:
        return 0 if value is None else value


class ExecutorResult(BaseModel):
    code: str = "OK"
    stage: str = ""
    message: str = ""
    lease_version: int = 2
    node_pid: int | None = None
    phase: str = "finished"
