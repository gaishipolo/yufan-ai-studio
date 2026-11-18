"""上游 `status=0` 到底是「排队」还是「已在生成」，决定框架要不要顺延墙钟。

2026-11-17 的任务 29dc66ae 就是因为把排队算进了生成，在 48% 被判超时（上游其实
还在正常排队）。桥接层靠 `queueReason` 区分这两种情况：写错一边，排队时间就会被
算进 `generation_timeout_sec`，误判超时原样复发。

这里用 node 真跑一遍 `bridge.mjs` 的 `resolveStatus`，把三件事钉死：
1. `status=0` 带 `queueReason` → `queued`（框架顺延墙钟）；
2. `status=0` 不带 `queueReason` → `running`（真的在生成，墙钟照走）；
3. 终态照旧透传，未知状态返回空让调用方抛错。
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

BRIDGE = (
    Path(__file__).resolve().parents[1] / "src/modules/model_driver/libtv/runtime/bridge.mjs"
)

pytestmark = pytest.mark.skipif(
    shutil.which("node") is None or not BRIDGE.is_file(),
    reason="需要 node 与 bridge.mjs",
)


def _statuses(cases: list[dict[str, object]]) -> list[str | None]:
    script = (
        f"import({json.dumps(BRIDGE.as_uri())}).then(m => {{"
        f"console.log(JSON.stringify({json.dumps(cases)}.map(p => m.resolveStatus(p) ?? null)));"
        "});"
    )
    completed = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, timeout=30, check=False
    )
    assert completed.returncode == 0, completed.stderr[-400:]
    return json.loads(completed.stdout)


def test_queue_reason_is_what_makes_a_task_queued() -> None:
    """免费队列/限流队列 → 排队；没有 queueReason 的 status=0 已经是生成中。"""
    cases: list[dict[str, object]] = [
        {"status": 0, "queueReason": "UNLIMITED_SERIAL"},
        {"status": 0, "queueReason": "RATE_LIMIT"},
        {"status": 0, "queueReason": ""},
        {"status": 0},
        {"status": 1},
    ]

    assert _statuses(cases) == ["queued", "queued", "running", "running", "running"]


def test_terminal_states_pass_through_and_unknown_ones_stay_unmapped() -> None:
    cases: list[dict[str, object]] = [
        {"status": 2},
        {"status": 3, "failedReason": "audit rejected"},
        {"status": 4},
        {"status": 9},
    ]

    assert _statuses(cases) == ["generated", "failed", "timeout", None]
