"""模板库契约：封装（参考素材包）与替换运行。

**字段形状刻意与旧版一致**，包括 camelCase 别名、默认值和校验时机。旧实现把
同样的结构定义在 `replacement_schema.py` 里，本轮删掉重建时保留了它的外形：
前端 41 个文件按这些形状对接，改形状等于把「换存储」变成「换产品」。
真正变的是数据来源——从 JSON 目录换成六张表，而这件事对调用方不可见。

`PackBinding` / `aligned_bindings` / `aligned_required_roles` 原先在
`core/media.py`，随封装一起收进模块（理由见 `pack_roles.py` 的 docstring）。
"""

from __future__ import annotations

from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import Field, model_validator

from core.schema import CamelModel, UtcDateTime
from modules.template.channels import ReplacementChannel
from modules.template.pack_roles import BASE_ROLE, PACK_ROLES, PACK_SCOPES

ReplacementKind = Literal["character", "product", "scene"]
ReviewReason = Literal["replacement", "preservation", "visual", "audio", "other"]


# --------------------------------------------------------------------------- #
# 封装：逐图用途
# --------------------------------------------------------------------------- #
class PackBinding(CamelModel):
    """一张参考图在封装里的用途；description 只补充本次任务特有的事实。"""

    role: str = ""
    description: str = ""


def aligned_bindings(kind: str, reference_ids: list[str], bindings: list) -> list[PackBinding]:
    """把用途对齐到参考图列表：非法或缺失的用途落到基准用途，多余的截掉。

    旧包只有 reference_ids、没有用途，读出来必须是「每张都能用」的状态；
    前端提交的 bindings 长度不一致时也走这里兜底，不让脏数据进存储。
    """
    allowed = PACK_ROLES.get(kind, {})
    base = BASE_ROLE.get(kind, "front")
    rows = [b if isinstance(b, PackBinding) else PackBinding.model_validate(b) for b in bindings]
    roles = [row.role if row.role in allowed else base for row in rows]
    roles = (roles + [base] * len(reference_ids))[: len(reference_ids)]
    return [
        PackBinding(
            role=role,
            description=rows[index].description if index < len(rows) else "",
        )
        for index, role in enumerate(roles)
    ]


def aligned_required_roles(kind: str, required: list[str]) -> list[str]:
    """必填用途永远包含基准用途，顺序按用途表固定。"""
    allowed = PACK_ROLES.get(kind, {})
    base = BASE_ROLE.get(kind, "front")
    picked = [role for role in required if role in allowed and role != base]
    return [base, *picked]


# --------------------------------------------------------------------------- #
# 评价
# --------------------------------------------------------------------------- #
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


# --------------------------------------------------------------------------- #
# 替换运行
# --------------------------------------------------------------------------- #
class ReplacementInputAsset(CamelModel):
    id: str
    name: str
    role: str
    type: str
    url: str
    token: str = Field(default="", description="当次输入的 Mixed 编号，无法从历史快照确定时留空")
    usage: str = Field(default="", description="当次快照中记录的媒体用途说明")
    preview_url: str | None = Field(default=None, description="授权读取当次输入快照的缩略图")


class ReplacementInputSummary(CamelModel):
    assets: list[ReplacementInputAsset] = Field(default_factory=list)
    cover_reference_id: str | None = None
    wardrobe_mode: str = "preserve"
    replacement_scope: str = ""
    notes: str = ""
    scene_prompt: str | None = Field(default=None, description="本次实际使用的可编辑场景描述")


class PrepareReplacement(CamelModel):
    request_id: UUID
    channel: ReplacementChannel = "libtv"
    kind: ReplacementKind
    target: str = Field(default="", max_length=160)
    source_id: str
    video_template_id: str | None = Field(default=None, max_length=64)
    scene_prompt: str | None = Field(default=None, max_length=4000,
                                    description="文字换场景的描述；省略使用模板默认值，空白拒绝")
    reference_ids: list[str] = Field(default_factory=list, max_length=10)
    # 与生图 / 视频同一套 `{{Text N}}`：在 scenePrompt / notes / replacementScope
    # 里写占位符，受理期按当时版本冻结正文，编译后再注入（编译器不收花括号）。
    text_library_ids: list[str] = Field(
        default_factory=list,
        max_length=8,
        description="绑定的文本资产 id，按顺序对应 `{{Text N}}`；最多 8 份",
    )
    # 多人物替换：模板声明了几个「正面身份图」槽位，工作台就要求选几个人物卡。
    # 第 1 位仍走 `packId` + `packVersion`（版本要锁），这里只放第 2 位起的包，
    # 顺序即提示词槽位顺序。空列表就是原来的单人/产品替换，行为不变。
    extra_pack_ids: list[str] = Field(
        default_factory=list,
        max_length=5,
        description="第 2 位及以后的素材包 id，按提示词槽位顺序；第一包仍用 packId",
    )
    extra_pack_versions: list[Annotated[int, Field(ge=1)]] = Field(
        default_factory=list,
        max_length=5,
        description="与 extraPackIds 顺序对应的发布版本；旧调用省略时由服务端解析并冻结",
    )
    wardrobe_mode: Literal["preserve", "reference"] = "preserve"
    # 固定视频模板按提示词声明参数；通用产品替换仍校验素材包范围。
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
    channel: ReplacementChannel = "libtv"
    channel_label: str = "LibTV · Wan 3.0 · 720P"
    kind: ReplacementKind
    target: str
    status: str
    archive_path: str | None = Field(
        default=None,
        description="工作区绝对路径（排查用）；目录已被清理或换机器后为 null",
    )
    created_at: UtcDateTime
    started_at: UtcDateTime | None = Field(
        default=None, description="确认提交（进入排队）的时间，未开始为 null"
    )
    finished_at: UtcDateTime | None = Field(
        default=None, description="这一轮结束的时间；未结束为 null"
    )
    elapsed_sec: int | None = Field(
        default=None,
        description="确认提交到结束的耗时（秒）；未完成时按到此刻计，未开始为 null",
    )
    prompt: str
    text_library_ids: list[str] = Field(
        default_factory=list, description="本次绑定的文本资产 id（顺序即 `{{Text N}}` 编号）"
    )
    text_bindings: list[dict[str, Any]] = Field(
        default_factory=list, description="绑定的文案与版本快照（不含正文）"
    )
    prompt_mode: Literal["fixed", "compiled"] = Field(
        default="compiled",
        description=(
            "fixed=封装视频模板时写死的原文，逐字提交；compiled=按素材与替换范围现编。"
            "展示层据此告诉用户「跑的就是你封装时那份提示词」。"
        ),
    )
    video_template_name: str | None = Field(
        default=None, description="fixed 时出自哪条视频模板；compiled 为 null"
    )
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


# --------------------------------------------------------------------------- #
# 封装：对外形状
# --------------------------------------------------------------------------- #
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


class ReplacementChannelOut(CamelModel):
    id: ReplacementChannel
    label: str
    model: str
    enabled: bool
    billing: Literal["libtv", "aliyun"]
    unavailable_reason: str = ""


class ReplacementCatalog(CamelModel):
    channels: list[ReplacementChannelOut] = Field(default_factory=list)
    provider: str = "LibTV · Wan 3.0 · 720P"
    max_credits: int = 0
    kinds: list[str] = ["character", "product", "scene"]
    runs: list[ReplacementRunOut]
    packs: list[ReplacementPack] = []
    # 封装与替换共用的用途/范围词表，前端据此渲染选项，不再各自硬编码
    pack_roles: dict[str, dict[str, str]] = Field(default_factory=lambda: dict(PACK_ROLES))
    base_roles: dict[str, str] = Field(default_factory=lambda: dict(BASE_ROLE))
    pack_scopes: dict[str, str] = Field(default_factory=lambda: dict(PACK_SCOPES))
