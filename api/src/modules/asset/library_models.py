"""素材库的「非素材」实体：团队、字节、分类。

与 `models.Asset` 分开成文件，是因为 `models.py` 已经是素材记录的领域说明
（两个封面字段为什么不是重复、`source` 为什么只剩一个写入路径），再塞三张表
会让那份说明读不下去。`models.py` 末尾 import 本模块，保证 `db.registry`
扫描 `modules.asset.models` 时这些表都进 `Base.metadata`。

## 为什么这里一个外键都没有

全仓的素材表都不用数据库外键（`assets.id` 被模板、任务按字符串引用）。
理由是这些表要跨 SQLite / PG 双跑，而 SQLite 的外键约束默认关闭、
`batch_alter_table` 重建表时外键还会挡路。约束由服务层与唯一索引承担，
删除顺序由服务显式编排——写成外键会让「删主体不删成员」这条铁律
变成一次级联删除，正好相反。

## 三个表的职责边界

- `teams` / `team_members`：**组织边界**。它决定「这个库归谁」，
  不决定「这条素材是什么」。
- `blobs`：**字节事实**。同一份字节只有一行，`sha256` 唯一；素材记录可以有多条
  指向它。这是「复制到另一个库不重复占空间」与「删除不影响别人」的共同前提。
- `categories`：**内容组织**。按库隔离（`scope` + `owner_key`），
  与权限无关——权限在顶栏切换器，不在分类树里。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Index,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base, UUIDMixin, utcnow

__all__ = ["Blob", "Category", "Team", "TeamMember"]


class Team(Base, UUIDMixin):
    """一级组织单元。首批三个（内容 / 运营 / 技术），id 来自 `core.teams`。

    **不做层级**：团队下面是人，不是子团队。真需要分组时用分类（`categories`），
    因为「组」是内容组织问题，而团队是权限边界——两者混在一起，
    就会出现「这个子团队能不能看父团队的素材」这种没有答案的问题。
    """

    __tablename__ = "teams"

    name: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    # 机器可读的短标识（content / ops / tech），前端按它选图标与默认分类树
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    # active / archived。归档的团队仍在库里（历史素材的可见域指向它），
    # 只是不再出现在切换器与新增选择里。
    status: Mapped[str] = mapped_column(String(16), default="active", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )


class TeamMember(Base, UUIDMixin):
    """人与团队的关系。一人可属多团队（A1 的结论）。

    `role` 是**团队内**角色：`admin` 能审批交付、管理成员；`member` 只能读写
    本团队库。它与系统角色（editor / viewer）正交——系统角色决定动作能力，
    团队角色决定作用范围，判定永远是「范围 + 动作」两元。
    """

    __tablename__ = "team_members"

    team_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    role: Mapped[str] = mapped_column(String(16), default="member", nullable=False)
    joined_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    __table_args__ = (
        # 「同一人在同一团队里只有一条关系」：重复加入会让成员数、审批人列表、
        # 权限合并三处口径全部错一遍，而症状只是「人数多了一个」。
        UniqueConstraint("team_id", "user_id", name="uq_team_members_team_user"),
    )


class Blob(Base, UUIDMixin):
    """一份字节。**同一份内容只有一行**，`sha256` 唯一。

    `storage_key` 与 `preview_key` 提到这一层，是因为它们由内容决定
    （`blobs/ab/abcdef…`），不属于任何一次归属。放在素材记录上时，
    同一份文件被两个人上传就会存两个 key（虽然指向同一份字节），
    换桶或迁移时要改的行数随归属数增长。

    `tier` 是**派生值**，不是写入时拍定的：取引用它的素材里最高的 stage
    （候选 → `draft`，采纳 → `final`）。采纳一张图不该搬一次字节——
    几十 MB 的视频挪一次桶就是一次失败点。
    """

    __tablename__ = "blobs"

    # 摘要可能算不出来（历史行、迁移脚本），所以可空；NULL 不参与唯一约束
    sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    size_bytes: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    mime_type: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    # 归档根下的相对 key（`blobs/ab/abcdef….mp4`），与对象存储不是同一棵树
    storage_key: Mapped[str] = mapped_column(String(256), default="", nullable=False)
    # 托管缩略图 key（视频首帧、图片降采样）；空串表示没派生成功
    preview_key: Mapped[str] = mapped_column(String(256), default="", nullable=False)
    # draft / final，由引用它的素材推导；`expires_at` 只在 draft 时有值
    tier: Mapped[str] = mapped_column(String(16), default="final", nullable=False)
    # 有多少条**未删除的**素材记录指向它。0 才是可回收；删除素材只减引用不删字节
    ref_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    __table_args__ = (
        # 部分唯一索引：`sha256` 为 NULL 的行不参与（NULL 互不相同）。
        # SQLite 与 PG 都支持 `WHERE`，两边条件必须写成分支参数而不是字符串，
        # 否则 SQLite 会把 `IS NOT NULL` 当成字符串常量比较，索引静默失效。
        Index(
            "uq_blobs_sha256",
            "sha256",
            unique=True,
            sqlite_where=text("sha256 IS NOT NULL"),
            postgresql_where=text("sha256 IS NOT NULL"),
        ),
        # 草稿回收任务按 (tier, expires_at) 扫，别让它退化成全表扫
        Index(
            "ix_blobs_expiring",
            "tier",
            "expires_at",
            sqlite_where=text("expires_at IS NOT NULL"),
            postgresql_where=text("expires_at IS NOT NULL"),
        ),
    )


class Category(Base, UUIDMixin):
    """内容分类。**每个库一棵树**，`scope` + `owner_key` 决定是哪一棵。

    | scope | owner_key | 是谁的分类树 |
    | --- | --- | --- |
    | `personal` | 用户 id | 个人库 |
    | `team` | 团队 id | 该团队库 |
    | `brand` | 空串 | 品牌公共库 |

    **不做 `parent_id`**：二级是「媒体类型」分面，不是真实节点（C1）。
    建了列，就一定有人往里插第三层，然后面包屑、计数、拖拽落点全部要重做。

    `is_inbox` 标记系统收件箱：每个库有且只有一个，不可改名/删除/归档。
    用真实行而不是 `category_id = NULL`，是为了让「只属于一个分类」成为
    强不变量——否则每个计数、每次移动都要多一个 NULL 分支，而「未分类」
    恰恰是最高频的入口。
    """

    __tablename__ = "categories"

    scope: Mapped[str] = mapped_column(String(16), nullable=False)
    # 个人库放 user_id、团队库放 team_id、公共库留空串。空串而不是 NULL：
    # 部分唯一索引里 NULL 互不相同，会把「公共库分类重名」放过去。
    owner_key: Mapped[str] = mapped_column(String(32), default="", nullable=False)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    # system（收件箱与五个初始用途）/ user（用户自建）。系统分类不可删
    kind: Mapped[str] = mapped_column(String(16), default="user", nullable=False)
    is_inbox: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # 归档而不是删除：分类消失了，里面的素材与历史统计还要能解释
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    __table_args__ = (
        # 同一棵树里不能重名。归档的分类不参与，否则「删掉重名分类」会失败。
        Index(
            "uq_categories_tree_name",
            "scope",
            "owner_key",
            "name",
            unique=True,
            sqlite_where=text("archived_at IS NULL"),
            postgresql_where=text("archived_at IS NULL"),
        ),
        # 每个库只能有一个收件箱——两棵收件箱会让「未分类 12」变成「6 + 6」，
        # 而用户不知道为什么两边都有一半。
        Index(
            "uq_categories_inbox",
            "scope",
            "owner_key",
            unique=True,
            sqlite_where=text("is_inbox = 1"),
            postgresql_where=text("is_inbox"),
        ),
        Index("ix_categories_tree", "scope", "owner_key", "sort_order"),
    )
