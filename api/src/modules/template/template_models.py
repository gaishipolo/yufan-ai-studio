"""素材模板实体：模板 head + 不可变版本 + 试跑记录（M3.5）。

## 与「封装」（`template_packs`）的关系

封装是**上一代**：它只有"一堆参考图 + 逐图用途"，没有槽位声明、没有参考视频、
没有试跑。模板是它的继任者，`pack_id → subject_id` 的映射已经由 M2.5 建好，
所以这一版**不迁移封装数据**：旧封装的读路径照旧，模板从空表开始。

表名带 `asset_` 前缀是**产品归属**（它属于素材资产库，不属替换流水线），
放在 `modules/template` 是**代码归属**（角色词表、编辑器、发布校验都在这里）。
两者一致才叫"同类"，而这两件事的答案正好不同——写在这里免得下次搬来搬去。

## 三张表不能合并

| 表 | 回答什么问题 | 为什么不能并进 head |
| --- | --- | --- |
| `asset_templates` | 这个模板**现在**长什么样 | 当前状态，会被反复修改 |
| `asset_template_versions` | 它在 v2 时是什么样 | 已发布版本不可变；已提交任务要能复现旧快照 |
| `template_try_runs` | 上线前跑过没有、结论如何 | 它是**证据**，发布门槛要查；进 head 只能存一条 |

## 为什么版本存 JSON 快照

版本是冻结证据：只整块读、从不按字段查。拆成"版本 + 版本槽位 + 版本成员"
三张表会让发布变成一次多表插入，而它本来就该是一次原子写。
（封装当年正是拆了，因为 `reference_ids` 要按 `asset_id` 反查"谁在用它"。
模板不引参考图，它引的是**主体**——反查走主体那张表，这里没有这个需求。）
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, Float, Index, Integer, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base, TimestampMixin, UUIDMixin

__all__ = [
    "TEMPLATE_STATUSES",
    "TRY_RUN_VERDICTS",
    "AssetTemplate",
    "AssetTemplateVersion",
    "TemplateTryRun",
]

#: `disabled` 是运营主动下架，与"还没发布"（`current_version == 0`）不是一回事。
TEMPLATE_STATUSES = ("draft", "published", "disabled")

#: 试跑结论。只有 `pass` 能当作发布前置——`fail` 与"没跑过"在门槛上等价，
#: 但分开存，因为"跑过但失败了"要能看到失败原因。
TRY_RUN_VERDICTS = ("pass", "fail")


class AssetTemplate(Base, UUIDMixin, TimestampMixin):
    """模板 head：草稿与已发布共用一行。

    `revision` 是乐观锁（每次保存 +1），`current_version` 是已发布版本号
    （只在发布时 +1）。两者必须分清：合并成一个字段后，"保存草稿"会顺手
    把一个正在被任务引用的版本号改掉。
    """

    __tablename__ = "asset_templates"

    owner_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(160), default="", nullable=False)
    #: character / product / scene，决定角色词表（见 `pack_roles.PACK_ROLES`）
    kind: Mapped[str] = mapped_column(String(16), nullable=False, index=True)

    #: 参考视频：模板的运动段落标注与镜头时长都以它为准。可空——先写提示词再绑视频
    #: 是真实的新建顺序，强制非空只会逼用户先上传一个"随便什么"。
    reference_video_asset_id: Mapped[str | None] = mapped_column(String(32), nullable=True)

    #: 提示词模板，`{{role:front}}` 这类占位符在编译时替换成该角色的成员图说明
    prompt_template: Mapped[str] = mapped_column(Text, default="", nullable=False)
    #: 生成参数（时长、比例、模型偏好）。不进槽位：它不参与"够不够成员"的判定
    params: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    #: 槽位声明。形状由 `slots.Slots.parse` 保证，这里不加 CheckConstraint：
    #: 跨 sqlite/postgres 的 JSON 约束写法不一致，而校验必须在写入前发生
    slots: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)

    status: Mapped[str] = mapped_column(String(16), default="draft", nullable=False, index=True)
    current_version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    revision: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        # 「我维护的模板」：owner + 未删除，按更新时间倒序
        Index(
            "ix_asset_templates_owner_active",
            "owner_id",
            "updated_at",
            sqlite_where=text("deleted_at IS NULL"),
            postgresql_where=text("deleted_at IS NULL"),
        ),
        # 「可用的模板」：已发布 + 未删除，按类型过滤
        Index(
            "ix_asset_templates_public",
            "kind",
            "published_at",
            sqlite_where=text("status = 'published' AND deleted_at IS NULL"),
            postgresql_where=text("status = 'published' AND deleted_at IS NULL"),
        ),
        # 「参考视频失效检测」按 asset_id 反查：视频被删时要能列出受影响的模板
        Index("ix_asset_templates_video", "reference_video_asset_id"),
    )


class AssetTemplateVersion(Base, UUIDMixin, TimestampMixin):
    """已发布模板的不可变快照。

    `UNIQUE(template_id, version)` 就是 CAS：两个实例同时想写 v3，数据库只让
    一个成功，另一个拿到 IntegrityError 后重试 v4。与封装的版本行同一套做法。

    快照里连 `slots` 一起冻结：**改槽位等于发新版本**。否则"上周能跑的模板
    今天跑不了了"，而任务表里那条 v3 记录还在——没人能解释为什么。
    """

    __tablename__ = "asset_template_versions"

    template_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)

    #: 冻结的完整快照（name / kind / reference_video_asset_id / prompt_template /
    #: params / slots）。整块存，只整块读
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    #: 发布时冻结的校验结论：必需角色当时由哪些主体满足。事后排查"为什么这个
    #: 模板突然没人可用"时，这是唯一能回答"当时行不行"的证据
    capability_note: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)

    published_by: Mapped[str] = mapped_column(String(32), default="", nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        # 这条索引同时是「版本号不被覆盖」的保证，别删。
        Index("uq_asset_template_versions_number", "template_id", "version", unique=True),
    )


class TemplateTryRun(Base, UUIDMixin, TimestampMixin):
    """一次试跑。

    试跑是**发布门槛的证据**，不是运行日志：它有独立的团队额度子项、产物进
    `draft` 桶、不写业务用量。所以要记录"谁跑的、用哪个主体、当时哪一版、
    结果哪几张、结论 pass/fail"——发布时只查 `verdict == 'pass'` 且
    `template_version == 当前版本`。

    版本号必须一起存：v2 跑过 pass 不能让 v3 直接发布，那是两套槽位。
    """

    __tablename__ = "template_try_runs"

    template_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    #: 试跑时模板的 `current_version`。0 表示还没发布过（试跑允许在草稿态做）
    template_version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    #: 用哪个主体跑的。为空表示"直接按临时拼的成员跑"（调试用，不计入门槛）
    subject_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)

    result_asset_ids: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    verdict: Mapped[str] = mapped_column(String(8), default="fail", nullable=False)
    note: Mapped[str] = mapped_column(Text, default="", nullable=False)
    # 试跑花的额度要能看到，否则「团队额度怎么少了」又要靠对账
    cost_units: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)

    created_by: Mapped[str] = mapped_column(String(32), default="", nullable=False)
    __table_args__ = (
        # 「这个模板跑过几次、最近一次什么结论」：按模板取最近几条
        Index("ix_template_try_runs_recent", "template_id", "created_at"),
    )
