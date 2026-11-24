"""工作区 GC 的四条判据（2026-11-24 补）。

GC 会**物理删除**运行工作区，所以判据必须逐条钉住 —— 其中「没有 DB 回执
就不删」是二次计费的最后一道防线：工作区是唯一凭证，删了就再也判断不出
这个 run 到底提交过没有（重提 = 二次计费）。

四条判据（全满足才删）：
1. 终态：run.json 的 status 在终态集合里
2. 已迁 DB：`submission_receipts` 里有这条 run 的回执
3. 超过保留期：默认 30 天
4. 无锁：有锁文件的目录一律跳过
"""

import json
import time

import pytest
from sqlalchemy import delete

from db.session import get_session_factory
from modules.model_driver import maintenance
from modules.model_driver.models import SubmissionReceipt

#: 超过 30 天保留期（与 `maintenance.WORKSPACE_RETENTION_SEC` 同口径）。
OLD = time.time() - 60 * 24 * 3600
#: 还在保留期内。
RECENT = time.time() - 3600


@pytest.fixture
async def clean_receipts():
    """回执在全局测试库里，不像文件那样随 tmp_path 隔离 —— 每条测试前后清表。"""
    async with get_session_factory()() as session:
        await session.execute(delete(SubmissionReceipt))
        await session.commit()
    yield
    async with get_session_factory()() as session:
        await session.execute(delete(SubmissionReceipt))
        await session.commit()


def _make_run(root, *, run_id="run-old", status="succeeded", age=OLD):
    """造一个运行目录（`run.json` 在目录里，父目录放锁）。"""
    folder = root / "2026" / "11" / "01" / run_id
    folder.mkdir(parents=True)
    (folder / "run.json").write_text(
        json.dumps({"run_id": run_id, "status": status, "updated_at": age}),
        encoding="utf-8",
    )
    return folder


async def _add_receipt(run_id: str, *, provider: str = "bailian") -> None:
    async with get_session_factory()() as session:
        session.add(
            SubmissionReceipt(
                provider=provider,
                run_id=run_id,
                model="wan2.2-t2v-plus",
                status="submitted",
                external_id="task-1",
            )
        )
        await session.commit()


def _lock(folder) -> None:
    locks = folder.parent / ".locks"
    locks.mkdir(parents=True, exist_ok=True)
    (locks / f"{folder.name}.lock").write_text("", encoding="utf-8")


async def test_gc_keeps_a_workspace_without_a_receipt(tmp_path, monkeypatch, clean_receipts):
    """没有 DB 回执 → **绝不删**。

    工作区是唯一凭证：没有回执就说明还没迁，删了等于把「这个 run 提交过
    没有」的证据删掉 → 后续重提会二次计费。
    """
    folder = _make_run(tmp_path)
    monkeypatch.setattr(maintenance, "existing_runs", lambda: [folder])

    await maintenance.gc_old_workspaces()

    assert folder.exists(), "没有 DB 回执的工作区必须保留"


async def test_gc_removes_an_old_terminal_workspace_with_a_receipt(
    tmp_path, monkeypatch, clean_receipts
):
    """四条判据全满足 → 删。"""
    folder = _make_run(tmp_path)
    await _add_receipt("run-old")
    monkeypatch.setattr(maintenance, "existing_runs", lambda: [folder])

    await maintenance.gc_old_workspaces()

    assert not folder.exists(), "终态 + 有回执 + 超期 + 无锁就该清掉"


async def test_gc_keeps_a_locked_workspace(tmp_path, monkeypatch, clean_receipts):
    """有锁 → 跳过。执行器可能还活着，只是暂时没写状态。"""
    folder = _make_run(tmp_path)
    await _add_receipt("run-old")
    _lock(folder)
    monkeypatch.setattr(maintenance, "existing_runs", lambda: [folder])

    await maintenance.gc_old_workspaces()

    assert folder.exists(), "有锁的工作区必须保留"


async def test_gc_keeps_a_running_workspace(tmp_path, monkeypatch, clean_receipts):
    """非终态 → 跳过。正在跑的任务绝不碰。"""
    folder = _make_run(tmp_path, status="running")
    await _add_receipt("run-old")
    monkeypatch.setattr(maintenance, "existing_runs", lambda: [folder])

    await maintenance.gc_old_workspaces()

    assert folder.exists(), "正在跑的工作区必须保留"


async def test_gc_keeps_a_recent_workspace(tmp_path, monkeypatch, clean_receipts):
    """还在保留期内 → 跳过（即便已终态且已回执）。"""
    folder = _make_run(tmp_path, age=RECENT)
    await _add_receipt("run-old")
    monkeypatch.setattr(maintenance, "existing_runs", lambda: [folder])

    await maintenance.gc_old_workspaces()

    assert folder.exists(), "保留期内的工作区必须保留"


async def test_gc_ignores_a_broken_run_json(tmp_path, monkeypatch, clean_receipts):
    """run.json 坏了 → 跳过，不猜。

    判不出来状态就删，等于赌它跑完了。宁可留垃圾，不能删错。
    """
    folder = _make_run(tmp_path)
    (folder / "run.json").write_text("{ not json", encoding="utf-8")
    await _add_receipt("run-old")
    monkeypatch.setattr(maintenance, "existing_runs", lambda: [folder])

    await maintenance.gc_old_workspaces()

    assert folder.exists(), "状态判不出来的目录必须保留"


async def test_gc_does_not_touch_other_runs(tmp_path, monkeypatch, clean_receipts):
    """只清命中的那个目录，别的运行目录不受影响。"""
    old = _make_run(tmp_path, run_id="run-old")
    other = _make_run(tmp_path, run_id="run-other", status="running")
    await _add_receipt("run-old")
    monkeypatch.setattr(maintenance, "existing_runs", lambda: [old, other])

    await maintenance.gc_old_workspaces()

    assert not old.exists()
    assert other.exists()


async def test_gc_keeps_the_workspace_when_the_receipt_belongs_to_another_run(
    tmp_path, monkeypatch, clean_receipts
):
    """回执的 run_id 对不上 → 当作「没有回执」处理（不删）。

    GC 是按 run.json 里的 run_id 查回执的；查不到就是没迁过。宁可留垃圾。
    """
    folder = _make_run(tmp_path, run_id="run-old")
    await _add_receipt("run-someone-else")
    monkeypatch.setattr(maintenance, "existing_runs", lambda: [folder])

    await maintenance.gc_old_workspaces()

    assert folder.exists()


async def test_gc_keeps_the_workspace_when_the_run_id_is_missing(
    tmp_path, monkeypatch, clean_receipts
):
    """run.json 里没有 run_id → 判不出归属 → 不删。"""
    folder = tmp_path / "2026" / "11" / "01" / "run-x"
    folder.mkdir(parents=True)
    (folder / "run.json").write_text(
        json.dumps({"status": "succeeded", "updated_at": OLD}), encoding="utf-8"
    )
    monkeypatch.setattr(maintenance, "existing_runs", lambda: [folder])

    await maintenance.gc_old_workspaces()

    assert folder.exists()


async def test_gc_leaves_nothing_behind_after_deleting_a_workspace(
    tmp_path, monkeypatch, clean_receipts
):
    """删工作区后目录真的没了（不是只删文件、留下空壳）。"""
    folder = _make_run(tmp_path)
    (folder / "inputs").mkdir()
    (folder / "inputs/1.mp4").write_bytes(b"x")
    await _add_receipt("run-old")
    monkeypatch.setattr(maintenance, "existing_runs", lambda: [folder])

    await maintenance.gc_old_workspaces()

    assert not folder.exists()
    assert not (folder / "inputs").exists()
    assert not any(folder.parent.iterdir()), "运行目录的父目录不该留残余"
