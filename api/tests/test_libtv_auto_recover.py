"""「回执未知」不再让人二选一：重试时先自证清白。

用户点了重试 = 他想提交。此时归档说"上次回执未知"，桥接层会拒绝一切重提。
以前这里直接抛一段命令让人手工核对；现在先用消费明细判断：

- 命中一条 → 上游真建了任务，直接接回（不花钱）；
- 确认没扣费 → 当场解封并继续提交（就是用户想做的事）；
- 判断不了 → 回到原来那段命令（宁可多问一句）。

这里只测判定这一步（`_resolve_unknown`），不碰真实上游。
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

import pytest

from modules.model_driver.drivers.libtv import _resolve_unknown
from modules.model_driver.libtv import account
from modules.model_driver.libtv.account import ConsumptionItem, ConsumptionPage
from modules.model_driver.libtv.state import LibTVState

TASK = "20261118101503751524489"
SINCE = datetime(2026, 11, 18, 2, 11, 0, tzinfo=UTC)
PROMPT = "真人实拍电商口播质感。9:16 竖屏，10 秒，正面平视固定中景，一镜到底，仅保留人物对白。"


def _run(tmp_path: Path, items: list[ConsumptionItem]) -> tuple[str, str]:
    (tmp_path / "inputs").mkdir()
    (tmp_path / "inputs/prompt.txt").write_text(PROMPT, encoding="utf-8")
    previous = LibTVState(
        id="a" * 32, status="submission_unknown", created_at=SINCE, browser_url="http://127.0.0.1:9412"
    )

    async def fake_consumption(page: int, size: int, account_url: str) -> ConsumptionPage:
        return ConsumptionPage(total=len(items), page=page, page_size=size, items=items)

    original = account.consumption
    account.consumption = fake_consumption  # type: ignore[assignment]
    try:
        return asyncio.run(_resolve_unknown(tmp_path, previous, "http://127.0.0.1:9412"))
    finally:
        account.consumption = original  # type: ignore[assignment]


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


def test_adopts_when_the_charge_is_there(tmp_path: Path) -> None:
    action, task_id = _run(
        tmp_path,
        [
            _spend(TASK, int(SINCE.timestamp()) + 30, PROMPT[:30]),
            _spend("20261117153703957194489", int(SINCE.timestamp()) - 3600, "更早的一条"),
        ],
    )

    assert (action, task_id) == ("adopt", TASK)


def test_returns_unknown_when_no_charge_reached_the_submission_moment(tmp_path: Path) -> None:
    """消费流水仅在任务结算后入账：未命中时绝不自动解封重提，避免排队中的任务重复下单。"""
    action, task_id = _run(
        tmp_path,
        [_spend("20261117153703957194489", int(SINCE.timestamp()) - 3600, "更早的一条")],
    )

    assert (action, task_id) == ("unknown", "")


def test_refuses_to_guess_when_the_window_is_not_covered(tmp_path: Path) -> None:
    """只翻到提交之后的记录：这时候「没命中」说明不了问题，交回给人。"""
    action, _ = _run(
        tmp_path, [_spend(TASK, int(SINCE.timestamp()) + 30, "别的任务")]
    )

    assert action == "unknown"


@pytest.mark.parametrize("missing", ["inputs", "inputs/prompt.txt"])
def test_unknown_without_the_prompt_snapshot(tmp_path: Path, missing: str) -> None:
    """档案不全时不许猜：没有 prompt.txt 就没法按提示词对账。"""
    if missing == "inputs/prompt.txt":
        (tmp_path / "inputs").mkdir()
        (tmp_path / "inputs/prompt.txt").write_text("", encoding="utf-8")
        (tmp_path / "inputs/prompt.txt").unlink()

    previous = LibTVState(id="a" * 32, status="submission_unknown", created_at=SINCE)
    action, _ = asyncio.run(_resolve_unknown(tmp_path, previous, "http://127.0.0.1:9412"))

    assert action == "unknown"


def test_unknown_when_run_json_lacks_created_at(tmp_path: Path) -> None:
    """历史 run.json 缺少 created_at 时绝不能用 now() 兜底，否则会把已扣费任务误判为未提交。"""
    (tmp_path / "inputs").mkdir()
    (tmp_path / "inputs/prompt.txt").write_text(PROMPT, encoding="utf-8")
    previous = LibTVState.model_validate(
        {"id": "a" * 32, "status": "submission_unknown", "browser_url": "http://127.0.0.1:9412"}
    )
    assert previous.created_at is None

    action, _ = asyncio.run(_resolve_unknown(tmp_path, previous, "http://127.0.0.1:9412"))
    assert action == "unknown"


def test_unknown_when_consumption_query_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from core.exceptions import UpstreamError

    (tmp_path / "inputs").mkdir()
    (tmp_path / "inputs/prompt.txt").write_text(PROMPT, encoding="utf-8")
    previous = LibTVState(
        id="a" * 32, status="submission_unknown", created_at=SINCE, browser_url="http://127.0.0.1:9412"
    )

    async def broken_consumption(page: int, size: int, account_url: str) -> ConsumptionPage:
        raise UpstreamError("browser offline")

    monkeypatch.setattr(account, "consumption", broken_consumption)
    action, _ = asyncio.run(_resolve_unknown(tmp_path, previous, "http://127.0.0.1:9412"))
    assert action == "unknown"


def test_submit_persists_adopted_task_into_run_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """自动接回上游任务时必须写回 run.json，否则后续 poll 会报 No task ID。"""
    from infra.ai.base import Capability, GenerationRequest
    from modules.model_driver.drivers import libtv as driver_mod
    from modules.model_driver.libtv import executor

    (tmp_path / "inputs").mkdir()
    (tmp_path / "inputs/prompt.txt").write_text(PROMPT, encoding="utf-8")
    (tmp_path / "inputs/request.json").write_text("{}", encoding="utf-8")
    state_holder = {
        "meta": LibTVState(
            id="a" * 32,
            status="submission_unknown",
            created_at=SINCE,
            browser_url="http://127.0.0.1:9412",
        )
    }

    async def fake_materialize(request: GenerationRequest) -> None:
        return None

    async def fake_consumption(page: int, size: int, account_url: str) -> ConsumptionPage:
        return ConsumptionPage(
            total=2,
            page=page,
            page_size=size,
            items=[
                _spend(TASK, int(SINCE.timestamp()) + 30, PROMPT[:30]),
                _spend("20261117153703957194489", int(SINCE.timestamp()) - 3600, "更早的一条"),
            ],
        )

    monkeypatch.setattr(driver_mod, "materialize", fake_materialize)
    monkeypatch.setattr(executor, "run_path", lambda _: tmp_path)
    monkeypatch.setattr(executor, "browser_for", lambda _: "http://127.0.0.1:9412")
    monkeypatch.setattr(executor, "state", lambda _: state_holder["meta"])
    monkeypatch.setattr(
        executor, "save_state", lambda _, meta: state_holder.__setitem__("meta", meta)
    )
    monkeypatch.setattr(account, "consumption", fake_consumption)

    req = GenerationRequest(
        capability=Capability.VIDEO,
        model="libtv-wan3-replacement",
        prompt=PROMPT,
        params={"archiveRunId": "a" * 32},
        request_id="a" * 32,
    )
    external_id = asyncio.run(driver_mod.LibTVProvider().submit(req))

    assert external_id == f"{'a' * 32}:{TASK}"
    assert state_holder["meta"].task_id == TASK
    assert state_holder["meta"].status == "running"


def test_empty_consumption_page_stops_pagination(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "inputs").mkdir()
    (tmp_path / "inputs/prompt.txt").write_text(PROMPT, encoding="utf-8")
    pages: list[int] = []

    async def empty_consumption(page: int, size: int, account_url: str) -> ConsumptionPage:
        pages.append(page)
        return ConsumptionPage(total=0, page=page, page_size=size, items=[])

    monkeypatch.setattr(account, "consumption", empty_consumption)
    verdict = asyncio.run(
        account.auto_reconcile_verdict(
            tmp_path, SINCE, "http://127.0.0.1:9412", claimed_task_ids=()
        )
    )

    assert verdict.action == "unknown"
    assert pages == [1]


def test_fails_closed_when_claimed_ids_query_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "inputs").mkdir()
    (tmp_path / "inputs/prompt.txt").write_text(PROMPT, encoding="utf-8")

    async def broken_claimed() -> set[str]:
        raise OSError("db down")

    monkeypatch.setattr(account, "_claimed_upstream_task_ids", broken_claimed)
    verdict = asyncio.run(account.auto_reconcile_verdict(tmp_path, SINCE, "http://127.0.0.1:9412"))

    assert verdict.action == "unknown"


def test_excludes_task_id_already_claimed_in_db(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """同提示词批量任务中，另一条 VideoTask 已认领的 task_id 不会被当前单接回。"""
    import uuid

    from db.session import get_session_factory
    from modules.task.models import VideoTask

    other_id = uuid.uuid4().hex
    claimed_task = "20261118109999999999999"

    async def seed_claimed() -> None:
        async with get_session_factory()() as session:
            session.add(
                VideoTask(
                    id=other_id,
                    title="claimed-sibling",
                    prompt=PROMPT,
                    model="libtv-wan3-replacement",
                    model_id="libtv-wan3-replacement",
                    status="completed",
                    author_id="test-user",
                    external_id=f"{other_id}:{claimed_task}",
                )
            )
            await session.commit()

    async def cleanup_claimed() -> None:
        async with get_session_factory()() as session:
            row = await session.get(VideoTask, other_id)
            if row is not None:
                await session.delete(row)
                await session.commit()

    async def fake_consumption(page: int, size: int, account_url: str) -> ConsumptionPage:
        return ConsumptionPage(
            total=2,
            page=page,
            page_size=size,
            items=[
                _spend(claimed_task, int(SINCE.timestamp()) + 30, PROMPT[:30]),
                _spend("20261117153703957194489", int(SINCE.timestamp()) - 3600, "更早的一条"),
            ],
        )

    (tmp_path / "inputs").mkdir()
    (tmp_path / "inputs/prompt.txt").write_text(PROMPT, encoding="utf-8")
    monkeypatch.setattr(account, "consumption", fake_consumption)
    asyncio.run(seed_claimed())
    try:
        verdict = asyncio.run(
            account.auto_reconcile_verdict(tmp_path, SINCE, "http://127.0.0.1:9412")
        )
    finally:
        asyncio.run(cleanup_claimed())

    assert verdict.action == "unknown"


def test_bare_external_ids_do_not_count_as_claimed_upstream_tasks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """别的驱动会把**裸 id**（无 `run:` 前缀）写进 external_id：它们和 LibTV 的
    `originalBizNo` 不是一个命名空间，不能拿来挡掉本单该接回的那一条。"""
    import uuid

    from db.session import get_session_factory
    from modules.task.models import VideoTask

    other_id = uuid.uuid4().hex
    upstream_task = "20261118109999999999999"

    async def seed_claimed() -> None:
        async with get_session_factory()() as session:
            session.add(
                VideoTask(
                    id=other_id,
                    title="agnes-sibling",
                    prompt=PROMPT,
                    model="agnes-video",
                    model_id="agnes-video",
                    status="completed",
                    author_id="test-user",
                    external_id=upstream_task,  # 裸 id：不是本系统记的 `run:task`
                )
            )
            await session.commit()

    async def cleanup_claimed() -> None:
        async with get_session_factory()() as session:
            row = await session.get(VideoTask, other_id)
            if row is not None:
                await session.delete(row)
                await session.commit()

    async def fake_consumption(page: int, size: int, account_url: str) -> ConsumptionPage:
        return ConsumptionPage(
            total=1,
            page=page,
            page_size=size,
            items=[_spend(upstream_task, int(SINCE.timestamp()) + 30, PROMPT[:30])],
        )

    (tmp_path / "inputs").mkdir()
    (tmp_path / "inputs/prompt.txt").write_text(PROMPT, encoding="utf-8")
    monkeypatch.setattr(account, "consumption", fake_consumption)
    asyncio.run(seed_claimed())
    try:
        verdict = asyncio.run(
            account.auto_reconcile_verdict(tmp_path, SINCE, "http://127.0.0.1:9412")
        )
    finally:
        asyncio.run(cleanup_claimed())

    assert (verdict.action, verdict.task_id) == ("adopt", upstream_task)


def test_naive_created_at_is_treated_as_utc(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """不带时区的 created_at 必须按 UTC 解释，否则东八区会往前错 8 小时扫进旧单。"""
    naive_since = datetime(2026, 11, 18, 2, 11, 0)
    parsed = LibTVState.model_validate(
        {"id": "d" * 32, "status": "submission_unknown", "created_at": "2026-11-18T02:11:00"}
    )
    assert parsed.created_at is not None and parsed.created_at.tzinfo == UTC

    seen_since: list[int] = []
    monkeypatch.setattr(
        account,
        "consumption",
        lambda page, size, account_url: asyncio.sleep(
            0,
            result=ConsumptionPage(
                total=1,
                page=page,
                page_size=size,
                items=[_spend(TASK, int(SINCE.timestamp()) - 3600, PROMPT[:30])],
            ),
        ),
    )
    orig_verdict = account.auto_verdict

    def capture_verdict(items, *, prompt, since, claimed_task_ids=()):  # type: ignore[no-untyped-def]
        seen_since.append(since)
        return orig_verdict(items, prompt=prompt, since=since, claimed_task_ids=claimed_task_ids)

    monkeypatch.setattr(account, "auto_verdict", capture_verdict)
    (tmp_path / "inputs").mkdir()
    (tmp_path / "inputs/prompt.txt").write_text(PROMPT, encoding="utf-8")
    verdict = asyncio.run(
        account.auto_reconcile_verdict(
            tmp_path, naive_since, "http://127.0.0.1:9412", claimed_task_ids=()
        )
    )

    assert seen_since == [int(SINCE.timestamp())]
    assert verdict.action == "unknown"


def test_submitting_status_also_enters_auto_recover(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from infra.ai.base import Capability, GenerationRequest
    from modules.model_driver.drivers import libtv as driver_mod
    from modules.model_driver.libtv import executor

    (tmp_path / "inputs").mkdir()
    (tmp_path / "inputs/prompt.txt").write_text(PROMPT, encoding="utf-8")
    (tmp_path / "inputs/request.json").write_text("{}", encoding="utf-8")
    state_holder = {
        "meta": LibTVState(
            id="b" * 32,
            status="submitting",
            created_at=SINCE,
            browser_url="http://127.0.0.1:9412",
        )
    }

    async def fake_materialize(request: GenerationRequest) -> None:
        return None

    async def fake_consumption(page: int, size: int, account_url: str) -> ConsumptionPage:
        return ConsumptionPage(
            total=2,
            page=page,
            page_size=size,
            items=[
                _spend(TASK, int(SINCE.timestamp()) + 30, PROMPT[:30]),
                _spend("20261117153703957194489", int(SINCE.timestamp()) - 3600, "更早的一条"),
            ],
        )

    monkeypatch.setattr(driver_mod, "materialize", fake_materialize)
    monkeypatch.setattr(executor, "run_path", lambda _: tmp_path)
    monkeypatch.setattr(executor, "browser_for", lambda _: "http://127.0.0.1:9412")
    monkeypatch.setattr(executor, "state", lambda _: state_holder["meta"])
    monkeypatch.setattr(
        executor, "save_state", lambda _, meta: state_holder.__setitem__("meta", meta)
    )
    monkeypatch.setattr(account, "consumption", fake_consumption)

    req = GenerationRequest(
        capability=Capability.VIDEO,
        model="libtv-wan3-replacement",
        prompt=PROMPT,
        params={"archiveRunId": "b" * 32},
        request_id="b" * 32,
    )
    external_id = asyncio.run(driver_mod.LibTVProvider().submit(req))

    assert external_id == f"{'b' * 32}:{TASK}"
    assert state_holder["meta"].status == "running"


def test_requeue_when_previous_executor_still_busy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from core.exceptions import RateLimited
    from infra.ai.base import Capability, GenerationRequest
    from modules.model_driver.drivers import libtv as driver_mod
    from modules.model_driver.libtv import executor, process

    (tmp_path / "inputs").mkdir()
    (tmp_path / "inputs/request.json").write_text("{}", encoding="utf-8")
    meta = LibTVState(
        id="c" * 32,
        status="submitting",
        created_at=SINCE,
        browser_url="http://127.0.0.1:9412",
    )

    async def fake_materialize(request: GenerationRequest) -> None:
        return None

    monkeypatch.setattr(driver_mod, "materialize", fake_materialize)
    monkeypatch.setattr(executor, "run_path", lambda _: tmp_path)
    monkeypatch.setattr(executor, "browser_for", lambda _: "http://127.0.0.1:9412")
    monkeypatch.setattr(executor, "state", lambda _: meta)
    monkeypatch.setattr(process, "executor_busy", lambda _: True)

    req = GenerationRequest(
        capability=Capability.VIDEO,
        model="libtv-wan3-replacement",
        prompt=PROMPT,
        params={"archiveRunId": "c" * 32},
        request_id="c" * 32,
    )
    with pytest.raises(RateLimited):
        asyncio.run(driver_mod.LibTVProvider().submit(req))
