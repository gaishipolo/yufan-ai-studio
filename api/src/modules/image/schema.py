"""生图模块出入参。字段用 snake_case 书写，序列化自动转 camelCase。"""

from __future__ import annotations

from pydantic import Field

from core.schema import CamelModel, UtcDateTime


class ImageJobOut(CamelModel):
    id: str = Field(description="生图记录 ID")
    name: str = Field(description="名称")
    prompt: str = Field(description="画面描述")
    size: str = Field(description="尺寸，如 `1024x1024`")
    count: int = Field(default=1, description="一次生成的张数")
    status: str = Field(
        default="queued", description="状态：`queued` / `running` / `succeeded` / `failed`"
    )
    progress: int = Field(default=0, description="进度 0-100")
    model: str = Field(default="", description="模型可读名")
    model_id: str | None = Field(default=None, description="模型目录 id")
    cover_url: str = Field(
        default="",
        description="封面图地址（落库存对象 key，出参按当前存储驱动现签，可直接用于 <img>）",
    )
    output_url: str | None = Field(
        default=None,
        description="成图地址（成功后才有）：同样是按当前驱动现签的可访问地址，"
        "不要把它当「永久链接」持久化到别处",
    )
    error_message: str | None = Field(default=None, description="失败原因")
    external_id: str | None = Field(
        default=None, description="供应商侧任务号；提交成功后有值，可用于对账"
    )
    previous_external_id: str | None = Field(
        default=None,
        description="上一次提交的供应商任务号；带 confirmCharged 重试后保留的对账凭证",
    )
    previous_external_ids: list[str] = Field(
        default_factory=list,
        description=(
            "被替换掉的全部供应商任务号（旧的在前）。连续多次确认已扣费重试时，"
            "previousExternalId 只保留最近一个，全量凭证看这里"
        ),
    )
    created_at: UtcDateTime = Field(description="创建时间（UTC）")


class ImageRetryOut(CamelModel):
    """重试只回 id / status。

    不回整份 `ImageJobOut`：持有 `image:retry:any` 的人可以帮别人重跑，
    但没有 `image:read:all`，不该顺带读到别人的 prompt（与 task 的重试同一口径）。
    """

    id: str = Field(description="生图记录 ID")
    status: str = Field(description="重试后的状态，固定为 `queued`")


class GenerateImageIn(CamelModel):
    prompt: str = Field(min_length=1, max_length=2000, description="画面描述")
    size: str = Field(default="1024x1024", max_length=32, description="宽x高，如 `1024x1536`")
    count: int = Field(default=1, ge=1, le=4, description="张数")
    # 留空用该能力的默认模型；换供应商时前端只需换 id，接口不变
    model: str = Field(default="", max_length=64, description="模型 id，留空取默认")
