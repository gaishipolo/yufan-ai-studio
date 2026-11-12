"""把统一生成任务接进 `generation.runner` 的通用编排链。

注册之后，注入口的 `submit()` 只需要落一行 `driver_jobs` 再调
`get_runner().dispatch(KIND_MODEL_DRIVER, task_id)`，提交、轮询、重试、
超时、启动恢复全部由框架接管，本模块不重复实现一遍状态机。
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from generation.pipeline import register_pipeline
from generation.spec import GenerationRecord
from infra.ai.base import Capability, GenerationRequest
from modules.model_driver.models import KIND_MODEL_DRIVER
from modules.model_driver.store import DriverJobStore


@register_pipeline
class ModelDriverPipeline:
    kind = KIND_MODEL_DRIVER
    # 协议要求一个能力标记。驱动模块是**多能力**的：真正的 capability 存在
    # 任务行上（提交时按模型目录解析），`build_request` 从那里取，这个字段
    # 只是满足形状要求的默认值，不参与任何路由判断。
    capability = Capability.IMAGE

    # 统一门面的任务也要按当次记录的模型找回原供应商。驱动文件被删掉 /
    # 模型被下线之后，兜底会让重启恢复把任务打到别的驱动上，产物根本不是
    # 用户要的那个模型。
    strict_model = True

    # 模型从目录里消失 = 这条记录不可能再有结果 → 当场判失败。
    fail_on_model_gone = True

    def store(self, session: AsyncSession) -> DriverJobStore:
        return DriverJobStore(session)

    async def list_unfinished(
        self, session: AsyncSession, limit: int
    ) -> Sequence[GenerationRecord]:
        return await self.store(session).list_unfinished(session, limit)

    async def build_request(self, record: GenerationRecord) -> GenerationRequest:
        capability = record.params.get("capability") or self.capability.value
        params = record.params.get("params") or {}
        return GenerationRequest(
            capability=Capability(capability),
            model=record.model,
            prompt=record.prompt,
            params=params if isinstance(params, dict) else {},
            reference_urls=record.reference_urls,
            request_id=record.id,
        )
