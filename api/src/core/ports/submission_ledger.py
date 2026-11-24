"""提交台账的注入口。

## 为什么台账要走注入口，而不是让恢复器直接 import

台账的实现在 `modules/model_driver/` —— 只有它知道台账表长什么样、哪个驱动
用了哪个 provider。但要查台账的有两处：

- 框架恢复器 `generation/recovery.py`（图片等走框架恢复的 kind）；
- 视频链自己的恢复器 `modules/task/worker.py`。

而 `modules/image` / `modules/task` **不能 import `modules/model_driver`**
（`tests/test_architecture.py` 的硬规则：业务模块之间只能经 `core/ports` 通信），
框架层也不该反向依赖 modules。

所以台账能力经 `core/ports` 暴露 —— 与 `model_generation` 同一个道理：
**模块间调用模型层的唯一合法方式**。

## 未注册时是中性值（不是 503）

查不到台账 → 返回 `None` → 恢复器保持现状（判「提交结果未知」，等人工核对）。

这与 `model_generation` 的 fail-closed 刻意不同：台账回填**不是**放行不可逆操作的
判定，回填不了只是「不能自动续接」，不该把整条恢复流程变成 503。
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from core.ports.host import Port

__all__ = [
    "SubmissionLedger",
    "has_submission_ledger",
    "lookup_recovery_external_id",
    "register_submission_ledger",
    "reset_submission_ledger",
]


@runtime_checkable
class SubmissionLedger(Protocol):
    """提交台账的只读查询能力。"""

    async def find_external_id(self, record_id: str) -> str | None:
        """按业务记录 id 找**最后一次提交尝试**的可续接凭证。

        返回 `None` 表示「没有可回填的凭证」—— 调用方据此保持现状
        （判「提交结果未知」，等人工核对），**不要编一个值**。
        """
        ...


_ledger: Port[SubmissionLedger] = Port("submission-ledger", multi=False)


def register_submission_ledger(impl: SubmissionLedger) -> SubmissionLedger:
    """注册台账实现。`register_*` 语义：不许两个实现抢同一个位置。"""
    return _ledger.provide(impl, owner="modules.model_driver")


def reset_submission_ledger() -> None:
    """仅测试使用。"""
    _ledger.reset()


def has_submission_ledger() -> bool:
    return _ledger.has()


async def lookup_recovery_external_id(record_id: str) -> str | None:
    """查这条业务记录有没有可回填的凭证；注入口没接上时返回 `None`（中性值）。

    这是**消费函数** —— 未注册不抛异常，只是什么都查不到。
    """
    impl = _ledger.single()
    if impl is None:
        return None
    return await impl.find_external_id(record_id)
