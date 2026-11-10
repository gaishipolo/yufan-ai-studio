"""业务侧需要实现的存储协议。

框架只认识这七个方法，不认识任何 ORM 实体。因此：

- 视频任务：`VideoTaskStore` 操作 `video_tasks` 表
- 图片生成：`ImageGenerationStore` 操作 `image_jobs` 表
- 语音合成：`VoiceStore` 操作 `voice_jobs` 表

三者复用同一套提交/轮询/重试/超时逻辑，差异只在映射。
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from generation.spec import GenerationRecord


@runtime_checkable
class GenerationStore(Protocol):
    """业务侧的记录读写协议。

    **所有写方法必须返回 `bool`：True = 真的写进去了，False = 记录不存在或
    已处于终态、这次写入被忽略。** 这不是风格问题：`runner` 拿到 False 却继续
    往下走，就会出现「用户点了取消，随后收到生成成功通知，草稿箱还多一条」——
    落库没生效、事件和归档却照做，两边说法不一致。终态保护只有在调用方看
    返回值时才真正成立。
    """

    async def load(self, record_id: str) -> GenerationRecord | None:
        """读取记录；不存在返回 None（任务可能已被删除，不应报错）。"""
        ...

    async def mark_submitting(self, record_id: str) -> bool:
        """调用供应商**之前**落一次「提交意图」。

        没有这一步，进程在「供应商已受理 → 尚未写库」之间崩溃时，
        库里既没有 external_id 也没有任何痕迹，重启会当作「没提交过」再提交一次。
        """
        ...

    async def clear_submit_attempt(self, record_id: str) -> bool:
        """提交明确失败（拿到了异常）时清除意图标记，允许安全重试。"""
        ...

    async def mark_submitted(self, record_id: str, external_id: str) -> bool:
        """记录供应商任务 ID，状态置为进行中。"""
        ...

    async def mark_progress(self, record_id: str, progress: int) -> bool:
        """更新进度（0-100）。"""
        ...

    async def mark_succeeded(
        self,
        record_id: str,
        *,
        output_url: str | None,
        cover_url: str | None,
        progress: int = 100,
    ) -> bool:
        """成功终态。"""
        ...

    async def mark_failed(self, record_id: str, error: str) -> bool:
        """失败终态，`error` 会展示给用户，注意脱敏。"""
        ...
