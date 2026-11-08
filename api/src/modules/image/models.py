"""生图任务记录 ORM 实体。

字段命名刻意对齐 `modules/task/models.py`：`generation` 框架只认
`GenerationRecord`，两张表的差异全部由各自的 Store 吸收，所以这里多一个、
少一个字段都不影响框架，只影响各自 Store 里的那一次映射。
"""

from __future__ import annotations

import json
from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base, TimestampMixin, UUIDMixin
from generation.spec import STATUS_QUEUED


class ImageJob(Base, TimestampMixin, UUIDMixin):
    """一次生图请求。表名带模块前缀，避免与其他模块撞名。"""

    __tablename__ = "image_jobs"

    name: Mapped[str] = mapped_column(String(120), nullable=False)
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    size: Mapped[str] = mapped_column(String(32), default="1024x1024", nullable=False)
    count: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    status: Mapped[str] = mapped_column(
        String(20), default=STATUS_QUEUED, nullable=False, index=True
    )
    # 可读名（展示用）；model_id 是与模型目录对齐的 id，供路由与聚合使用
    model: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    model_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)

    progress: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # 这两个列存的是**对象 key**，不是 URL：地址在出参时按当前存储驱动现签。
    # 落库地址一旦带上网址形态，换一次驱动存量链接就整批失联。
    cover_url: Mapped[str] = mapped_column(String(512), default="", nullable=False)
    output_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(512), nullable=True)

    owner_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)

    # 供应商侧任务标识；同步供应商（Agnes）也会写，便于对账
    external_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    # 上一次提交的供应商任务号。重试必须清 external_id 才能重投，清掉之后
    # 对账凭证就没了；"可能已计费"的那次提交要留在这里（R12-01）。
    previous_external_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # 被替换掉的全部任务号（JSON 数组，旧的在前）。单槽不够用：连续两次
    # 「确认已扣费」重试后，第一份凭证会被第二次覆盖（R13-02）；而审计写入
    # 按设计吞异常，不能把它当唯一备份。存文本而不是 JSONB 列，是为了
    # sqlite / PG 同一套代码，且这张表从不需要按历史内容查询。
    external_id_history: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 提交时供应商形态快照 "sync" / "async"：启动恢复按记录自身分类，
    # 不按当前模型目录重新解析（R12-05）。迁移前的老记录为 NULL。
    submit_mode: Mapped[str | None] = mapped_column(String(8), nullable=True)
    # >0 且无 external_id = 提交结果未知，重启不得自动重提（可能已计费）
    submit_attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    @property
    def previous_external_ids(self) -> list[str]:
        """历史任务号（旧的在前）。列里是 JSON 文本，解析失败按空处理。

        直接暴露成列表属性，是为了让 `ImageJobOut` 能用 `from_attributes`
        读到它——契约里多一个「全量凭证」字段，不必给每个构造点加转换代码。
        """
        return parse_external_id_history(self.external_id_history)


def parse_external_id_history(raw: str | None) -> list[str]:
    """把 `external_id_history` 的 JSON 文本解析成列表，脏数据不抛。"""
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return []
    if not isinstance(data, list):
        return []
    return [str(item) for item in data if str(item).strip()]


def remember_previous_external_id(job: ImageJob, external_id: str) -> list[str]:
    """把即将被清掉的供应商任务号**追加**进历史，返回追加后的全量列表。

    重试要清 `external_id` 才能重新提交，但那个任务号是「上一次可能已扣费」
    的唯一凭证。留一个槽位不够：第二次确认会把第一次的覆盖掉（R13-02），
    而审计 detail 按设计会吞异常，不能当唯一备份——两份会一起丢。
    """
    history = parse_external_id_history(job.external_id_history)
    history.append(external_id)
    job.external_id_history = json.dumps(history, ensure_ascii=False)
    job.previous_external_id = external_id
    return history
