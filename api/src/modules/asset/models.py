"""素材资产 ORM 实体。

表是**素材的唯一索引**：用户上传的原片、从盘上登记的模板素材都落在这里，
不再有第二份 JSON 目录当真相源。文件本身在对象存储里，`storage_key` 是相对 key，
换 local / S3 只改驱动，不动数据。

**生成产物目前不在这张表里**——它落在运行工作区，入库这一步还没做
（`AssetSource.GENERATED` 是预留值，见 `schema.py`）。写查询时按「已入库」
理解本表，不要把「素材库」当成「所有用过的媒体文件」。

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
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base, TimestampMixin, UUIDMixin


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
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Asset {self.id} {self.type} {self.name[:16]!r}>"
