"""替换模板契约；已准备和已生成是不同状态。"""

from typing import Literal
from uuid import UUID

from pydantic import Field

from core.schema import CamelModel, UtcDateTime

ReplacementKind = Literal["character", "product", "scene"]


class PrepareReplacement(CamelModel):
    request_id: UUID
    kind: ReplacementKind
    target: str = Field(min_length=1, max_length=160)
    source_id: str
    reference_ids: list[str] = Field(min_length=1, max_length=10)
    wardrobe_mode: Literal["preserve", "reference"] = "preserve"
    replacement_scope: str = Field(
        default="人物上身穿着、手持展示产品、盒内可见产品、屏幕商品", max_length=1000
    )
    notes: str = Field(default="", max_length=2000)
    pack_id: str | None = None
    aspect_ratio: Literal["9:16", "16:9", "1:1", "adaptive"] = "adaptive"


class ReconfirmReplacement(CamelModel):
    """确认「上一次提交可能已经花了钱」后才允许重提。

    默认 False：一次误点就变成第二次计费，是这套流程里最贵的一个操作。
    """

    confirm_charged: bool = Field(
        default=False,
        description="确认上一次提交可能已产生费用，仍要重新提交（默认拒绝）",
    )


class ReplacementRunOut(CamelModel):
    id: str
    kind: ReplacementKind
    target: str
    status: str
    archive_path: str
    created_at: UtcDateTime
    prompt: str
    task_id: str | None = None
    estimated_credits: float | None = None
    credits: float | None = None
    progress: int = 0
    review_status: str = "pending"
    last_error: str | None = None
    output_url: str | None = None


class ReplacementPack(CamelModel):
    id: str
    kind: ReplacementKind
    name: str
    reference_ids: list[str]
    wardrobe_mode: Literal["preserve", "reference"] = "preserve"
    features: str = ""
    instructions: str = ""


class ReplacementCatalog(CamelModel):
    provider: str = "LibTV · Wan 3.0 · 720P"
    max_credits: int = 0
    kinds: list[str] = ["character", "product", "scene"]
    runs: list[ReplacementRunOut]
    packs: list[ReplacementPack] = []
