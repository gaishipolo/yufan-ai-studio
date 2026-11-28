"""提交台账的注入口。

## 为什么台账要走注入口，而不是让恢复器直接 import

台账的实现在 `modules/model_driver/` —— 只有它知道台账表长什么样、哪个驱动
用了哪个 provider。但要查台账的是：

- 框架恢复器 `generation/recovery.py`（视频、图片等所有 kind 共用）。

而 `modules/image` / `modules/task` **不能 import `modules/model_driver`**
（`tests/test_architecture.py` 的硬规则：业务模块之间只能经 `core/ports` 通信），
框架层也不该反向依赖 modules。

所以台账能力经 `core/ports` 暴露 —— 与 `model_generation` 同一个道理：
**模块间调用模型层的唯一合法方式**。

## 未注册时是中性值（不是 503）

查不到台账 → 返回 `None` → 恢复器保持现状（判「提交结果未知」，等人工核对）。

这与 `model_generation` 的 fail-closed 刻意不同：台账回填**不是**放行不可逆操作的
判定，回填不了只是「不能自动续接」，不该把整条恢复流程变成 503。

## 为什么这里还有「作废」这个写操作

人工重提（`task/service.py::retry_task`、`image/service.py::retry`、
`model_driver/service.py::retry`）会清掉业务表的 `external_id`，但台账里上一轮的
`submitted` 行还在。百炼的台账键就是业务记录 id，于是下一次 `submit` 的只读恢复
会把**上一轮的任务号**捡回来 —— 用户点了「重新生成」，实际上一次付费 POST 都没发，
拿回的还是上一轮那条失败的任务。必须有一个显式的动作告诉台账「这一轮作废了，
下一轮是新的」，而这只能从业务层发起，所以经同一个注入口暴露。

读取仍然保持中性；作废在没接实现时也是中性（返回 0），因为只有真正接了台账的
部署才存在这个坑。
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from core.ports.host import Port

__all__ = [
    "SubmissionLedger",
    "abandon_submission",
    "has_submission_ledger",
    "lookup_recovery_external_id",
    "register_submission_ledger",
    "reset_submission_ledger",
]


@runtime_checkable
class SubmissionLedger(Protocol):
    """提交台账的查询与人工作废能力。"""

    async def find_external_id(self, record_id: str) -> str | None:
        """按业务记录 id 找**最后一次提交尝试**的可续接凭证。

        返回 `None` 表示「没有可回填的凭证」—— 调用方据此保持现状
        （判「提交结果未知」，等人工核对），**不要编一个值**。
        """
        ...

    async def abandon(self, record_id: str) -> int:
        """人工重提：作废这条记录的既有凭证，让下一轮提交真正发出请求。

        返回作废的行数；`0` 表示本来就没有可作废的凭证（第一次提交、或上一轮
        已被拒绝）。旧行**保留为历史**（改状态并挪键），不删除 —— 账单对不上时
        还能查到上一轮的任务号。
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


async def abandon_submission(record_id: str) -> int:
    """人工重提时作废旧凭证；注入口没接上时返回 `0`（中性值）。

    和查询一样是**消费函数**：台账没装配时不能让用户的重试整条 503 ——
    那种部署本来就没有百炼的台账坑。
    """
    impl = _ledger.single()
    if impl is None:
        return 0
    return await impl.abandon(record_id)
