"""素材资产 ORM 实体。

表是**素材的唯一索引**：用户上传的原片、从盘上登记的模板素材都落在这里，
不再有第二份 JSON 目录当真相源。文件本身在对象存储里，`storage_key` 是相对 key，
换 local / S3 只改驱动，不动数据。

**生成产物入库这一步还没接上**——它目前落在运行工作区（`AssetSource.GENERATED`
仍是预留值，见 `schema.py`）。但 M3 起本表已经能表达「刚生成、还没被采纳」的
素材：`origin=generate` + `stage=candidate`，由 `CandidateService.register_generated()`
写入（当前只有演示数据调用它；真正的接入点在生成流水线的收尾）。写查询时按
「已入库」理解本表，不要把「素材库」当成「所有用过的媒体文件」。

两个封面字段不是重复：

- `cover_url`：**远端**封面。供应商返回的签名地址、seed 里的示例图属于这一类，
  它不由我们托管，也就不该被 `url_for()` 重新拼。
- `cover_key`：**我们托管的**缩略图对象 key（视频首帧、图片降采样）。

出参按「有 `cover_key` 就用它换 URL，否则退回 `cover_url`」合成 `coverUrl`。
这样切存储驱动时，托管封面跟着 key 走，远端封面也不会因为换桶而集体失效。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
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
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db.base import Base, TimestampMixin, UUIDMixin

# 副作用导入：`db.registry` 只扫 `modules.<name>.models`，标签、主体、配方那几张表
# 要跟着这个模块一起进 `Base.metadata`——漏掉它的症状是 `alembic check` 认为要删表。
from modules.asset import recipe_models, subject_models, tag_models  # noqa: F401
from modules.asset.library_models import Blob


class Asset(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "assets"

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    type: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    mime_type: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)

    cover_url: Mapped[str] = mapped_column(String(512), default="", nullable=False)
    cover_key: Mapped[str] = mapped_column(String(256), default="", nullable=False)
    storage_key: Mapped[str] = mapped_column(String(256), default="", nullable=False)

    width: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    height: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    duration: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    has_audio: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # 内容摘要：重复上传同一文件时直接返回已有记录（同 owner 内去重）。
    # 允许 NULL 而不是空串 —— 唯一索引在 SQLite/PG 里都把多个 NULL 视为互不相同，
    # 历史归档行与「算不出摘要」的行因此可以共存；空串会互相撞车。
    sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # 归档分类（source / character / product / scene / audio）；NULL = 未分类。
    # 上传的个人素材默认未分类，归档导入时按目录归类。
    category: Mapped[str | None] = mapped_column(String(16), nullable=True, index=True)

    # 来源：**当前所有写入都是 `archive`**（网页上传与盘上登记走同一个 `_ingest`）。
    # 字节一律进归档存储，「归档」已经是素材的存在形式，不再是来源之一。
    # `upload` / `generated` 没有写入路径（`upload` 只剩早期数据，`generated` 是
    # 预留），由 `schema.AssetSource` 的守卫测试盯着——见那边的说明。
    # 它不参与鉴权，也**不能**再拿来当查询过滤条件：`archive_repository` 里的
    # 每个查询都带 `source == ARCHIVE`，新增别的来源会写进去却在列表里看不见。
    source: Mapped[str] = mapped_column(String(16), default="archive", nullable=False)

    # 被模板/任务引用的次数。仅供列表展示；**删除时的引用校验不读它**
    # （计数会漂移，真正的判定走 `api/ports.py` 的注入口）。
    ref_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    owner_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)

    # ------------------------------------------------------------------ 字节与归属（M1）
    # 字节事实提到 `blobs` 表：同一份内容只有一行，多条素材记录指向它。
    # **可空**是为了让「回填还没跑到」的存量行仍然可读——读路径在 `blob_id`
    # 为空时回落到 `storage_key` / `size_bytes` / `sha256` 这三列，
    # 回填完成后（`app assets backfill-blobs`）才在 M1-G 收紧。
    # 写成 NOT NULL 会让部署中途的读写直接 500，而那些行并没有坏。
    blob_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)

    # 分类树上的位置。`category` 字符串在 M1 期仍是读路径（前端按它筛），
    # 这一列先回填、由 doctor 校验一致性，M2 换壳时才切读。
    category_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)

    # 库边界：personal / team / brand。**单值**，不是多值叠加——多值会让
    # 「它现在到底谁能改」不再是可判定的单一事实（设计 §6.1）。
    # 不加单列索引：`ix_assets_library` 的首列就是它，复合索引已经覆盖
    # 「按库 + 按团队 + 按时间」这一种查询形状，再单建一条等于给写路径加负担。
    visibility: Mapped[str] = mapped_column(String(16), default="personal", nullable=False)
    # `visibility=team` 时必填。存量素材留空：它们当时只属于个人，
    # 猜一个团队归属比起留空是更坏的错——留空还能靠时间线反推，猜错无从发现。
    team_id: Mapped[str | None] = mapped_column(String(32), nullable=True)

    # 系统判定的媒体类型，**不接受客户端指定**。值域比旧的 `type` 多一个
    # `document`（为脚本、PDF 留位）。可空是回填中间态，M1-G 一并收紧。
    media_type: Mapped[str | None] = mapped_column(String(16), nullable=True)

    # ------------------------------------------------------------------ 生成与候选（M3）
    # 内容是怎么来的：`upload`（人上传/归档导入）/ `generate`（AI 产物）。
    # **不可改**：改它等于改写"这份东西是不是 AI 生成的"，而它正是合规标识的依据。
    # 与 `source` 是两个问题：后者说"哪条流水线登记了它"（恒为 archive）。
    origin: Mapped[str] = mapped_column(String(16), default="upload", nullable=False)

    # 生命周期：`candidate`（刚生成、待确认）/ `adopted`（已成为素材）
    # / `discarded`（人工弃用）/ `expired`（超时未确认，**不复活**）。
    # 只有 `adopted` 能被主体引用、被模板消费、打标签、进向量索引——
    # 四条写路径各判一次，因为漏一处就漏一个口子。
    # 默认 `adopted`：历史行与上传都是"已确认"，候选必须显式声明。
    stage: Mapped[str] = mapped_column(String(16), default="adopted", nullable=False)

    # 生成型必填。为空 = 不是生成产物，或配方还没回填（doctor 会报后者）
    recipe_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    # 采纳时间。**不建 `adopted_by`**：采纳者恒等于生成者（A4），
    # 多一列只会多一个可以互相矛盾的字段
    adopted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # 合规四档（B2）。上传件默认 `approved`（不是 AI 产物，无需审核）；
    # 生成件默认 `pending`——**默认值刻意选"没通过"**，这样漏写审核的路径
    # 会在发布主体时被拦住，而不是悄悄放行。
    compliance_status: Mapped[str] = mapped_column(
        String(16), default="pending", nullable=False
    )
    # 驳回理由走枚举 + 自由文本：枚举能让统计聚合，自由文本才说得清"这张手画成了六根手指"
    compliance_note: Mapped[str] = mapped_column(Text, default="", nullable=False)
    reviewed_by: Mapped[str | None] = mapped_column(String(32), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # 只读投影：`blob_id` 为空时访问它是 None，调用方必须回落旧列。
    # `lazy="selectin"` 而不是默认的 lazy：异步会话里隐式懒加载会抛
    # `MissingGreenlet`，而那个报错离「少写一个 options()」有十层调用栈。
    blob: Mapped[Blob | None] = relationship(
        Blob,
        primaryjoin="foreign(Asset.blob_id) == Blob.id",
        lazy="selectin",
        viewonly=True,
    )

    # ------------------------------------------------------------------ 共享 / 回收站
    # 这三列原先只存在 `catalog/assets/*.json` 里（归档盘上的第二份真相）。
    # 索引搬进 DB 之后必须跟着搬，否则「列表按 DB、可见性按 JSON」，删掉的素材
    # 会从另一条路径复活。`state_version` 是乐观锁：改名/分享/删除都要带版本，
    # 客户端拿旧版本重试时能分清「响应丢了」和「别人改过」。
    shared: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    shared_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    state_version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    __table_args__ = (
        Index("ix_assets_owner_type", "owner_id", "type"),
        # 列表分页的排序键是 (created_at desc, id desc)，索引与它同序才不用额外排序
        Index("ix_assets_owner_created", "owner_id", "created_at"),
        # 公共原片池的查询形状：`category='source' AND shared AND deleted_at IS NULL`
        # 是全表范围扫描（不带 owner），补一条部分索引，避免它退化成全表扫。
        Index(
            "ix_assets_shared_source",
            "category",
            "created_at",
            sqlite_where=text("shared = 1 AND deleted_at IS NULL"),
            postgresql_where=text("shared AND deleted_at IS NULL"),
        ),
        # 同 owner 内按内容去重，口径**必须与 `ArchiveService._find_by_digest` 一致**：
        # `(owner_id, sha256, category)`，且只看未删除的行。
        #
        # 这条索引曾经是 `UNIQUE (owner_id, sha256) WHERE source != 'archive'`——
        # 那前提是「网页上传记 upload、盘上登记记 archive」。接口合并后所有写入都记
        # archive，于是**从上线起它一行都没进过**：看着在防并发重复，实际没有任何约束。
        #
        # 三个条件缺一不可：
        # - `category`：同一张图同时当人物参考与产品参考是正常用法，合并会误伤；
        # - `deleted_at IS NULL`：回收站里的旧记录不该挡住「重新上传同一个文件」；
        # - `sha256` 可为 NULL：摘要算不出来的行不参与（NULL 互不相同）。
        Index(
            "uq_assets_owner_sha256",
            "owner_id",
            "sha256",
            "category",
            unique=True,
            sqlite_where=text("deleted_at IS NULL"),
            postgresql_where=text("deleted_at IS NULL"),
        ),
        # 按库浏览的主查询形状：`WHERE visibility=? AND team_id=? ORDER BY created_at DESC`。
        # 不带这条索引时，「团队库」的每次翻页都要扫全表——而团队库会先于个人库长大。
        Index("ix_assets_library", "visibility", "team_id", "created_at"),
        # 分类条的计数与筛选：`WHERE category_id=?`，同样按时间倒序。
        Index("ix_assets_category_created", "category_id", "created_at"),
        # 候选确认区的主查询形状：`WHERE owner_id=? AND stage='candidate' ORDER BY created_at`。
        # 不带这条索引时，每打开一次素材页都要把个人库全扫一遍——而候选只是其中
        # 极少数几行，代价与收益完全不成比例。
        Index("ix_assets_owner_stage", "owner_id", "stage", "created_at"),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Asset {self.id} {self.type} {self.name[:16]!r}>"
