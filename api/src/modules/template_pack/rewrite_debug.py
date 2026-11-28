"""LLM 调试台的只读服务：改写一次 + 编译预览，不创建任务。"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.exceptions import AppError, BadRequest, NotFound
from core.template_spec import (
    FilledAsset,
    FilledSlot,
    TemplateSpec,
    compile_spec,
    prompt_limit_for_capability,
)
from modules.template_pack.models import TemplateSpec as SpecRow
from modules.template_pack.random_settings import read_random_settings, save_random_settings
from modules.template_pack.randomness import (
    DEFAULT_SYSTEM_PROMPT,
    DEFAULT_TEMPERATURE,
    MAX_REWRITE_SOURCE_CHARS,
    RandomRewriteSettings,
    RewriteTrace,
    rewrite_slot_texts_trace,
)
from modules.template_pack.rewrite_debug_schema import (
    DebugTemplateOut,
    RandomRewriteSettingsIn,
    RandomRewriteSettingsOut,
    RewriteDebugIn,
    RewriteDebugOptionsOut,
    RewriteDebugOut,
    RewriteDebugUsageOut,
)
from modules.template_pack.slot_text_overrides import apply_slot_text_overrides
from modules.template_pack.spec_catalog import model_capability
from modules.template_pack.spec_service import load_version_row, spec_of_version


async def debug_options(session: AsyncSession) -> RewriteDebugOptionsOut:
    """可调试的模板 + 默认 system prompt / 参数。"""
    rows = list(
        (
            await session.scalars(
                select(SpecRow)
                .where(SpecRow.deleted_at.is_(None), SpecRow.current_version > 0)
                .order_by(SpecRow.name, SpecRow.spec_key)
            )
        ).all()
    )
    state = await read_random_settings(session)
    current = state.settings
    return RewriteDebugOptionsOut(
        templates=[
            DebugTemplateOut(
                key=row.spec_key,
                name=row.name,
                version=row.current_version,
                status=row.status,
            )
            for row in rows
        ],
        default_system_prompt=DEFAULT_SYSTEM_PROMPT,
        default_temperature=DEFAULT_TEMPERATURE,
        max_source_chars=MAX_REWRITE_SOURCE_CHARS,
        current_settings=RandomRewriteSettingsOut(
            model=current.model,
            system_prompt=current.system_prompt,
            temperature=current.temperature,
            max_tokens=current.max_tokens,
            extra_params=dict(current.extra_params),
        ),
        customized=state.customized,
    )


def _preview_fills(spec: TemplateSpec) -> tuple[dict[str, FilledSlot], list[str]]:
    """为「执行前提示词」预览合成必填素材；不查库、不落盘。

    可选槽位在随机档下不传素材，所以不合成。固定槽位用版本里钉的 asset id；
    用户必填槽位用 `debug:` 前缀占位，只用于让编号与段落展开成立。
    """
    fills: dict[str, FilledSlot] = {}
    synthetic: list[str] = []
    for slot in sorted(spec.slots, key=lambda item: (item.position, item.key)):
        label = slot.label.strip() or slot.key
        if slot.binding == "fixed":
            if not slot.asset_id:
                raise BadRequest(f"固定槽位「{label}」没有素材，无法生成预览")
            ids = [slot.asset_id]
        elif slot.min_count > 0:
            ids = [f"debug:{slot.key}:{index + 1}" for index in range(slot.min_count)]
            synthetic.append(slot.key)
        else:
            continue
        fills[slot.key] = FilledSlot(
            slot_key=slot.key,
            assets=tuple(
                FilledAsset(
                    asset_id=asset_id,
                    kind=slot.type,
                    name=f"调试占位 {label} {index + 1}",
                )
                for index, asset_id in enumerate(ids)
            ),
        )
    return fills, synthetic


def _base_fields(
    payload: RewriteDebugIn,
    trace: RewriteTrace,
) -> dict[str, Any]:
    request = trace.request
    messages = request.messages if request is not None else ()
    system_prompt = messages[0].content if messages else payload.system_prompt
    user_payload = messages[1].content if len(messages) > 1 else ""
    response = trace.response
    usage = response.usage if response is not None else None
    return {
        "requested_model": request.model if request is not None else payload.model,
        "system_prompt": system_prompt,
        "user_payload": user_payload,
        "request_params": dict(request.params) if request is not None else {},
        "raw_output": response.text if response is not None else "",
        "rewritten_texts": dict(trace.overrides),
        "llm_model": response.model if response is not None else "",
        "llm_platform_model": response.platform_model if response is not None else "",
        "finish_reason": response.finish_reason if response is not None else "",
        "usage": RewriteDebugUsageOut(
            prompt_tokens=usage.prompt_tokens if usage else 0,
            completion_tokens=usage.completion_tokens if usage else 0,
            total_tokens=usage.total_tokens if usage else 0,
        ),
        "latency_ms": trace.latency_ms,
    }


async def run_debug_rewrite(session: AsyncSession, payload: RewriteDebugIn) -> RewriteDebugOut:
    """调一次 LLM 改写并编译最终提示词；不创建任务、不落库。"""
    head = await session.scalar(
        select(SpecRow).where(
            SpecRow.spec_key == payload.template_key,
            SpecRow.deleted_at.is_(None),
        )
    )
    if head is None:
        raise NotFound(f"配置不存在：{payload.template_key}")
    version_no = payload.version or head.current_version
    if version_no <= 0:
        raise BadRequest(f"配置 {payload.template_key} 还没有发布版本")
    row = await load_version_row(session, head, version_no)
    spec = await spec_of_version(session, head, row, model=row.model)

    trace = await rewrite_slot_texts_trace(
        spec,
        settings=RandomRewriteSettings(
            model=payload.model,
            system_prompt=payload.system_prompt,
            temperature=payload.temperature,
            max_tokens=payload.max_tokens,
            extra_params=dict(payload.extra_params),
        ),
    )
    base = _base_fields(payload, trace)
    if trace.error is not None:
        return RewriteDebugOut(ok=False, error=str(trace.error), **base)

    assert trace.spec is not None
    try:
        applied = apply_slot_text_overrides(trace.spec, trace.overrides, {})
        fills, synthetic = _preview_fills(applied)
        compiled = compile_spec(applied, fills)
    except AppError as exc:
        return RewriteDebugOut(ok=False, error=str(exc), **base)

    capability = model_capability(compiled.model)
    limit = prompt_limit_for_capability(capability)
    return RewriteDebugOut(
        ok=True,
        final_prompt=compiled.prompt,
        reference_library_ids=list(compiled.reference_ids),
        text_library_ids=list(compiled.text_ids),
        compiled_params=dict(compiled.params),
        prompt_length=len(compiled.prompt),
        prompt_limit=limit,
        capability=capability,
        synthetic_slots=synthetic,
        **base,
    )


async def save_debug_settings(
    session: AsyncSession, payload: RandomRewriteSettingsIn, *, actor: str
) -> RandomRewriteSettingsOut:
    state = await save_random_settings(
        session,
        model=payload.model,
        system_prompt=payload.system_prompt,
        temperature=payload.temperature,
        max_tokens=payload.max_tokens,
        extra_params=payload.extra_params,
        actor=actor,
    )
    settings = state.settings
    return RandomRewriteSettingsOut(
        model=settings.model,
        system_prompt=settings.system_prompt,
        temperature=settings.temperature,
        max_tokens=settings.max_tokens,
        extra_params=dict(settings.extra_params),
    )


__all__ = ["debug_options", "run_debug_rewrite", "save_debug_settings"]