"""任务 ORM 实体。"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from core.library_text import parse_text_refs, text_ref_bindings
from db.base import Base, TimestampMixin, UUIDMixin
from modules.task.enums import AspectRatio, TaskStatus


class VideoTask(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "video_tasks"

    title: Mapped[str] = mapped_column(String(200), nullable=False)
    prompt: Mapped[str] = mapped_column(Text, nullable=False)

    status: Mapped[str] = mapped_column(
        String(20), default=TaskStatus.QUEUED.value, nullable=False, index=True
    )
    # 可读名（展示 / 搜索用），与 model_id 成对写入
    model: Mapped[str] = mapped_column(String(64), nullable=False)
    # 模型目录 id：与 `GET /models` 的 id 对齐，供聚合与供应商路由使用。
    # 历史数据可能为空，读出时由 ModelRouter 反查补齐。
    model_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    duration_sec: Mapped[int] = mapped_column(Integer, default=5, nullable=False)
    aspect_ratio: Mapped[str] = mapped_column(
        String(8), default=AspectRatio.PORTRAIT.value, nullable=False
    )
    # 生成风格预设与参考图：契约里声明了就必须落库，否则客户端会「以为生效了」。
    # 参考图存的是**资产库 v2 的 id**（`library_assets.id`），不是旧 `assets.id`：
    # 前端先传进资产库拿到 id 再下发的。名字里带 `library` 是为了让架构守卫与
    # 删除保护都按 v2 口径认它（见 `modules/task/library_references.py`）。
    style_preset: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # 计费模式：`free` 免费队列 / `credits` 积分加速。
    #
    # 只有 LibTV 两条队列都走，其余模型恒为 `free`。**按任务落库**而不是
    # 每次提交现读：这条值决定上游走哪条队列，一旦提交出去就可能计费，
    # 重试时必须用**当初那一条**，不能因为账号权益变了就换队列（换了等于
    # 用不同的计费口径重下单）。默认值 `free` 是保守的那一侧：没声明就
    # 排免费队列，不会误扣积分。
    billing_mode: Mapped[str] = mapped_column(String(16), default="free", nullable=False)
    # 模型声明的**其余**参数（JSON 文本）。
    #
    # 为什么要这一列：时长与画幅有顶层列，计费模式与风格预设也各有一列，
    # 但模型随时可能再声明别的参数（`quality` / `seed` / `resolution`…）。
    # 没有这一列时前端只有两个选择：写死一套键（模型一改就 400），
    # 或者把声明的参数全塞进提示词（供应商收到的是文字不是参数）。
    #
    # 与生图 `image_jobs.params_json` 同一口径：**键必须来自模型 schema**
    # （受理期按 schema 校验，未声明的键 400），不声明的键不落库。
    # 顶层那几个键不进这里 —— 避免两份真相（顶层改了、这里没改）。
    params_json: Mapped[str] = mapped_column(Text, default="", nullable=False)
    reference_library_ids: Mapped[list[str]] = mapped_column(
        JSON, default=list, nullable=False
    )
    # 文本素材冻结快照（JSON 文本），见 `core.library_text`。重试从本行重建
    # GenerationRecord，不落库就会在用户改稿后静默换一段提示词。
    text_json: Mapped[str] = mapped_column(Text, default="", nullable=False)

    progress: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    cover_url: Mapped[str] = mapped_column(String(512), default="", nullable=False)

    author_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    author_name: Mapped[str] = mapped_column(String(64), default="", nullable=False)

    downloadable: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    error_message: Mapped[str | None] = mapped_column(String(512), nullable=True)
    # 供应商侧任务标识，用于轮询外部进度
    external_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    # 「上一次可能已扣费」的任务号。重试要清 `external_id` 才能重新提交，
    # 但那个号是唯一对账凭证 —— 只留一个槽位会被第二次确认覆盖，所以同时
    # 追加进 `external_id_history`（与 `image_jobs` 同一口径，见 R13-02）。
    previous_external_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    external_id_history: Mapped[str | None] = mapped_column(Text, nullable=True)
    output_url: Mapped[str | None] = mapped_column(String(512), nullable=True)

    # 「已尝试提交」次数：>0 且没有 external_id 说明进程在「供应商已受理、尚未落库」
    # 的窗口里中断，重启时绝不能自动重提（可能已经计费）
    submit_attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        # 列表默认按「作者 + 创建时间倒序」翻页，走联合索引
        Index("ix_video_tasks_author_created", "author_id", "created_at"),
        Index("ix_video_tasks_status_created", "status", "created_at"),
    )

    @property
    def text_refs(self) -> list[dict[str, Any]]:
        """冻结的文本快照（含正文），只给提交期注入用。"""
        return parse_text_refs(self.text_json)

    @property
    def text_library_ids(self) -> list[str]:
        return [str(item.get("assetId") or "") for item in self.text_refs if item.get("assetId")]

    @property
    def text_bindings(self) -> list[dict[str, Any]]:
        """出参用的精简投影，不带正文。"""
        return text_ref_bindings(self.text_refs)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<VideoTask {self.id} {self.status} {self.title[:16]!r}>"


class TaskFavorite(Base, UUIDMixin, TimestampMixin):
    """谁收藏了哪条任务 —— 收藏是**按账号**的，不是任务行上的开关。

    原来是 `video_tasks.favorited` 一个布尔，出参文案却写「当前账号是否已收藏」：
    A 收藏之后 B 也看到 true，B 点一次「取消收藏」直接 `PATCH {"favorited": false}`
    把 A 的收藏一起清掉。共享可见（`task:read:all`）下这不是小概率——只要有人
    点开别人的任务卡片取消收藏就会发生。

    一行 = 一个账号收藏一条任务。没有独立的 `id` 之外的业务键，唯一约束
    `(user_id, task_id)` 就是「同一个人不能收藏两次」；重复收藏靠它拦，
    不需要先读再写。

    刻意不加外键：全项目都是「业务表之间只认 id」，级联清理写在 service 里
    （`delete_tasks` 会顺手删掉对应的收藏行）。
    """

    __tablename__ = "task_favorites"

    user_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    task_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)

    __table_args__ = (
        UniqueConstraint("user_id", "task_id", name="uq_task_favorites_user_task"),
        # 「这一页里我收藏了哪几条」是列表每页都要跑的查询，按 (user, task) 走
        Index("ix_task_favorites_user_task", "user_id", "task_id"),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<TaskFavorite {self.user_id} → {self.task_id}>"


def dumps_params(params: Mapping[str, Any]) -> str:
    """把额外参数写成 JSON 文本；空参数写空串（不是 `{}`），便于「无参数」判空。

    与生图 `modules/image/models.py::dumps_params` 同一口径：两边存的都是
    「模型声明了、但没有独立列的那部分参数」。
    """
    if not params:
        return ""
    return json.dumps(dict(params), ensure_ascii=False)


def parse_params_json(raw: str | None) -> dict[str, Any]:
    """解析参数快照；脏数据（非 JSON、非对象）整条丢掉而不是抛。

    读路径对脏数据必须宽容，写路径才严格 —— 一条历史脏行不该让整个
    任务中心 500。
    """
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return dict(data) if isinstance(data, dict) else {}


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


def remember_previous_external_id(task: VideoTask, external_id: str) -> list[str]:
    """把即将被清掉的供应商任务号追加进历史，返回追加后的全量列表。

    重试是**花钱**的动作：旧任务号既要在记录上留一份（页面/DB 可查），也要在
    历史里留全量。只靠审计不够 —— `audit.record` 按设计吞异常。
    """
    history = parse_external_id_history(task.external_id_history)
    history.append(external_id)
    task.external_id_history = json.dumps(history, ensure_ascii=False)
    task.previous_external_id = external_id
    return history
