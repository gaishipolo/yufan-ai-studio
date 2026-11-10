"""模型驱动模块出入参（snake_case 书写，序列化自动转 camelCase）。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import Field

from core.schema import CamelModel

__all__ = [
    "DriverListOut",
    "DriverOut",
    "ModelListOut",
    "ModelOut",
    "SubmitIn",
    "TaskOut",
]


class DriverOut(CamelModel):
    name: str
    label: str
    version: str
    path: str
    capabilities: list[str] = Field(default_factory=list)
    model_count: int = 0
    supports_poll: bool = False
    loaded: bool = True
    error: str = ""


class DriverListOut(CamelModel):
    items: list[DriverOut] = Field(default_factory=list)
    total: int = 0
    errors: list[str] = Field(default_factory=list)


class ModelOut(CamelModel):
    id: str
    label: str
    capability: str
    provider: str
    description: str = ""
    param_schema: dict[str, Any] = Field(default_factory=dict)


class ModelListOut(CamelModel):
    items: list[ModelOut] = Field(default_factory=list)
    total: int = 0


class SubmitIn(CamelModel):
    model: str
    prompt: str
    name: str = ""
    params: dict[str, Any] = Field(default_factory=dict)
    reference_urls: list[str] = Field(default_factory=list)
    source: str = ""


class TaskOut(CamelModel):
    id: str
    name: str = ""
    model: str
    capability: str
    status: str
    progress: int = 0
    output_url: str | None = None
    cover_url: str | None = None
    error: str | None = None
    external_id: str | None = None
    owner_id: str = ""
    source: str = ""
    submit_attempts: int = 0
    created_at: datetime | None = None
