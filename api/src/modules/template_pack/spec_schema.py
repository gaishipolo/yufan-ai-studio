"""模板规格的 HTTP 契约：入参严格（`extra="forbid"`），出参与入参同形。

**出参复用入参的嵌套体**（`SpecSlotOut` 直接继承 `SpecSlotIn`）：形状一致，
不另造一份会漂移的副本。这与 `manifest_schema` 的取舍一致。

## 为什么 `draft` 与 `latest` 分开返回

`draft` 是"正在编辑、还没发布"的那份；`latest` 是"上一次真正对外"的快照。
两者同时给出，页面才能显示"有未发布的改动"。只给一份会让"发布后改了草稿"
看起来像已经生效。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import ConfigDict, Field, field_validator

from core.schema import CamelModel
from core.template_spec import (
    MAX_PROMPT,
    MAX_SLOT_REFS,
    MAX_SLOTS,
    RefMode,
    SlotBinding,
    SlotSource,
    SlotType,
)

#: 槽位 key（进 `{{slot:key}}`，只允许下划线）
_KEY = r"^[a-z][a-z0-9_]{0,39}$"
#: 规格 key（URL 段，允许连字符）
_SPEC_KEY = r"^[a-z][a-z0-9_-]{0,39}$"


class StrictIn(CamelModel):
    """入参拒绝未声明的键：少一个拼写错误就变成"配置被静默忽略"。"""

    model_config = ConfigDict(extra="forbid")


class SpecSlotIn(StrictIn):
    key: str = Field(pattern=_KEY)
    type: SlotType
    source: SlotSource = "asset"
    binding: SlotBinding = "user"
    ref_mode: RefMode = "mixed"
    min_count: int = Field(default=1, ge=0, le=MAX_SLOT_REFS)
    max_count: int = Field(default=1, ge=1, le=MAX_SLOT_REFS)
    label: str = Field(default="", max_length=80)
    usage: str = Field(default="", max_length=500)
    asset_id: str | None = Field(default=None, max_length=32)
    pack_id: str | None = Field(default=None, max_length=32)
    position: int = Field(default=0, ge=0, le=MAX_SLOTS * 10)


class SpecPromptIn(StrictIn):
    locked: str = Field(default="", max_length=MAX_PROMPT)
    editable: str = Field(default="", max_length=MAX_PROMPT)


class TemplateSpecIn(StrictIn):
    """一份规格（草稿的整份内容）。`key` 是幂等边界，发布后不可改名。"""

    key: str = Field(pattern=_SPEC_KEY)
    name: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=2000)
    model: str = Field(min_length=1, max_length=80)
    params: dict[str, Any] = Field(default_factory=dict)
    prompt: SpecPromptIn
    slots: list[SpecSlotIn] = Field(default_factory=list, max_length=MAX_SLOTS)
    attributes: dict[str, str] = Field(default_factory=dict)
    #: 乐观锁：与 head 上的 `revision` 比对。为空表示"不校验版本"（首次保存）。
    revision: int | None = Field(default=None, ge=0)

    @field_validator("params")
    @classmethod
    def _params_are_flat(cls, value: dict[str, Any]) -> dict[str, Any]:
        # 参数最终原样进执行请求；嵌套结构在执行器那侧只会变成 400 或静默忽略。
        if any(isinstance(item, (dict, list)) for item in value.values()):
            raise ValueError("参数只能是标量（字符串 / 数字 / 布尔）")
        return value


class SpecSlotOut(SpecSlotIn):
    """槽位出参：与入参同形。"""


class SpecVersionOut(CamelModel):
    """一次发布快照。`slots` 的顺序就是编号顺序（`position` 升序返回）。"""

    version: int
    model: str
    params: dict[str, Any]
    prompt: SpecPromptIn
    slots: list[SpecSlotOut]
    fingerprint: str = ""
    published_by: str = ""
    published_at: datetime | None = None


class TemplateSpecOut(CamelModel):
    id: str
    key: str
    name: str
    description: str
    owner_id: str
    status: Literal["draft", "published", "disabled"]
    revision: int
    fingerprint: str
    current_version: int
    published_at: datetime | None = None
    disabled_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    #: 正在编辑的整份内容；为空表示还没保存过。
    draft: TemplateSpecIn | None = None
    #: 最近一次发布；为空表示从未发布。
    latest: SpecVersionOut | None = None


class TemplateSpecListOut(CamelModel):
    items: list[TemplateSpecOut] = Field(default_factory=list)
    total: int = 0


class SpecValidateOut(CamelModel):
    valid: bool = True
    fingerprint: str


class SpecPublishOut(CamelModel):
    version: int
    spec: TemplateSpecOut


class SpecCompileIn(StrictIn):
    """填表结果：`{slotKey: [assetId…]}`，可带一份改写后的可编辑段提示词。

    `source=pack` 的槽位传一个封装 id；`source=asset` 的槽位按用户挑选顺序传。
    `version` 为空表示用当前发布版本。

    `promptOverride` 是**填表时改写的 `editable` 段**（`locked` 段永不可改）。
    不传 = 用发布版本里那份原文。模板的 `editable` 为空（提示词全固定）时传它会 400 ——
    那是模板作者明确关掉的入口，不该由调用方绕开。
    """

    version: int | None = Field(default=None, ge=1)
    slot_values: dict[str, list[str]] = Field(default_factory=dict)
    prompt_override: str | None = Field(
        default=None,
        max_length=20000,
        description="改写后的可编辑段提示词；`locked` 段不受影响",
    )


class CompiledSlotOut(CamelModel):
    """哪几张素材拿到了哪个编号 —— 只给前端画角标，不参与提交。"""

    key: str
    tokens: list[str] = Field(default_factory=list)
    asset_ids: list[str] = Field(default_factory=list)


class SpecCompileOut(CamelModel):
    """编译结果（模板层）。

    **不是**任何端点的请求体：`/tasks` 的时长与画幅是顶层字段、不接受自由
    `params`。摊平由提交方按目标端点做（模板库页面对照生成的 `CreateTaskIn` 类型），
    这里刻意不假设端点，否则换端点就要改编译规则。
    """

    version: int
    model: str
    prompt: str
    params: dict[str, Any]
    reference_library_ids: list[str] = Field(default_factory=list)
    text_library_ids: list[str] = Field(default_factory=list)
    slots: list[CompiledSlotOut] = Field(default_factory=list)
