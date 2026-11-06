"""替换模板契约；已准备和已生成是不同状态。"""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator

from core.media import PackBinding, aligned_bindings, aligned_required_roles
from core.pack_roles import BASE_ROLE, PACK_ROLES, PACK_SCOPES
from core.schema import CamelModel, UtcDateTime

ReplacementKind = Literal["character", "product", "scene"]
ReviewReason = Literal["replacement", "preservation", "visual", "audio", "other"]


class ReplacementReviewIn(CamelModel):
    status: Literal["accepted", "rejected"]
    expected_version: int = Field(ge=0)
    reason_codes: list[ReviewReason] = Field(default_factory=list, max_length=5)
    comment: str = Field(default="", max_length=2000)

    @model_validator(mode="after")
    def validate_reasons(self) -> ReplacementReviewIn:
        if len(set(self.reason_codes)) != len(self.reason_codes):
            raise ValueError("评价原因不能重复")
        if self.status == "rejected" and not self.reason_codes:
            raise ValueError("不可用时请选择至少一个原因")
        if self.status == "accepted" and self.reason_codes:
            raise ValueError("可用评价不应包含不可用原因")
        return self


class ReplacementReviewEvent(CamelModel):
    status: str = "pending"
    version: int = 0
    reason_codes: list[ReviewReason] = Field(default_factory=list)
    comment: str = ""
    reviewed_by: str | None = None
    reviewed_at: UtcDateTime | None = None


class ReplacementReview(ReplacementReviewEvent):
    history: list[ReplacementReviewEvent] = Field(default_factory=list)


class ReplacementInputAsset(CamelModel):
    id: str
    name: str
    role: str
    type: str
    url: str


class ReplacementInputSummary(CamelModel):
    assets: list[ReplacementInputAsset] = Field(default_factory=list)
    cover_reference_id: str | None = None
    wardrobe_mode: str = "preserve"
    replacement_scope: str = ""
    notes: str = ""


class PrepareReplacement(CamelModel):
    request_id: UUID
    kind: ReplacementKind
    target: str = Field(default="", max_length=160)
    source_id: str
    reference_ids: list[str] = Field(default_factory=list, max_length=10)
    wardrobe_mode: Literal["preserve", "reference"] = "preserve"
    # 不再给默认值：范围必须来自素材包审核结果，默认值会悄悄承诺包外的位置。
    replacement_scope: str = Field(default="", max_length=1000)
    notes: str = Field(default="", max_length=2000)
    pack_id: str | None = None
    pack_version: int | None = Field(default=None, ge=1)
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
    display_title: str = Field(default="", description="统一展示名，列表/弹窗/任务中心共用")
    task_id: str | None = None
    estimated_credits: float | None = None
    credits: float | None = None
    progress: int = 0
    review_status: str = "pending"
    review: ReplacementReview | None = None
    input_summary: ReplacementInputSummary | None = None
    last_error: str | None = None
    output_url: str | None = None
    deleted_at: UtcDateTime | None = None
    billing_mode: Literal["free", "credits"] = Field(
        default="free", description="free=0 积分权益队列；credits=本次任务用积分进正常队列"
    )


class ExecuteReplacement(CamelModel):
    """提交或加速一条替换任务。

    `credits` 只作用于这条任务：提交时关掉账号的 0 积分权益标签，走正常积分队列，
    **不动账号级开关**，因此不会让同账号的其他人下一次提交莫名其妙开始花钱。
    不传表示沿用档案里当前的计费模式——「继续查询 / 重试原任务」走的就是这条路，
    它们不该顺带把一条已加速的任务改回免费队列。
    """

    billing_mode: Literal["free", "credits"] | None = None


class ReplacementPack(CamelModel):
    id: str
    kind: ReplacementKind
    name: str
    reference_ids: list[str]
    cover_reference_id: str | None = None
    wardrobe_mode: Literal["preserve", "reference"] = "preserve"
    features: str = ""
    instructions: str = ""
    version: int = 0
    available: bool | None = None
    cover_url: str | None = None
    preview_urls: list[str] = Field(default_factory=list)
    # 逐图用途与 reference_ids 同序；缺省时前端按基准用途展示
    bindings: list[PackBinding] = Field(default_factory=list)
    required_roles: list[str] = Field(default_factory=list)
    supported_scopes: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def align_pack_bindings(self) -> ReplacementPack:
        self.bindings = aligned_bindings(self.kind, self.reference_ids, self.bindings)
        self.required_roles = aligned_required_roles(self.kind, self.required_roles)
        return self


class PackDraftIn(CamelModel):
    request_id: UUID
    expected_revision: int = Field(default=0, ge=0)
    kind: ReplacementKind
    name: str = Field(default="", max_length=160)
    reference_ids: list[str] = Field(default_factory=list, max_length=10)
    cover_reference_id: str | None = None
    wardrobe_mode: Literal["preserve", "reference"] = "preserve"
    features: str = Field(default="", max_length=2000)
    instructions: str = Field(default="", max_length=2000)
    bindings: list[PackBinding] = Field(default_factory=list, max_length=10)
    required_roles: list[str] = Field(default_factory=list, max_length=8)
    supported_scopes: list[str] = Field(default_factory=list, max_length=8)


class PackAction(CamelModel):
    expected_revision: int = Field(ge=0)


class PackManaged(ReplacementPack):
    revision: int = 0
    status: Literal["draft", "published", "disabled"] = "draft"
    # 服务端算好的「还差什么」，草稿列表和发布按钮直接用它，避免前后端各写一套规则
    gaps: list[str] = Field(default_factory=list)


class PackManagedList(CamelModel):
    items: list[PackManaged]


class PackPublicList(CamelModel):
    items: list[ReplacementPack]
    total: int
    page: int
    page_size: int


class ReplacementCatalog(CamelModel):
    provider: str = "LibTV · Wan 3.0 · 720P"
    max_credits: int = 0
    kinds: list[str] = ["character", "product", "scene"]
    runs: list[ReplacementRunOut]
    packs: list[ReplacementPack] = []
    # 封装与替换共用的用途/范围词表，前端据此渲染选项，不再各自硬编码
    pack_roles: dict[str, dict[str, str]] = Field(default_factory=lambda: dict(PACK_ROLES))
    base_roles: dict[str, str] = Field(default_factory=lambda: dict(BASE_ROLE))
    pack_scopes: dict[str, str] = Field(default_factory=lambda: dict(PACK_SCOPES))
