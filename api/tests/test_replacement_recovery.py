"""回归：失败续做、锁恢复与原框架生命周期，全部使用隔离素材和上游替身。"""

from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

# 显式导入共享夹具供 pytest 注册。
from test_replacement_archive import archive_root, materials  # noqa: F401

from core.events import EventBus
from core.exceptions import Conflict, UpstreamError
from generation.runner import JOB_SUBMIT, GenerationRunner
from infra.ai.libtv import LibTVProvider
from infra.ai.libtv_process import (
    ACCOUNT_LOCK,
    clear_finished_lock,
    legacy_worker_active,
    supervise,
)
from infra.ai.libtv_state import ExecutorResult
from infra.archive import lease
from infra.archive.base import acquire, release
from infra.archive.local import LocalArchiveStorage
from infra.queue.base import Job
from infra.queue.memory import MemoryJobQueue
from modules.template import replacement_service as service
from modules.template.replacement_repository import ReplacementRepository
from modules.template.replacement_schema import PrepareReplacement
from modules.template.replacement_worker import recover_state, schedule_state, set_billing_mode


@pytest.fixture
def prepared(materials: tuple[dict, dict]) -> tuple[PrepareReplacement, Path]:  # noqa: F811
    source, image = materials
    payload = PrepareReplacement(
        request_id=uuid.uuid4(),
        kind="character",
        target="测试目标",
        source_id=source["id"],
        reference_ids=[image["id"]],
    )
    return payload, Path(service.prepare(payload, "u_001").archive_path)


def test_partial_prepare_is_indexed_and_same_request_can_resume(materials, monkeypatch):  # noqa: F811
    source, image = materials
    payload = PrepareReplacement(
        request_id=uuid.uuid4(),
        kind="character",
        target="续做目标",
        source_id=source["id"],
        reference_ids=[image["id"]],
    )
    original = service._compile

    def fail(*args):
        raise UpstreamError("模拟编译中断")

    monkeypatch.setattr(service, "_compile", fail)
    with pytest.raises(UpstreamError):
        service.prepare(payload, "u_001")
    repo = ReplacementRepository()
    path = repo.path(payload.request_id.hex)
    assert repo.load(path).status == "prepare_failed"
    snapshot = (path / "inputs/reference-video.mp4").read_bytes()
    monkeypatch.setattr(service, "_compile", original)
    result = service.prepare(payload, "u_001")
    assert result.id == payload.request_id.hex and result.status == "prepared"
    assert (path / "inputs/reference-video.mp4").read_bytes() == snapshot
    assert len(repo.all_paths()) == 1


def test_process_exit_releases_native_lease(tmp_path):
    target, ready, stop = tmp_path / "lease.lock", tmp_path / "ready", tmp_path / "stop"
    script = """
import os, sys, time
from pathlib import Path
from infra.archive import lease
ctx = lease(Path(sys.argv[1]))
ctx.__enter__()
Path(sys.argv[2]).write_text('ready')
while not Path(sys.argv[3]).exists():
    time.sleep(0.02)
os._exit(0)  # 不执行 finally，模拟真正崩溃，而非只杀 Windows venv 启动器。
"""
    child = subprocess.Popen([sys.executable, "-c", script, str(target), str(ready), str(stop)])
    try:
        import time

        deadline = time.monotonic() + 10
        while not ready.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert ready.exists()
        with pytest.raises(Conflict), lease(target):
            pass
    finally:
        stop.touch()
        child.wait(timeout=5)
    assert target.exists()
    with lease(target):
        pass


def test_stale_worker_lock_cleanup_requires_confirmed_exit(archive_root, monkeypatch):  # noqa: F811
    # run 目录必须落在归档根内：`storage.read` 只接受根内的位置（越界即拒绝），
    # 这条约束在把归档协议句柄化之后才真正生效。
    records = archive_root / "records"
    records.mkdir()
    lock = records / "worker.lock"
    lock.write_text("old")
    storage = LocalArchiveStorage()
    with pytest.raises(Conflict):
        clear_finished_lock(archive_root, storage)
    storage.write(
        records / "executor-result.json", ExecutorResult(node_pid=os.getpid()).model_dump()
    )
    with pytest.raises(Conflict):
        clear_finished_lock(archive_root, storage)
    assert lock.exists()
    monkeypatch.setattr("infra.ai.libtv_process.pid_alive", lambda pid: False)
    clear_finished_lock(archive_root, storage)
    assert not lock.exists()


def test_account_lock_does_not_collide_with_legacy_worker_lock(archive_root, tmp_path):  # noqa: F811
    """旧夜间工作台常驻持有的进程锁，不能被当成「账号执行器被占用」。

    `run_libtv_worker` 用 `with process_lock(): while True: tick()`，从启动一直
    持有 `libtv/state/workbench-worker.lock`。新工作台早期把同一把文件当作账号
    互斥锁，旧 worker 只要还在跑，每个阶段都会 EXECUTOR_BUSY（实测档案里
    upload 连挂 6 次、node 从未启动、node_pid 恒为 null）。
    """
    legacy = tmp_path / "legacy"
    (legacy / "libtv/state").mkdir(parents=True)
    stream = (legacy / "libtv/state/workbench-worker.lock").open("a+b")
    stream.write(b"0")
    stream.flush()
    stream.seek(0)
    acquire(stream)  # 模拟旧 worker 常驻持有
    try:
        assert legacy_worker_active(legacy) is True
        # 账号串行用的是归档盘上自己的锁，与旧进程锁互不相干。
        with lease(LocalArchiveStorage().resolve(ACCOUNT_LOCK)):
            pass
    finally:
        release(stream)
        stream.close()
    assert legacy_worker_active(legacy) is False


def test_billing_mode_switch_only_before_submission(prepared):
    """加速只能在提交前改；提交后重复确认只能算「继续查询」。

    已经交给上游的那一笔是按当时的队列发出去的，事后改档案只会让
    「这条到底走了免费队列还是积分队列」的核对失去依据。
    """
    _, path = prepared
    repo = ReplacementRepository()
    run_id = repo.load(path).id

    meta = repo.load(path)
    meta.task_id = "upstream-1"
    repo.save(path, meta)
    # 想把已在免费队列的任务改成积分：这会真花钱，必须拒绝并说清出路。
    with pytest.raises(Conflict):
        set_billing_mode(run_id, "u_001", "credits")
    assert repo.load(path).billing_mode == "free"

    # 反过来：已加速的任务被再点一次「确认生成」，那只是查询进度。
    # 这里若报错，前端就会把一次正常的查询显示成「执行请求失败」。
    meta = repo.load(path)
    meta.billing_mode = "credits"
    repo.save(path, meta)
    set_billing_mode(run_id, "u_001", "free")
    assert repo.load(path).billing_mode == "credits"


def test_switching_to_credits_releases_waiting_free(prepared):
    """免费队列报价非 0 才会停在 waiting_free；换积分队列后必须能继续。

    否则点了加速仍然停在原地，看起来和「按钮没反应」一模一样。
    """
    _, path = prepared
    repo = ReplacementRepository()
    meta = repo.load(path)
    meta.status = "waiting_free"
    meta.estimated_credits = 40
    repo.save(path, meta)

    set_billing_mode(meta.id, "u_001", "credits")
    updated = repo.load(path)
    assert updated.status == "uploaded"
    # 免费队列的报价不能当成积分队列的账单，重新估价后才能填。
    assert updated.estimated_credits is None


def test_run_lock_name_is_skipped_by_bridge_checksum_walk(prepared, tmp_path, monkeypatch):
    """锁文件必须命中桥接层 update() 的排除规则。

    这条约束不写死就会复发：锁叫 `.lease` 时，update() 遍历 records 给每个
    文件算 sha256，会读到被自己锁住的字节，SMB 上直接 EBUSY——自动执行必失败，
    手工执行却成功，极难定位。
    """
    from infra.ai import libtv_process
    from infra.ai.libtv_state import RUN_LOCK
    from modules.template.replacement_management import MANAGEMENT_LOCK
    from modules.template.replacement_review import REVIEW_LOCK

    # 三个锁都落在 update() 的遍历范围内（records/、review/），必须全部命中排除规则。
    for name in (RUN_LOCK, MANAGEMENT_LOCK, REVIEW_LOCK):
        assert re.search(r"\.(lock|download|tmp)$", name), name

    _, path = prepared
    seen: list[str] = []
    real = libtv_process.lease

    def spy(target, *args, **kwargs):
        seen.append(Path(target).name)
        return real(target, *args, **kwargs)

    monkeypatch.setattr(libtv_process, "lease", spy)
    libtv_process.supervise("poll", path, tmp_path / "legacy", "unused")

    assert seen == [Path(RUN_LOCK).name]


def test_supervisor_keeps_active_lock_and_receipt(prepared, tmp_path):
    _, path = prepared
    repo = ReplacementRepository()
    with lease(path / "records/executor.lock"):
        result = supervise("submit", path, tmp_path / "legacy", "unused")
        assert result.code == "EXECUTOR_BUSY"
        with pytest.raises(Conflict):
            schedule_state(repo.load(path).id, "u_001")
        with pytest.raises(Conflict):
            recover_state(repo.load(path).id)
    assert repo.load(path).status == "prepared"


async def test_api_wait_cancellation_does_not_kill_supervisor(prepared, monkeypatch):
    from infra.ai import libtv

    # 旧运行时根目录现在是驱动私有配置且默认为空（不再有 E:/yufan-ai 这种
    # 「只在那台机器上存在」的默认值），这里显式给一个，否则 execute_stage
    # 会在起子进程之前就按「未配置」报错
    monkeypatch.setattr(libtv.get_libtv_settings(), "legacy_root", "/tmp/legacy-stub")

    payload, _ = prepared
    import threading

    started, finished = threading.Event(), threading.Event()

    class Process:
        returncode = 1

        def communicate(self, payload: bytes) -> None:
            # 参数走 stdin 的 JSON（A4）：顺手验证启动方发出的确实是带版本的 JSON，
            # 而不是退化成位置参数
            body = json.loads(payload)
            assert body["stage"] == "submit"
            assert body["version"] >= 1
            started.set()
            finished.wait(timeout=5)

        def kill(self):
            pytest.fail("API 不能杀掉独立监督进程")

    def spawn(*args, **kwargs):
        return Process()

    monkeypatch.setattr(libtv.subprocess, "Popen", spawn)
    task = asyncio.create_task(libtv.execute_stage(payload.request_id.hex, "submit"))
    assert await asyncio.to_thread(started.wait, 5)
    try:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        finished.set()


async def test_replacement_runs_through_framework_and_events(prepared, monkeypatch):
    from generation import runner as framework
    from infra.ai import libtv

    payload, path = prepared
    repo = ReplacementRepository()
    meta = repo.load(path)
    meta.status = "uploaded"
    repo.save(path, meta)
    stages = []

    async def execute(run_id, stage):
        stages.append(stage)
        with repo.edit(run_id) as current:
            if stage == "submit":
                assert current.submit_attempts == 1
                current.task_id = "fake-task"
                current.status = "submitted"
            else:
                current.status = "success"
        return ExecutorResult(stage=stage)

    bus = EventBus()
    events = []

    @bus.subscribe("*")
    async def receive(event):
        events.append(event.name)

    monkeypatch.setattr(libtv, "execute_stage", execute)
    monkeypatch.setattr(
        framework,
        "get_model_router",
        lambda: SimpleNamespace(provider_for=lambda model: LibTVProvider()),
    )
    monkeypatch.setattr(framework, "get_event_bus", lambda: bus)
    monkeypatch.setattr(framework.settings, "generation_poll_interval_sec", 0)
    queue = MemoryJobQueue()
    runner = GenerationRunner(queue)
    await runner.handle_submit(
        Job(
            name=JOB_SUBMIT,
            payload={"kind": "template_replacement", "record_id": payload.request_id.hex},
        )
    )
    job = await queue.dequeue(timeout=1)
    assert job is not None and job.name == "generation.poll"
    await runner.handle_poll(job)
    latest = repo.load(path)
    assert latest.framework_status == "success"
    assert latest.task_id == "fake-task" and latest.submit_attempts == 0
    assert stages == ["submit", "poll"]
    assert events == ["generation.submitted", "generation.succeeded"]


@pytest.mark.parametrize("status,attempts", [("waiting_free", 0), ("submission_unknown", 1)])
async def test_framework_preserves_free_gate_and_unknown_receipts(
    prepared, monkeypatch, status, attempts
):
    from generation import runner as framework
    from infra.ai import libtv

    payload, path = prepared
    repo = ReplacementRepository()
    meta = repo.load(path)
    meta.status = "uploaded"
    repo.save(path, meta)

    async def execute(run_id, stage):
        with repo.edit(run_id) as current:
            current.status = status
        return ExecutorResult(stage=stage)

    monkeypatch.setattr(libtv, "execute_stage", execute)
    monkeypatch.setattr(
        framework,
        "get_model_router",
        lambda: SimpleNamespace(provider_for=lambda model: LibTVProvider()),
    )
    runner = GenerationRunner(MemoryJobQueue())
    await runner.handle_submit(
        Job(
            name=JOB_SUBMIT,
            payload={"kind": "template_replacement", "record_id": payload.request_id.hex},
        )
    )
    latest = repo.load(path)
    assert latest.status == status and latest.submit_attempts == attempts


async def test_template_provider_rejects_other_module_archive_pointer():
    from core.exceptions import BadRequest
    from infra.ai.base import Capability, GenerationRequest

    with pytest.raises(BadRequest):
        await LibTVProvider().submit(
            GenerationRequest(
                capability=Capability.VIDEO,
                model="libtv-wan3-replacement",
                prompt="",
                request_id="other-task",
                params={"archiveRunId": "a" * 32},
            )
        )


async def test_supervisor_records_exit_and_cleans_dead_node_lock(prepared, tmp_path, monkeypatch):
    from infra.ai import libtv_process

    _, path = prepared
    fake = tmp_path / "fake-bridge.mjs"
    fake.write_text(
        "import {writeFileSync} from 'node:fs';import path from 'node:path';"
        "writeFileSync(path.join(process.argv[3],'records/worker.lock'),'test');"
        "process.exitCode=1;",
        encoding="utf-8",
    )
    monkeypatch.setattr(libtv_process, "RUNTIME", fake)
    result = await asyncio.to_thread(
        libtv_process.run_node,
        "upload",
        path,
        tmp_path / "fake-legacy",
        "node",
        LocalArchiveStorage(),
    )
    assert result.code == "ADAPTER_FAILED" and result.phase == "finished"
    assert result.node_pid is not None
    assert not (path / "records/worker.lock").exists()
    assert LocalArchiveStorage().read(path / "records/executor-result.json")["phase"] == "finished"


async def test_stale_cached_bridge_is_replaced_at_stage_start(prepared, tmp_path, monkeypatch):
    """run/code 里残留的旧 bridge 必须被当前版本覆盖。

    之前只在文件不存在时复制，导致桥接层升级后老任务继续跑旧副本，
    表现是「代码改了、行为完全没变」，极难定位。
    """
    from infra.ai import libtv_process

    _, path = prepared
    cached = path / "code/runtime-v2/bridge.mjs"
    cached.parent.mkdir(parents=True, exist_ok=True)
    cached.write_text("// stale bridge", encoding="utf-8")
    fake = tmp_path / "fake-bridge.mjs"
    fake.write_text("process.exitCode=0;", encoding="utf-8")
    monkeypatch.setattr(libtv_process, "RUNTIME", fake)

    await asyncio.to_thread(
        libtv_process.run_node,
        "upload",
        path,
        tmp_path / "fake-legacy",
        "node",
        LocalArchiveStorage(),
    )

    assert cached.read_text(encoding="utf-8") == "process.exitCode=0;"


async def test_queue_failure_remains_visible_and_retryable(prepared, monkeypatch):
    from core.config import settings
    from modules.template import replacement_worker as worker

    # 本例隔离验证入队故障恢复；发布授权另由跨账号集成用例覆盖。
    monkeypatch.setattr(worker.MediaCatalog, "check_run", lambda *_: None)

    payload, path = prepared

    async def unavailable(*args, **kwargs):
        raise RuntimeError("模拟队列不可用")

    monkeypatch.setattr(settings, "worker_enabled", True)
    monkeypatch.setattr(worker, "enqueue", unavailable)
    with pytest.raises(UpstreamError):
        await worker.schedule(payload.request_id.hex, "u_001")
    meta = ReplacementRepository().load(path)
    assert meta.status == "service_error" and not meta.task_id and not meta.submit_attempts


# --------------------------------------------------------------------------- #
# R14-02：恢复集合必须收敛（终态不再入队）
# --------------------------------------------------------------------------- #
def test_resume_converges_and_skips_settled_runs(archive_root, monkeypatch):  # noqa: F811
    """两次启动投递的必须是不同批次：第二次应为空。

    原来用 `meta.task_id` 做条件，命中所有历史成功记录——成功记录每次启动都白投
    一次，而「本地已判失败但有任务号」的记录会被 `recover_state` 翻回 running
    并再查一次上游，集合永不收敛（R14-02）。
    """
    from core.config import settings
    from modules.template import replacement_worker as worker
    from modules.template.replacement_repository import ReplacementManifest

    repo = ReplacementRepository()

    def make(status: str, *, attempts: int = 0, task_id: str | None = None) -> str:
        run_id = uuid.uuid4().hex
        path = archive_root / "runs/2026/11/06" / f"character-{run_id}"
        (path / "records").mkdir(parents=True, exist_ok=True)
        repo.save(
            path,
            ReplacementManifest(
                id=run_id,
                kind="character",
                target="收敛用例",
                owner_id="u_001",
                fingerprint="converge",
                status=status,
                submit_attempts=attempts,
                task_id=task_id,
            ),
        )
        repo.index(path, run_id)
        return run_id

    success = make("success", task_id="T-SUCCESS")
    failed = make("failed", task_id="T-FAILED")
    unknown = make("submission_unknown", attempts=1)
    queued = make("queued")

    class _Queue:
        def __init__(self) -> None:
            self.sent: list[str] = []

        async def enqueue(self, job: Job, delay: float = 0) -> None:
            self.sent.append(str(job.payload["id"]))

    queue = _Queue()
    monkeypatch.setattr(worker, "get_queue", lambda: queue)
    # R15-01：不再把 `app_env` 改成 prod 才敢调 resume()。下面这条断言就是那道
    # 环境早退的回归锁——它一旦回来，本用例立刻红（而不是「悄悄什么都不做」）。
    assert settings.app_env == "test", "本用例刻意在 test 环境下跑恢复扫描"
    monkeypatch.setattr(settings, "worker_enabled", True)

    asyncio.run(worker.resume())
    first = set(queue.sent)
    queue.sent.clear()
    asyncio.run(worker.resume())
    second = set(queue.sent)

    assert first == {queued}, "只有「还需要推进」的档案该入队"
    # `queued` 那条没有任何 worker 消费过，它本来就该继续被恢复——收敛指的是
    # 「终态不再回来」，不是「第二次一条都不投」。
    assert second == {queued}, "第二次启动把终态记录又捞回来了：恢复集合没有收敛"
    assert not ({success, failed, unknown} & (first | second))


# --------------------------------------------------------------------------- #
# R15-01：恢复链路的可测性（不再靠 APP_ENV 早退）与「等锁」的上限
# --------------------------------------------------------------------------- #
class _RecordingQueue:
    """记下每次投递的作业与延迟：重投轮次、退避曲线都要能看到。"""

    def __init__(self) -> None:
        self.sent: list[tuple[Job, float]] = []

    async def enqueue(self, job: Job, delay: float = 0) -> None:
        self.sent.append((job, delay))


def _repo_with(manifest, *, repair_error: Exception | None = None):
    class _Repo:
        repaired = 0
        enumerated = 0

        def repair_indexes(self) -> None:
            if repair_error is not None:
                raise repair_error
            self.repaired += 1

        def all_paths(self) -> list[Path]:
            self.enumerated += 1
            return [Path("/injected/run")]

        def load(self, path: Path):
            return manifest

    return _Repo()


def test_resume_takes_repository_as_explicit_input(monkeypatch):
    """注入 repo 后不得再去碰全局共享盘。

    这是替掉 `app_env == "test"` 早退的东西：测试要隔离的是**数据源**，所以就让
    数据源变成参数，而不是让整段逻辑在某个环境里不存在。
    """
    from core.config import settings
    from modules.template import replacement_worker as worker
    from modules.template.replacement_repository import ReplacementManifest

    manifest = ReplacementManifest(
        id="f" * 32,
        kind="character",
        target="注入用例",
        owner_id="u_001",
        fingerprint="injected",
        status="queued",
    )
    repo = _repo_with(manifest)
    queue = _RecordingQueue()
    monkeypatch.setattr(worker, "get_queue", lambda: queue)
    monkeypatch.setattr(settings, "worker_enabled", True)

    def _no_global_repo(*args, **kwargs):
        raise AssertionError("resume() 去碰全局共享盘了：应该只用注入的 repo")

    monkeypatch.setattr(worker, "ReplacementRepository", _no_global_repo)

    asyncio.run(worker.resume(repo=repo))

    assert repo.repaired == 1, "注入的 repo 该被用来补索引"
    assert [job.payload["id"] for job, _ in queue.sent] == ["f" * 32]


def test_resume_waits_when_shared_disk_is_down(monkeypatch):
    """共享盘掉线：留一句告警就收工，不往队列里投没人能跑的任务。"""
    from core.config import settings
    from modules.template import replacement_worker as worker

    repo = _repo_with(None, repair_error=OSError("共享盘掉线"))
    queue = _RecordingQueue()
    monkeypatch.setattr(worker, "get_queue", lambda: queue)
    monkeypatch.setattr(settings, "worker_enabled", True)

    asyncio.run(worker.resume(repo=repo))

    assert queue.sent == []
    assert repo.enumerated == 0, "盘都读不到还去枚举索引"


def test_resume_does_nothing_when_worker_is_off(monkeypatch):
    """`worker_enabled` 仍然是一道门：只读实例不该恢复（R13-01 的前提）。"""
    from core.config import settings
    from modules.template import replacement_worker as worker

    repo = _repo_with(None)
    monkeypatch.setattr(worker, "get_queue", lambda: _RecordingQueue())
    monkeypatch.setattr(settings, "worker_enabled", False)

    asyncio.run(worker.resume(repo=repo))

    assert repo.repaired == 0 and repo.enumerated == 0


def test_recover_waits_for_run_lock_with_bounded_retries(monkeypatch):
    """档案锁一直被占：退避重投，但**次数有上限**，用完告警放手。

    老代码每次冲突都 `enqueue_recovery(delay=30)`，而重投是新建作业——一个卡死的
    监督进程能让这条 run 每 30 秒唤醒一次、永不停止。
    """
    from modules.template import replacement_worker as worker

    queue = _RecordingQueue()
    monkeypatch.setattr(worker, "get_queue", lambda: queue)

    def busy(_run_id: str) -> str:
        raise Conflict("档案正被处理")

    monkeypatch.setattr(worker, "recover_state", busy)

    before = worker.RECOVERY_LOCK_GIVEUPS.value()
    # 起始 payload 不带 attempt：库里可能还躺着旧版本投下的作业，必须当第 0 轮处理
    payload: dict[str, object] = {"id": "a" * 32}
    delays: list[float] = []
    for _ in range(worker.RECOVER_LOCK_MAX_RETRIES + 5):
        queue.sent.clear()
        asyncio.run(
            worker.recover(Job(name="template.replacement.recover", payload=payload))
        )
        if not queue.sent:
            break
        job, delay = queue.sent[0]
        delays.append(delay)
        payload = job.payload

    assert len(delays) == worker.RECOVER_LOCK_MAX_RETRIES, "抢不到锁的重投必须有上限"
    assert delays == [30.0, 60.0, 120.0, 240.0, 480.0, 600.0], "退避该翻倍并封顶"
    assert payload["attempt"] == worker.RECOVER_LOCK_MAX_RETRIES
    assert worker.RECOVERY_LOCK_GIVEUPS.value() == before + 1


def test_recover_stops_when_nothing_left_to_do(monkeypatch):
    """`recover_state` 说没事可做时不能顺手重投（否则又是一个无限循环）。"""
    from modules.template import replacement_worker as worker

    queue = _RecordingQueue()
    monkeypatch.setattr(worker, "get_queue", lambda: queue)
    monkeypatch.setattr(worker, "recover_state", lambda _run_id: "done")

    asyncio.run(
        worker.recover(
            Job(name="template.replacement.recover", payload={"id": "b" * 32})
        )
    )

    assert queue.sent == []


# --------------------------------------------------------------------------- #
# R15-02：提交时写下供应商形态快照，恢复不必再解释「当前」模型目录
# --------------------------------------------------------------------------- #
def _patch_router(monkeypatch, *, pollable: bool, resolved: bool = True) -> None:
    class _Provider:
        supports_poll = pollable

    class _Router:
        def resolve(self, model: str) -> object | None:
            return object() if resolved else None

        def provider_for(self, model: str) -> _Provider:
            return _Provider()

    monkeypatch.setattr("infra.get_model_router", lambda: _Router())


def test_submit_records_provider_shape_for_recovery(prepared, monkeypatch):
    """提交成功即写下形态快照：恢复器据此分类，不再看当前目录。

    `_supports_poll` 的目录回退有「只有一个供应商时兜底」的行为，echo-only 环境里
    会把一条历史 Agnes 记录认成别的 provider——分类一翻，记录要么被 `resume()` 到
    恒返回 FAILED 的 `poll()` 上判死，要么被误判成同步供应商（T4/R12-05）。
    """
    from generation.recovery import _supports_poll
    from modules.template import replacement_pipeline as pipeline

    payload, path = prepared
    run_id = payload.request_id.hex
    _patch_router(monkeypatch, pollable=True)

    store = pipeline.ReplacementStore(session=None)  # type: ignore[arg-type]
    asyncio.run(store.mark_submitting(run_id))
    asyncio.run(store.mark_submitted(run_id, f"{run_id}:T-100"))

    meta = ReplacementRepository().load(path)
    assert meta.submit_mode == "async", "提交成功却没留下供应商形态快照"
    record = asyncio.run(store.load(run_id))
    assert record is not None and _supports_poll(record), "有快照却仍走目录解析"


def test_unresolvable_catalog_leaves_snapshot_empty(prepared, monkeypatch):
    """目录答不出来就留空：恢复器按「不能续查」处理，让人核对。"""
    from generation.recovery import _supports_poll
    from modules.template import replacement_pipeline as pipeline

    payload, path = prepared
    run_id = payload.request_id.hex
    _patch_router(monkeypatch, pollable=True, resolved=False)

    store = pipeline.ReplacementStore(session=None)  # type: ignore[arg-type]
    asyncio.run(store.mark_submitting(run_id))
    asyncio.run(store.mark_submitted(run_id, f"{run_id}:T-101"))

    assert ReplacementRepository().load(path).submit_mode is None
    record = asyncio.run(store.load(run_id))
    assert record is not None and not _supports_poll(record), "空快照必须落到安全一侧"


def test_reschedule_drops_stale_snapshot(prepared, monkeypatch):
    """人工再点「继续」= 新一次提交，旧快照必须作废。

    否则恢复器会拿上一条记录的形态处置这一次：上次是同步、这次换成了可轮询的
    供应商时，一条正在跑的任务会被直接判死。
    """
    from modules.template import replacement_worker as worker

    payload, path = prepared
    run_id = payload.request_id.hex
    repo = ReplacementRepository()
    with repo.edit(run_id) as meta:
        meta.status = "uploaded"
        meta.framework_status = "failed"
        meta.submit_mode = "sync"

    monkeypatch.setattr(worker.MediaCatalog, "check_run", lambda *args, **kwargs: None)

    assert worker.schedule_state(run_id, "u_001") == "prepare"
    assert repo.load(path).submit_mode is None


# --------------------------------------------------------------------------- #
# R15-02：提交时写下供应商形态快照，恢复不必再解释「当前」模型目录
