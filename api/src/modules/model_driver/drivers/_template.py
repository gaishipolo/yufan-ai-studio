"""驱动模板：复制成 `<name>.py` 再改三个方法体即可。

文件名以下划线开头，`registry._iter_driver_modules()` 会跳过它，
所以这份骨架不会被当成真驱动载入、也不会出现在 `app drivers list` 里。

用法：

    cp _template.py my_vendor.py
    # 然后只改四处：META、MODELS（含 provider）、类里的 name/capabilities、
    # 以及 submit / poll / cancel 的方法体

两种形态二选一（不是框架偏好，取决于供应商 API）：

- 异步（主流视频厂商）：`submit` 返回任务号 `str`，`supports_poll = True`，
  框架按 `generation_poll_interval_sec` 回来调 `poll`；
- 同步（多数图片厂商）：`submit` 自己等出结果、直接返回终态
  `GenerationProgress`，`supports_poll = False`，`poll` 不会被调用。

同步形态会占住一个 worker 槽位直到出结果，这是供应商的事实约束。
两种形态的完整可运行版本见同目录 `_mock_async.py` / `_mock_sync.py`。

那两个示例驱动同样以下划线开头，所以**不会被自动载入**——示例模型要是混进
模型目录，前端选择器里就会多出「示例同步生图」这种用户点不了、提交了也只
返回占位图的条目。需要离线联调时，显式调用
`modules.model_driver.registry.register_example_drivers()` 装上它们。
"""

from __future__ import annotations

from infra.ai.base import (
    Capability,
    GenerationProgress,
    GenerationRequest,
    ModelSpec,
)
from modules.model_driver.spec import DriverMeta, SubmitResult

# 可选。不写也能跑，目录里只显示驱动名与版本占位。
META = DriverMeta(
    label="模板驱动",
    description="从 _template.py 复制出来的驱动",
    version="1.0.0",
    author="",
)

# 至少一个。每个 ModelSpec.provider 必须等于下面类的 name。
# param_schema 只给前端渲染表单用，供应商真正认的参数请照它收敛。
MODELS = (
    ModelSpec(
        id="template-image-1",
        label="模板示例模型",
        capability=Capability.IMAGE,
        provider="template_driver",
        param_schema={
            "size": {
                "type": "enum",
                "options": ["1024x1024"],
                "default": "1024x1024",
            },
        },
    ),
)


class TemplateDriver:
    """一个驱动 = 一个供应商（或供应商的一个账号池）。"""

    name = "template_driver"
    capabilities = frozenset({Capability.IMAGE})
    models = MODELS
    supports_poll = True

    # 可选：单次提交最多等多久（秒）。同步出图的驱动**必须**声明，且不小于
    # 上游最长耗时——框架默认 120 秒先到点会取消提交，任务重投时「有提交意图、
    # 没有任务号」，直接判「提交结果未知」（钱可能花了、图丢了、不许重试）。
    # submit_timeout_sec = 180.0

    # 可选：声明「我的模型是该能力的默认」（frozenset({Capability.IMAGE})）。
    # 不写就按 `drivers/` 的扫描顺序，增删一个驱动文件就会换掉默认。
    # preferred_for = frozenset({Capability.IMAGE})

    async def submit(self, request: GenerationRequest) -> SubmitResult:
        """怎么调用。

        异步厂商：发出请求，返回供应商任务号 `str`。
        同步厂商：自己等到结果，返回 `GenerationProgress`（state 用
        `GenerationState.SUCCEEDED` / `FAILED`，需要时从 `infra.ai.base` 导入）。

        可用的入参：`request.capability` / `model` / `prompt` / `params` /
        `reference_urls` / `request_id`（本模块任务 id，用于账单对账）。

        **注意**：请求已发出但没拿到明确受理结果时，请抛异常而不是返回猜测的
        任务号——框架会把「提交结果未知」判死，绝不自动重提，避免二次计费。
        """
        raise NotImplementedError("TODO: 调用供应商接口，返回任务号或终态快照")

    async def poll(self, external_id: str, *, model: str = "") -> GenerationProgress:
        """怎么查询结果。同步驱动不需要实现。"""
        raise NotImplementedError("TODO: 查询供应商任务状态，映射成 GenerationProgress")

    async def cancel(self, external_id: str) -> None:
        """怎么停止。供应商不支持取消时保持空实现即可。"""
        return None


Driver = TemplateDriver
