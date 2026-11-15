"""LibTV 的下载阶段：独立后台作业。

## 为什么是独立一跳

成片下载是 240 秒量级的非计费工作（`vendor` 里的 `downloadResult` 直接拉 CDN），
塞进轮询那一跳会顶掉「poll + 落库」的 45 秒预算（`runner.handle_poll` 的
`asyncio.timeout` 只包这两步）。驱动在 `poll()` 里看到 `generated` 就投这一条，
下载完成后再由下一跳轮询看到 `success`。

## 为什么 handler 住在驱动模块

它只做一件事：把 `execute_stage(run_id, "download")` 串起来，属于驱动自己的
执行语义，不是业务编排。放这里也就不需要让 `modules/task` 反过来 import
`modules.model_driver`（模块之间不许互相 import）。

## 这个 handler 曾经消失过

P3 删 `modules/template` 时它随替换链一起被删了，而 `drivers/libtv.py` 仍在
投 `libtv.download` —— 队列只会打一条「未知任务类型，丢弃」，下游没人发现：
片子照样在生成，只是没人取回。声明在 `ModuleSpec.jobs` 里，漏注册会启动失败。
"""

from __future__ import annotations

from core.logging import get_logger
from infra.queue.base import Job
from modules.model_driver.libtv import executor
from workers import register_handler

logger = get_logger(__name__)

JOB_DOWNLOAD = "libtv.download"


@register_handler(JOB_DOWNLOAD)
async def download(job: Job) -> None:
    """把工作区里的成片下载下来（幂等：文件已存在时适配器直接返回）。"""
    run_id = str(job.payload.get("id") or "")
    if not run_id:
        # 参数缺失是投递方的 bug，不能当成功跳过：静默跳过会让任务停在 generated。
        raise ValueError("libtv.download 缺少 id")

    result = await executor.execute_stage(run_id, "download")
    if result.code != "OK":
        # 交给队列重试：失败多是浏览器/网络瞬时问题，下一次通常就过了。
        raise RuntimeError(f"LibTV 下载未完成：{result.message or result.code}")
    logger.info("LibTV 成片已下载 run=%s", run_id)
