"""替换运行的 ORM 实体：调用档案、本次输入、评价。

**素材包与视频模板的三张表已经搬到 `modules/template_pack`**（2026-11-14 C2）：
封装数据的读写在那边，本模块只负责「怎么替换」，两者之间只经
`core.ports.template_runtime` 交换冻结快照，不共享 ORM 行。

**为什么不用外键**：全项目没有一处 `ForeignKey`（见 `modules/task/models.py`），
靠字符串 id + 应用层校验。跨 sqlite/postgres 行为一致，且删记录不会级联炸掉
历史。这里沿用同一约定，靠复合索引保证唯一性。

**三张表的分工**：

| 表 | 回答什么问题 | 为什么不能合并 |
|---|---|---|
| `replacement_runs` | 这次替换进行到哪一步 | 状态机要按 status 查、按 created_at 排序 |
| `replacement_run_inputs` | 这次用了哪几张图、什么用途 | 重跑要能原样复原输入，不能只存 JSON 快照 |
| `replacement_reviews` | 用户怎么评价这次成片 | 评价要能按 version 做乐观锁 |
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, Float, Index, Integer, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from core.library_text import parse_text_refs, text_ref_bindings
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


class ReplacementRun(Base, UUIDMixin, TimestampMixin):
    """一次替换调用。

    `id` 就是前端传的 `request_id`（32 位 hex）：幂等边界落在主键上，
    同一个 `request_id` 重复提交只会命中同一行，不需要额外的去重键。

    `output_key` 与 `prompt` 这类大字段直接在行上：它们只被详情页读，
    不参与列表排序，拆表只会多一次 join。
    """

    __tablename__ = "replacement_runs"

    # All ORM writes participate in optimistic concurrency, including worker and
    # HTTP paths outside GenerationRunner. Projections carry the same revision.
    state_version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    __mapper_args__ = {"version_id_col": state_version}

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
    # **实际发出去那段字节**的 sha256，在所有后处理之后由 Python 算一次。
    # 编译器自己那两个哈希（`template_sha256` / `spec_sha256`）哈希的是模板和输入，
    # 中间还会被 `inject_masked_text_refs` / `translate_prompt` 改写，事后拿着它们
    # 复核不了「当时到底发了什么」。NULL = 这一行是在加这列之前准备的，**不回填**：
    # 回填只能哈希现在这一列 `prompt`，而那恰好是无法自证的部分。
    prompt_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # 文本素材冻结快照（JSON 文本）。`{{Text N}}` 在编译后注入进 `prompt`，
    # 但快照要留着：详情页要说清「当时用的是哪一稿」，重跑也不该重新读资产。
    text_json: Mapped[str] = mapped_column(Text, default="", nullable=False)
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
    # 「确认提交 → 出片」的起止，只由 `modules/template/run_clock.py` 写，
    # 别在状态赋值处另外记时间。`created_at` 回答不了这个问题——`prepared` 的
    # 档案可以放几天再点确认；`updated_at` 更不行——评价、回收站、补积分都会
    # 推着它往后走。
    #
    # 两列在初始迁移（844779d1b4c9）里就存在，模型一直漏声明，`alembic check`
    # 会把它当成「模型里没有、库里有」的待删除列而报红。补声明而不是删列：
    # 历史数据里的真实耗时不该被一次收漂移抹掉。
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    @property
    def text_refs(self) -> list[dict[str, Any]]:
        return parse_text_refs(self.text_json)

    @property
    def text_library_ids(self) -> list[str]:
        return [str(item.get("assetId") or "") for item in self.text_refs if item.get("assetId")]

    @property
    def text_bindings(self) -> list[dict[str, Any]]:
        """出参投影，不带正文。"""
        return text_ref_bindings(self.text_refs)

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
    # 回放只认它：字节已经物化，不依赖任何一张素材表（旧素材库下线后，
    # 历史任务仍可复核的依据就是这一列）。
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
