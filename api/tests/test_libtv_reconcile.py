"""「提交回执未知」不能是一条死路：要么排队等，要么给出能执行的解封动作。

2026-11-17 现场：用户连点三次「重新生成」，
- 第二个任务撞上账号级串行锁 → 直接判失败「账号执行器正在执行其他任务」；
- 另外两个的归档已经是 `submission_unknown` → 重试永远得到
  「提交结果未知…请核对后手动重试」，而这句话让做的动作根本走不通。

这里把三件事钉住：
1. 账号忙 = 排队信号（`RateLimited`），不是失败；
2. 回执未知 = 409 + 解封命令，而不是「再点一次试试」；
3. `reconcile` 的状态迁移本身不会把「已受理」误放成「可重提」。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from core.exceptions import BadRequest, RateLimited
from modules.model_driver.drivers.libtv import _stage_failure, _uncertain_hint
from modules.model_driver.libtv.reconcile import reconcile
from modules.model_driver.libtv.state import ExecutorResult, LibTVState

RUN = "a" * 32
ACCOUNT = "http://127.0.0.1:9412"


def _state(status: str, task_id: str | None = None) -> LibTVState:
    return LibTVState(id=RUN, status=status, task_id=task_id, browser_url=ACCOUNT)


# --------------------------------------------------------------------------- #
# 账号忙：排队，不是失败
# --------------------------------------------------------------------------- #
def test_busy_account_is_a_queueing_signal_not_a_failure() -> None:
    """账号被别的任务占着时抛 `RateLimited`，框架会按等待表重排（任务保持 queued）。

    以前这里抛 `BadRequest`，runner 直接判失败 —— 用户连点两个任务，
    第二个就得到「提交生成失败」，而它其实只是排在后面。
    """
    exc = _stage_failure(
        ExecutorResult(
            stage="upload",
            code="EXECUTOR_BUSY",
            message="账号执行器正在执行其他任务，请稍后继续",
        ),
        RUN,
        ACCOUNT,
    )

    assert isinstance(exc, RateLimited)
    assert ACCOUNT in exc.message, "报错要指明是哪个账号占着，多账号下这是唯一线索"


def test_other_executor_failures_stay_hard_failures() -> None:
    exc = _stage_failure(
        ExecutorResult(
            stage="upload",
            code="STALE_LOCK",
            message="上一次执行未正常退出，遗留锁需核对",
        ),
        RUN,
        ACCOUNT,
    )

    assert isinstance(exc, BadRequest)
    assert not isinstance(exc, RateLimited)


# --------------------------------------------------------------------------- #
# 回执未知：给命令，不给「再试一次」
# --------------------------------------------------------------------------- #
def test_uncertain_hint_names_the_commands_and_the_account() -> None:
    text = _uncertain_hint(RUN, ACCOUNT)

    assert "libtv-reconcile" in text
    assert "--not-submitted" in text and "--adopt-task" in text
    assert ACCOUNT in text and RUN in text


# --------------------------------------------------------------------------- #
# reconcile：状态迁移
# --------------------------------------------------------------------------- #
def test_not_submitted_releases_back_to_uploaded() -> None:
    after = reconcile(_state("submission_unknown"), not_submitted=True)

    assert after.status == "uploaded"
    assert after.task_id is None


def test_not_submitted_is_refused_when_upstream_already_accepted() -> None:
    """有 task_id 就说明上游受理了：解封 = 允许再提交一次 = 第二份账单。"""
    with pytest.raises(BadRequest) as exc:
        reconcile(
            _state("submission_unknown", task_id="20261117000000000000001"),
            not_submitted=True,
        )

    assert "--adopt-task" in exc.value.message


def test_adopt_task_resumes_the_same_upstream_job() -> None:
    after = reconcile(_state("submission_unknown"), adopt_task=" 20261117000000000000001 ")

    assert after.status == "running"
    assert after.task_id == "20261117000000000000001"


def test_adopt_task_refuses_to_overwrite_a_different_id() -> None:
    with pytest.raises(BadRequest):
        reconcile(_state("submission_unknown", task_id="111"), adopt_task="222")


def test_exactly_one_of_the_two_actions() -> None:
    with pytest.raises(BadRequest):
        reconcile(_state("submission_unknown"))
    with pytest.raises(BadRequest):
        reconcile(_state("submission_unknown"), not_submitted=True, adopt_task="222")


def test_only_reconcilable_states_can_be_touched() -> None:
    """已经出片 / 上游自己判失败的档案不给解封：前者没必要，后者会再花一次钱。"""
    for status in ("success", "failed", "prepared"):
        with pytest.raises(BadRequest):
            reconcile(_state(status), not_submitted=True)


def test_refuses_while_the_executor_is_still_running() -> None:
    """执行器还在跑时不许改账。

    `submitting` 正是「桥接层正在提交」的那一瞬间：此时解封会让档案说「没提交」，
    而桥接层随后写回 `task_id` —— 钱可能已经花了，两边从此对不上。
    """
    with pytest.raises(BadRequest) as exc:
        reconcile(_state("submission_unknown"), not_submitted=True, executor_busy=True)

    assert "执行器" in exc.value.message


# --------------------------------------------------------------------------- #
# 自动对账：用消费明细替人做「去 LibTV 看一眼」
# --------------------------------------------------------------------------- #
from modules.model_driver.libtv.account import ConsumptionItem  # noqa: E402
from modules.model_driver.libtv.reconcile import auto_verdict  # noqa: E402

PROMPT = "真人实拍电商口播。9:16 竖屏，正面平视固定中景，一镜到底。全程无字幕、无水印。"
SINCE = 1_794_900_000


def _spend(task_id: str, seconds: int, summary: str) -> ConsumptionItem:
    return ConsumptionItem(
        amount=80,
        seconds=seconds,
        source_name="LibTV生视频",
        task_id=task_id,
        project_name="批量生成1",
        model_name="Wan 3.0",
        summary=summary,
    )


def test_auto_adopts_when_the_charge_exists() -> None:
    """消费明细里有这条 → 上游真建了任务，接回它，不要重提。"""
    verdict = auto_verdict(
        [_spend("20261118101503751524489", SINCE + 120, PROMPT[:60])], prompt=PROMPT, since=SINCE
    )

    assert verdict.action == "adopt"
    assert verdict.task_id == "20261118101503751524489"


def test_auto_releases_when_there_is_no_charge() -> None:
    """要能说「没扣费」，得先证明**翻到了提交时刻之前**（否则只是没看到）。"""
    older = _spend("20261117153703957194489", SINCE - 3600, "别的任务")
    verdict = auto_verdict([older], prompt=PROMPT, since=SINCE)

    assert verdict.action == "release"


def test_auto_refuses_to_release_without_covering_the_window() -> None:
    """只翻了最近几条、没覆盖到提交时刻 → 不敢断言「没扣费」，交回给人。"""
    assert auto_verdict([], prompt=PROMPT, since=SINCE).action == "unknown"
    only_newer = [_spend("20261118101503751524489", SINCE + 60, "别的任务")]
    assert auto_verdict(only_newer, prompt=PROMPT, since=SINCE).action == "unknown"


def test_auto_ignores_charges_from_before_this_submission() -> None:
    """更早的同款提示词属于另一次任务，算进来就会接错。"""
    verdict = auto_verdict(
        [_spend("20261117153703957194489", SINCE - 7200, PROMPT[:60])], prompt=PROMPT, since=SINCE
    )

    assert verdict.action == "release"


def test_auto_ignores_records_with_a_different_prompt() -> None:
    verdict = auto_verdict(
        [
            _spend("20261118100000000000001", SINCE + 60, "【目标与产品】生成12秒、9:16、720P"),
            _spend("20261117153703957194489", SINCE - 60, "更早的一条，用来证明窗口被覆盖"),
        ],
        prompt=PROMPT,
        since=SINCE,
    )

    assert verdict.action == "release"


def test_auto_refuses_to_guess_between_two_candidates() -> None:
    verdict = auto_verdict(
        [
            _spend("20261118101503751524489", SINCE + 60, PROMPT[:60]),
            _spend("20261118101603751524490", SINCE + 90, PROMPT[:60]),
        ],
        prompt=PROMPT,
        since=SINCE,
    )

    assert verdict.action == "ambiguous"
    assert verdict.candidates == ["20261118101503751524489", "20261118101603751524490"]


def test_auto_ignores_task_ids_already_claimed_by_other_tasks() -> None:
    """同一提示词批量跑多单：另一单已认领的 task_id 绝不能被当前单误接回。"""
    verdict = auto_verdict(
        [
            _spend("20261118101503751524489", SINCE + 60, PROMPT[:60]),
            _spend("20261117153703957194489", SINCE - 60, "更早的记录"),
        ],
        prompt=PROMPT,
        since=SINCE,
        claimed_task_ids={"20261118101503751524489"},
    )

    assert verdict.action == "release"


def test_auto_normalizes_newlines_and_tabs_in_summary() -> None:
    multiline_prompt = "真人实拍\n电商口播。\t9:16 竖屏，正面平视固定中景。"
    verdict = auto_verdict(
        [
            _spend(
                "20261118101503751524489",
                SINCE + 60,
                "真人实拍电商口播。\n\t9:16 竖屏，正面平视固定中景。",
            )
        ],
        prompt=multiline_prompt,
        since=SINCE,
    )

    assert verdict.action == "adopt"
    assert verdict.task_id == "20261118101503751524489"


def test_cli_auto_verdict_refuses_when_created_at_is_missing(tmp_path: Path) -> None:
    import asyncio

    from modules.model_driver.cli import _auto_verdict

    (tmp_path / "inputs").mkdir()
    (tmp_path / "inputs/prompt.txt").write_text(PROMPT, encoding="utf-8")
    before = LibTVState.model_validate(
        {"id": RUN, "status": "submission_unknown", "browser_url": ACCOUNT}
    )
    assert before.created_at is None

    verdict = asyncio.run(_auto_verdict(tmp_path, before))
    assert verdict.action == "unknown"
