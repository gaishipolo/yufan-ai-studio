"""模板库 ORM 实体：封装（参考素材包）+ 替换运行。

**为什么在数据库里**：上一版把这些存在归档盘的 `catalog/packs/*.json` 与本机
`runs/<id>/run.json`，代价是三个说不清的问题——列表要逐个读盘、owner 过滤只能
在 Python 里做（旧 `list_runs` 的注释自己承认「留待单独一轮」）、多实例部署时
两个进程看到的「同一份封装」可以不一致。这三件事数据库都免费提供。

**为什么不用外键**：全项目没有一处 `ForeignKey`（见 `modules/task/models.py`），
靠字符串 id + 应用层校验。跨 sqlite/postgres 行为一致，且删记录不会级联炸掉
历史。这里沿用同一约定，靠复合索引保证唯一性。

**六张表的分工**：

| 表 | 回答什么问题 | 为什么不能合并 |
|---|---|---|
| `template_packs` | 这个封装现在长什么样（草稿与已发布共用一行） | 它是「当前状态」，会被反复修改 |
| `template_pack_references` | 这个封装引用了哪些素材 | 删素材前要问「还有谁在用」，不能扫 JSON |
| `template_pack_versions` | 这个封装在 v2 时是什么样 | 已发布版本按定义不可变，必须能独立存在 |
| `replacement_runs` | 这次替换进行到哪一步 | 状态机要按 status 查、按 created_at 排序 |
| `replacement_run_inputs` | 这次用了哪几张图、什么用途 | 重跑要能原样复原输入，不能只存 JSON 快照 |
| `replacement_reviews` | 用户怎么评价这次成片 | 评价要能按 version 做乐观锁 |
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, Float, Index, Integer, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base, TimestampMixin, UUIDMixin

# 封装生命周期。`disabled` 是运营主动下架，与「还没发布」（version=0）不是一回事。
PACK_STATUSES = ("draft", "published", "disabled")

# 替换运行的生命周期。前三个由 prepare 写，中间三个由排队/执行器写，
# 后四个是终态或需人工介入的状态。
# `submission_unknown` 单独存在的原因见 `replacement_service` 的 reconfirm：
# 它表示「上一次提交可能已经花了钱」，既不能当失败重提，也不能当成功。
RUN_STATUSES = (
    "preparing",
    "prepared",
    "prepare_failed",
    "queued",
    "waiting_free",
    "waiting_executor",
    "running",
    "success",
    "failed",
    "service_error",
    "submission_unknown",
)


class TemplatePack(Base, UUIDMixin, TimestampMixin):
    """封装 head：草稿与已发布共用一行，靠 `status` 区分。

    拆成「草稿表 + 发布表」会让每次保存都要同步两张表，而草稿的唯一用途就是
    被发布覆盖。合成一行的代价只有一个：`version` 与 `revision` 必须分清——
    `revision` 是乐观锁（每次保存 +1），`version` 是已发布版本号（只在发布时 +1）。
    """

    __tablename__ = "template_packs"

    kind: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(160), default="", nullable=False)
    owner_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)

    status: Mapped[str] = mapped_column(String(16), default="draft", nullable=False, index=True)
    # 已发布版本号，0 表示从未发布。发布时只增不减；停用不改它。
    version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # 乐观锁：前端提交 `expectedRevision`，对不上就是「别人改过了」。
    revision: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    features: Mapped[str] = mapped_column(Text, default="", nullable=False)
    instructions: Mapped[str] = mapped_column(Text, default="", nullable=False)
    wardrobe_mode: Mapped[str] = mapped_column(String(16), default="preserve", nullable=False)

    # 主图：为空时按 `position=0` 那张。显式存的原因是「换主图」不该等于
    # 「把那张图拖到第一位」——重排会改变提示词里的编号顺序。
    cover_asset_id: Mapped[str | None] = mapped_column(String(32), nullable=True)

    # 以下是**待发布状态**：草稿编辑中随手改，发布时整块冻结进版本行。
    # 放 head 而不放 reference 表：它们描述「整个包」而不是「某一张图」，
    # 且与版本行一一对应，发布时直接拷贝。
    required_roles: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    supported_scopes: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)

    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        # 「我管理的封装」列表形状：owner + 未删除，按更新时间倒序
        Index(
            "ix_template_packs_owner_active",
            "owner_id",
            "updated_at",
            sqlite_where=text("deleted_at IS NULL"),
            postgresql_where=text("deleted_at IS NULL"),
        ),
        # 「可用封装」列表形状：已发布 + 未删除，按类型过滤
        Index(
            "ix_template_packs_public",
            "kind",
            "published_at",
            sqlite_where=text("status = 'published' AND deleted_at IS NULL"),
            postgresql_where=text("status = 'published' AND deleted_at IS NULL"),
        ),
    )


class TemplatePackReference(Base, UUIDMixin):
    """封装里的一张参考图。

    `position` 而不是只存数组：`bindings` 与 `reference_ids` 必须**同序**，
    提示词编译器按这个顺序编号（`reference-1.jpg`、`reference-2.jpg`）。
    存 JSON 数组会让「调换主图」变成一次整列重写，且没法按 `asset_id` 建索引
    ——而索引正是「删除素材前问还有谁在用」要的东西。
    """

    __tablename__ = "template_pack_references"

    pack_id: Mapped[str] = mapped_column(String(32), nullable=False)
    asset_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    # 用途 key（front / multiview / detail…），词表在 modules/template/pack_roles.py
    role: Mapped[str] = mapped_column(String(24), default="", nullable=False)
    # 这张图在提示词里的补充说明，只写本次特有的事实
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)

    __table_args__ = (
        Index("uq_template_pack_references_position", "pack_id", "position", unique=True),
        Index("ix_template_pack_references_asset", "asset_id", "pack_id"),
    )


class TemplatePackVersion(Base, UUIDMixin, TimestampMixin):
    """已发布封装的不可变快照。

    **唯一性约束就是 CAS**：上一版靠 `storage.create()`（`If-None-Match: *`）
    原子抢版号，现在换成 `UNIQUE(pack_id, version)`——两个实例同时想写 v3，
    数据库只会让一个成功，另一个拿到 IntegrityError 后重试 v4。语义完全一致，
    但不再需要「抢版本文件」这种绕法。

    内容存 JSON 而不是拆子表：版本是**冻结证据**，只整块读、从不按字段查。
    拆表会让「发布」变成一次多表插入，而它本来就该是一次原子写。
    """

    __tablename__ = "template_pack_versions"

    pack_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)

    reference_ids: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    # 逐图用途，与 reference_ids 同序：[{role, description}, ...]
    bindings: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list, nullable=False)
    required_roles: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    supported_scopes: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)

    features: Mapped[str] = mapped_column(Text, default="", nullable=False)
    instructions: Mapped[str] = mapped_column(Text, default="", nullable=False)
    wardrobe_mode: Mapped[str] = mapped_column(String(16), default="preserve", nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        # 这条索引同时是「版本号不被覆盖」的保证，别删。
        Index("uq_template_pack_versions_number", "pack_id", "version", unique=True),
    )


class ReplacementRun(Base, UUIDMixin, TimestampMixin):
    """一次替换调用。

    `id` 就是前端传的 `request_id`（32 位 hex）：幂等边界落在主键上，
    同一个 `request_id` 重复提交只会命中同一行，不需要额外的去重键。

    `output_key` 与 `prompt` 这类大字段直接在行上：它们只被详情页读，
    不参与列表排序，拆表只会多一次 join。
    """

    __tablename__ = "replacement_runs"

    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    target: Mapped[str] = mapped_column(String(160), default="", nullable=False)
    owner_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)

    status: Mapped[str] = mapped_column(String(24), default="preparing", nullable=False)
    # 框架流水线的状态（queued/running/success/failed），与业务 status 分开记：
    # 业务要表达 `waiting_free` 这种「排队中但在等权益」的细分，框架只认四个值。
    framework_status: Mapped[str | None] = mapped_column(String(16), nullable=True)

    # 幂等指纹：同 request_id 但配置不同时拒绝，避免「改了一半又提交」
    fingerprint: Mapped[str] = mapped_column(String(64), default="", nullable=False)

    pack_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    pack_version: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # 编译后的提示词：详情页与任务中心直接展示，不再回读文件
    prompt: Mapped[str] = mapped_column(Text, default="", nullable=False)
    # 编译输入快照：回溯「这条提示词是怎么来的」唯一依据
    prompt_spec: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    # 提交时的完整配置（PrepareReplacement 的 model_dump），重跑要用
    configuration: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    # 列表页直接用的摘要：原片名、参考图、范围、备注
    input_summary: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)

    task_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    duration: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    progress_percent: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    estimated_credits: Mapped[float | None] = mapped_column(Float, nullable=True)
    credits: Mapped[float | None] = mapped_column(Float, nullable=True)
    # free = 0 积分权益队列；credits = 本次用积分走正常队列
    billing_mode: Mapped[str] = mapped_column(String(16), default="free", nullable=False)

    # 提交尝试次数：>0 且没有 task_id 时，失败就是「不知道有没有送达」
    submit_attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # 提交成功那一刻的供应商形态（async/sync）。恢复时不能拿当前模型目录重解析：
    # 供应商下线或换配置会让同一条历史记录被分类成另一个 provider，把能续查的记录判死。
    submit_mode: Mapped[str | None] = mapped_column(String(16), nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(32), nullable=True)

    # 成片在归档存储里的 key；为空表示还没下载完成
    output_key: Mapped[str | None] = mapped_column(String(256), nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # 「确认提交 → 出片」的起止：只由 `run_clock` 写，别在状态赋值处另外记时间。
    # `created_at` 回答不了这个问题——`prepared` 可以放几天再点确认；
    # `updated_at` 更不行——评价、回收站、补积分都会推着它往后走。
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        # 任务列表：我的 + 未删除 + 按时间倒序。这是最热的一条查询。
        Index(
            "ix_replacement_runs_owner_created",
            "owner_id",
            "created_at",
            sqlite_where=text("deleted_at IS NULL"),
            postgresql_where=text("deleted_at IS NULL"),
        ),
        # 启动恢复要扫「非终态」的行：status 上必须有索引，否则每次启动全表扫
        Index("ix_replacement_runs_status_created", "status", "created_at"),
    )


class ReplacementRunInput(Base, UUIDMixin):
    """这次替换用到的一张素材。

    与 `template_pack_references` 分开存：封装是「可复用的清单」，
    运行输入是「这一次实际用的」（封装后来可能改了，历史记录的输入不该跟着变）。
    """

    __tablename__ = "replacement_run_inputs"

    run_id: Mapped[str] = mapped_column(String(32), nullable=False)
    asset_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    # source_video / identity / target_product / target_scene
    role: Mapped[str] = mapped_column(String(24), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    usage: Mapped[str] = mapped_column(Text, default="", nullable=False)
    # 物化后的对象存储 key（inputs/reference-1.jpg）。执行器按它取字节。
    storage_key: Mapped[str] = mapped_column(String(256), default="", nullable=False)

    __table_args__ = (
        Index("uq_replacement_run_inputs_position", "run_id", "position", unique=True),
    )


class ReplacementReview(Base, UUIDMixin, TimestampMixin):
    """用户对成片的评价。

    `version` 是**评价自己的**乐观锁，不是封装的版本：同一条运行可能被同一个人
    改主意（先评「不可用」再改「可用」）。历史追加在 `history` JSON 里，
    不另开表——它只被详情弹窗整块读，查询需求为零。
    """

    __tablename__ = "replacement_reviews"

    run_id: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    status: Mapped[str] = mapped_column(String(16), default="pending", nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # 不可用原因（replacement/preservation/visual/audio/other），可用时为空
    reason_codes: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    comment: Mapped[str] = mapped_column(Text, default="", nullable=False)
    reviewed_by: Mapped[str | None] = mapped_column(String(32), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # 每次修改追加一条：{status, version, reasonCodes, comment, reviewedBy, reviewedAt}
    history: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list, nullable=False)
