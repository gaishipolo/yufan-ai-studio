"""封装模板出入参。

一条封装模板 = 参考视频 + 固定提示词 + 一组**槽位**。
槽位分两种形态，二选一：

- `fixed`：钉死一条素材（发布时定好，运行时不可换）。
- `filtered`：声明一条准入规则（发布时定好规则，运行时用户在规则内选）。

`{{Mixed N}}` 的编号由 `position` 决定：position=1 固定是参考视频，
其余按顺序对应 `{{Mixed 2}}`、`{{Mixed 3}}`……
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from core.schema import CamelModel, UtcDateTime, VerbatimText

__all__ = [
    "MAX_REFERENCE_IMAGES",
    "SlotKind",
    "TemplatePackCandidateItem",
    "TemplatePackCandidateListOut",
    "TemplatePackDraftIn",
    "TemplatePackListOut",
    "TemplatePackOut",
    "TemplatePackPromptIn",
    "TemplatePackSlotIn",
    "TemplatePackSlotOut",
    "TemplatePackStatus",
    "TemplatePackVerifyIn",
    "TemplatePackVerifyOut",
]

#: 参考图数量上限（原片不计入）。
#:
#: **刻意不 import `modules/template` 的那份同名常量** —— 模块之间不许互相
#: import（`tests/test_modules_do_not_import_each_other` 用 AST 静态拦），
#: 而把存量常量上移到 `core/` 属于跨模块重构，不该混在这个新功能里。
#: 两份值必须一致（都是 10），否则会出现「模板保存成功、准备时被拒」。
MAX_REFERENCE_IMAGES = 10

#: 槽位形态。`fixed` 钉死素材，`filtered` 声明准入规则。
SlotKind = Literal["fixed", "filtered"]

#: 模板状态。
TemplatePackStatus = Literal["draft", "published", "disabled"]

#: 素材类别白名单。
#:
#: **不含 `text`**：文本资产是「文件型资产」，不是参考图。把它塞进
#: `referenceLibraryIds` 会让供应商参数校验失败。资产库选择器同样默认排除
#: 文本（见 `HANDOFF.md` §5 的选择器不变量）。
MediaKind = Literal["video", "image", "audio"]


class TemplatePackSlotIn(CamelModel):
    """一个槽位的入参。两种形态的字段互斥，service 会校验。"""

    token: str = Field(
        pattern=r"^\{\{Mixed [1-9]\d*\}\}$",
        description="占位符原文，如 {{Mixed 2}}；必须与 position 对应",
    )
    slot_kind: SlotKind = Field(description="fixed=钉死素材，filtered=声明准入规则")
    label: str = Field(default="", max_length=64, description="槽位显示名")

    # ---- fixed 形态 ----
    asset_id: str | None = Field(
        default=None, max_length=64, description="fixed：钉死的素材 id（资产库）"
    )
    media_type: MediaKind = Field(
        default="image", description="fixed：素材类别"
    )

    # ---- filtered 形态 ----
    filter_kind: MediaKind | None = Field(
        default=None, description="filtered：素材类别（不含 text）"
    )
    filter_tag_ids: list[str] = Field(
        default_factory=list,
        max_length=20,
        description="filtered：一级标签 id 列表；选中一级会展开成「自身+直属二级」",
    )
    filter_tag_match: Literal["any"] = Field(
        default="any",
        description="filtered：标签组合方式。只支持 any（任一命中即可）——"
        "资产库的筛选口径唯一（标签 OR 展开到二级），提供 all 会承诺一个"
        "兑现不了的更严筛选",
    )
    required: bool = Field(default=True, description="该槽位是否必填")


class TemplatePackDraftIn(CamelModel):
    """保存一条封装模板（草稿）。幂等：同 key 同配置返回原行。"""

    name: str = Field(min_length=1, max_length=160, description="模板名称")
    template_key: str = Field(
        default="",
        max_length=80,
        description="稳定标识：同键同配置返回原模板，同键异配置 409；留空自动生成",
    )
    description: str = Field(default="", max_length=2000, description="模板说明")
    prompt: VerbatimText = Field(
        min_length=1, max_length=20000, description="固定提示词原文，含 {{Mixed N}} 占位符"
    )
    source_video_asset_id: str = Field(
        min_length=1, max_length=64, description="参考视频的资产库 id"
    )
    source_duration_sec: float = Field(
        default=0, ge=0, description="参考视频时长（秒）"
    )
    aspect_ratio: str = Field(default="9:16", max_length=16, description="输出画幅")
    slots: list[TemplatePackSlotIn] = Field(
        min_length=1,
        max_length=MAX_REFERENCE_IMAGES + 1,
        description="槽位清单；第 1 项固定是参考视频",
    )
    cover_url: str = Field(default="", max_length=512, description="封面地址")


class TemplatePackSlotOut(CamelModel):
    """一个槽位的出参。两种形态的字段都带出来，由 `slotKind` 区分。"""

    position: int
    token: str
    slot_kind: SlotKind
    label: str = ""
    asset_id: str | None = None
    media_type: str = "image"
    asset_name: str = ""
    filter_kind: str = ""
    filter_tag_ids: list[str] = Field(default_factory=list)
    filter_tag_names: list[str] = Field(default_factory=list)
    filter_tag_match: str = "any"
    required: bool = True


class TemplatePackOut(CamelModel):
    """一条封装模板的完整出参。"""

    id: str
    template_key: str
    name: str
    description: str
    prompt: VerbatimText
    prompt_sha256: str = Field(description="提示词 sha256，防止文字被悄悄改掉")
    source_video_asset_id: str
    source_duration_sec: float = 0.0
    aspect_ratio: str = "9:16"
    cover_url: str = ""
    status: TemplatePackStatus = "draft"
    owner_id: str
    slots: list[TemplatePackSlotOut] = Field(default_factory=list)
    published_at: UtcDateTime | None = None
    created_at: UtcDateTime | None = None
    updated_at: UtcDateTime | None = None


class TemplatePackListOut(CamelModel):
    items: list[TemplatePackOut] = Field(default_factory=list)
    total: int = 0


class TemplatePackPromptIn(CamelModel):
    """改固定提示词原文。槽位引用按新原文重算。"""

    prompt: VerbatimText = Field(
        min_length=1, max_length=20000, description="新的固定提示词原文"
    )


class TemplatePackVerifyIn(CamelModel):
    """运行时提交的用户选材。

    `picks` 是 `{槽位标识: 素材 id}`，键用提示词里出现的原文
    （如 `{{Mixed 2}}`），值是资产库 id。
    """

    picks: dict[str, str] = Field(
        default_factory=dict,
        description="槽位选材：{token: assetId}。只填需要用户选择的槽位",
    )


class TemplatePackVerifyOut(CamelModel):
    """复核结果。通过复核的槽位与素材 id。"""

    picks: dict[str, str] = Field(
        default_factory=dict, description="通过复核的 {token: assetId}"
    )


class TemplatePackCandidateItem(CamelModel):
    """候选素材池里的一项。"""

    asset_id: str
    name: str
    kind: str
    cover_url: str = ""
    size_bytes: int = 0
    width: int = 0
    height: int = 0


class TemplatePackCandidateListOut(CamelModel):
    """某个筛选槽位的合规素材池。"""

    token: str
    label: str = ""
    filter_kind: str = ""
    filter_tag_ids: list[str] = Field(default_factory=list)
    filter_tag_names: list[str] = Field(default_factory=list)
    items: list[TemplatePackCandidateItem] = Field(default_factory=list)
    total: int = 0
