"""主体（成组资产）：把「同一个产品的五张图」当成一个可调用的对象。

## 为什么主体在 `asset` 模块而不是 `template`

主体是**素材的聚合**，不是模板的一部分：它的成员是素材、它的可见域与分类都来自
素材库、它被删掉时成员一条都不能少（铁律①）。模板只是**消费者**——模板通过槽位
按角色要主体。把主体放 template，会让「删一个模板包」和「删一组素材」变成同一件事，
而那两件事的风险差着一个数量级。

## 与现有 `template_packs` 的关系

现有 pack 已经是一个"1–10 张图 + 主图 + 固定参数 + 发布版本"的东西——它其实就是主体，
只是当年长在模板模块里。M2.5 把它平移过来：

| 旧 | 新 |
| --- | --- |
| `template_packs` | `subjects` |
| `template_pack_references` | `subject_members`（按 `position` 填 `sort_order`） |
| 主图 `cover_asset_id` | 同名字段 |
| `template_pack_versions` | `subject_versions`（`members` 存 JSON 快照） |
| `kind` | `subject_type` |
| `required_roles` | `requires`（角色 + 能力两段） |

`subject_legacy_packs` 是 B5 的**历史链接保险**：外部系统与旧书签按 `packId` 访问，
这张表让它们还能解析到主体。它只增不删，等所有调用方切完（M5 之后）再清。

## 为什么不建外键

与 `library_models.py` 同一个理由：SQLite 默认关外键、`batch_alter_table` 重建表时
外键会挡路。更重要的是「删主体不删成员」——写成 `ON DELETE CASCADE` 正好相反。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base, UUIDMixin, utcnow

__all__ = [
    "SUBJECT_STATUSES",
    "SUBJECT_TYPES",
    "Subject",
    "SubjectLegacyPack",
    "SubjectMember",
    "SubjectVersion",
]

#: 主体类型。与模板槽位的 `subject_type` 同源（产品 / 人物 / 场景 / 道具）。
SUBJECT_TYPES = ("product", "character", "scene", "prop")

#: draft（组装中）/ published（可被模板调用）/ archived（下架，历史版本仍可读）
SUBJECT_STATUSES = ("draft", "published", "archived")


class Subject(Base, UUIDMixin):
    """一组素材的 head。草稿与已发布共用一行，靠 `status` 区分。

    与 `template_packs` 同构：`revision` 是乐观锁（每次保存 +1），`current_version`
    是已发布版本号（只在发布时 +1）。两者必须分清——混用会让「保存了但没发布」
    把版本号推上去，而模板缓存按版本号失效。
    """

    __tablename__ = "subjects"

    subject_type: Mapped[str] = mapped_column(String(16), default="product", nullable=False)
    name: Mapped[str] = mapped_column(String(160), default="", nullable=False)

    # 归属：一次判定「在哪个库」。`visibility` 与 `team_id` 同 `assets` 的语义
    visibility: Mapped[str] = mapped_column(String(16), default="personal", nullable=False)
    team_id: Mapped[str] = mapped_column(String(32), default="", nullable=False)
    # 分类树上的一级分类；空串表示未归类（与素材一致，不建 NULL 分支）
    category_id: Mapped[str] = mapped_column(String(32), default="", nullable=False)
    owner_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)

    cover_asset_id: Mapped[str] = mapped_column(String(32), default="", nullable=False)
    # 自由属性（材质 / 尺寸 / 卖点…）。只整块读写，不按字段查
    attributes: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    # 发布前必须满足的能力（`generate` / `download` / `derive`）。
    # 与 `required_roles` 分开：角色是"有谁"，能力是"能做什么"，
    # 合成一个可比较的枚举就会被迫在"批量抬升成员"和"放弃校验"之间二选一。
    requires: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    required_roles: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)

    status: Mapped[str] = mapped_column(String(16), default="draft", nullable=False, index=True)
    current_version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    revision: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )

    __table_args__ = (
        # 「我在管理的」列表形状：owner + 未归档，按更新时间倒序
        Index(
            "ix_subjects_owner_active",
            "owner_id",
            "updated_at",
            sqlite_where=text("archived_at IS NULL"),
            postgresql_where=text("archived_at IS NULL"),
        ),
        # 「可调用的」列表形状：已发布 + 按类型过滤。模板 prepare 走这条
        Index(
            "ix_subjects_public",
            "subject_type",
            "published_at",
            sqlite_where=text("status = 'published' AND archived_at IS NULL"),
            postgresql_where=text("status = 'published' AND archived_at IS NULL"),
        ),
    )


class SubjectMember(Base, UUIDMixin):
    """主体里的一条素材。

    `sort_order` 而不是只存数组：提示词编译器按顺序编号，而顺序是人工摆的
    （"第一张永远是正面图"）。存 JSON 数组会让"调换顺序"变成一次整列重写，
    也没法按 `asset_id` 建索引——而索引正是"删素材前问还有谁在用"要的东西。

    `grants` 是**这张图**允许的能力：`generate`（可被读进模型）/ `download`
    （可下载原件）/ `derive`（可被衍生）。主体的能力取成员的交集，
    所以 `generate` 一旦有成员不给，整个主体就不能生成——这是木桶最短边，
    取并集会等于"公开一个产品 = 公开它全部参考原片"。
    """

    __tablename__ = "subject_members"

    subject_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    asset_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    # 角色（front / side / detail…），词表沿用模板的 `pack_roles`
    role: Mapped[str] = mapped_column(String(24), default="", nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    grants: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    # 是否参与最终输出。参考图可以不进输出（只给模型看），
    # 而"参与输出"决定它的能力是否压主体的交集
    in_output: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    added_by: Mapped[str] = mapped_column(String(32), default="", nullable=False)
    added_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    __table_args__ = (
        # 「同一素材在同一主体里只能有一条」：重复会出现两个编号指向同一张图，
        # 提示词里写着 reference-1 / reference-3，而模型看到的是同一张
        UniqueConstraint("subject_id", "asset_id", name="uq_subject_members_pair"),
        UniqueConstraint("subject_id", "sort_order", name="uq_subject_members_order"),
        # 反查「这张图还被谁用着」——删素材前必须问的那一句
        Index("ix_subject_members_asset", "asset_id", "subject_id"),
    )


class SubjectVersion(Base, UUIDMixin):
    """已发布主体的不可变快照。

    **唯一定义就是 CAS**：两个实例同时想写 v3，`UNIQUE(subject_id, version)`
    只让一个成功，另一个拿到 `IntegrityError` 后重试 v4。

    `members` 存 JSON 而不是指向 `subject_members`：版本是**冻结证据**，
    发布之后有人改了成员、甚至删了那张图，这一版仍然要能回答"当时是哪几张、
    什么顺序、什么能力"。指向活表的话，改一次成员就让历史版本改了口。
    """

    __tablename__ = "subject_versions"

    subject_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)

    #: `[{asset_id, sha256, role, grants, in_output, sort_order}]`，顺序即编号顺序
    members: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list, nullable=False)
    cover_asset_id: Mapped[str] = mapped_column(String(32), default="", nullable=False)
    attributes: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    requires: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    required_roles: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)

    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    published_by: Mapped[str] = mapped_column(String(32), default="", nullable=False)

    __table_args__ = (
        # 这条索引同时是「版本号不被覆盖」的保证，别删
        Index("uq_subject_versions_number", "subject_id", "version", unique=True),
    )


class SubjectLegacyPack(Base, UUIDMixin):
    """旧 `packId` → 新 `subject_id` 的解析表（B5）。

    外部系统、旧书签、别人贴的工单里全是 `packId`。这张表让它们还能打开，
    **只增不删**：删了就等于让一批历史链接 404，而 404 不会被当成"迁移完成"，
    只会被当成"系统坏了"。

    `pack_id` 加唯一索引：一个旧包只能映射到一个主体（重复映射会让同一个链接
    在不同时刻打开不同东西，那比 404 更难查）。
    """

    __tablename__ = "subject_legacy_packs"

    pack_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    subject_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    note: Mapped[str] = mapped_column(Text, default="", nullable=False)
