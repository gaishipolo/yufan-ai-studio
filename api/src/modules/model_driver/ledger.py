"""提交台账的注入口实现（`core/ports/submission_ledger` 的那一端）。

## 为什么实现放在模块层

台账表与台账语义都属于模型驱动模块 —— 只有它知道哪个驱动用了哪个 provider、
台账键怎么拼。而要查台账的是：

- 框架恢复器 `generation/recovery.py`（图片等走框架恢复的 kind）；
- 视频链自己的恢复器 `modules/task/worker.py`。

这两处**都不能直接 import 本模块**（`tests/test_architecture.py` 的硬规则：
业务模块之间只能经 `core/ports` 通信），所以能力经注入口暴露。

## 能力边界

暴露「按业务记录 id 找可续接凭证」和「人工重提时作废旧凭证」两件事。台账的
其余语义（轮次键、三态判定、抢占）留在 `kit/submission_guard.py` 内部，不外泄。
"""

from __future__ import annotations

from modules.model_driver.kit.submission_guard import (
    abandon_receipt,
    find_recoverable_external_id,
)


class SubmissionLedgerFacade:
    """提交台账的门面（查询 + 人工作废）。"""

    async def find_external_id(self, record_id: str) -> str | None:
        """按业务记录 id 找最后一次提交尝试的可续接凭证；没有就返回 `None`。

        这里**不传 factory** —— 让 kit 自己取会话工厂，测试改一处即可。
        """
        return await find_recoverable_external_id(record_id)

    async def abandon(self, record_id: str) -> int:
        """人工重提：作废旧轮凭证，让下一轮真正发出付费 POST。"""
        return await abandon_receipt(record_id)
