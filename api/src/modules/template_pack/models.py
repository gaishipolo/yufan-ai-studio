"""template_pack 的 ORM 实体。

## 过渡状态（迁移阶段 B）

本文件暂时**并存两套表**：

- `template_pack_templates` / `template_pack_slots`：旧的「带筛选约束的封装模板」。
  用户已拍板由 `template_packager` 的完整能力覆盖，阶段 C 连同 service / router
  一起下线，届时删表。
- `template_manifests` / `template_manifest_packs` / `template_manifest_references` /
  `template_manifest_functions`：`manifest.json` DB 化后的配置层（本阶段新增）。

`db.registry.load_all_models()` 只扫 `modules.*.models`，所以新表必须放在这个
文件里；另起 `manifest_models.py` 不会被发现，会让 `create_all` 与 Alembic 都缺表。

## manifest 四张表为什么这么切

原 manifest 是两层：`key/name/video` + `packs[]` + `functions[]`，且 `packs[]`
可被多个 function 按 key 共享、可复用已发布包。

- head 表存整份配置的身份、原片与发布状态；
- packs 独立成表（复用靠 `reuse_pack_id`，发布回执存 `published_pack_version`）；
- references 独立成表：删素材前要问「还有谁在用」，扫 JSON 列做不到；
- functions 是最终的工作台卡片，发布后对应一条 `video_templates` 行。

**素材一律引用 `library_assets.id`（内容寻址）**，不再存本地文件路径：字节变了
就是新 id，packager 原来「算文件 sha256 + 防预检后被换」那套补偿逻辑整块不需要。
配置指纹只由结构与 asset id 决定，换机器 / 换存储驱动都不变。

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

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base, TimestampMixin, UUIDMixin

__all__ = [
    "TemplatePackRow",
    "TemplatePackSlotRow",
    "TemplateManifest",
    "TemplateManifestPack",
    "TemplateManifestReference",
    "TemplateManifestFunction",
    "MANIFEST_STATUSES",
]

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

    #: 参考视频（`library_assets.id`）。
    source_video_asset_id: Mapped[str] = mapped_column(String(64), default="", nullable=False)
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


# ---------------------------------------------------------------------------
# manifest 配置层（阶段 B 新增）
# ---------------------------------------------------------------------------

#: 与旧 `PACK_STATUSES` 同口径。`draft` 只作者可见，`published` 进模板库，
#: `disabled` 保留历史但不再对外。终态不可再改内容 —— 要改就发新版本。
MANIFEST_STATUSES = ("draft", "published", "disabled")


class TemplateManifest(Base, UUIDMixin, TimestampMixin):
    """一份 DB 化的 manifest 配置 head。

    `template_key` 是**幂等边界**：同键同 `fingerprint` 返回原行，同键异指纹
    409。原 packager 靠 `uuid5(owner+key)` 派生 `templateKey` + journal 进度文件
    才做到，现在一次唯一索引查询就够了，且跨进程成立。

    `version` 是已发布版本号（只在发布时 +1），`revision` 是乐观锁（每次保存
    +1）—— 两者混用会让「别人改过了」判不出来。
    """

    __tablename__ = "template_manifests"

    template_key: Mapped[str] = mapped_column(String(80), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str] = mapped_column(String(2000), default="", nullable=False)
    owner_id: Mapped[str] = mapped_column(String(32), nullable=False)

    # 原片一律来自资产库。原 manifest 的 `video.shared` 语义保留为「发布时对
    # 原片可见性的要求」：素材本身已在资产库里，不再有上传 / 共享审批这一步。
    video_asset_id: Mapped[str] = mapped_column(String(64), nullable=False)
    video_shared: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    status: Mapped[str] = mapped_column(String(16), default="draft", nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    revision: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # 配置摘要：结构 + asset id 的规范 JSON 的 sha256。不含机器路径、不含文件字节。
    fingerprint: Mapped[str] = mapped_column(String(64), default="", nullable=False)

    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        Index("uq_template_manifests_key", "template_key", unique=True),
        Index("ix_template_manifests_owner_status", "owner_id", "status"),
        Index("ix_template_manifests_status", "status"),
    )


class TemplateManifestPack(Base, UUIDMixin, TimestampMixin):
    """配置里的一个素材包。

    `reuse_pack_id` 非空表示**复用已发布的包**（原 manifest 的「references 为空
    + 可选 packId」），此时本行的 references 必须为空，且不许覆盖已发布包配置。
    发布回执两列记「这次实际用了哪个包的哪一版」：远端包被手工改过要靠它比对，
    回放也靠它，不重新解析配置。
    """

    __tablename__ = "template_manifest_packs"

    manifest_id: Mapped[str] = mapped_column(String(32), nullable=False)
    pack_key: Mapped[str] = mapped_column(String(40), nullable=False)
    name: Mapped[str] = mapped_column(String(160), default="", nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    reuse_pack_id: Mapped[str] = mapped_column(String(32), default="", nullable=False)

    required_roles: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    supported_scopes: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    wardrobe_mode: Mapped[str] = mapped_column(
        String(16), default="preserve", nullable=False
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)

    published_pack_id: Mapped[str] = mapped_column(String(32), default="", nullable=False)
    published_pack_version: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False
    )

    __table_args__ = (
        Index(
            "uq_template_manifest_packs_key", "manifest_id", "pack_key", unique=True
        ),
        Index(
            "uq_template_manifest_packs_position", "manifest_id", "position", unique=True
        ),
    )


class TemplateManifestReference(Base, UUIDMixin):
    """素材包里的一张参考图。

    `position` 即 `{{Mixed N}}` 的编号依据：原 packager 靠 `mediaOrder` 显式排列，
    DB 化后顺序由这一列固定，换主图不该等于「把那张图拖到第一位」。
    `asset_id` 上的索引是删除保护的依据 —— 删素材前要问「还有谁在用」。
    """

    __tablename__ = "template_manifest_references"

    pack_id: Mapped[str] = mapped_column(String(32), nullable=False)
    ref_key: Mapped[str] = mapped_column(String(40), nullable=False)
    asset_id: Mapped[str] = mapped_column(String(64), nullable=False)
    role: Mapped[str] = mapped_column(String(24), default="", nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)

    __table_args__ = (
        Index(
            "uq_template_manifest_references_key", "pack_id", "ref_key", unique=True
        ),
        Index(
            "uq_template_manifest_references_position", "pack_id", "position", unique=True
        ),
        Index("ix_template_manifest_references_asset", "asset_id", "pack_id"),
    )


class TemplateManifestFunction(Base, UUIDMixin, TimestampMixin):
    """配置里的一个功能（工作台卡片）。

    发布后对应一条 `video_templates` 行，`published_template_id` 是回执。
    `scene_prompt_default` 非空 = 文字换场景（`inputMode=video_scene_text`），
    这条路径没有素材包、没有 mediaOrder，只有原片一个输入。

    `prompt` 用 `Text` 而非 `String(20000)`：与 `library_asset_generation.prompt`
    同样吃过「长文案已完成但内容没了」的亏，长度约束放在校验层。
    """

    __tablename__ = "template_manifest_functions"

    manifest_id: Mapped[str] = mapped_column(String(32), nullable=False)
    function_key: Mapped[str] = mapped_column(String(40), nullable=False)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    pack_key: Mapped[str] = mapped_column(String(40), default="", nullable=False)
    kind: Mapped[str] = mapped_column(String(16), default="", nullable=False)

    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    prompt_sha256: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    scene_prompt_default: Mapped[str | None] = mapped_column(Text, nullable=True)
    input_mode: Mapped[str] = mapped_column(String(24), default="", nullable=False)

    media_order: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    bindings: Mapped[dict[str, str] | None] = mapped_column(JSON, nullable=True)
    scope_options: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    aspect_ratio: Mapped[str] = mapped_column(String(16), default="9:16", nullable=False)
    duration: Mapped[int | None] = mapped_column(Integer, nullable=True)

    position: Mapped[int] = mapped_column(Integer, nullable=False)
    published_template_id: Mapped[str] = mapped_column(
        String(32), default="", nullable=False
    )

    __table_args__ = (
        Index(
            "uq_template_manifest_functions_key",
            "manifest_id",
            "function_key",
            unique=True,
        ),
        Index(
            "uq_template_manifest_functions_position",
            "manifest_id",
            "position",
            unique=True,
        ),
    )
