"""生成编排的核心数据类型。

这里的 `GenerationRecord` 是**跨业务表的统一视图**：视频任务、图片生成、
语音合成都映射成同一组字段，于是提交/轮询/重试/超时的逻辑只需要写一遍。

业务模块通过实现 `GenerationStore` 完成「自己的表 ↔ 这个视图」的双向映射，
框架不认识任何 ORM 实体。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from infra.ai.base import GenerationState

# --------------------------------------------------------------------------- #
# 状态常量：与各业务表的 status 字段取值保持一致
# --------------------------------------------------------------------------- #
STATUS_QUEUED = "queued"
STATUS_RUNNING = "running"
STATUS_SUCCEEDED = "succeeded"
STATUS_FAILED = "failed"

TERMINAL_STATUSES = frozenset({STATUS_SUCCEEDED, STATUS_FAILED})

# 与供应商状态的一一映射（业务表不直接暴露供应商词汇）
STATE_TO_STATUS: Mapping[GenerationState, str] = {
    GenerationState.QUEUED: STATUS_QUEUED,
    GenerationState.RUNNING: STATUS_RUNNING,
    GenerationState.SUCCEEDED: STATUS_SUCCEEDED,
    GenerationState.FAILED: STATUS_FAILED,
}


@dataclass(slots=True)
class GenerationRecord:
    """一条生成记录的通用视图。"""

    id: str
    kind: str
    model: str
    prompt: str
    status: str
    owner_id: str
    params: dict[str, Any] = field(default_factory=dict)
    external_id: str | None = None
    progress: int = 0
    output_url: str | None = None
    cover_url: str | None = None
    error: str | None = None
    created_at: datetime | None = None
    reference_urls: tuple[str, ...] = ()
    # 已经尝试过几次提交。>0 且 external_id 为空 = 提交结果未知（见 runner 说明）
    submit_attempts: int = 0
    # 提交时供应商形态的快照："sync" / "async"，由 Store 在 mark_submitted 时落库。
    # 启动恢复必须用它解释历史记录，而不是「拿当前模型目录重新解析」——
    # 供应商下线、换配置、测试环境只加载 echo 都会让后者分类翻转（R12-05）。
    # None = 迁移前的老记录，恢复器按「不能续查」的安全默认值处理。
    submit_mode: str | None = None
    # 业务表的「终态」取值可能不同（如任务表用 completed），由 store 在映射时指定
    terminal_statuses: tuple[str, ...] = (STATUS_SUCCEEDED, STATUS_FAILED)

    @property
    def terminal(self) -> bool:
        return self.status in self.terminal_statuses

    @property
    def submitted(self) -> bool:
        return bool(self.external_id)

    @property
    def submit_unknown(self) -> bool:
        """供应商可能已受理，但结果没有落库。

        这种记录**不能自动重提**：重提就是第二份账单、第二个产物。
        出路是人工核对后点「重试」——业务侧的 retry 命令会复位提交意图标记
        （见 `modules/task/service.py::retry_task`）。
        """
        return self.submit_attempts > 0 and not self.external_id
