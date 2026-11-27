"""模板封装的 ORM 实体：模板规格（head / 发布快照 / 槽位）。

## 只剩一套表

2026-11-14 P3 终局：素材包（`template_packs` 四表）、视频模板（`video_templates`）、
manifest 配置（`template_manifests` 四表）与替换调用档案（`replacement_runs` 三表）
全部下线，模板只存在于 `template_specs`。它们是同一次「统一到 task」的几半：

- 素材包 → 资产库封装（`kind=pack`）；
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
    Boolean,
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
    "CASE_STATUSES",
    "SPEC_STATUSES",
    "TemplateCase",
    "TemplateSpec",
    "TemplateSpecSlot",
    "TemplateSpecVersion",
]

#: 规格生命周期。`disabled` 是"下架"，不是删除：历史版本与已生成任务照旧可查。
SPEC_STATUSES = ("draft", "published", "disabled")

#: 案例生命周期。`offline` 是"下架"，不是删除：下架后案例不出现在复用选择器里，
#: 但管理界面仍然看得到、也能重新上架。
CASE_STATUSES = ("published", "offline")


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
    #: 使用模板时**允许选用的模型清单**（发布时冻结）。空 = 只有 `model` 那一个。
    #:
    #: 为什么进发布快照而不是只存在草稿里：使用者读的是**发布版本**，清单是
    #: 「这份模板能跑在哪几款模型上」的一部分，跟着版本走才对 —— 草稿改了清单
    #: 但没发版，使用者看到的就还是旧清单。
    allowed_models: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    params: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    #: 整段提示词。**不分固定段 / 可编辑段**（2026-11-14）：模板库不改文字，
    #: 需要可变的地方用 `text` 槽位（编译成 `{{Text N}}` 由执行层注入正文）。
    prompt: Mapped[str] = mapped_column(Text, default="", nullable=False)
    #: 「随机」档用的整段提示词。空 = 该模板不支持随机档（前端把档位置灰）。
    #:
    #: 为什么进发布快照：它和使用者的「随机档」选择直接相关，跟着版本走才对 ——
    #: 草稿改了随机档提示词但没发版，使用者拿到的应该还是已发布版本的那一段。
    random_prompt: Mapped[str] = mapped_column(Text, default="", nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    published_by: Mapped[str] = mapped_column(String(32), default="", nullable=False)
    published_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )


class TemplateSpecSlot(Base, UUIDMixin):
    """某个发布版本的一个槽位声明。

    `position` 就是 `{{Mixed N}}` 的顺序；**这里不存编号**（编号由编译按实际填充
    数量算出来，见 `core/template_spec/compile.py`）。

    **`group_key` 同时决定素材从哪来**：空 → 从候选素材里挑；非空 → 这一组
    共用一次「选包」，只能从封装包取。所以没有单独的来源列 ——
    2026-11-20 删掉了 `pick_mode`：它与 `group_key` 表达同一件事，两处并存时
    会出现「在容器里、却声明从素材取」这种没法解释的组合。（更早的 2026-11-14
    还删过一根 `source`，那根与 `type` 抢同一件事。）
    `tag_ids` 只是候选集范围。
    """

    __tablename__ = "template_spec_slots"
    __table_args__ = (
        UniqueConstraint("version_id", "slot_key", name="uq_template_spec_slots_key"),
        UniqueConstraint("version_id", "position", name="uq_template_spec_slots_position"),
    )

    version_id: Mapped[str] = mapped_column(String(32), nullable=False)
    slot_key: Mapped[str] = mapped_column(String(40), nullable=False)
    #: `image` | `video` | `audio` | `text`（= 资产库的 kind）
    type: Mapped[str] = mapped_column(String(16), nullable=False)
    #: `fixed` | `user`
    binding: Mapped[str] = mapped_column(String(16), default="user", nullable=False)
    #: `mixed` | `attachment`
    ref_mode: Mapped[str] = mapped_column(String(16), default="mixed", nullable=False)
    #: 所属封装容器（key；空 = 独立槽位）。**列名带 `_key`**：`group` 在 SQL 里是
    #: 保留字，裸用会在某些方言上要加引号。
    group_key: Mapped[str] = mapped_column(String(40), default="", nullable=False)
    #: 容器名。**组内每一行都写同一个值**（冗余）：发布快照只有槽位行，没有
    #: 「容器表」；只写组内第一行会依赖 `position` 顺序，拖一下顺序就取不到
    #: 名字。读取时取首个非空值（`spec_service._group_outs`），所以被手改过一行
    #: 的坏数据也有确定行为。老版本（列是后加的）为空串 → 前端回退成
    #: 「封装槽位 N」。
    group_label: Mapped[str] = mapped_column(String(80), default="", nullable=False)
    min_count: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    max_count: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    label: Mapped[str] = mapped_column(String(80), default="", nullable=False)
    asset_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    #: 候选集的标签 id（任意级别；空 = 不限）。
    #:
    #: `tag_id` 是**老形状**（单个标签），保留着是为了让旧发布版本还能被读出来：
    #: 新代码读 `tag_ids`，读不到时退回 `tag_id`（见 `spec_service._slot_out`）。
    #: 它不再单独承担语义 —— 新写入的行两列都会填（`tag_id` 记第一个标签，
    #: 纯粹是为了降级时旧代码还能看懂）。
    tag_ids: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    tag_id: Mapped[str] = mapped_column(String(32), default="", nullable=False)
    #: **选中时文案**：使用者填了素材时，提示词里这一段怎么说（可含自引用）。
    #: 空 = 直接插 `{{Mixed N}}`（旧行为）。
    filled_text: Mapped[str] = mapped_column(String(2000), default="", nullable=False)
    #: **未选时文案**：使用者没填素材时这一段怎么说（纯提示词，不含槽位引用）。
    #: 空 = 编译期报错（可选槽位且被引用时，发布闸门就会拦）。
    empty_text: Mapped[str] = mapped_column(String(2000), default="", nullable=False)
    #: 「未选时文案」是否来自正文里的框选段（2026-11-27）。
    #:
    #: 老数据一律 False → 预检会要求作者在正文里重新框选一次（见
    #: core/template_spec/rules.py::validate_optional_slot_refs）。
    empty_text_from_anchor: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)


class TemplateCase(Base, UUIDMixin, TimestampMixin):
    """模板案例（`template_cases`）：把一份模板发布成**可复用**的案例。

    ## 和素材库「案例池」不是一回事

    素材库那边也有「案例」（`library_assets.purpose = case`）：那是**一条素材**
    （图片/视频）被标出来当模板卡片的封面与悬停视频 —— 它决定卡片长什么样。

    这里的案例是**一份模板配置**：名称 + 描述 + 分类 + 关联的模板 + 配置快照，
    别人可以拿它当起点复制一份新模板。两个概念都叫「案例」，但一个是素材，
    一个是配置快照，权限码也分开（`library:case:*` / `template_pack:case:*`）。

    ## 案例与模板的关联关系

    **N:1** —— 多个案例可以来自同一个模板；每个案例恰好关联一个模板。

    案例里存的是**发布那一刻的模板配置快照**（`config`），不是活引用：

    - 模板之后改了什么，**已发布的案例不受影响** —— 复用者拿到的一定是当初那份；
    - 反过来，案例被删 / 被改，也不会碰到模板本体。

    `spec_id` / `spec_key` / `spec_version` 只是**回溯线索**（"它来自哪个模板的哪一版"），
    不参与复用逻辑；`spec_key` 在模板改名后依然指向同一个模板（key 才是身份）。

    模板被软删时案例**不跟着删**：案例带着自己的快照照样能复用，只是界面上要
    标一句"来源模板已删除"（`sourceMissing`）。

    ## 状态机

    ```
    published（已发布，可被复用）
      └── offline（下架：不可复用，但可重新上架；管理界面里看得到）
    ```

    删除是**软删**（`deleted_at`），不是物理删除 —— 与模板一致（`template_specs`
    也是软删 + 回收站）。案例没有别的表引用，删了不会留下悬空引用。
    """

    __tablename__ = "template_cases"
    __table_args__ = (
        Index("ix_template_cases_owner_status", "owner_id", "status"),
        Index("ix_template_cases_category", "category"),
    )

    #: 案例名。**独立于模板名**：同一个模板可以发布成多个案例
    #: （「赛博朋克广告片」「赛博朋克短剧」共用一份配置）。
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    #: 分类。空串 = 未分类（前端归到「未分类」）。
    category: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    #: 来源模板（回溯用，不建外键 —— 全项目惯例）。
    spec_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    #: 来源模板的 key。模板改名不影响它。
    spec_key: Mapped[str] = mapped_column(String(80), nullable=False)
    #: 发布案例时来源模板的版本号；0 = 模板从未发版过（用的是草稿）。
    spec_version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    #: 模板配置的快照（camelCase，与 `TemplateSpecIn` 同形）。
    config: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    #: `published` | `offline`
    status: Mapped[str] = mapped_column(String(16), default="published", nullable=False, index=True)
    #: 发布者。
    owner_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    #: 最近一次下架时间；重新上架时清空。
    offline_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    #: 软删时间。非空 = 在回收站里，列表与复用选择器都看不到它。
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
