"""0 积分权益到期之后，积分队列还得能用。

2026-11-30 是这批权益的到期时间。到期前账号有可关闭的 0 积分标签，
到期后没有标签可关 —— 旧实现把「没有标签」当成错误（三处各抛一次），
于是到期当天积分队列 100% 提交失败，而报错写着 "needs manual review"。

这里用 node 真跑一遍 vendor 的 `applyBilling`，钉住两条：
1. 无标签：仍然是积分队列（`infiniteSwitch=1`），但不带 `disableBenefitTags`；
2. 有标签：顶层与 params 各写一份（上游就是这么要求的）。
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

VENDOR = Path(__file__).resolve().parents[1] / "src/modules/model_driver/libtv/runtime/vendor"
BILLING = VENDOR / "libtv/adapters/billing.mjs"

pytestmark = pytest.mark.skipif(
    shutil.which("node") is None or not BILLING.is_file(),
    reason="需要 node 与 vendor 里的 billing.mjs",
)


def _apply(tags: list[str]) -> dict:
    body = json.dumps(tags)
    script = (
        f"import({json.dumps(BILLING.as_uri())}).then(m => {{"
        "const req = {params:{prompt:'p'}, taskType:'video'};"
        f"const out = m.applyBilling(req, 'credits', {body});"
        "console.log(JSON.stringify(out));});"
    )
    completed = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, timeout=30, check=False
    )
    assert completed.returncode == 0, completed.stderr[-400:]
    return json.loads(completed.stdout)


def test_no_active_tag_falls_back_to_plain_credit_queue() -> None:
    """权益到期后：没有标签可关，但「走积分队列」这件事本身不变。"""
    out = _apply([])

    assert out["params"]["infiniteSwitch"] == 1
    assert "disableBenefitTags" not in out, "没有权益时不该凭空写出一个空标签数组"
    assert "disableBenefitTags" not in out["params"]
    assert out["requestId"], "requestId 每次都要重生成（幂等的边界见 P3-1）"


def test_active_tag_is_written_at_both_levels() -> None:
    tag = "rateLimit_powerZero_20261111"
    out = _apply([tag])

    assert out["disableBenefitTags"] == [tag]
    assert out["params"]["disableBenefitTags"] == [tag]
    assert out["params"]["infiniteSwitch"] == 1
