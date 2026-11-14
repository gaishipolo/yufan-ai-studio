"""生图模块出入参。字段用 snake_case 书写，序列化自动转 camelCase。"""

from __future__ import annotations

from typing import Any

from pydantic import Field

from core.schema import CamelModel, UtcDateTime


class ImageJobOut(CamelModel):
    id: str = Field(description="生图记录 ID")
    name: str = Field(description="名称")
    prompt: str = Field(description="画面描述")
    size: str = Field(description="尺寸，如 `1024x1024`")
    count: int = Field(default=1, description="一次生成的张数")
    params: dict[str, Any] = Field(
        default_factory=dict,
        description="模型额外参数（不含 size / count），键按提交时的模型 `paramSchema`",
    )
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
    reference_library_ids: list[str] = Field(
        default_factory=list,
        description="提交时带的参考图资产 id（顺序即提交顺序）；纯文生图为空",
    )
    text_library_ids: list[str] = Field(
        default_factory=list,
        description="绑定的文本资产 id（顺序即提示词里 `{{Text N}}` 的编号顺序）",
    )
    text_bindings: list[dict[str, Any]] = Field(
        default_factory=list,
        description="绑定的文案与版本快照（不含正文）：assetId / name / version / sha256",
    )
    created_at: UtcDateTime = Field(description="创建时间（UTC）")


class ImageRetryOut(CamelModel):
    """重试只回 id / status。

    不回整份 `ImageJobOut`：持有 `image:retry:any` 的人可以帮别人重跑，
    但没有 `image:read:all`，不该顺带读到别人的 prompt（与 task 的重试同一口径）。
    """

    id: str = Field(description="生图记录 ID")
    status: str = Field(description="重试后的状态，固定为 `queued`")


class DeleteImagesIn(CamelModel):
    ids: list[str] = Field(
        min_length=1, max_length=200, description="要删除的生图记录 ID 列表"
    )


class DeleteImagesOut(CamelModel):
    """与 `DELETE /tasks` 同一形状：删了几条、哪些被跳过。

    跳过的原因是「不存在」或「属于他人且没有 `image:read:all`」——两者对外
    不做区分（不向无权限者泄漏「这个 id 存在」），细节只进服务端日志。
    """

    deleted: int = Field(description="实际删除的条数")
    skipped: list[str] = Field(
        default_factory=list,
        description="被跳过的记录 ID：不存在，或属于他人且没有 `image:read:all`",
    )


class GenerateImageIn(CamelModel):
    prompt: str = Field(min_length=1, max_length=2000, description="画面描述")
    size: str = Field(
        default="",
        max_length=32,
        description="宽x高，如 `1024x1536`；留空取模型 `paramSchema.size.default`",
    )
    count: int = Field(
        default=0,
        ge=0,
        le=16,
        description="张数；`0` 表示取模型 `paramSchema.count.default`，上限以模型声明为准",
    )
    params: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "模型声明的其他参数，键与 `GET /models` 返回的 `paramSchema` 一致"
            "（如 `quality` / `seed`）。未声明的键会被 400 拒绝，避免打错字被静默忽略；"
            "与顶层 `size` / `count` 重复时以顶层为准"
        ),
    )
    # 留空用该能力的默认模型；换供应商时前端只需换 id，接口不变
    model: str = Field(default="", max_length=64, description="模型 id，留空取默认")
    # 不叫 `reference_asset_ids`：那个名字在架构守卫
    # （`test_every_asset_reference_writer_declares_a_source`）里等于「往自己的表里
    # 存了旧 `assets.id`」，会被要求声明 `asset_reference_sources` 并注册一个
    # `AssetReferenceChecker`。这里存的是 v2 素材库（`library_assets`）的 id，
    # 删除保护走资产库自己的机制，不是旧素材库那套。
    reference_library_ids: list[str] = Field(
        default_factory=list,
        max_length=16,
        description=(
            "参考图（图生图 / 多图合成）的素材库资产 id，**按顺序生效**："
            "多图合成时提示词里的「第一张 / 第二张」指的就是这个顺序，重排会让"
            "提示词与图片错位。只接受 `image` 类型且当前账号能读的资产，"
            "张数上限由模型 `maxReferences` 决定（`0` = 该模型不支持参考图）。"
            "传 id 而不是 URL：我们的取件链接是私签的、供应商访问不到，"
            "服务端读字节后以 `data:image/...;base64,...` 交给供应商"
        ),
    )
    # 文本不是参考图，也不是模型参数：它是一段要拼进 prompt 的正文。
    # 单独一个字段，避免 `_validate_params` 的「未声明键 400」把文案 id 拦掉，
    # 也避免和 `reference_library_ids` 混在一起让 `maxReferences` 少算一张图。
    text_library_ids: list[str] = Field(
        default_factory=list,
        max_length=8,
        description=(
            "要拼进提示词的文本资产 id，**按顺序生效**：`{{Text 1}}` 指第 1 份、"
            "`{{Text 2}}` 指第 2 份。提交时把正文按当时版本冻结进任务，"
            "之后编辑资产不会改变这次生成与它的重试。只接受当前账号可读、"
            "在库的 `text` 类型资产；一次最多 8 份"
        ),
    )
