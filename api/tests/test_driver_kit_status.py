"""kit/status：上游状态 → 平台状态的唯一实现。

驱动只声明**数据**（词表 + 兜底口径），判定逻辑只有一份 —— 与 `kit/errors.py`
同一套思路：实现收进 kit，语义留在驱动。

这里钉住三件事：

1. **查表前统一小写去空白** —— bailian 回大写、agnes 有 `in_progress`；
2. **成功但没给产物地址 → 判失败**（不是「成功但地址为空」：后者让下游以为有产物）；
3. **未知状态词走驱动声明的兜底** —— agnes 视频当「还在跑」、grsai 当失败，
   两条路的文案与进度口径都不同，不能混。
"""

from __future__ import annotations

import importlib

from infra.ai.base import GenerationState
from modules.model_driver.kit.status import progress_from_status, resolve_state

STATUS_MAP = {
    "running": GenerationState.RUNNING,
    "succeeded": GenerationState.SUCCEEDED,
    "failed": GenerationState.FAILED,
}


def test_状态词统一小写去空白后查表() -> None:
    """上游大小写不一致（百炼是大写）——查表前必须归一。"""
    assert resolve_state("  SUCCEEDED ", STATUS_MAP) is GenerationState.SUCCEEDED
    assert resolve_state("Running", STATUS_MAP) is GenerationState.RUNNING


def test_查不到返回声明的兜底() -> None:
    assert resolve_state("mystery", STATUS_MAP) is None
    assert resolve_state("", STATUS_MAP) is None
    assert (
        resolve_state("mystery", STATUS_MAP, unknown=GenerationState.RUNNING)
        is GenerationState.RUNNING
    )


def test_成功且有地址() -> None:
    progress = progress_from_status(
        external_id="task-1",
        raw_status="succeeded",
        status_map=STATUS_MAP,
        output_url="https://cdn.example.com/a.png",
    )
    assert progress.state is GenerationState.SUCCEEDED
    assert progress.output_url == "https://cdn.example.com/a.png"
    assert progress.progress == 100


def test_成功但没地址判失败() -> None:
    """上游说完成了却没给产物地址 → 判失败，不能让下游以为有东西可取。"""
    progress = progress_from_status(
        external_id="task-1",
        raw_status="succeeded",
        status_map=STATUS_MAP,
        missing_output_error="上游说完成了但没给地址",
    )
    assert progress.state is GenerationState.FAILED
    assert progress.error == "上游说完成了但没给地址"
    assert progress.retryable is False


def test_失败按状态词给不同文案() -> None:
    """内容违规与生成失败不是一句话 → 由驱动按状态词给文案。"""
    errors = {"violation": "判定内容违规", "failed": "生成失败"}
    status_map = {**STATUS_MAP, "violation": GenerationState.FAILED}

    violation = progress_from_status(
        external_id="task-1",
        raw_status="violation",
        status_map=status_map,
        status_errors=errors,
        error="兜底文案",
    )
    failed = progress_from_status(
        external_id="task-1",
        raw_status="failed",
        status_map=status_map,
        status_errors=errors,
        error="兜底文案",
    )
    assert violation.error == "判定内容违规"
    assert failed.error == "生成失败"


def test_未知状态走驱动声明的兜底() -> None:
    """未知 = 还在跑（agnes 口径）：进度用上游给的，不当成失败。"""
    progress = progress_from_status(
        external_id="task-1",
        raw_status="brand_new_state",
        status_map=STATUS_MAP,
        progress=42,
        unknown_state=GenerationState.RUNNING,
    )
    assert progress.state is GenerationState.RUNNING
    assert progress.progress == 42


def test_未知状态判失败时用独立文案与进度() -> None:
    """未知 = 失败（grsai 口径）：文案与「命中的失败」分开，进度用上游给的。"""
    progress = progress_from_status(
        external_id="task-1",
        raw_status="brand_new_state",
        status_map=STATUS_MAP,
        progress=42,
        status_errors={"failed": "生成失败"},
        unknown_state=GenerationState.FAILED,
        unknown_error="返回了无法识别的任务状态",
        failed_progress=100,
    )
    assert progress.state is GenerationState.FAILED
    assert progress.error == "返回了无法识别的任务状态"
    assert progress.progress == 42, "未知状态不套用「命中失败」的 100 口径"


def test_命中失败时进度可声明() -> None:
    progress = progress_from_status(
        external_id="task-1",
        raw_status="failed",
        status_map=STATUS_MAP,
        progress=30,
        error="生成失败",
        failed_progress=100,
    )
    assert progress.state is GenerationState.FAILED
    assert progress.progress == 100


def test_各驱动的状态词表都是小写() -> None:
    """查表前统一小写 —— 词表若写成大写就永远命中不了。"""
    for name in ("agnes_video", "grsai", "grsai_video", "bailian"):
        module = importlib.import_module(f"modules.model_driver.drivers.{name}")
        table = getattr(module, "_STATUS_MAP", None)
        assert table, f"{name} 没有声明状态词表"
        for word in table:
            assert word == word.lower(), f"{name} 的状态词 {word!r} 不是小写"
