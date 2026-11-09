"""受控标签词表、素材关联与保存视图。

与 `library_models.py` 分开：那三张表是「库的骨架」（团队 / 字节 / 分类），
这三张是「组织与检索的辅助层」，改它们的理由不一样，混在一个文件里会让
「为什么这张表没有外键」这类说明被稀释。

## 为什么标签不存 `use_count`

`use_count` 是**派生值**，不是事实。存下来之后有两条路径会让它对不上：

1. 素材进回收站 / 被恢复——关联行还在，但「在用」的定义变了；
2. 标签合并、关联被批量改写——一次 `UPDATE` 漏掉计数就永久偏了。

而它要回答的问题（新词用满 5 次才进筛选条常显区、90 天零使用提示归档）
都可以用一条 `GROUP BY` 算出来。46 个词 × 几十万条关联的 `GROUP BY` 是
索引扫描的量级，比「计数悄悄错了半年没人发现」便宜得多。
**该仓已经为计数器付过一次代价**（`assets.ref_count` 与真实引用数漂移），
所以这里只记事实：`last_used_at`（最后一次被打上的时间）。

## 为什么 `name` 全局唯一而不是 `(dimension, name)` 唯一

设计文档 §3.3 的要求是「同维度内唯一，且全局不重名」。全局唯一同时满足两条，
而且能挡住一个具体症状：允许跨维度同名时，筛选条会出现两个一模一样的分组标题
（「风格 / 写实」与「镜头 / 写实」），用户无从选择，点哪个都像点错了。

## 为什么合并要留 `merged_into`

保存视图里存的是 `tag_id`。两个同义词合并时若直接删掉旧行，沉淀在视图里的
旧 ID 会变成空结果——用户看到的是「我保存的筛选没了」，而不是「标签合并过」。
`merged_into` 让旧 ID 能一跳解析到新 ID，代价是一列字符串。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base, UUIDMixin, utcnow

__all__ = ["AssetTag", "AssetView", "Tag"]


class Tag(Base, UUIDMixin):
    """受控词表里的一个词。维度见 `tag_vocabulary.DIMENSIONS`。

    `status` 只有两个值：`active` / `retired`。**没有物理删除**：
    历史素材上的关联、历史筛选结果都要能解释，删行等于让它们静默改变。
    """

    __tablename__ = "tags"

    # style / scene / composition / shot / placement / content
    dimension: Mapped[str] = mapped_column(String(16), nullable=False)
    name: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="active", nullable=False)
    # manual（管理员新增）/ inferred（系统推断）。推断出来的词在卡片上要有区分标记
    source: Mapped[str] = mapped_column(String(16), default="manual", nullable=False)
    created_by: Mapped[str] = mapped_column(String(32), default="", nullable=False)
    # 最后一次被贴到素材上的时间。**不是计数**，只服务「90 天零使用提示归档」
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # 合并去向：被合并掉的词保留原行，筛选时一跳解析到新词
    merged_into: Mapped[str] = mapped_column(String(32), default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    __table_args__ = (
        # 全局唯一：见模块说明。同时隐含了「同维度内唯一」
        Index("uq_tags_name", "name", unique=True),
        # 筛选条按维度取词、按状态过滤
        Index("ix_tags_dimension_status", "dimension", "status"),
    )


class AssetTag(Base):
    """素材 ↔ 标签。复合主键即为「同一条素材不能重复打同一个词」的约束。"""

    __tablename__ = "asset_tags"

    asset_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    tag_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    added_by: Mapped[str] = mapped_column(String(32), default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    __table_args__ = (
        # 反查：`WHERE tag_id IN (...)` 算交集与计数都走这条
        Index("ix_asset_tags_tag_asset", "tag_id", "asset_id"),
    )


class AssetView(Base, UUIDMixin):
    """保存的筛选视图。**个人私有**，没有团队共享（共享会引出「谁改的」这条线）。"""

    __tablename__ = "asset_views"

    owner_id: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(40), nullable=False)
    # 原始筛选参数（JSON 字符串），不存 SQL：它是"把当时的条件再放回去"，
    # 不是"把当时的结果固定下来"。存 SQL 会在字段改名后变成一段无法维护的方言。
    query_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    sort: Mapped[str] = mapped_column(String(24), default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    __table_args__ = (
        # 同一个人的视图名不能重名，否则恢复时无法判断该用哪个
        Index("uq_asset_views_owner_name", "owner_id", "name", unique=True),
    )
