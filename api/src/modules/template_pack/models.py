"""模板封装的 ORM 实体：模板规格（head / 发布快照 / 槽位）。

## 只剩一套表

2026-11-14 P3 终局：素材包（`template_packs` 四表）、视频模板（`video_templates`）、
manifest 配置（`template_manifests` 四表）与替换调用档案（`replacement_runs` 三表）
全部下线，模板只存在于 `template_specs`。它们是同一次「统一到 task」的几半：

- 素材包 → 资产库封装（`kind=pack`），模板用 `type=pack` 槽位引用它的 id；
- 视频模板 / manifest 功能 → 一条 spec；
- 替换专用编排 → 普通 `POST /tasks` + 声明了 `replacement` 的模型。

所以这里没有「配置层 / 投影层」两层了：**发布快照本身就是运行侧的唯一事实**。

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
    DateTime,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base, TimestampMixin, UUIDMixin, utcnow

__all__ = [
    "SPEC_STATUSES",
    "TemplateSpec",
    "TemplateSpecSlot",
    "TemplateSpecVersion",
]

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
    永远看到它发布那一刻的原文（`tests/test_template_spec_api.py` 钉这条）。
    展示案例（`demo_asset_id`）**不在这里**：它只影响卡片长什么样，改图不该要求
    发新版本 —— 所以它只存在 head 的草稿里。
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
    #: 整段提示词。**不分固定段 / 可编辑段**（2026-11-14）：模板库不改文字，
    #: 需要可变的地方用 `text` 槽位（编译成 `{{Text N}}` 由执行层注入正文）。
    prompt: Mapped[str] = mapped_column(Text, default="", nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    published_by: Mapped[str] = mapped_column(String(32), default="", nullable=False)
    published_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )


class TemplateSpecSlot(Base, UUIDMixin):
    """某个发布版本的一个槽位声明。

    `position` 就是 `{{Mixed N}}` 的顺序；**这里不存编号**（编号由编译按实际填充
    数量算出来，见 `core/template_spec/compile.py`）。

    `type=pack` 时只存 `pack_id`，成员与角色留在资产库 —— 模板不复制成员清单，
    否则库改了成员模板还按旧的算（§12 不变量 9）。

    **没有 `source` 列**（2026-11-14 删）：`type=pack` 就是"要一个封装"，两根轴
    表达同一件事时接口能收到自相矛盾的组合。`tag_id` 只是候选集范围。
    """

    __tablename__ = "template_spec_slots"
    __table_args__ = (
        UniqueConstraint("version_id", "slot_key", name="uq_template_spec_slots_key"),
        UniqueConstraint("version_id", "position", name="uq_template_spec_slots_position"),
    )

    version_id: Mapped[str] = mapped_column(String(32), nullable=False)
    slot_key: Mapped[str] = mapped_column(String(40), nullable=False)
    #: `image` | `video` | `audio` | `text` | `pack`（= 资产库的 kind）
    type: Mapped[str] = mapped_column(String(16), nullable=False)
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
    #: 一级标签 id（候选集范围）。空串 = 不限。
    tag_id: Mapped[str] = mapped_column(String(32), default="", nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
