"""素材资产 ORM 实体。

表是**素材的唯一索引**：上传的个人素材、归档库导入的模板素材、生成产物都落在这里，
不再有第二份 JSON 目录当真相源。文件本身在对象存储里，`storage_key` 是相对 key，
换 local / S3 只改驱动，不动数据。

两个封面字段不是重复：

- `cover_url`：**远端**封面。供应商返回的签名地址、seed 里的示例图属于这一类，
  它不由我们托管，也就不该被 `url_for()` 重新拼。
- `cover_key`：**我们托管的**缩略图对象 key（视频首帧、图片降采样）。

出参按「有 `cover_key` 就用它换 URL，否则退回 `cover_url`」合成 `coverUrl`。
这样切存储驱动时，托管封面跟着 key 走，远端封面也不会因为换桶而集体失效。
"""

from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    Boolean,
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

    # 来源：upload（网页上传）/ archive（归档库导入）/ generated（生成产物）。
    # 它不参与鉴权，只用来回答「这条记录是怎么来的」和做运营统计。
    source: Mapped[str] = mapped_column(String(16), default="upload", nullable=False)

    # 被模板/任务引用的次数。仅供列表展示；**删除时的引用校验不读它**
    # （计数会漂移，真正的判定走 `api/ports.py` 的注入口）。
    ref_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    owner_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)

    __table_args__ = (
        Index("ix_assets_owner_type", "owner_id", "type"),
        # 列表分页的排序键是 (created_at desc, id desc)，索引与它同序才不用额外排序
        Index("ix_assets_owner_created", "owner_id", "created_at"),
        # 同 owner 内按内容去重，**只约束非归档来源**：
        # 归档登记的是「盘上已经存在的文件」，它与同一个人上传的同一份文件是两条
        # 真实记录（两份物理副本、两套 URL）。用全表唯一约束会把登记直接拒掉。
        # 带 `where` 的部分索引 SQLite 与 PostgreSQL 都支持。
        Index(
            "uq_assets_owner_sha256",
            "owner_id",
            "sha256",
            unique=True,
            sqlite_where=text("source != 'archive'"),
            postgresql_where=text("source != 'archive'"),
        ),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Asset {self.id} {self.type} {self.name[:16]!r}>"
