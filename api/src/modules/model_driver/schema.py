"""模型驱动模块出入参（snake_case 书写，序列化自动转 camelCase）。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import Field

from core.schema import CamelModel

__all__ = [
    "DriverListOut",
    "DriverOut",
    "ModelListOut",
    "ModelOut",
    "SubmitIn",
    "TaskOut",
]


class DriverOut(CamelModel):
    name: str
    label: str
    version: str
    path: str
    capabilities: list[str] = Field(default_factory=list)
    model_count: int = 0
    supports_poll: bool = False
    loaded: bool = True
    error: str = ""


class DriverListOut(CamelModel):
    items: list[DriverOut] = Field(default_factory=list)
    total: int = 0
    errors: list[str] = Field(default_factory=list)


class ModelOut(CamelModel):
    id: str
    label: str
    capability: str
    provider: str
    description: str = ""
    param_schema: dict[str, Any] = Field(default_factory=dict)
    # 这个模型最多收几张参考图，`0` = 纯文生图。**必须出参**：前端据此决定
    # 要不要渲染参考图入口，提交端据此拦住超量的 `reference_urls`。
    # 少了这个字段，统一门面就成了绕过 `max_references` 的后门——
    # 服务端知道上限、接口不告诉调用方，调用方就永远按「几张都行」提交。
    replacement: bool = Field(
        default=False,
        description=(
            "这个模型能不能跑替换链（按「原片 + 素材包」替换人物/产品/场景）。"
            "由驱动声明：模板目录据此筛选，调用方不得从槽位组合推断"
        ),
    )
    max_references: int = Field(
        default=0,
        description=(
            "这个模型最多接受几张参考图（图生图 / 多图合成），`0` 表示纯文生图。"
            "参考图不是 `paramSchema` 里的参数：它的值是「一批图片」，"
            "提交时走 `referenceUrls` 这条独立入参"
        ),
    )


class ModelListOut(CamelModel):
    items: list[ModelOut] = Field(default_factory=list)
    total: int = 0


class SubmitIn(CamelModel):
    model: str
    prompt: str
    name: str = ""
    params: dict[str, Any] = Field(default_factory=dict)
    reference_urls: list[str] = Field(default_factory=list)
    source: str = ""


class TaskOut(CamelModel):
    id: str
    name: str = ""
    model: str
    capability: str
    status: str
    progress: int = 0
    output_url: str | None = None
    cover_url: str | None = None
    error: str | None = None
    external_id: str | None = None
    owner_id: str = ""
    source: str = ""
    submit_attempts: int = 0
    created_at: datetime | None = None


# --------------------------------------------------------------------------- #
# LibTV 账号面（额度 / 排队 / 消费）
#
# 这几组字段**不是我们的口径**，是上游接口的原文（`/api/www/member/account`、
# `/api/task/generation/progress`、`/api/www/power/translogs/management`）。
# 刻意不做「换算成我们的说法」：排查时要拿这些值和浏览器里看到的对上。
# --------------------------------------------------------------------------- #
class LibTvAssetOut(CamelModel):
    usable_power: int = Field(default=0, description="可用积分（含赠送与套餐）")
    free_usable_power: int = Field(default=0, description="其中赠送积分部分")
    used_power: int = Field(default=0, description="历史累计已用")
    total_power: int = Field(default=0, description="累计获得")
    task_can_submit: int = Field(default=0, description="账号当下还能提交几个任务")
    concurrent: int = Field(default=0, description="-1 表示不限制并发")
    member_level: str = ""
    expire_date: str = ""


class LibTvBenefitOut(CamelModel):
    model_version_id: int | None = None
    target_name: str = ""
    activity_name: str = ""
    end_time: str = Field(default="", description="权益到期时间；到期后同一批任务会开始扣积分")
    tags: list[str] = Field(default_factory=list)


class LibTvAccountOut(CamelModel):
    account: str = Field(
        default="", description="查询用的 CDP 地址（9411 免费队列 / 9412 积分队列）"
    )
    asset: LibTvAssetOut | None = None
    free_power: float = 0
    free_power_expire_at: str | None = None
    zero_power_benefits: list[LibTvBenefitOut] = Field(default_factory=list)


class LibTvQueueItemOut(CamelModel):
    task_id: str
    status: int = Field(default=0, description="0 排队/生成中 · 1 生成中 · 2 成功 · 3 失败")
    percent: int = 0
    queue_reason: str = Field(
        default="",
        description="UNLIMITED_SERIAL = 0 积分队列排队；RATE_LIMIT = 被上游限流推迟",
    )
    queue_position: int = Field(default=0, description="这台机器在该队列里的位次")
    queue_total: int = Field(default=0)
    queue_limit: int = Field(default=10, description="画布 0 积分队列的名额，默认 10")
    power: int = 0
    benefit_tag: str = ""
    remain_seconds: int = 0
    describe: str = Field(default="", description="给人看的一句话，省得前端自己拼")


class LibTvProgressIn(CamelModel):
    task_ids: list[str] = Field(default_factory=list, description="上游 task_id 列表")
    account: str = Field(
        default="", description="留空按配置挑：积分看 9412，免费队列看第一个在线的"
    )


class LibTvProgressOut(CamelModel):
    items: list[LibTvQueueItemOut] = Field(default_factory=list)


class LibTvConsumptionItemOut(CamelModel):
    amount: int = 0
    seconds: int = Field(default=0, description="扣费时间（Unix 秒）")
    source_name: str = ""
    task_id: str = Field(default="", description="上游 task_id（originalBizNo）：对账用")
    project_name: str = ""
    model_name: str = ""
    summary: str = ""


class LibTvConsumptionOut(CamelModel):
    total: int = 0
    page: int = 1
    page_size: int = 20
    total_count: int = 0
    total_amount: int = 0
    items: list[LibTvConsumptionItemOut] = Field(default_factory=list)
