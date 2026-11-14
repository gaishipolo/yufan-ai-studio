"""模板封装的 ORM 实体：素材包（head / 参考图 / 冻结版本）+ 视频模板 + manifest 配置。

## 两套表，各管一段

| 表 | 谁写 | 谁读 |
|---|---|---|
| `template_manifests` 四表 | 运营在 `/template-manifests` 配好并发布 | 编写侧目录端口 |
| `template_packs` 四表 | 同一次发布的**投影** | `template_runtime` 端口（运行侧） |

配置层是「运营改了什么」，投影层是「运行时要用的冻结版本 + 模板原文」。两层
刻意不合并：编写侧每加一个字段都会影响界面，而运行侧只需要不会随编辑界面演进的
那几十个字段。

**2026-11-14 C2**：这四张表原本在 `modules/template`，与替换执行链混在一个模块里。
用户拍板「封装模块只管数据、通过 ports 对外提供」，于是整块搬到这里，表名不变
（不需要数据迁移）。

## 素材一律引用 `library_assets.id`（内容寻址）

不再存本地文件路径：字节变了就是新 id，packager 原来「算文件 sha256 + 防预检后
被换」那套补偿逻辑整块不需要。配置指纹只由结构与 asset id 决定，换机器 / 换存储
驱动都不变。

## 为什么不用外键

全项目没有一处 `ForeignKey`，靠字符串 id + 应用层校验（跨 sqlite/postgres 行为
一致，删记录不会级联炸掉历史）。唯一性靠复合索引表达。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base, TimestampMixin, UUIDMixin, utcnow

__all__ = [
    "MANIFEST_STATUSES",
    "TemplateManifest",
    "TemplateManifestFunction",
    "TemplateManifestPack",
    "TemplateManifestReference",
    "SPEC_STATUSES",
    "TemplatePack",
    "TemplatePackReference",
    "TemplatePackVersion",
    "TemplateSpec",
    "TemplateSpecSlot",
    "TemplateSpecVersion",
    "VideoTemplate",
]

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


class TemplatePack(Base, UUIDMixin, TimestampMixin):
    """封装 head：草稿与已发布共用一行，靠 `status` 区分。

    拆成「草稿表 + 发布表」会让每次保存都要同步两张表，而草稿的唯一用途就是
    被发布覆盖。合成一行的代价只有一个：`version` 与 `revision` 必须分清——
    `revision` 是乐观锁（每次保存 +1），`version` 是已发布版本号（只在发布时 +1）。
    """

    __tablename__ = "template_packs"

    kind: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(160), default="", nullable=False)
    owner_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)

    status: Mapped[str] = mapped_column(String(16), default="draft", nullable=False, index=True)
    # 已发布版本号，0 表示从未发布。发布时只增不减；停用不改它。
    version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # 乐观锁：前端提交 `expectedRevision`，对不上就是「别人改过了」。
    revision: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    features: Mapped[str] = mapped_column(Text, default="", nullable=False)
    instructions: Mapped[str] = mapped_column(Text, default="", nullable=False)
    wardrobe_mode: Mapped[str] = mapped_column(String(16), default="preserve", nullable=False)

    # 主图：为空时按 `position=0` 那张。显式存的原因是「换主图」不该等于
    # 「把那张图拖到第一位」——重排会改变提示词里的编号顺序。
    cover_asset_id: Mapped[str | None] = mapped_column(String(32), nullable=True)

    # 以下是**待发布状态**：草稿编辑中随手改，发布时整块冻结进版本行。
    # 放 head 而不放 reference 表：它们描述「整个包」而不是「某一张图」，
    # 且与版本行一一对应，发布时直接拷贝。
    required_roles: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    supported_scopes: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)

    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        # 「我管理的封装」列表形状：owner + 未删除，按更新时间倒序
        Index(
            "ix_template_packs_owner_active",
            "owner_id",
            "updated_at",
            sqlite_where=text("deleted_at IS NULL"),
            postgresql_where=text("deleted_at IS NULL"),
        ),
        # 「可用封装」列表形状：已发布 + 未删除，按类型过滤
        Index(
            "ix_template_packs_public",
            "kind",
            "published_at",
            sqlite_where=text("status = 'published' AND deleted_at IS NULL"),
            postgresql_where=text("status = 'published' AND deleted_at IS NULL"),
        ),
    )


class TemplatePackReference(Base, UUIDMixin):
    """封装里的一张参考图。

    `position` 而不是只存数组：`bindings` 与 `reference_ids` 必须**同序**，
    提示词编译器按这个顺序编号（`reference-1.jpg`、`reference-2.jpg`）。
    存 JSON 数组会让「调换主图」变成一次整列重写，且没法按 `asset_id` 建索引
    ——而索引正是「删除素材前问还有谁在用」要的东西。
    """

    __tablename__ = "template_pack_references"

    pack_id: Mapped[str] = mapped_column(String(32), nullable=False)
    asset_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    # 用途 key（front / multiview / detail…），词表在 core/template_rules/roles.py
    role: Mapped[str] = mapped_column(String(24), default="", nullable=False)
    # 这张图在提示词里的补充说明，只写本次特有的事实
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)

    __table_args__ = (
        Index("uq_template_pack_references_position", "pack_id", "position", unique=True),
        Index("ix_template_pack_references_asset", "asset_id", "pack_id"),
    )


class TemplatePackVersion(Base, UUIDMixin, TimestampMixin):
    """已发布封装的不可变快照。

    **唯一性约束就是 CAS**：上一版靠 `storage.create()`（`If-None-Match: *`）
    原子抢版号，现在换成 `UNIQUE(pack_id, version)`——两个实例同时想写 v3，
    数据库只会让一个成功，另一个拿到 IntegrityError 后重试 v4。语义完全一致，
    但不再需要「抢版本文件」这种绕法。

    内容存 JSON 而不是拆子表：版本是**冻结证据**，只整块读、从不按字段查。
    拆表会让「发布」变成一次多表插入，而它本来就该是一次原子写。
    """

    __tablename__ = "template_pack_versions"

    pack_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)

    reference_ids: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    # 逐图用途，与 reference_ids 同序：[{role, description}, ...]
    bindings: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list, nullable=False)
    required_roles: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    supported_scopes: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)

    features: Mapped[str] = mapped_column(Text, default="", nullable=False)
    instructions: Mapped[str] = mapped_column(Text, default="", nullable=False)
    wardrobe_mode: Mapped[str] = mapped_column(String(16), default="preserve", nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        # 这条索引同时是「版本号不被覆盖」的保证，别删。
        Index("uq_template_pack_versions_number", "pack_id", "version", unique=True),
    )


class VideoTemplate(Base, UUIDMixin, TimestampMixin):
    """一条视频模板：固定样片 + 固定提示词 + 一个已发布替换素材包。

    **为什么从 JSON 目录搬进表**：`api/var/video_templates/catalog.json` 时期
    「读全量 → 追加 → 写全量」只能靠一把**进程内** `threading.Lock` 互斥，
    多 worker 下两个封装并发时后写的会把前一条整份覆盖——丢数据且不报错。
    当时不用表的理由（部署库的 `alembic_version` 指向不存在的 revision）已经
    不成立：`b4e2d8f1a630` 就在迁移链里，`alembic current` 就是 head。

    搬过来顺带解决三件事：`catalog_stamp()`（拿文件 mtime 当缓存代次）不再需要、
    每次写前复制 20 份整份备份不再需要、`_LOCK` 里「抢占固定蓝本」换成了
    `uq_video_templates_pinned` 这条**部分唯一索引**——两个并发写入只有一个能成功，
    另一个由数据库拒绝，语义与原来一样但跨进程成立。

    **没有 `owner_id`**：这一版仍然「全库共享同一份模板目录」，归属校验是计划
    步 3 的事。现在加一列却没有消费方，等于给后面留一个「看起来有权限其实没判」
    的字段，比没有更糟。
    """

    __tablename__ = "video_templates"

    # 稳定标识：脚本重试边界。同键同配置返回原行，同键异配置 409
    # （对应 `video_template_service.create` 里的比对，不是靠唯一索引报错）。
    template_key: Mapped[str] = mapped_column(String(80), nullable=False, unique=True)

    name: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    # 固定提示词原文，逐字保留（`VerbatimText`），摘要按同一份原文算
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    prompt_sha256: Mapped[str] = mapped_column(String(64), default="", nullable=False)

    source_video_asset_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    source_video_path: Mapped[str] = mapped_column(String(512), default="", nullable=False)
    source_video_name: Mapped[str] = mapped_column(String(200), default="", nullable=False)
    source_duration_sec: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    aspect_ratio: Mapped[str] = mapped_column(String(16), default="9:16", nullable=False)

    # 快照：封装时的包名与版本。包后来改名/升版不影响这条模板的展示与回放
    pack_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    pack_name: Mapped[str] = mapped_column(String(160), default="", nullable=False)
    pack_version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    default_kind: Mapped[str] = mapped_column(String(16), default="product", nullable=False)

    # 媒体清单（`[{token, role, type, assetId, name, file, inPrompt}]`）。
    # 存 JSON 而不是子表：它只被整块读，且**顺序即语义**（下标 +1 就是 {{Mixed N}}），
    # 拆表要额外维护 position 且每次读都重排，换不来任何查询能力。
    media_order: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list, nullable=False)
    generation: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    rules: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)

    cover_url: Mapped[str] = mapped_column(String(512), default="", nullable=False)
    # 固定蓝本：全库仅一条。唯一性由下面的部分唯一索引保证，不靠应用层先清后设
    pinned: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    __table_args__ = (
        # 部分唯一索引 = 「全库只有一个固定蓝本」，跨进程成立。
        # `WHERE pinned` 在两个库上都合法：sqlite 把非 0 当真。
        Index(
            "uq_video_templates_pinned",
            "pinned",
            unique=True,
            sqlite_where=text("pinned"),
            postgresql_where=text("pinned"),
        ),
        # `find_for_run()` 的主查询路径：按原片 + 处理类型找模板。
        Index("ix_video_templates_source_kind", "source_video_asset_id", "default_kind"),
    )


# --------------------------------------------------------------------------- #
# 模板规格（见 `docs/TEMPLATE_SPEC.md`）
# --------------------------------------------------------------------------- #
#: 规格生命周期。`disabled` 是"下架"，不是删除：历史版本与已生成任务照旧可查。
SPEC_STATUSES = ("draft", "published", "disabled")


class TemplateSpec(Base, UUIDMixin, TimestampMixin):
    """模板规格的 head：**编辑态** + 生命周期 + 乐观锁。

    已发布内容不在这里 —— 它在 `template_spec_versions`。head 只保留"正在编辑的
    那份草稿"（`draft` JSON）与"最新版本号"。这样做的原因：草稿只有一个人写、
    整份替换，没有按行查询的需求；用行存反而要区分 `version=0` 与"发布中"两种
    半成品状态。发布时一次性把 draft 展开成 version + slots，之后不可变。

    `draft` 存的是 `TemplateSpec` 的 camelCase JSON（与契约同形），读取时用
    `core.template_spec` 的 dataclass 反序列化，不在 ORM 层做拆列。
    """

    __tablename__ = "template_specs"
    # 唯一约束声明成 `UniqueConstraint` 而不是列上 `unique=True`：后者在迁移里
    # 到底是建约束还是建唯一索引会随方言变，`compare_metadata` 会报"缺约束"。
    __table_args__ = (
        UniqueConstraint("spec_key", name="uq_template_specs_spec_key"),
    )

    #: 幂等边界。发布后不可改：改模板 = 换 key 发新版本。
    spec_key: Mapped[str] = mapped_column(String(80), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str] = mapped_column(String(2000), default="", nullable=False)
    owner_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(16), default="draft", nullable=False, index=True)
    draft: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    #: 每次保存 +1；与前端传来的 `revision` 比对做乐观锁。
    revision: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    #: draft 的结构摘要：同 key 同指纹 = 幂等重发，不新增修订。
    fingerprint: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    #: 最新已发布版本号；0 = 从未发布。
    current_version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    disabled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class TemplateSpecVersion(Base, UUIDMixin):
    """发布快照。**只插入、不更新**：改模板 = 新版本。

    提示词存在这里而不是引用 head 的 draft：head 会继续被编辑，而历史任务必须
    永远看到它发布那一刻的原文（`tests/test_template_spec_publish.py` 钉这条）。
    """

    __tablename__ = "template_spec_versions"
    __table_args__ = (
        UniqueConstraint("spec_id", "version", name="uq_template_spec_versions_spec_version"),
        Index("ix_template_spec_versions_spec", "spec_id"),
    )

    spec_id: Mapped[str] = mapped_column(String(32), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    #: 发布时该模型必须在目录里存在；参数键必须落在它的 `param_schema` 内。
    model: Mapped[str] = mapped_column(String(80), nullable=False)
    params: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    prompt_locked: Mapped[str] = mapped_column(Text, default="", nullable=False)
    prompt_editable: Mapped[str] = mapped_column(Text, default="", nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    published_by: Mapped[str] = mapped_column(String(32), default="", nullable=False)
    published_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )


class TemplateSpecSlot(Base, UUIDMixin):
    """某个发布版本的一个槽位声明。

    `position` 就是 `{{Mixed N}}` 的顺序；**这里不存编号**（编号由编译按实际填充
    数量算出来，见 `core/template_spec/compile.py`）。

    `source=pack` 时只存 `pack_id`，成员与角色留在资产库 —— 模板不复制成员清单，
    否则库改了成员模板还按旧的算（§12 不变量 9）。
    """

    __tablename__ = "template_spec_slots"
    __table_args__ = (
        UniqueConstraint("version_id", "slot_key", name="uq_template_spec_slots_key"),
        UniqueConstraint("version_id", "position", name="uq_template_spec_slots_position"),
    )

    version_id: Mapped[str] = mapped_column(String(32), nullable=False)
    slot_key: Mapped[str] = mapped_column(String(40), nullable=False)
    type: Mapped[str] = mapped_column(String(16), nullable=False)
    #: `asset` | `pack`
    source: Mapped[str] = mapped_column(String(16), default="asset", nullable=False)
    #: `fixed` | `user`
    binding: Mapped[str] = mapped_column(String(16), default="user", nullable=False)
    #: `mixed` | `attachment`
    ref_mode: Mapped[str] = mapped_column(String(16), default="mixed", nullable=False)
    min_count: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    max_count: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    label: Mapped[str] = mapped_column(String(80), default="", nullable=False)
    usage_text: Mapped[str] = mapped_column(String(500), default="", nullable=False)
    asset_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    pack_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
