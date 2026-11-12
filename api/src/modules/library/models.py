"""资产库 v2 ORM 实体（11 张 `library_*` 表）。

**与现役 `assets` 表完全独立**：不共享主键、不共享字节前缀、不共享权限。
并存期两套库同时对在线，任何「顺手复用一下旧表」的改动都会让切换与回滚失效。

三条写作约定：

1. **无外键**：与项目其它模块一致（`assets` 也没有 FK）。跨表的完整性靠服务层
   守卫与不变式测试，不靠数据库级联——级联在并存期会把旧库的行一起带走。
2. **状态用字符串存**，值来自 `core.ports.library_lookup` 的枚举。不要用
   `use_enum_values=True`（记忆里的老坑）：注解写枚举、运行时是 str，IDE 与
   mypy 会全部失效。
3. **索引的口径必须与服务层查询一字不差**。部分唯一索引尤其如此：索引拦下了、
   服务层查不到，客户端收到的就是 500 而不是可读的冲突。

不变式（M2 用测试钉住）：

- A：`state ∈ {draft, trash} ⇒ visibility = personal`；
- B：`state = trash ⇒ library_asset_groups` 里没有该 asset 的行。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Float,
    Index,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base, TimestampMixin, UUIDMixin, utcnow


class LibraryAsset(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "library_assets"

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    origin: Mapped[str] = mapped_column(String(20), default="upload", nullable=False)
    mime_type: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)

    # 内容寻址：`library/blobs/ab/<sha256><ext>`。同一份字节被多人使用时物理只有一份。
    sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    storage_key: Mapped[str] = mapped_column(String(256), default="", nullable=False)
    # 我们托管的缩略图 key（音轨没有）。远端封面不放这里：v2 不保存供应商签名地址。
    cover_key: Mapped[str] = mapped_column(String(256), default="", nullable=False)

    width: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    height: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    duration: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    has_audio: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    owner_id: Mapped[str] = mapped_column(String(32), nullable=False)
    visibility: Mapped[str] = mapped_column(String(16), default="personal", nullable=False)
    state: Mapped[str] = mapped_column(String(16), default="active", nullable=False)

    # 回收站：`trashed_from_state` 决定恢复去向（草稿驳回的回到草稿，不是直接入库）。
    trashed_from_state: Mapped[str | None] = mapped_column(String(16), nullable=True)
    trash_reason: Mapped[str | None] = mapped_column(String(20), nullable=True)
    trash_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deleted_by: Mapped[str | None] = mapped_column(String(32), nullable=True)

    # 草稿审核轨迹的「最新结论」冗余在这里，列表页不必 join 轨迹表。
    reviewed_by: Mapped[str | None] = mapped_column(String(32), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reject_reason: Mapped[str | None] = mapped_column(String(200), nullable=True)

    # 乐观锁：批量写、审核、共享决定都带版本。从 1 开始，0 表示「没带版本」。
    state_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    # 文本资产专用，其它类型恒为空串 / 1。`text_excerpt` 随列表一起返回，
    # 否则 24 张文字卡就要读 24 次 blob；`text_version` 是**内容版本**，
    # 与 `state_version`（并发乐观锁）不是一回事，别混用。
    text_excerpt: Mapped[str] = mapped_column(String(320), default="", nullable=False)
    text_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    ref_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # 幂等键：`sourceModule:sourceTaskId[:index]`，或上传方自己给的 `sourceKey`。
    source_key: Mapped[str | None] = mapped_column(String(160), nullable=True)

    __table_args__ = (
        # 列表主查询：本人 + 状态 + 类型，按创建时间倒序
        Index("ix_library_assets_owner_state_kind", "owner_id", "state", "kind", "created_at"),
        # 小组/团队入口：不按 owner 过滤的可见集查询
        Index(
            "ix_library_assets_visibility_state_kind",
            "visibility",
            "state",
            "kind",
            "created_at",
        ),
        # 回收站 GC：`state='trash' AND trash_expires_at < now`
        Index("ix_library_assets_trash_expiry", "state", "trash_expires_at"),
        # 秒传 / 去重：同 owner、同内容、同类型只有一条「未删除」的行。
        # 口径必须与 `LibraryService._find_duplicate` 一致：`deleted_at IS NULL`。
        Index(
            "uq_library_assets_owner_sha_kind",
            "owner_id",
            "sha256",
            "kind",
            unique=True,
            sqlite_where=text("deleted_at IS NULL AND sha256 IS NOT NULL"),
            postgresql_where=text("deleted_at IS NULL AND sha256 IS NOT NULL"),
        ),
        # 生成回写幂等：同一个 sourceKey 只落一行。NULL 不参与（多次上传可都不带 key）。
        Index(
            "uq_library_assets_source_key",
            "source_key",
            unique=True,
            sqlite_where=text("source_key IS NOT NULL"),
            postgresql_where=text("source_key IS NOT NULL"),
        ),
    )


class LibraryAssetGroup(Base, UUIDMixin):
    """资产 → 小组的可见范围。`visibility=team` 时不需要行。"""

    __tablename__ = "library_asset_groups"

    asset_id: Mapped[str] = mapped_column(String(32), nullable=False)
    group_id: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )

    __table_args__ = (
        Index("uq_library_asset_groups", "asset_id", "group_id", unique=True),
        Index("ix_library_asset_groups_group", "group_id"),
    )


class LibraryTag(Base, UUIDMixin):
    """标签词表，**固定两级**：`parent_id == ""` 是一级，否则是它旗下的二级。

    ## 为什么用空串而不是 NULL 表示「一级」

    PG 与 SQLite 的普通唯一索引里 NULL 互不相等，`UNIQUE(name, parent_id)` 会让
    两个同名一级标签同时存在。填一个不可能出现的哨兵值（uuid 不会是空串）后，
    唯一约束可以退化成最朴素的双列索引，跨库行为一致。项目里
    `library_share_requests.group_id` 也是同一套做法。

    ## 为什么唯一约束变成 `(name, parent_id)`

    二级标签是「挂在某个一级下面的词」，两个不同系列都叫「小红书」是合理的；
    但同一个一级下面不该有两个同名子标签，否则标签栏两行里会出现两个一模一样
    的按钮、点哪个都分不清。一级之间仍保持全局同名唯一。

    不加外键（本文件顶部约定 1）：层级合法性由 `TagService.move` 守卫，不靠级联。
    """

    __tablename__ = "library_tags"

    name: Mapped[str] = mapped_column(String(40), nullable=False)
    #: 空串 = 一级；非空 = 父标签 id。固定两级，禁止三级（服务层守卫）。
    parent_id: Mapped[str] = mapped_column(
        String(32), default="", server_default="", nullable=False
    )
    created_by: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )

    __table_args__ = (
        Index("uq_library_tags_name", "name", "parent_id", unique=True),
        Index("ix_library_tags_parent", "parent_id"),
    )


class LibraryAssetTag(Base, UUIDMixin):
    __tablename__ = "library_asset_tags"

    asset_id: Mapped[str] = mapped_column(String(32), nullable=False)
    tag_id: Mapped[str] = mapped_column(String(32), nullable=False)

    __table_args__ = (
        Index("uq_library_asset_tags", "asset_id", "tag_id", unique=True),
        Index("ix_library_asset_tags_tag", "tag_id"),
    )


class LibraryPackItem(Base, UUIDMixin):
    """封装成员。`kind=pack` 的资产行只是壳，成员在这里。"""

    __tablename__ = "library_pack_items"

    pack_id: Mapped[str] = mapped_column(String(32), nullable=False)
    asset_id: Mapped[str] = mapped_column(String(32), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    role: Mapped[str] = mapped_column(String(40), default="", nullable=False)
    description: Mapped[str] = mapped_column(String(200), default="", nullable=False)

    __table_args__ = (
        Index("uq_library_pack_items_position", "pack_id", "position", unique=True),
        Index("ix_library_pack_items_asset", "asset_id"),
    )


class LibraryShareRequest(Base, UUIDMixin):
    """共享 / 撤销共享申请单。撤销也走这里，`action=revoke`。"""

    __tablename__ = "library_share_requests"

    asset_id: Mapped[str] = mapped_column(String(32), nullable=False)
    applicant_id: Mapped[str] = mapped_column(String(32), nullable=False)
    action: Mapped[str] = mapped_column(String(10), nullable=False)
    target: Mapped[str] = mapped_column(String(10), nullable=False)
    # team 申请填空串而不是 NULL：部分唯一索引里 NULL 互不相同，会让「同一资产
    # 同时存在两条 team pending」悄悄成立。
    group_id: Mapped[str] = mapped_column(String(32), default="", nullable=False)
    status: Mapped[str] = mapped_column(String(12), default="pending", nullable=False)
    reviewer_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    note: Mapped[str] = mapped_column(String(200), default="", nullable=False)
    reason: Mapped[str] = mapped_column(String(200), default="", nullable=False)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expected_version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )

    __table_args__ = (
        Index(
            "uq_library_share_requests_pending",
            "asset_id",
            "action",
            "target",
            "group_id",
            unique=True,
            sqlite_where=text("status = 'pending'"),
            postgresql_where=text("status = 'pending'"),
        ),
        Index("ix_library_share_requests_applicant", "applicant_id", "status", "created_at"),
        Index("ix_library_share_requests_asset", "asset_id", "created_at"),
    )


class LibraryDraftReview(Base, UUIDMixin):
    """草稿审核轨迹（采纳 / 驳回）。

    刻意不复用 `library_share_requests`：那张表是「可见性流转」，这张是
    「生命周期流转」。混在一起会让「待我审核」列表分不清在审可见范围还是审入库。
    """

    __tablename__ = "library_draft_reviews"

    asset_id: Mapped[str] = mapped_column(String(32), nullable=False)
    reviewer_id: Mapped[str] = mapped_column(String(32), nullable=False)
    decision: Mapped[str] = mapped_column(String(12), nullable=False)
    reason: Mapped[str] = mapped_column(String(200), default="", nullable=False)
    snapshot: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )

    __table_args__ = (Index("ix_library_draft_reviews_asset", "asset_id", "created_at"),)


class LibraryGroupLeader(Base, UUIDMixin):
    """小组负责人映射。

    刻意放在 v2 而不是 identity：identity 的三层授权刚上线、正在被真实用户使用，
    为「资产库审核人」改它的组模型，会把两个变更的风险绑在一起。
    """

    __tablename__ = "library_group_leaders"

    group_id: Mapped[str] = mapped_column(String(32), nullable=False)
    user_id: Mapped[str] = mapped_column(String(32), nullable=False)

    __table_args__ = (
        Index("uq_library_group_leaders", "group_id", "user_id", unique=True),
        Index("ix_library_group_leaders_user", "user_id"),
    )


class LibraryAssetGeneration(Base):
    """生成溯源快照，1:1 对应资产行，主键就是 `asset_id`。

    **冻结写入**：入库那一刻的业务表值抄进来，之后不再联表查询。生成参数散落在
    `image_jobs` / `video_tasks` / `replacement_runs` 三张表，且历史数据没有完整
    记录；详情页对缺失字段显示「未记录」，而不是去别的模块现查。
    """

    __tablename__ = "library_asset_generation"

    asset_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    source_module: Mapped[str] = mapped_column(String(20), nullable=False)
    source_task_id: Mapped[str] = mapped_column(String(64), nullable=False)
    model: Mapped[str] = mapped_column(String(80), default="", nullable=False)
    model_id: Mapped[str] = mapped_column(String(80), default="", nullable=False)
    # 不设长度：这里存的是**注入正文之后**的提示词（`{{Text N}}` 会被换成整段文案，
    # 单份上限 1MB）。`String(4000)` 时代 PG 会报 `value too long`，而异常被
    # `runner._archive` 吞成一行日志 —— 任务显示成功、草稿箱里没有东西。
    prompt: Mapped[str] = mapped_column(Text, default="", nullable=False)
    size: Mapped[str] = mapped_column(String(32), default="", nullable=False)
    count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    duration_sec: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    aspect_ratio: Mapped[str] = mapped_column(String(16), default="", nullable=False)
    style_preset: Mapped[str] = mapped_column(String(40), default="", nullable=False)
    seed: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    reference_asset_ids: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    pack_id: Mapped[str] = mapped_column(String(32), default="", nullable=False)
    pack_version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    prompt_spec: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    configuration: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    external_id: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    credits: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    generated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )


class LibraryTextVersion(Base, UUIDMixin):
    """文本资产的**不可变**版本快照。

    为什么把整份正文再存一遍（而不是只存 `storage_key`）：blob 的 GC
    （`app prune-blobs`）按 `library_assets.storage_key` / `cover_key` 数引用，
    编辑会换 key，旧版本若只剩一个版本行里的 key，那块字节会被当成孤儿删掉，
    历史就再也读不回来。文本上限 1MB，行内留一份是最省心的做法。

    刻意为「只增不删」：恢复旧版本是**再写一版**，不改历史行——否则
    「第 3 版当时是什么」会随着一次恢复操作被改写，快照就失去证据意义。
    """

    __tablename__ = "library_text_versions"

    asset_id: Mapped[str] = mapped_column(String(32), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    note: Mapped[str] = mapped_column(String(200), default="", nullable=False)
    created_by: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )

    __table_args__ = (
        Index("uq_library_text_versions_number", "asset_id", "version", unique=True),
        Index("ix_library_text_versions_asset", "asset_id", "created_at"),
    )


class LibraryDownloadJob(Base, UUIDMixin, TimestampMixin):
    """批量打包任务。zip 在 `library/downloads/` 前缀下，24h 过期。"""

    __tablename__ = "library_download_jobs"

    owner_id: Mapped[str] = mapped_column(String(32), nullable=False)
    asset_ids: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    status: Mapped[str] = mapped_column(String(12), default="queued", nullable=False)
    storage_key: Mapped[str] = mapped_column(String(256), default="", nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    error: Mapped[str] = mapped_column(String(500), default="", nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        # created_at 的独立索引由 `TimestampMixin` 的 index=True 生成，不要手写：
        # 在 __table_args__ 里按字符串引用 mixin 列，建表期解析不到会直接报
        # `ConstraintColumnNotFoundError`。
        Index("ix_library_download_jobs_owner", "owner_id"),
        Index("ix_library_download_jobs_expiry", "status", "expires_at"),
    )
