"""视频模板出入参。

一条视频模板 = 固定样片 + 固定提示词 + 一个已发布替换素材包。
`mediaOrder` 决定接口里 mixedList 的顺序，也就决定 `{{Mixed N}}` 各指向谁：
第 1 位固定是原片，其后按素材包逐图**用途**（front / multiview / detail）
排列。生成时按用途从当前素材包取图，所以同一份提示词换一个产品包照样成立：
`{{Mixed 2}}` 永远是该包的正面主图，`{{Mixed 3}}` 永远是三视图。
素材包本身的图片顺序不重排，封装者只改提示词里的 token 编号。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from core.schema import CamelModel, UtcDateTime

ReplacementKindLiteral = Literal["character", "product", "scene"]


class VideoTemplateMedia(CamelModel):
    """mixedList 里的一项；下标 +1 就是 {{Mixed N}} 编号。"""

    token: str = Field(description="形如 {{Mixed 3}}，与所在下标严格对应")
    role: str = Field(default="", description="source_video / front / multiview / detail 等用途")
    type: str = Field(default="image", description="video / image / audio")
    asset_id: str | None = Field(default=None, description="素材库 asset id")
    name: str = Field(default="", description="素材显示名")
    file: str = Field(default="", description="本地文件相对路径（可选）")
    in_prompt: bool = Field(default=False, description="是否被提示词显式引用")


class VideoTemplateDraftIn(CamelModel):
    """封装一条新的视频模板。

    `mediaOrder` 决定 mixedList 顺序（即 `{{Mixed N}}` 指向谁）：显式给出时按它拼，
    必须是原片在第 1 位、其余图片与素材包「同一批图」（可换序，不能换图/少图）；
    留空则由服务端按 `packId` 的包内顺序自动生成。
    """

    name: str = Field(min_length=1, max_length=160, description="模板名称")
    template_key: str = Field(default="", max_length=80, description="稳定标识，便于后续复制与导入")
    description: str = Field(default="", max_length=2000, description="模板说明")
    prompt: str = Field(
        min_length=1, max_length=20000, description="固定提示词原文，含 {{Mixed N}} 占位符"
    )
    source_video_name: str = Field(default="", max_length=200, description="样片文件名")
    source_video_asset_id: str | None = Field(default=None, description="样片在素材库中的 asset id")
    source_video_path: str = Field(default="", max_length=512, description="样片本地路径（可选）")
    source_duration_sec: float = Field(
        default=0, ge=0, description="样片真实时长，用于提示与档位说明"
    )
    aspect_ratio: str = Field(default="9:16", max_length=16, description="输出画幅")
    pack_id: str | None = Field(default=None, description="关联的已发布替换素材包")
    default_kind: ReplacementKindLiteral = Field(
        default="product",
        description="选中这张原片卡片时，替换配置默认切到的替换类型",
    )
    media_order: list[VideoTemplateMedia] = Field(
        default_factory=list, description="留空则由素材包顺序生成"
    )
    generation: dict[str, Any] = Field(default_factory=dict, description="生成参数，覆盖默认值")
    rules: dict[str, Any] = Field(default_factory=dict, description="封装规则，覆盖默认值")
    cover_url: str = Field(default="", max_length=512, description="封面地址")
    pinned: bool = Field(default=False, description="是否设为固定蓝本（全库仅一个）")


class VideoTemplateOut(CamelModel):
    id: str
    template_key: str
    name: str
    description: str
    prompt: str
    prompt_sha256: str = Field(description="提示词 sha256，防止文字被悄悄改掉")
    source_video_name: str
    source_video_asset_id: str | None = None
    source_video_path: str
    source_duration_sec: float
    aspect_ratio: str
    pack_id: str | None = None
    pack_name: str = ""
    pack_version: int = 0
    default_kind: ReplacementKindLiteral = "product"
    media_order: list[VideoTemplateMedia] = Field(default_factory=list)
    generation: dict[str, Any] = Field(default_factory=dict)
    rules: dict[str, Any] = Field(default_factory=dict)
    cover_url: str = ""
    pinned: bool = False
    created_at: UtcDateTime
    updated_at: UtcDateTime


class VideoTemplateAttachSourceIn(CamelModel):
    """把样片登记后的 source 资产绑定到模板：绑定后它才是「全部模板」里可选的卡片。"""

    source_video_asset_id: str = Field(
        min_length=1, max_length=64, description="素材库里 category=source 的 asset id"
    )


class VideoTemplateListOut(CamelModel):
    items: list[VideoTemplateOut] = Field(default_factory=list)
    total: int = 0
    pinned_id: str | None = Field(default=None, description="当前固定蓝本的 id")
