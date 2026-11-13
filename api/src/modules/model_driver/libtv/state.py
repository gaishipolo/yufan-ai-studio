"""LibTV 适配器的持久化执行状态，不包含模板业务。"""

from __future__ import annotations

from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

# 单个任务内的执行器锁文件名。后缀必须是 .lock：桥接层的 update() 会遍历
# inputs/outputs/records/review/code 给每个文件算 sha256，只跳过
# `.lock/.download/.tmp`。若叫 `.lease`，它会去读刚刚被自己锁住的字节，
# 在归档盘（SMB）上直接 EBUSY——表现为每次自动执行都失败、手工执行却成功。
RUN_LOCK = "records/executor.lock"
# 启动方（libtv.py）与监督进程（libtv_process.py）之间的参数协议版本。
# 放在这里是因为两侧都已经依赖本模块；改协议时只改这一个数，且必须想清楚旧进程怎么办。
# v2（2026-11-09）：payload 去掉 legacyRoot，适配器目录改由监督进程按仓库内位置计算。
# 旧进程收到 v2 会因版本不符直接退出，不会拿着半个参数包去跑任务。
LIBTV_PROCESS_PROTOCOL = 2


class LibTVState(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: str
    status: str = "prepared"
    task_id: str | None = None
    submit_attempts: int = 0
    framework_status: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    progress_percent: int = 0
    estimated_credits: float | None = None
    credits: float | None = None
    last_error: str | None = None
    error_code: str | None = None
    preparation_ready: bool = False

    @field_validator("progress_percent", mode="before")
    @classmethod
    def normalize_progress(cls, value: object) -> object:
        return 0 if value is None else value


class ExecutorResult(BaseModel):
    code: str = "OK"
    stage: str = ""
    message: str = ""
    lease_version: int = 2
    node_pid: int | None = None
    phase: str = "finished"
