"""template_pack 的 ORM 实体。

## 表名为什么带 `templates` 后缀

`modules/template` 已经占了 `template_packs` / `template_pack_references` /
`template_pack_versions` 三张表（那套是「素材包 + 视频模板」）。
本模块是**另一套东西**——带筛选约束的封装模板——但同属「模板」语义，
沿用 `template_pack_` 前缀会让人分不清两张表各是什么。

于是主表叫 `template_pack_templates`（模块名 + 实体名），槽位表叫
`template_pack_slots`。读表名就知道谁是谁，不必去翻 models.py。

## 两张表的分工

主表存「模板是什么」：名字、提示词原文、原片、状态。
槽位表存「提示词里每个 `{{Mixed N}}` 指向什么」：要么钉死一条素材
（`slot_kind=fixed`），要么声明一条准入规则（`slot_kind=filtered`）。

**槽位独立成表而不是塞进主表的 JSON 列**，理由与 `modules/template` 把
`media_order` 存 JSON 的做法相反，理由有三：

1. 槽位要按 `position` 排序并按 `template_id` 查 —— JSON 列查不动，
   「列出所有已发布模板的第 2 号槽位」这种问题会逼着扫全表再在 Python 里筛。
2. 筛选条件是**业务约束**，不是展示字段。JSON 列里的东西没有索引、没有
   外键、没有类型保证，改坏了要等运行时才发现。
3. `filter_tag_ids` 是 JSON 数组（一级标签可以有多个），这个确实适合 JSON 列 ——
   但它是**槽位内部的一个字段**，不影响槽位本身要独立成行这件事。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base, TimestampMixin, UUIDMixin

__all__ = ["TemplatePackRow", "TemplatePackSlotRow"]

#: 模板状态。`draft` 只有作者可见；`published` 进模板库；`disabled` 保留历史
#: 但不再对外。终态（published/disabled）不可再改内容 —— 要改就发新版本。
STATUS_DRAFT = "draft"
STATUS_PUBLISHED = "published"
STATUS_DISABLED = "disabled"


class TemplatePackRow(Base, UUIDMixin, TimestampMixin):
    """一条封装模板。"""

    __tablename__ = "template_pack_templates"

    #: 稳定标识：同键同配置返回原行（幂等），同键异配置 409。
    #: 脚本重试的边界靠它 —— 没有它，重试一次就多一条模板。
    #:
    #: **不写 `index=True`**：唯一索引（`uq_template_pack_templates_key`）已经
    #: 能按它查，再加一个普通索引是重复；而 `alembic check` 会把两者当成
    #: 「两个不同的索引」报差异。
    template_key: Mapped[str] = mapped_column(String(80), nullable=False)

    name: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str] = mapped_column(String(2000), default="", nullable=False)

    #: 提示词原文，逐字保存（`VerbatimText` 语义）。
    prompt: Mapped[str] = mapped_column(String(20000), nullable=False)
    #: 提示词摘要。防止文字被悄悄改掉 —— 归档要能回答「当时发出去的是哪一版」。
    prompt_sha256: Mapped[str] = mapped_column(String(64), default="", nullable=False)

    #: 参考视频。id 空间显式声明：v1 与 v2 都是 32 位 hex，形状不可区分，
    #: 靠猜会把一侧的 id 读成另一侧的空壳。
    source_video_asset_id: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    source_video_id_space: Mapped[str] = mapped_column(String(8), default="v2", nullable=False)
    source_duration_sec: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)

    aspect_ratio: Mapped[str] = mapped_column(String(16), default="9:16", nullable=False)
    cover_url: Mapped[str] = mapped_column(String(512), default="", nullable=False)

    status: Mapped[str] = mapped_column(
        String(16), default=STATUS_DRAFT, nullable=False, index=True
    )
    owner_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        Index("uq_template_pack_templates_key", "template_key", unique=True),
    )


class TemplatePackSlotRow(Base, UUIDMixin, TimestampMixin):
    """提示词里的一个槽位。

    **二选一**：`slot_kind=fixed` 时 `asset_id` 有值、`filter_*` 为空；
    `slot_kind=filtered` 时反过来。两种形态混在一行里会让「这个槽位到底
    是钉死的还是可选的」变成读代码才知道的事 —— 所以用显式枚举，
    并在 service 里做互斥校验。
    """

    __tablename__ = "template_pack_slots"

    template_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("template_pack_templates.id"), nullable=False, index=True
    )
    #: 槽位序号，从 1 起。**决定 `{{Mixed N}}` 编号**。
    position: Mapped[int] = mapped_column(Integer, nullable=False)

    #: `fixed` / `filtered`
    slot_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    #: 占位符原文，如 `{{Mixed 2}}`。
    token: Mapped[str] = mapped_column(String(32), nullable=False)
    #: 槽位显示名，如「产品主图」。给用户看的，不参与任何判定。
    label: Mapped[str] = mapped_column(String(64), default="", nullable=False)

    # ---- fixed 形态 ----
    asset_id: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    asset_id_space: Mapped[str] = mapped_column(String(8), default="v2", nullable=False)
    media_type: Mapped[str] = mapped_column(String(16), default="image", nullable=False)

    # ---- filtered 形态 ----
    #: 素材类别白名单：image / video / audio。**不含 text** —— 文本资产不是参考图。
    filter_kind: Mapped[str] = mapped_column(String(16), default="", nullable=False)
    #: 一级标签 id 列表。存 id 不存名字：改名不该让已发布模板的约束失效。
    filter_tag_ids: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    #: 标签组合方式。**只有 `any`**：资产库的 `find_ids` 唯一口径就是 OR
    #: （`tag_id.in_(expanded)`），没有「全部匹配」的实现。留着 `all` 会
    #: 让用户选了一个说好更严、实际更松的条件 —— 筛选只收窄不放宽。
    filter_tag_match: Mapped[str] = mapped_column(String(8), default="any", nullable=False)
    #: 该槽位是否必填。默认必填 —— 少了它，提示词就会指向一个空槽位。
    required: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    __table_args__ = (
        Index("uq_template_pack_slots_position", "template_id", "position", unique=True),
    )
