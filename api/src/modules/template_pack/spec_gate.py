"""模板发布闸：把“草稿能不能存、能不能发布、发布冻结成什么”收在一个接口里。

草稿、预检、发布三个入口共用这套检查序列；`compile` 不在这里，继续消费已经
冻结的版本，走纯 `compile_spec`。
"""

from __future__ import annotations

import hashlib
import inspect
import json
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, replace
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from core.context import CurrentUser
from core.exceptions import BadRequest
from core.ports.library_lookup import (
    LibraryAssetRef,
    LibraryAssetState,
    LibraryVisibility,
    get_library_lookup,
)
from core.ports.model_generation import ModelSummary
from core.template_spec import (
    EXECUTION_PROMPT_LIMIT,
    MAX_SLOT_REFS,
    InputSlot,
    SpecGroup,
    TemplateSpec,
    inline_slot_ref,
    normalize_refs,
    prompt_limit_for_capability,
    slot_ref_modes,
    validate_optional_slot_refs,
    validate_required_slot_refs,
    validate_spec,
)
from modules.template_pack.spec_catalog import (
    capability_label,
    effective_model_ids,
    model_catalog,
)
from modules.template_pack.spec_schema import TemplateSpecIn


@dataclass(frozen=True, slots=True)
class Blocker:
    """一项发布阻塞。`code` 给测试/前端定位，`message` 是用户文案。"""

    code: str
    message: str
    field: str | None = None


@dataclass(frozen=True, slots=True)
class PublishSnapshot:
    """发布版本真正要写进快照的内容。"""

    model: str
    allowed_models: tuple[str, ...]
    params: Mapping[str, Any]
    prompt: str
    random_prompt: str
    slots: tuple[InputSlot, ...]
    groups: tuple[SpecGroup, ...]


@dataclass(frozen=True, slots=True)
class GateReport:
    """一次 gate 检查的纯数据结果；不依赖 HTTP 异常。"""

    blockers: tuple[Blocker, ...]
    fingerprint: str
    snapshot: PublishSnapshot | None = None

    @property
    def ok(self) -> bool:
        return not self.blockers


def to_core(payload: TemplateSpecIn) -> TemplateSpec:
    """入参 → core 词汇。校验只发生在 core，不在这里复制一份。"""
    return TemplateSpec(
        key=payload.key,
        name=payload.name,
        description=payload.description,
        model=payload.model,
        allowed_models=tuple(payload.allowed_models),
        params=dict(payload.params),
        prompt=payload.prompt,
        random_prompt=payload.random_prompt,
        slots=tuple(
            InputSlot(
                key=slot.key,
                type=slot.type,
                binding=slot.binding,
                ref_mode=slot.ref_mode,
                group=slot.group,
                min_count=slot.min_count,
                max_count=slot.max_count,
                label=slot.label,
                asset_id=slot.asset_id,
                tag_ids=tuple(slot.tag_ids),
                filled_text=slot.filled_text,
                empty_text=slot.empty_text,
                empty_text_from_anchor=slot.empty_text_from_anchor,
                position=slot.position,
            )
            for slot in payload.slots
        ),
        attributes=dict(payload.attributes),
        demo_asset_id=payload.demo_asset_id,
        groups=tuple(SpecGroup(key=group.key, label=group.label) for group in payload.groups),
    )


def fingerprint_of(payload: TemplateSpecIn) -> str:
    """草稿摘要：按 key 排序的规范 JSON 的 sha256。"""
    data = payload.model_dump(mode="json", by_alias=True, exclude={"revision"})
    return hashlib.sha256(
        json.dumps(data, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()


Check = Callable[[], None | Awaitable[None]]


async def _first_blocker(
    code: str, field: str | None, check: Check
) -> Blocker | None:
    try:
        result = check()
        if inspect.isawaitable(result):
            await result
    except BadRequest as exc:
        return Blocker(code=code, message=str(exc), field=field)
    return None


def _blocked(fingerprint: str, blocker: Blocker) -> GateReport:
    return GateReport(blockers=(blocker,), fingerprint=fingerprint)


async def review_draft(
    session: AsyncSession, actor: CurrentUser, payload: TemplateSpecIn
) -> GateReport:
    """保存草稿的检查：结构、模型、引用、作者可读；跳过上限/全员可读/内联。"""
    fingerprint = fingerprint_of(payload)
    spec = to_core(payload)
    checks: tuple[tuple[str, str | None, Check], ...] = (
        ("spec_invalid", "prompt", lambda: validate_spec(spec)),
        ("model_invalid", "model", lambda: _check_model(spec)),
        ("required_slot_refs", "slots", lambda: validate_required_slot_refs(spec)),
        ("optional_slot_refs", "slots", lambda: validate_optional_slot_refs(spec)),
        ("fixed_asset_unreadable", "slots", lambda: _check_fixed_refs(session, actor, spec)),
        ("demo_asset_unreadable", "slots", lambda: _check_demo_ref(session, actor, spec)),
        (
            "fixed_text_unreadable",
            "slots",
            lambda: _check_fixed_text_readable(session, actor, spec),
        ),
    )
    for code, field, check in checks:
        blocker = await _first_blocker(code, field, check)
        if blocker is not None:
            return _blocked(fingerprint, blocker)
    return GateReport(blockers=(), fingerprint=fingerprint)


async def review_publish(
    session: AsyncSession, actor: CurrentUser, payload: TemplateSpecIn
) -> GateReport:
    """发布预演：全员可读、内联、内联后上限都查；通过时才产出冻结快照。"""
    fingerprint = fingerprint_of(payload)
    spec = to_core(payload)
    checks: tuple[tuple[str, str | None, Check], ...] = (
        ("spec_invalid", "prompt", lambda: validate_spec(spec)),
        ("model_invalid", "model", lambda: _check_model(spec)),
        ("required_slot_refs", "slots", lambda: validate_required_slot_refs(spec)),
        ("optional_slot_refs", "slots", lambda: validate_optional_slot_refs(spec)),
        (
            "fixed_asset_unreadable",
            "slots",
            lambda: _check_fixed_refs(session, actor, spec, require_public=True),
        ),
        (
            "demo_asset_unreadable",
            "slots",
            lambda: _check_demo_ref(session, actor, spec, require_public=True),
        ),
    )
    for code, field, check in checks:
        blocker = await _first_blocker(code, field, check)
        if blocker is not None:
            return _blocked(fingerprint, blocker)

    try:
        prompt, random_prompt, inlined_slots = await _inline_fixed_text(session, actor, spec)
    except BadRequest as exc:
        return _blocked(fingerprint, Blocker("fixed_text_unreadable", str(exc), "slots"))

    blocker = await _first_blocker(
        "not_executable",
        "prompt",
        lambda: _check_executable(replace(spec, prompt=prompt, slots=inlined_slots)),
    )
    if blocker is not None:
        return _blocked(fingerprint, blocker)

    return GateReport(
        blockers=(),
        fingerprint=fingerprint,
        snapshot=PublishSnapshot(
            model=spec.model,
            allowed_models=spec.allowed_models,
            params=spec.params,
            prompt=prompt,
            random_prompt=random_prompt,
            slots=inlined_slots,
            groups=spec.groups,
        ),
    )


def _spec_prompt_limit(spec: TemplateSpec) -> tuple[int, str]:
    catalog = model_catalog()
    limits = [
        (prompt_limit_for_capability(model.capability), model.capability)
        for model_id in effective_model_ids(spec.model, spec.allowed_models)
        if (model := catalog.get(model_id)) is not None
    ]
    if not limits:
        return EXECUTION_PROMPT_LIMIT, "video"
    return min(limits)


def _check_executable(spec: TemplateSpec) -> None:
    spec = normalize_refs(spec)
    total = len(spec.prompt)
    limit, capability = _spec_prompt_limit(spec)
    if total > limit:
        raise BadRequest(
            f"提示词共 {total} 字，超过执行层上限 {limit} 字"
            f"（{capability_label(capability)}模型）"
        )

    missing = [
        slot.label.strip() or slot.key
        for slot in spec.slots
        if slot.type in ("image", "video") and slot.binding == "user" and not slot.tag_ids
    ]
    if missing:
        raise BadRequest(
            "这些槽位要圈候选标签（模板库靠它列候选；来源=封装包的槽位还靠它判断"
            "包里哪一张进这一格）：" + "、".join(missing)
        )
    _check_optional_slots_have_fallback(spec)


def _check_optional_slots_have_fallback(spec: TemplateSpec) -> None:
    modes = slot_ref_modes(spec.prompt)
    if spec.random_prompt.strip():
        for key, ref_modes in slot_ref_modes(spec.random_prompt).items():
            modes.setdefault(key, set()).update(ref_modes)
    missing = [
        slot.label.strip() or slot.key
        for slot in spec.slots
        if slot.key in modes
        and slot.min_count == 0
        and slot.binding == "user"
        and not slot.empty_text.strip()
    ]
    if missing:
        raise BadRequest(
            "这些可选槽位被提示词引用了，要写「未选时文案」"
            "（使用者不填素材时这一段怎么说）：" + "、".join(missing)
        )


async def _check_model(spec: TemplateSpec) -> None:
    catalog = model_catalog()
    for model_id in effective_model_ids(spec.model, spec.allowed_models):
        model = catalog.get(model_id)
        if model is None:
            raise BadRequest(f"模型不存在或未启用：{model_id}")
        _check_model_params(spec, model)


def _check_model_params(spec: TemplateSpec, model: ModelSummary) -> None:
    schema = dict(model.param_schema or {})
    unknown = sorted(set(spec.params) - set(schema))
    if unknown:
        raise BadRequest(
            f"模型 {model.id} 不支持参数 {'、'.join(unknown)}；"
            f"可用：{'、'.join(sorted(schema)) or '（无）'}"
        )
    locked = sorted(
        key
        for key in spec.params
        if isinstance(schema.get(key), dict) and schema[key].get("userSelectable")
    )
    if locked:
        labels = "、".join(
            str(schema[key].get("label") or key)
            for key in locked  # type: ignore[index]
        )
        raise BadRequest(
            f"{labels} 由使用模板的人来选，封装时不用设置；它会在模板库填表时让用户自己选",
            details={"userSelectableParams": locked},
        )
    mixed = [slot for slot in spec.slots if slot.ref_mode == "mixed"]
    upper = sum(1 if slot.binding == "fixed" else slot.max_count for slot in mixed)
    if upper > MAX_SLOT_REFS:
        raise BadRequest(f"参与编号的素材上限 {upper} 个，超过 {MAX_SLOT_REFS}")
    if upper > model.max_references:
        if model.max_references == 0:
            raise BadRequest(f"模型 {model.id} 不接受参考素材，去掉需要编号的槽位")
        raise BadRequest(
            f"模型 {model.id} 最多接 {model.max_references} 张参考素材，"
            f"这份模板按上限算要 {upper} 张（固定槽位 1 张 + 用户槽位的张数）"
        )


def _is_publicly_readable(ref: LibraryAssetRef) -> bool:
    if ref.state != LibraryAssetState.ACTIVE:
        return False
    return ref.purpose == "case" or ref.visibility == LibraryVisibility.TEAM


async def _check_fixed_refs(
    session: AsyncSession,
    actor: CurrentUser,
    spec: TemplateSpec,
    *,
    require_public: bool = False,
) -> None:
    lookup = get_library_lookup()
    asset_ids = [slot.asset_id for slot in spec.slots if slot.asset_id and slot.type != "text"]
    if not asset_ids:
        return
    readable = await lookup.readable_ids(session, actor, asset_ids)
    missing = [item for item in asset_ids if item not in readable]
    if missing:
        raise BadRequest(f"固定素材不可用或无权访问：{'、'.join(missing)}")
    if not require_public:
        return
    refs = await lookup.get_many(session, asset_ids)
    private = [item for item in asset_ids if item in refs and not _is_publicly_readable(refs[item])]
    if private:
        raise BadRequest(
            f"固定素材只有你自己能看到，发布后别人用不了：{'、'.join(private)}。"
            "请把它移进案例池，或改成团队可见。"
        )


async def _check_demo_ref(
    session: AsyncSession,
    actor: CurrentUser,
    spec: TemplateSpec,
    *,
    require_public: bool = False,
) -> None:
    asset_id = spec.demo_asset_id
    if not asset_id:
        return
    lookup = get_library_lookup()
    readable = await lookup.readable_ids(session, actor, [asset_id])
    if asset_id not in readable:
        raise BadRequest("展示案例不可用或无权访问")
    if not require_public:
        return
    refs = await lookup.get_many(session, [asset_id])
    ref = refs.get(asset_id)
    if ref is not None and not _is_publicly_readable(ref):
        raise BadRequest(
            "展示案例只有你自己能看到，别人打开模板库看不到。请把它移进案例池，或改成团队可见。"
        )


async def _check_fixed_text_readable(
    session: AsyncSession, actor: CurrentUser, spec: TemplateSpec
) -> None:
    fixed = [
        slot
        for slot in spec.slots
        if slot.type == "text" and slot.binding == "fixed" and slot.asset_id
    ]
    if not fixed:
        return
    lookup = get_library_lookup()
    texts = await lookup.read_text_many(
        session, actor, [slot.asset_id for slot in fixed if slot.asset_id]
    )
    missing = [slot for slot in fixed if not slot.asset_id or slot.asset_id not in texts]
    if missing:
        names = "、".join(slot.label.strip() or slot.key for slot in missing)
        raise BadRequest(f"固定文案「{names}」读不到正文或已被删除")


async def _inline_fixed_text(
    session: AsyncSession, actor: CurrentUser, spec: TemplateSpec
) -> tuple[str, str, tuple[InputSlot, ...]]:
    fixed = [
        slot
        for slot in spec.slots
        if slot.type == "text" and slot.binding == "fixed" and slot.asset_id
    ]
    if not fixed:
        return spec.prompt, spec.random_prompt, spec.slots

    lookup = get_library_lookup()
    texts = await lookup.read_text_many(
        session, actor, [slot.asset_id for slot in fixed if slot.asset_id]
    )
    prompt, random_prompt = spec.prompt, spec.random_prompt
    for slot in fixed:
        ref = texts.get(slot.asset_id or "")
        if ref is None:
            raise BadRequest(
                f"固定文案「{slot.label or slot.key}」读不到正文或已被删除：{slot.asset_id}"
            )
        prompt = inline_slot_ref(prompt, slot.key, ref.content)
        random_prompt = inline_slot_ref(random_prompt, slot.key, ref.content)

    dropped = {slot.key for slot in fixed}
    return prompt, random_prompt, tuple(slot for slot in spec.slots if slot.key not in dropped)