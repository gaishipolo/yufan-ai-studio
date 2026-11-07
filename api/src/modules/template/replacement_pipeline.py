"""替换档案到原框架 GenerationStore / Pipeline 的适配。"""

from __future__ import annotations

import asyncio
from collections.abc import Callable

from sqlalchemy.ext.asyncio import AsyncSession

from generation.pipeline import register_pipeline
from generation.spec import GenerationRecord
from infra.ai.base import Capability, GenerationRequest
from infra.ai.libtv import MODEL_ID
from modules.template.replacement_repository import ReplacementManifest, ReplacementRepository

KIND_REPLACEMENT = "template_replacement"


def _snapshot_submit_mode() -> str | None:
    """提交成功时把供应商形态写进档案（与 `image.pipeline` 同一口径）。

    用 `MODEL_ID` 而不是 `meta.model`：真正提交走的是 LibTV，档案里那个
    `model` 字段是历史展示值（默认 `wanx3.0`），拿它去解析目录只会解析失败。

    取不到就留 `None`：快照是给恢复器分类用的，解析不出来宁可让它按
    「不能续查、请人核对」处理，也不猜一个可能把记录判死的答案。一次成功提交
    绝不能因为这个增强动作而失败，所以整段吞异常。
    """
    from infra import get_model_router

    try:
        router = get_model_router()
        if router.resolve(MODEL_ID) is None:
            return None
        provider = router.provider_for(MODEL_ID)
    except Exception:  # noqa: BLE001 - 快照是增强，不能让一次成功提交失败
        return None
    return "async" if getattr(provider, "supports_poll", False) else "sync"


class ReplacementStore:
    def __init__(self, session: AsyncSession) -> None:
        self.repo = ReplacementRepository()

    async def load(self, record_id: str) -> GenerationRecord | None:
        def load() -> GenerationRecord:
            path = self.repo.path(record_id)
            meta = self.repo.load(path)
            return GenerationRecord(
                id=meta.id,
                kind=KIND_REPLACEMENT,
                model=MODEL_ID,
                prompt=(path / "inputs/prompt.txt").read_text(encoding="utf-8"),
                status=meta.framework_status or "queued",
                owner_id=meta.owner_id,
                external_id=f"{meta.id}:{meta.task_id}" if meta.task_id else None,
                progress=meta.progress_percent,
                error=meta.last_error,
                created_at=meta.created_at,
                params={"archiveRunId": meta.id},
                submit_attempts=meta.submit_attempts,
                submit_mode=meta.submit_mode,
                terminal_statuses=("success", "failed"),
            )

        return await asyncio.to_thread(load)

    async def _update(self, record_id: str, change: Callable[[ReplacementManifest], None]) -> None:
        def update() -> None:
            with self.repo.edit(record_id) as meta:
                change(meta)

        await asyncio.to_thread(update)

    async def mark_submitting(self, record_id: str) -> None:
        def change(meta: ReplacementManifest) -> None:
            meta.submit_attempts += 1

        await self._update(record_id, change)

    async def clear_submit_attempt(self, record_id: str) -> None:
        await self._update(record_id, lambda meta: setattr(meta, "submit_attempts", 0))

    async def mark_submitted(self, record_id: str, external_id: str) -> None:
        def change(meta: ReplacementManifest) -> None:
            meta.task_id = external_id.partition(":")[2]
            meta.status = "running"
            meta.framework_status = "running"
            meta.submit_attempts = 0
            meta.last_error = None
            meta.submit_mode = _snapshot_submit_mode()

        await self._update(record_id, change)

    async def mark_progress(self, record_id: str, progress: int) -> None:
        def change(meta: ReplacementManifest) -> None:
            meta.progress_percent = max(meta.progress_percent, min(99, progress))

        await self._update(record_id, change)

    async def mark_succeeded(
        self, record_id: str, *, output_url: str | None, cover_url: str | None, progress: int = 100
    ) -> None:
        def change(meta: ReplacementManifest) -> None:
            meta.status = "success"
            meta.framework_status = "success"
            meta.progress_percent = 100
            meta.last_error = None

        await self._update(record_id, change)

    async def mark_failed(self, record_id: str, error: str) -> None:
        def change(meta: ReplacementManifest) -> None:
            meta.framework_status = "failed"
            meta.last_error = error
            if meta.submit_attempts > 0 and not meta.task_id:
                meta.status = "submission_unknown"
            elif meta.status == "waiting_free":
                return
            elif "占用" in error:
                meta.status = "waiting_executor"
            else:
                meta.status = "failed" if meta.task_id else "service_error"

        await self._update(record_id, change)


@register_pipeline
class ReplacementPipeline:
    kind = KIND_REPLACEMENT
    capability = Capability.VIDEO

    def store(self, session: AsyncSession) -> ReplacementStore:
        return ReplacementStore(session)

    async def build_request(self, record: GenerationRecord) -> GenerationRequest:
        return GenerationRequest(
            capability=self.capability,
            model=record.model,
            prompt=record.prompt,
            params=record.params,
            request_id=record.id,
        )
