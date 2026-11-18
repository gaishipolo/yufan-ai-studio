"""把「提交回执未知」的档案人工解封 / 接回。

## 为什么需要它

`submit` 阶段的请求发出去、回执没回来时，桥接层把 `run.json` 置成
`submission_unknown`，之后**任何**重提都会被它自己拒掉（`禁止重提`）。
这是对的：不知道上游有没有扣费，再发一次可能就是第二份账单。

但界面上只有一个「重新生成」，点下去永远得到同一句「提交结果未知…
请核对后手动重试」——这句文案承诺了一个**做不到**的动作：重试必然再次撞上
同一道闸门。真正该做的是先人工看一眼上游，再二选一：

- 上游没有这笔任务 → `--not-submitted`：状态退回 `uploaded`，可以安全重提；
- 上游已经在跑（拿到 task_id）→ `--adopt-task`：把 id 写回档案。之后在任务页
  点重试会**复用同一个上游任务**继续查询/下载，不会重新下单（`submit()` 看到
  `previous.task_id` 就直接返回它）。

本模块只做状态迁移（纯函数，好测）；读档案、写档案、拼报错文案分别在 CLI 与驱动里。
"""

from __future__ import annotations

from collections.abc import Collection, Sequence
from dataclasses import dataclass, field

from core.exceptions import BadRequest
from modules.model_driver.libtv.state import LibTVState

__all__ = ["RECONCILABLE", "AutoVerdict", "auto_verdict", "reconcile"]

#: 需要人工核对的起点。`success` 不必解封（已经出片）；上游自己判的 `failed`
#: 也不放行 —— 那种情况重新提交会再花一次钱，该由人决定是不是新建任务。
RECONCILABLE = frozenset({"submission_unknown", "submitting"})

_HINT = (
    "先到该账号的 LibTV 记录里核对这笔任务："
    "没有 → --not-submitted（解封后可安全重提）；"
    "已有 task_id → --adopt-task <上游 task_id>（接回查询，不会重新下单）"
)


def reconcile(
    state: LibTVState,
    *,
    not_submitted: bool = False,
    adopt_task: str | None = None,
    executor_busy: bool = False,
) -> LibTVState:
    """返回迁移后的状态；非法组合抛 `BadRequest`，调用方原样报给用户。"""
    if executor_busy:
        # `submitting` 状态下桥接层可能正在提交：这时候改账，等它写回 task_id
        # 就和档案对不上了（钱可能已经花了）。先让它退干净。
        raise BadRequest(
            "这个任务的执行器还没退出（可能正在上传或提交）：先确认进程已结束再核对，"
            "否则桥接层写回的回执会和这里改过的状态打架"
        )
    if not_submitted == bool(adopt_task):
        raise BadRequest(f"二选一：--not-submitted 或 --adopt-task。{_HINT}")
    if state.status not in RECONCILABLE:
        raise BadRequest(
            f"档案状态是 {state.status}，不在需要人工核对的 {sorted(RECONCILABLE)} 里"
        )

    if not_submitted:
        if state.task_id:
            # 有 task_id 说明上游已经受理：解封等于允许「再提交一次」= 第二份账单。
            raise BadRequest(
                f"档案里已有上游 task_id {state.task_id}，请用 --adopt-task {state.task_id} 接回"
            )
        return state.model_copy(
            update={"status": "uploaded", "last_error": None, "error_code": None}
        )

    task_id = (adopt_task or "").strip()
    if not task_id:
        raise BadRequest(f"--adopt-task 需要一个上游 task_id。{_HINT}")
    if state.task_id and state.task_id != task_id:
        # 两个 id 都算「上游已受理」，但只有一个是这批素材真正的那条，
        # 静默覆盖会让查询打到别人的任务上（同账号下的另一个任务也能查到）。
        raise BadRequest(
            f"档案里的 task_id 是 {state.task_id}，与传入的 {task_id} 不一致；先核对口径再改"
        )
    return state.model_copy(
        update={
            "task_id": task_id,
            "status": "running",
            "last_error": None,
            "error_code": None,
        }
    )


# --------------------------------------------------------------------------- #
# 自动对账：「回执未知」不必每次都由人二选一
#
# 消费明细里的 `originalBizNo` 就是上游 task_id，`transDesc` 是这段提示词。
# 拿「提示词前缀 + 提交时刻之后」去对，就能替人做那个判断：
#   - 命中一条  → 上游确实建了任务，接回它（--adopt-task）
#   - 一条没有  → 上游什么都没建，解封重提（--not-submitted）
#   - 命中多条  → 说不清是哪一条，交回给人，别猜
# --------------------------------------------------------------------------- #
#: 前缀取多长。太短会把同一批素材的不同任务混在一起，太长则受上游截断影响。
PROMPT_PREFIX = 24


@dataclass(frozen=True)
class AutoVerdict:
    """自动对账的结论。`action`: adopt / release / ambiguous / unknown。"""

    action: str
    task_id: str = ""
    candidates: list[str] = field(default_factory=list)
    since: int = 0

    def describe(self) -> str:
        if self.action == "adopt":
            return f"上游已建任务 {self.task_id}（消费明细命中）"
        if self.action == "release":
            return "上游没有这笔任务的消费记录，可安全解封重提"
        if self.action == "ambiguous":
            return f"命中 {len(self.candidates)} 条，无法判断是哪一条：{self.candidates}"
        return "消费明细里没有能与本任务匹配的记录（可能是提示词被截断）"


def _normalize(text: str) -> str:
    """去掉所有空白与换行，避免提示词与账单摘要因排版/换行差异匹配不上。"""
    return "".join(text.split())


def _prefix(prompt: str) -> str:
    """取提示词前缀用于匹配。"""
    return _normalize(prompt)[:PROMPT_PREFIX]


def auto_verdict(
    items: Sequence[object],
    *,
    prompt: str,
    since: int,
    claimed_task_ids: Collection[str] = (),
) -> AutoVerdict:
    """按消费明细判断「这笔到底有没有提交成功」。

    `items` 是 `account.ConsumptionItem` 列表（只用到 `seconds` / `task_id` / `summary`）。
    只认 `seconds >= since` 的记录：更早的同款提示词是另一次任务，不能算进来。
    `claimed_task_ids` 是已被本系统其它任务认领的上游 `task_id`：同一提示词批量跑
    多单时，绝不能把另一单的成品接回给当前单。
    """
    prefix = _prefix(prompt)
    if not prefix:
        return AutoVerdict(action="unknown", since=since)
    claimed = {str(task_id).strip() for task_id in claimed_task_ids if str(task_id).strip()}
    stamps = [int(getattr(item, "seconds", 0) or 0) for item in items]
    matched = {
        str(getattr(item, "task_id", "") or "")
        for item in items
        if int(getattr(item, "seconds", 0) or 0) >= since
        and _normalize(str(getattr(item, "summary", "") or "")).startswith(prefix)
        and str(getattr(item, "task_id", "") or "")
        and str(getattr(item, "task_id", "") or "") not in claimed
    }
    if len(matched) == 1:
        return AutoVerdict(action="adopt", task_id=matched.pop(), since=since)
    if matched:
        return AutoVerdict(action="ambiguous", candidates=sorted(matched), since=since)
    # 「没命中」不等于「没扣费」：只翻了最近几条时，提交时刻更早的记录根本没看到。
    # 必须确认翻到了 `since` 之前，才敢给出「可以安全解封」的结论。
    if not stamps or min(stamps) >= since:
        return AutoVerdict(action="unknown", since=since)
    return AutoVerdict(action="release", since=since)
