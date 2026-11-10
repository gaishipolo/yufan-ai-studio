"""模型驱动开发标准：一个 Python 文件 = 一个驱动。

## 开发者要写什么

在 `modules/model_driver/drivers/<name>.py` 新建一个文件，暴露 `Driver`：

```python
from infra.ai.base import (
    Capability, GenerationProgress, GenerationRequest,
    GenerationState, ModelSpec,
)
from modules.model_driver.spec import DriverMeta

META = DriverMeta(label="示例厂商", version="1.0.0")

class MyDriver:
    name = "my_vendor"
    capabilities = frozenset({Capability.IMAGE})
    models = (
        ModelSpec(id="my-image-1", label="示例生图", capability=Capability.IMAGE,
                  provider="my_vendor"),
    )
    supports_poll = True                      # 异步厂商；同步厂商设 False

    async def submit(self, request: GenerationRequest) -> str:
        ...                                   # 怎么调用：返回供应商任务号

    async def poll(self, external_id: str, *, model: str = "") -> GenerationProgress:
        ...                                   # 怎么查询结果

    async def cancel(self, external_id: str) -> None:
        ...                                   # 怎么停止

Driver = MyDriver
```

**不需要**改 `.env`、不需要注册、不需要写路由、不需要碰 `generation/`：
`registry.discover_drivers()` 会扫描这个目录，校验并注册到 `ModelRouter`。

## 两种供应商形态

- 异步：`submit` 返回 `str`（任务号），框架按节奏调 `poll`，`supports_poll=True`。
- 同步：`submit` 自己等出结果，直接返回终态 `GenerationProgress`，
  `poll` 不会被调用，`supports_poll=False`。同步会占住 worker 槽位，是厂商
  事实约束，不是框架缺陷。

## 校验口径

加载期逐项检查，不合规的文件**只红自己这一条**，不会让服务起不来；
`app drivers check` 与 `app doctor` 会把它报出来。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from infra.ai.base import (
    Capability,
    GenerationProgress,
    GenerationRequest,
    GenerationState,
    ModelProvider,
    ModelSpec,
    SubmitResult,
)

__all__ = [
    "Capability",
    "DriverMeta",
    "GenerationProgress",
    "GenerationRequest",
    "GenerationState",
    "ModelProvider",
    "ModelSpec",
    "SubmitResult",
    "validate_driver",
]


@dataclass(frozen=True, slots=True)
class DriverMeta:
    """可选元数据。不写 `META` 也能跑，目录里只显示驱动名与版本占位。"""

    label: str = ""
    description: str = ""
    version: str = "1.0.0"
    author: str = ""


DEFAULT_META = DriverMeta()

_METHODS = ("submit", "poll", "cancel")


def validate_driver(obj: Any) -> list[str]:
    """返回不合规项；空列表表示合规。

    刻意不用 `isinstance(obj, ModelProvider)`：runtime_checkable Protocol 对含
    数据属性的协议做 isinstance 时，不同 Python 版本行为不一致，而驱动文件由
    第三方开发者提供，校验必须当场给出可读的原因，不能抛类型错误。
    """
    problems: list[str] = []

    name = getattr(obj, "name", "")
    if not isinstance(name, str) or not (name.isascii() and name.isidentifier()):
        problems.append("name 必须是 ASCII 标识符（字母/数字/下划线，不以数字开头）")

    capabilities = getattr(obj, "capabilities", None)
    if not capabilities:
        problems.append("capabilities 不能为空，至少声明一种 Capability")
    elif not all(isinstance(item, Capability) for item in capabilities):
        problems.append("capabilities 必须全部是 Capability 枚举值")

    models = getattr(obj, "models", ())
    if not models:
        problems.append("models 不能为空，至少要声明一个 ModelSpec")
    else:
        for item in models:
            if not isinstance(item, ModelSpec):
                problems.append("models 必须全部是 ModelSpec")
                break
            if item.provider != name:
                problems.append(
                    f"模型 {item.id} 声明归属 {item.provider}，与驱动 name={name!r} 不一致"
                )

    for method in _METHODS:
        if not callable(getattr(obj, method, None)):
            problems.append(f"缺少可调用的 {method}()")

    return problems
