"""视频模板出入参。

一条视频模板 = 固定样片 + 固定提示词 + 一个已发布替换素材包。
`mediaOrder` 决定接口里 mixedList 的顺序，也就决定 `{{Mixed N}}` 各指向谁：
用途绑定模板按 rules.referenceBindings 保留原有 Mixed 编号，视频可以在任意声明位置；
图片从所选素材包按用途解析，未引用图片完整附带。多人换脸每个人物独立选包，
各自的 front 填各自槽位，不新建多人合并包。旧 mediaOrder 模式仍要求原片第1位。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from core.schema import CamelModel, UtcDateTime, VerbatimText

ReplacementKindLiteral = Literal["character", "product", "scene"]

# 通用编译器对 mixedList 里「图片」的硬上限（原片不计入）。保存端按封装时的
# 基础媒体算、准备端按本次所选素材包算，两侧必须共用这个值，否则会出现
# 「模板保存成功、换个图片更多的包准备时被拒」。
MAX_REFERENCE_IMAGES = 10


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
    旧模式必须原片第1位、其余图片与素材包同一批；用途绑定模式不传 mediaOrder，
    服务端按 referenceBindings 生成，保留视频所在编号并完整附带辅助参考。
    """

    name: str = Field(min_length=1, max_length=160, description="模板名称")
    template_key: str = Field(
        default="",
        max_length=80,
        description="稳定标识：同键同配置返回原模板，同键异配置409；留空自动生成。固定蓝本状态独立管理",
    )
    description: str = Field(default="", max_length=2000, description="模板说明")
    prompt: VerbatimText = Field(
        min_length=1, max_length=20000, description="固定提示词原文，含 {{Mixed N}} 占位符"
    )
    source_video_name: str = Field(default="", max_length=200, description="样片文件名")
    source_video_asset_id: str | None = Field(default=None, description="样片在素材库中的 asset id")
    source_video_id_space: Literal["v1", "v2"] = Field(
        default="v1",
        description="样片 id 属于哪套素材库：v1 是旧素材表，v2 是资产库。"
        "两套 id 都是 32 位 hex，**不能靠形状推断**，所以必须显式声明；"
        "缺省 v1 让旧调用一个字都不用改",
    )
    source_video_path: str = Field(default="", max_length=512, description="样片本地路径（可选）")
    source_duration_sec: float = Field(
        default=0, ge=0, description="样片真实时长，用于提示与档位说明"
    )
    aspect_ratio: str = Field(default="9:16", max_length=16, description="输出画幅")
    pack_id: str | None = Field(
        default=None,
        description="关联的已发布替换素材包；**可省略**——用途绑定模式下省略即"
        "「只声明槽位」，调用时按用途从用户自己选的包里取图",
    )
    default_kind: ReplacementKindLiteral = Field(
        default="product",
        description="选中这张原片卡片时，替换配置默认切到的替换类型",
    )
    media_order: list[VideoTemplateMedia] = Field(
        default_factory=list, description="留空则由素材包顺序生成"
    )
    generation: dict[str, Any] = Field(default_factory=dict, description="生成参数，覆盖默认值")
    rules: dict[str, Any] = Field(
        default_factory=dict,
        description="封装规则；referenceBindings 将连续 Mixed 编号绑定到 source_video/图片用途，"
        "服务端排列声明槽位并完整附带当前包其余图片，无需逐图引用",
    )
    cover_url: str = Field(default="", max_length=512, description="封面地址")
    pinned: bool = Field(default=False, description="是否设为固定蓝本（全库仅一个）")


class VideoTemplateOut(CamelModel):
    id: str
    template_key: str
    name: str
    description: str
    prompt: VerbatimText
    prompt_sha256: str = Field(description="提示词 sha256，防止文字被悄悄改掉")
    source_video_name: str
    source_video_asset_id: str | None = None
    source_video_id_space: str = "v1"
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
        min_length=1,
        max_length=64,
        description="原片 id：v1 是素材库里 category=source 的行，v2 是资产库里的视频",
    )
    source_video_id_space: Literal["v1", "v2"] = Field(
        default="v1", description="原片 id 属于哪套素材库；缺省 v1"
    )


class VideoTemplatePackReferenceIn(CamelModel):
    token: str = Field(pattern=r"^\{\{Mixed [1-9]\d*\}\}$")
    pack_id: str = Field(min_length=1, max_length=64)
    pack_version: int = Field(ge=1)
    asset_id: str = Field(min_length=1, max_length=64)


class VideoTemplatePromptIn(CamelModel):
    """原地保存固定提示词及跨封装图片引用，不改原片、档位或默认素材包。

    既有用途引用按新原文重算；跨封装引用锁定包与版本，图片追加到基础媒体之后。
    不再点名的图片仍可作为辅助输入；references 省略时保留，显式空数组清除固定引用。
    """

    prompt: VerbatimText = Field(
        min_length=1, max_length=20000, description="新的固定提示词原文，含 {{Mixed N}} 占位符"
    )
    references: list[VideoTemplatePackReferenceIn] | None = Field(
        default=None,
        max_length=MAX_REFERENCE_IMAGES,
        description="跨封装固定图片引用；省略保留原绑定，空数组清除",
    )


class VideoTemplateListOut(CamelModel):
    items: list[VideoTemplateOut] = Field(default_factory=list)
    total: int = 0
    pinned_id: str | None = Field(default=None, description="当前固定蓝本的 id")
