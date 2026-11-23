"""LibTV 账号面的出入参（snake_case 书写，序列化自动转 camelCase）。

框架公共的出入参在 `modules/model_driver/schema.py`；这里只放**供应商自己**的
字段——它们是上游接口的原文口径，框架不认识也不该认识。
"""

from __future__ import annotations

from pydantic import Field

from core.schema import CamelModel

__all__ = [
    "LibTvAccountOut",
    "LibTvAssetOut",
    "LibTvBenefitOut",
    "LibTvConsumptionItemOut",
    "LibTvConsumptionOut",
    "LibTvProgressIn",
    "LibTvProgressOut",
    "LibTvQueueItemOut",
]


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
    failed_reason: str = Field(default="", description="上游失败原因（status=3 时返回）")
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
