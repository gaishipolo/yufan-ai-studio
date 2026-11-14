"""模板规格的**跨模块词汇**：槽位、规格、编译结果。

## 为什么在 core

三处都要用同一套形状，而它们互相不能 import：

- `modules/template_pack`（封装定表）产出 `TemplateSpec`；
- 前端经 `compile` 拿到执行请求（`CompiledRequest` 的 JSON 形态）；
- 替换链经 `template-runtime` 端口读它，转成自己的快照。

与资产库封装词表的分工：那边是"用途词表"这类静态规则（归资产库模块），这边是
"规格长什么样 + 怎么算编号"。都不放 `core/ports/`——ports 回答「谁来实现」，
这里没有实现者、只有数据与纯函数。
"""

from core.template_spec.compile import compile_spec
from core.template_spec.models import (
    EXECUTION_PROMPT_LIMIT,
    MAX_PROMPT,
    MAX_SLOT_REFS,
    MAX_SLOTS,
    CompiledRequest,
    FilledAsset,
    FilledSlot,
    InputSlot,
    PromptParts,
    RefMode,
    SlotBinding,
    SlotSource,
    SlotType,
    TemplateSpec,
)
from core.template_spec.rules import validate_spec

__all__ = [
    "EXECUTION_PROMPT_LIMIT",
    "MAX_PROMPT",
    "MAX_SLOTS",
    "MAX_SLOT_REFS",
    "CompiledRequest",
    "FilledAsset",
    "FilledSlot",
    "InputSlot",
    "PromptParts",
    "RefMode",
    "SlotBinding",
    "SlotSource",
    "SlotType",
    "TemplateSpec",
    "compile_spec",
    "validate_spec",
]
