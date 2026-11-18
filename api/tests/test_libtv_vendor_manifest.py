"""vendor 快照必须与 `manifest.json` 的哈希逐字节一致。

桥接层（`runtime/bridge.mjs`）每次执行前做同一份校验，不一致就以 `RELEASE_INVALID`
拒绝执行 —— 症状是**所有 LibTV 替换任务在 upload 阶段失败**，报
`Vendored adapter changed: ...`，而 `git status` 干干净净，很难往换行符上想。

真发生过三次：
1. 目录从 `infra/ai/libtv_runtime/` 搬到 `modules/model_driver/libtv/runtime/`（61cdf87），
   `.gitattributes` 里的 `-text` 规则没跟着搬 → `core.autocrlf=true` 的机器把快照检出成
   CRLF → 字节哈希全变（2026-11-16 任务 40e9da57 就是这么失败的）；
2. 有人改了适配器、忘了刷新清单；
3. 2026-11-17：另一个 checkout（11-10 的老工作树）与主仓库共用同一个 Postgres 队列，
   任务被它接走就报 `RELEASE_INVALID`，而主仓库这边完全正常 —— 同一批任务时而成功
   时而失败。这一条靠 pytest 拦不住，所以 `process.py` 在启动 Node 前也跑了同一份自检
   （`vendor_problem`），失败时给的是文件名 + 期望/实际哈希 + 下一动作。

所以这里既比字节，也在「只差换行符」时把话说清楚，免得下次又排查一遍。
"""

from __future__ import annotations

import shutil
from pathlib import Path

from modules.model_driver.libtv.process import VENDOR, vendor_problem
from modules.model_driver.libtv.release import describe_mismatches, verify_vendor


def test_vendor_files_match_manifest() -> None:
    mismatches = verify_vendor(VENDOR)
    assert not mismatches, (
        "vendor 与 manifest.json 不一致，桥接层会以 RELEASE_INVALID 拒绝执行所有 LibTV 任务：\n"
        + describe_mismatches(mismatches, VENDOR)
    )
    assert vendor_problem(VENDOR) == "", "自检与测试口径必须一致"


def test_mismatch_names_the_file_and_the_next_action(tmp_path: Path) -> None:
    """改坏一个文件 → 自检要说清「哪个文件 + 该做什么」，而不是只回一个码。"""
    vendor = tmp_path / "vendor"
    shutil.copytree(VENDOR, vendor)
    broken = vendor / "libtv/adapters/billing.mjs"
    broken.write_bytes(broken.read_bytes() + b"\n// tampered\n")

    problem = vendor_problem(vendor)

    assert "libtv/adapters/billing.mjs" in problem
    assert "刷新" in problem and "checkout" in problem
    [mismatch] = verify_vendor(vendor)
    assert mismatch.line_ending_only is False


def test_crlf_only_mismatch_says_so(tmp_path: Path) -> None:
    """只差换行符是最常见的一次，提示必须直接指向 `.gitattributes`。"""
    vendor = tmp_path / "vendor"
    shutil.copytree(VENDOR, vendor)
    converted = vendor / "libtv/adapters/billing.mjs"
    converted.write_bytes(converted.read_bytes().replace(b"\n", b"\r\n"))

    [mismatch] = verify_vendor(vendor)

    assert mismatch.line_ending_only is True
    assert ".gitattributes" in mismatch.hint
