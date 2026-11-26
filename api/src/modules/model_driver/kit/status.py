"""上游状态 → 平台状态：**唯一实现**。

驱动只声明**数据**（它上游有哪些状态词、各自算平台的什么状态），
判定逻辑只有一份 —— 与 `kit/errors.py` 同一套思路：实现收进 kit，语义留在驱动
（各家的状态词本来就不一样：agnes 认 `processing`、grsai 认 `violation`、
bailian 用大写 `SUCCEEDED`）。

## 收的是哪一段

**只收「状态词 → 平台状态」**。产物地址取哪个字段、失败文案怎么写、
「完成了但没给地址」怎么办 —— 这些是供应商语义，各写各的；收到 kit 里就错了。

## 为什么值得收

状态词映射散在四个驱动的 if 链里，一处加词就可能漏一处；上游加了新状态时，
我们只能看到「一直转圈」，查不到原因。收进一张表之后，「这家有哪些状态词」
是**可对比的数据**，不再是散落的分支 —— 新供应商照抄一张表即可。

## 未知状态词怎么办

这是**业务口径**，各家不同，由驱动声明：

- agnes 视频：未知 = 还在跑（墙钟超时兜底）；驱动自己留一条 warning；
- grsai：未知 = 失败（多半是任务已过期、接口不认这个 id）；
- bailian：未知 = **抛错**（不是进度）—— 由驱动自己处置，它要的是「稍后继续查询」。

## 边界

`libtv` 不适用：它是执行器本地状态机（工作区 `run.json`），不经这里。
"""

from __future__ import annotations

from collections.abc import Mapping

from infra.ai.base import GenerationProgress, GenerationState

__all__ = ["progress_from_status", "resolve_state"]


def resolve_state(
    raw: str,
    status_map: Mapping[str, GenerationState],
    *,
    unknown: GenerationState | None = None,
) -> GenerationState | None:
    """查表：上游状态词 → 平台状态。查不到返回 `unknown`（可为空）。

    状态词统一「小写 + 去空白」再查表 —— bailian 回的是大写 `SUCCEEDED`，
    agnes 有 `in_progress` 这种带下划线的写法，各家大小写也不一致。
    """
    word = (raw or "").strip().lower()
    if not word:
        return unknown
    return status_map.get(word, unknown)


def progress_from_status(
    *,
    external_id: str,
    raw_status: str,
    status_map: Mapping[str, GenerationState],
    progress: int = 0,
    output_url: str | None = None,
    cover_url: str | None = None,
    error: str | None = None,
    status_errors: Mapping[str, str] | None = None,
    missing_output_error: str | None = None,
    unknown_state: GenerationState = GenerationState.FAILED,
    unknown_error: str | None = None,
    failed_progress: int | None = None,
) -> GenerationProgress:
    """上游状态 → 平台进度（异步驱动 poll 的统一骨架）。

    - `status_map`：驱动声明的「上游状态词 → 平台状态」。
    - `status_errors`：按状态词给不同文案（grsai 的 `violation` 与 `failed`
      不是一句话 —— 一个是内容违规，一个是生成失败）。
    - `missing_output_error`：成功但没给产物地址时的文案。
    - `unknown_state` / `unknown_error`：**查不到**状态词时的兜底口径
      （与「命中了一个失败状态」是两条路，进度口径也不同）。
    - `failed_progress`：命中失败状态时显示的进度；`None` = 用上游给的进度
      （agnes 跟着上游，grsai 记 100）。

    成功/失败的判定顺序固定：**先看状态，再看产物**。上游说完成了却没给地址，
    判失败而不是「成功但地址为空」——后者会让下游以为有产物可取。
    """
    word = (raw_status or "").strip().lower()

    if word not in status_map:
        # 未知状态词：走驱动声明的兜底（文案与进度都跟「命中的失败」分开）。
        if unknown_state is GenerationState.FAILED:
            return GenerationProgress(
                external_id=external_id,
                state=GenerationState.FAILED,
                progress=progress,
                error=unknown_error,
                retryable=False,
            )
        return GenerationProgress(
            external_id=external_id,
            state=unknown_state,
            progress=progress,
        )

    state = status_map[word]

    if state is GenerationState.SUCCEEDED:
        if not output_url:
            return GenerationProgress(
                external_id=external_id,
                state=GenerationState.FAILED,
                progress=100,
                error=missing_output_error or "上游报告完成但没有产物地址",
                retryable=False,
            )
        return GenerationProgress(
            external_id=external_id,
            state=GenerationState.SUCCEEDED,
            progress=100,
            output_url=output_url,
            cover_url=cover_url,
        )

    if state is GenerationState.FAILED:
        text = (status_errors or {}).get(word) or error
        return GenerationProgress(
            external_id=external_id,
            state=GenerationState.FAILED,
            progress=progress if failed_progress is None else failed_progress,
            error=text,
            retryable=False,
        )

    return GenerationProgress(
        external_id=external_id,
        state=state,
        progress=progress,
    )
