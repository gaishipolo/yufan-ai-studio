"""LibTV 适配器的持久化执行状态，不包含模板业务。"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, field_validator

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
    # 缺失时必须保持 None（绝不能用 now() 兜底）：自动对账以它作为「提交起点」，
    # 伪造成当前时刻会让更早的账单全部落到窗口之外，把已扣费任务误判为未提交。
    created_at: datetime | None = None
    progress_percent: int = 0
    estimated_credits: float | None = None
    credits: float | None = None
    last_error: str | None = None
    error_code: str | None = None
    failed_reason: str | None = None
    preparation_ready: bool = False
    # 计费模式（`free` / `credits`）。物化器在 `run.json` 里写它，桥接层据此
    # 决定走免费队列还是积分队列；执行器用它挑**哪个浏览器账号**：
    # 积分只在一个账号上有，免费队列不花积分，可以落在额度快用完的那个账号。
    billing_mode: str | None = None
    # 账号绑定：桥接层在第一个阶段把当时用的 CDP 地址写进来，之后就只读它。
    # 上传素材与上游 `task_id` 都属于那个账号，中途换号 = 查不到自己的任务。
    browser_url: str | None = None

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
