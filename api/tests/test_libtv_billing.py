"""真实计费适配器的回归守卫。

`test_replacement_archive.py` 里的 vendor 是测试替身，其中的 billing.mjs 是假实现，
覆盖不到仓库里真正跑生产的那个文件。这里直接加载真实 vendor，锁住两条契约：
清单必须 pin 住它，以及「只给这一条请求关掉 0 积分畅用」不能改坏。
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

from tests_template_helpers import add_run, drop_run

from core.config import settings
from db.session import get_session_factory
from modules.template import worker
from modules.template.workspace import write_state

RUNTIME = Path(__file__).resolve().parents[1] / "src/infra/ai/libtv_runtime"
VENDOR = RUNTIME / "vendor"


def _run_node(script: str) -> None:
    subprocess.run(
        [settings.node_binary, "--input-type=module", "-e", script],
        check=True,
        capture_output=True,
    )


def test_billing_adapter_is_pinned_in_vendor_manifest() -> None:
    script = (
        "import {verifyVendor} from " + json.dumps((RUNTIME / "bridge.mjs").as_uri()) + ";"
        "import assert from 'node:assert/strict';"
        "const manifest = await verifyVendor(" + json.dumps(str(VENDOR)) + ");"
        "assert.ok(manifest.files['libtv/adapters/billing.mjs'],"
        "'计费适配器必须纳入逐文件哈希校验');"
    )
    _run_node(script)


def test_absorb_reads_back_the_real_credit_charge(tmp_path: Path) -> None:
    """积分任务的真实扣费写在 run.json 里，必须回读到数据库供任务中心展示。"""
    write_state(
        tmp_path,
        {
            "status": "running",
            "task_id": "20261109160303324937197",
            "estimated_credits": 128,
            "credits": 128,
            "progress_percent": 16,
        },
    )
    run = SimpleNamespace(
        status="prepared", task_id=None, submit_attempts=0, progress_percent=0,
        estimated_credits=None, credits=None, last_error=None, error_code=None,
    )
    worker._absorb(run, tmp_path)
    assert run.credits == 128
    assert run.estimated_credits == 128
    assert run.task_id == "20261109160303324937197"


async def test_resume_requeues_a_running_task(monkeypatch) -> None:
    """重启后正在上游生成的任务必须自动续查，不能只靠人工点「继续」。"""
    run = await add_run(
        status="running",
        task_id="20261109160303324937197",
        framework_status="running",
    )
    try:
        captured: list[str] = []

        async def capture(run_id: str, **_: object) -> None:
            captured.append(run_id)

        monkeypatch.setattr(worker, "enqueue_recovery", capture)
        monkeypatch.setattr(settings, "worker_enabled", True)
        await worker.resume()
        assert run.id in captured
        async with get_session_factory()() as session:
            # 有 task_id 只查询、不重提，绝不产生第二次计费
            assert await worker.recover_state(session, run.id) == "poll"
    finally:
        await drop_run(run.id)


def test_bridge_retries_only_transient_smb_write_errors() -> None:
    """EPERM/EBUSY 只重放写盘；其它错误立即失败，且重试次数有上限。"""
    uri = json.dumps((RUNTIME / "bridge.mjs").as_uri())
    script = (
        "import {withTransientWriteRetry} from " + uri + ";"
        "import assert from 'node:assert/strict';"
        "let calls=0;"
        "const ok=await withTransientWriteRetry(async()=>{"
        "if(++calls<3) throw Error('EPERM: operation not permitted, rename run.json.tmp');"
        "return 'done';});"
        "assert.equal(ok,'done');assert.equal(calls,3);"
        "let hard=0;"
        "await assert.rejects(withTransientWriteRetry(async()=>{hard++;throw Error('BOOM');}));"
        "assert.equal(hard,1);"
        "let exhausted=0;"
        "await assert.rejects(withTransientWriteRetry(async()=>{"
        "exhausted++;throw Error('EPERM');}));"
        "assert.equal(exhausted,3);"
    )
    _run_node(script)


def test_receipt_power_reads_the_settled_charge(tmp_path: Path, monkeypatch) -> None:
    """下载阶段会覆盖 run.json 的 credits，结算值只能从提交回执取。"""
    (tmp_path / "records").mkdir()
    receipt = tmp_path / "records/submission.json"
    monkeypatch.setattr(worker, "run_path", lambda _run_id: tmp_path)
    receipt.write_text(
        json.dumps({"code": 0, "data": {"power": 128, "taskId": "20261109160303324937197"}}),
        encoding="utf-8",
    )
    assert worker.receipt_power("any") == 128.0
    receipt.write_text(json.dumps({"code": 0, "data": {"power": 0}}), encoding="utf-8")
    assert worker.receipt_power("any") == 0.0
    receipt.write_text(json.dumps({"code": 0, "data": {"power": None}}), encoding="utf-8")
    assert worker.receipt_power("any") is None
    receipt.unlink()
    assert worker.receipt_power("any") is None


def test_billing_adapter_opts_out_only_for_the_credits_request() -> None:
    script = (
        "import {applyBilling, activeZeroPowerTags, isZeroPowerBenefitTag} from "
        + json.dumps((VENDOR / "libtv/adapters/billing.mjs").as_uri())
        + ";"
        "import assert from 'node:assert/strict';"
        "const now = Date.parse('2026-11-09T15:00:00+08:00');"
        "const entries = ["
        "{benefitType:'powerZero',benefitStatus:1,endTime:'2026-11-19T15:13:52',tags:['rateLimit_powerZero_20261111']},"
        "{benefitType:'powerZero',benefitStatus:1,endTime:'2026-11-19T15:13:52',tags:['rateLimit_powerZero_20261111']},"
        "{benefitType:'powerZero',benefitStatus:2,endTime:'2026-11-19T15:13:52',tags:['rateLimit_powerZero_disabled']},"
        "{benefitType:'powerZero',benefitStatus:1,endTime:'2026-10-01T00:00:00',tags:['rateLimit_powerZero_expired']},"
        "{benefitType:'powerDiscount',benefitStatus:1,endTime:'2027-01-01T00:00:00',tags:[]}];"
        "const tags = activeZeroPowerTags(entries, now);"
        "assert.deepEqual(tags, ['rateLimit_powerZero_20261111']);"
        "assert.equal(isZeroPowerBenefitTag('rateLimit_powerFixed_x'), false);"
        "const free = {params:{infiniteSwitch:0,prompt:'x'}, requestId:'original-request'};"
        "assert.equal(applyBilling(free,'free',tags), free, '免费模式必须原样返回');"
        "const paid = applyBilling(free,'credits',tags);"
        "assert.equal(paid.params.infiniteSwitch, 1);"
        "assert.deepEqual(paid.disableBenefitTags, tags);"
        "assert.deepEqual(paid.params.disableBenefitTags, tags);"
        "assert.notEqual(paid.requestId, free.requestId, '上游要求重生成 requestId');"
        "assert.equal(free.params.infiniteSwitch, 0, '不能改动原请求');"
        "assert.equal(free.disableBenefitTags, undefined, '不能顺手把免费请求也关掉权益');"
        "assert.equal(paid.params.prompt, 'x', '除计费字段外不得丢内容');"
        "assert.throws(() => applyBilling(free,'credits',[]), /requires active/);"
        "assert.throws(() => applyBilling(free,'credits',['not_a_tag']), /requires active/);"
    )
    _run_node(script)
