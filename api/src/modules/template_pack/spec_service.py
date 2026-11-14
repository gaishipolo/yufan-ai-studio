"""模板规格的服务层：草稿保存、发布冻结、编译取请求体。

## 三条约束（都能在测试里找到对应断言）

1. **发布即冻结**：`template_spec_versions` 只插入不更新；同一份草稿重复发布
   返回同一个版本号（幂等），改了草稿才会 +1。
2. **不复制素材结构**：`source=pack` 的槽位只记包 id，编译时现场
   `expand_packs()` 展开；成员顺序由库给，本层不得重排。
3. **不自己物化**：这里只做 id 与元数据的校验，字节由 task 的
   `generic_inputs.materialize()` 落地。所以本模块**没有**任何写文件/上传代码。

## 为什么不在这里调 `POST /tasks`

业务模块之间禁止互相 import。`compile` 只把「填表结果」编成请求体返回，
转发给 `/tasks` 由前端做——那一步没有业务判断。
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import replace

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.context import CurrentUser
from core.exceptions import BadRequest, Conflict, NotFound
from core.ports.library_lookup import get_library_lookup
from core.ports.model_generation import get_model_generation
from core.template_spec import (
    EXECUTION_PROMPT_LIMIT,
    MAX_SLOT_REFS,
    CompiledRequest,
    FilledAsset,
    FilledSlot,
    InputSlot,
    PromptParts,
    TemplateSpec,
    compile_spec,
    validate_spec,
)
from db.base import utcnow
from modules.template_pack.models import (
    TemplateSpec as SpecRow,
)
from modules.template_pack.models import (
    TemplateSpecSlot,
    TemplateSpecVersion,
)
from modules.template_pack.spec_schema import (
    CompiledSlotOut,
    SpecCompileIn,
    SpecCompileOut,
    SpecPromptIn,
    SpecPublishOut,
    SpecSlotOut,
    SpecValidateOut,
    SpecVersionOut,
    TemplateSpecIn,
    TemplateSpecListOut,
    TemplateSpecOut,
)


# --------------------------------------------------------------------------- #
# 形状转换
# --------------------------------------------------------------------------- #
def to_core(payload: TemplateSpecIn) -> TemplateSpec:
    """入参 → core 词汇。校验只发生在 core，不在这里复制一份。"""
    return TemplateSpec(
        key=payload.key,
        name=payload.name,
        description=payload.description,
        model=payload.model,
        params=dict(payload.params),
        prompt=PromptParts(
            locked=payload.prompt.locked, editable=payload.prompt.editable
        ),
        slots=tuple(
            InputSlot(
                key=slot.key,
                type=slot.type,
                source=slot.source,
                binding=slot.binding,
                ref_mode=slot.ref_mode,
                min_count=slot.min_count,
                max_count=slot.max_count,
                label=slot.label,
                usage=slot.usage,
                asset_id=slot.asset_id,
                pack_id=slot.pack_id,
                position=slot.position,
            )
            for slot in payload.slots
        ),
        attributes=dict(payload.attributes),
    )


def fingerprint_of(payload: TemplateSpecIn) -> str:
    """草稿摘要：按 key 排序的规范 JSON 的 sha256。

    `revision` 不参与：它只是"第几次保存"，同一份内容重发不该产生新指纹。
    """
    data = payload.model_dump(mode="json", by_alias=True, exclude={"revision"})
    return hashlib.sha256(
        json.dumps(data, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def _slot_out(row: TemplateSpecSlot) -> SpecSlotOut:
    return SpecSlotOut(
        key=row.slot_key,
        type=row.type,  # type: ignore[arg-type]
        source=row.source,  # type: ignore[arg-type]
        binding=row.binding,  # type: ignore[arg-type]
        ref_mode=row.ref_mode,  # type: ignore[arg-type]
        min_count=row.min_count,
        max_count=row.max_count,
        label=row.label,
        usage=row.usage_text,
        asset_id=row.asset_id,
        pack_id=row.pack_id,
        position=row.position,
    )


def _spec_out(
    head: SpecRow, latest: TemplateSpecVersion | None, slots: Sequence[TemplateSpecSlot]
) -> TemplateSpecOut:
    draft = TemplateSpecIn.model_validate(head.draft) if head.draft else None
    return TemplateSpecOut(
        id=head.id,
        key=head.spec_key,
        name=head.name,
        description=head.description,
        owner_id=head.owner_id,
        status=head.status,  # type: ignore[arg-type]
        revision=head.revision,
        fingerprint=head.fingerprint,
        current_version=head.current_version,
        published_at=head.published_at,
        disabled_at=head.disabled_at,
        created_at=head.created_at,
        updated_at=head.updated_at,
        draft=draft,
        latest=(
            SpecVersionOut(
                version=latest.version,
                model=latest.model,
                params=dict(latest.params or {}),
                prompt=SpecPromptIn(
                    locked=latest.prompt_locked, editable=latest.prompt_editable
                ),
                slots=[_slot_out(slot) for slot in slots],
                fingerprint=latest.fingerprint,
                published_by=latest.published_by,
                published_at=latest.published_at,
            )
            if latest is not None
            else None
        ),
    )


# --------------------------------------------------------------------------- #
# 查库小工具
# --------------------------------------------------------------------------- #
async def _find(session: AsyncSession, key: str) -> SpecRow | None:
    return await session.scalar(
        select(SpecRow).where(
            SpecRow.spec_key == key, SpecRow.deleted_at.is_(None)
        )
    )


async def _latest(session: AsyncSession, head: SpecRow) -> TemplateSpecVersion | None:
    if head.current_version <= 0:
        return None
    return await session.scalar(
        select(TemplateSpecVersion).where(
            TemplateSpecVersion.spec_id == head.id,
            TemplateSpecVersion.version == head.current_version,
        )
    )


async def _slots(session: AsyncSession, version_id: str) -> list[TemplateSpecSlot]:
    """按 `position` 升序 —— 这个顺序就是编号顺序，别在别处再排一次。"""
    rows = await session.scalars(
        select(TemplateSpecSlot)
        .where(TemplateSpecSlot.version_id == version_id)
        .order_by(TemplateSpecSlot.position, TemplateSpecSlot.slot_key)
    )
    return list(rows)


async def _spec_out_of(session: AsyncSession, head: SpecRow) -> TemplateSpecOut:
    latest = await _latest(session, head)
    slots = await _slots(session, latest.id) if latest is not None else []
    return _spec_out(head, latest, slots)


# --------------------------------------------------------------------------- #
# 预检：结构（core） + 现实（模型目录 / 资产库）
# --------------------------------------------------------------------------- #
def _check_executable(spec: TemplateSpec) -> None:
    """提示词长度先按「两段原文之和」粗判。

    `{{slot:key*}}` 展开后可能比原文更长，所以这里只是**发布时的早提醒**；
    真正的判据在 `compile_for()` 里按展开后的成品长度算（那一处是精确的）。
    两处同一个常量，改上限只改 `EXECUTION_PROMPT_LIMIT`。
    """
    total = len(spec.prompt.locked) + len(spec.prompt.editable)
    if total > EXECUTION_PROMPT_LIMIT:
        raise BadRequest(
            f"提示词共 {total} 字，超过执行层上限 {EXECUTION_PROMPT_LIMIT} 字"
            "（通用生成任务的 prompt 上限）"
        )


async def _check_model(spec: TemplateSpec) -> None:
    """模型必须存在，参数键必须落在它的 `param_schema` 内。

    不做这一步的话，发布成功、用户在模板库填完表、点提交才被 422 —— 那时候
    用户已经贴不上原因了（"前几天还好好的"）。
    """
    catalog = {item.id: item for item in get_model_generation().models()}
    model = catalog.get(spec.model)
    if model is None:
        raise BadRequest(f"模型不存在或未启用：{spec.model}")
    schema = dict(model.param_schema or {})
    unknown = sorted(set(spec.params) - set(schema))
    if unknown:
        raise BadRequest(
            f"模型 {spec.model} 不支持参数 {'、'.join(unknown)}；"
            f"可用：{'、'.join(sorted(schema)) or '（无）'}"
        )
    mixed = [slot for slot in spec.slots if slot.ref_mode == "mixed"]
    if mixed and model.max_references == 0:
        raise BadRequest(f"模型 {spec.model} 不接受参考素材，去掉需要编号的槽位")
    # 固定槽位是确定的（1 个），用户槽位按上限算 —— 超了现在就报，别等提交。
    upper = sum(
        1 if slot.binding == "fixed" else slot.max_count for slot in mixed
    )
    if upper > MAX_SLOT_REFS:
        raise BadRequest(f"参与编号的素材上限 {upper} 个，超过 {MAX_SLOT_REFS}")


async def _check_fixed_refs(
    session: AsyncSession, actor: CurrentUser, spec: TemplateSpec
) -> None:
    """固定槽位在发布时必须**现在就能解释**：资产可读、封装成员可读。"""
    lookup = get_library_lookup()
    asset_ids = [
        slot.asset_id
        for slot in spec.slots
        if slot.source == "asset" and slot.asset_id
    ]
    if asset_ids:
        readable = await lookup.readable_ids(session, actor, asset_ids)
        missing = [item for item in asset_ids if item not in readable]
        if missing:
            raise BadRequest(f"固定素材不可用或无权访问：{'、'.join(missing)}")
    pack_ids = [
        slot.pack_id for slot in spec.slots if slot.source == "pack" and slot.pack_id
    ]
    if pack_ids:
        expanded = await lookup.expand_packs(session, actor, pack_ids)
        for pack_id in pack_ids:
            members = expanded.get(pack_id)
            if not members:
                raise BadRequest(f"固定封装不存在或无权访问：{pack_id}")
            blocked = [member.name or member.id for member in members if not member.readable]
            if blocked:
                raise BadRequest(f"固定封装的成员不可读：{'、'.join(blocked)}")


async def validate_draft(
    session: AsyncSession,
    actor: CurrentUser,
    payload: TemplateSpecIn,
    *,
    executable: bool = True,
) -> SpecValidateOut:
    """结构 + 模型 + 固定素材的全套预检；不落库，返回幂等指纹。

    `executable=False` 只给**保存草稿**用：草稿允许写超过执行上限的提示词（旧模板里
    有 2 万字级别的那种，P3 迁移要能先落成草稿）。预检与发布才判"这段文字跑得起来吗"。
    """
    spec = to_core(payload)
    validate_spec(spec)
    if executable:
        _check_executable(spec)
    await _check_model(spec)
    await _check_fixed_refs(session, actor, spec)
    return SpecValidateOut(valid=True, fingerprint=fingerprint_of(payload))


# --------------------------------------------------------------------------- #
# 写：保存草稿 / 发布 / 停用
# --------------------------------------------------------------------------- #
async def save(
    session: AsyncSession, actor: CurrentUser, payload: TemplateSpecIn
) -> TemplateSpecOut:
    """保存草稿。同 key 同内容幂等；`revision` 不匹配则 409。"""
    await validate_draft(session, actor, payload, executable=False)
    fingerprint = fingerprint_of(payload)
    head = await _find(session, payload.key)

    if head is None:
        head = SpecRow(
            spec_key=payload.key,
            name=payload.name,
            description=payload.description,
            owner_id=actor.id,
            status="draft",
            draft=payload.model_dump(mode="json", by_alias=True, exclude={"revision"}),
            revision=1,
            fingerprint=fingerprint,
            current_version=0,
        )
        session.add(head)
        await session.flush()
    else:
        if head.owner_id != actor.id:
            raise Conflict(f"配置 {payload.key} 属于其他用户")
        if payload.revision is not None and payload.revision != head.revision:
            raise Conflict(
                f"配置已被更新（当前第 {head.revision} 次修订），请刷新后重试"
            )
        if head.fingerprint == fingerprint:
            # 幂等：内容没变就不加修订号，也不把已发布状态打回草稿。
            out = await _spec_out_of(session, head)
            await session.commit()
            return out
        head.name = payload.name
        head.description = payload.description
        head.draft = payload.model_dump(mode="json", by_alias=True, exclude={"revision"})
        head.fingerprint = fingerprint
        head.revision += 1

    out = await _spec_out_of(session, head)
    # `get_session()` 只在异常回滚，提交由 service 显式控制；漏了就是 201 后读 404。
    await session.commit()
    return out


async def publish(session: AsyncSession, actor: CurrentUser, key: str) -> SpecPublishOut:
    """把当前草稿冻结成一个不可变版本。同草稿重复发布返回同一版本（幂等）。"""
    head = await _find(session, key)
    if head is None:
        raise NotFound(f"配置不存在：{key}")
    if head.owner_id != actor.id:
        raise Conflict(f"配置 {key} 属于其他用户")
    if not head.draft:
        raise BadRequest("还没有保存任何内容，无法发布")

    payload = TemplateSpecIn.model_validate(head.draft)
    spec = to_core(payload)
    validate_spec(spec)
    _check_executable(spec)
    await _check_model(spec)
    await _check_fixed_refs(session, actor, spec)

    latest = await _latest(session, head)
    fingerprint = fingerprint_of(payload)
    if latest is not None and latest.fingerprint == fingerprint:
        # 同一份草稿重发：版本号不动，也不新增快照行（回执要能重复调）。
        out = await _spec_out_of(session, head)
        await session.commit()
        return SpecPublishOut(version=latest.version, spec=out)

    version = head.current_version + 1
    row = TemplateSpecVersion(
        spec_id=head.id,
        version=version,
        model=spec.model,
        params=dict(spec.params),
        prompt_locked=spec.prompt.locked,
        prompt_editable=spec.prompt.editable,
        fingerprint=fingerprint,
        published_by=actor.id,
    )
    session.add(row)
    await session.flush()  # 主键在 flush 后才有，槽位要用它
    for slot in spec.slots:
        session.add(
            TemplateSpecSlot(
                version_id=row.id,
                slot_key=slot.key,
                type=slot.type,
                source=slot.source,
                binding=slot.binding,
                ref_mode=slot.ref_mode,
                min_count=slot.min_count,
                max_count=slot.max_count,
                label=slot.label,
                usage_text=slot.usage,
                asset_id=slot.asset_id,
                pack_id=slot.pack_id,
                position=slot.position,
            )
        )
    head.status = "published"
    head.current_version = version
    head.published_at = utcnow()
    head.disabled_at = None
    await session.flush()

    out = await _spec_out_of(session, head)
    await session.commit()
    return SpecPublishOut(version=version, spec=out)


async def disable(session: AsyncSession, actor: CurrentUser, key: str) -> TemplateSpecOut:
    """下架：不再出现在模板库，但历史版本与已生成任务照旧可查。"""
    head = await _find(session, key)
    if head is None:
        raise NotFound(f"配置不存在：{key}")
    if head.owner_id != actor.id:
        raise Conflict(f"配置 {key} 属于其他用户")
    if head.status != "disabled":
        head.status = "disabled"
        head.disabled_at = utcnow()
        await session.flush()
    out = await _spec_out_of(session, head)
    await session.commit()
    return out


# --------------------------------------------------------------------------- #
# 读
# --------------------------------------------------------------------------- #
async def get_out(session: AsyncSession, key: str) -> TemplateSpecOut:
    head = await _find(session, key)
    if head is None:
        raise NotFound(f"配置不存在：{key}")
    return await _spec_out_of(session, head)


async def list_out(
    session: AsyncSession,
    *,
    status: str = "",
    keyword: str = "",
    published_only: bool = False,
    page: int = 1,
    page_size: int = 20,
) -> TemplateSpecListOut:
    stmt = select(SpecRow).where(SpecRow.deleted_at.is_(None))
    count_stmt = select(func.count()).select_from(SpecRow).where(
        SpecRow.deleted_at.is_(None)
    )
    if published_only:
        stmt = stmt.where(SpecRow.status == "published")
        count_stmt = count_stmt.where(SpecRow.status == "published")
    elif status:
        stmt = stmt.where(SpecRow.status == status)
        count_stmt = count_stmt.where(SpecRow.status == status)
    needle = keyword.strip()
    if needle:
        # 名称或 key 命中即可：运营记的是其中一个，不该逼他们想另一个。
        stmt = stmt.where(
            SpecRow.name.contains(needle) | SpecRow.spec_key.contains(needle)
        )
        count_stmt = count_stmt.where(
            SpecRow.name.contains(needle) | SpecRow.spec_key.contains(needle)
        )
    total = int(await session.scalar(count_stmt) or 0)
    rows = await session.scalars(
        stmt.order_by(SpecRow.updated_at.desc(), SpecRow.id)
        .limit(page_size)
        .offset((page - 1) * page_size)
    )
    items = [await _spec_out_of(session, row) for row in rows]
    return TemplateSpecListOut(items=items, total=total)


# --------------------------------------------------------------------------- #
# 编译
# --------------------------------------------------------------------------- #
async def compile_for(
    session: AsyncSession, actor: CurrentUser, key: str, payload: SpecCompileIn
) -> SpecCompileOut:
    """把「填表结果」编成执行请求。纯映射：不落库、不调 task。"""
    head = await _find(session, key)
    if head is None:
        raise NotFound(f"配置不存在：{key}")
    if head.status == "disabled":
        raise Conflict(f"配置 {key} 已停用，不能用于新建任务")
    version_no = payload.version or head.current_version
    if version_no <= 0:
        raise Conflict(f"配置 {key} 还没有发布版本")
    row = await session.scalar(
        select(TemplateSpecVersion).where(
            TemplateSpecVersion.spec_id == head.id,
            TemplateSpecVersion.version == version_no,
        )
    )
    if row is None:
        raise NotFound(f"配置 {key} 没有第 {version_no} 版")
    slots = await _slots(session, row.id)
    spec = TemplateSpec(
        key=head.spec_key,
        name=head.name,
        model=row.model,
        params=dict(row.params or {}),
        prompt=PromptParts(locked=row.prompt_locked, editable=row.prompt_editable),
        slots=tuple(_slot_core(item) for item in slots),
    )
    if payload.prompt_override is not None:
        spec = _with_override(spec, payload.prompt_override)
    fills = await _fills(session, actor, spec, payload.slot_values)
    compiled = compile_spec(spec, fills)
    # 展开后的成品才是真正要发出去的那段：`{{slot:key*}}` 一次展开可能多出几十字。
    if len(compiled.prompt) > EXECUTION_PROMPT_LIMIT:
        raise BadRequest(
            f"编译后的提示词 {len(compiled.prompt)} 字，超过执行层上限 "
            f"{EXECUTION_PROMPT_LIMIT} 字；少填几张参考图或缩短描述"
        )
    return _compile_out(version_no, compiled, spec, fills)


def _with_override(spec: TemplateSpec, text: str) -> TemplateSpec:
    """把填表时改写的提示词换进 `editable` 段。

    三条口径，都是为了"模板作者定的边界不许被调用方绕开"：

    1. `locked` 段永不可改 —— 它对应封装时写死的画面约束；
    2. 模板的 `editable` 为空 = 作者关掉了这个入口，传 override 就 400（不是静默忽略）；
    3. override 里不许写 `{{Mixed N}}` —— 编号是编译派生的，写死会在少填时错位
       （与 `validate_spec()` 同一条判据，只是这里发生在填表侧）。
    """
    if not spec.prompt.editable.strip():
        raise BadRequest("该模板的提示词由封装固定，不能修改")
    if "{{Mixed" in text:
        raise BadRequest("提示词里不要直接写 {{Mixed N}}，用 {{slot:key}} 引用槽位")
    return replace(spec, prompt=PromptParts(locked=spec.prompt.locked, editable=text))


def _slot_core(row: TemplateSpecSlot) -> InputSlot:
    return InputSlot(
        key=row.slot_key,
        type=row.type,  # type: ignore[arg-type]
        source=row.source,  # type: ignore[arg-type]
        binding=row.binding,  # type: ignore[arg-type]
        ref_mode=row.ref_mode,  # type: ignore[arg-type]
        min_count=row.min_count,
        max_count=row.max_count,
        label=row.label,
        usage=row.usage_text,
        asset_id=row.asset_id,
        pack_id=row.pack_id,
        position=row.position,
    )


async def _fills(
    session: AsyncSession,
    actor: CurrentUser,
    spec: TemplateSpec,
    values: Mapping[str, Sequence[str]],
) -> dict[str, FilledSlot]:
    """把「用户填的 id」翻译成编译需要的 `FilledSlot`。

    素材事实只从端口取：单资产走 `get_many` / `readable_ids`，封装走
    `expand_packs`。这一层不查表、不猜类型，也不缓存成员。
    """
    lookup = get_library_lookup()
    fills: dict[str, FilledSlot] = {}
    for slot in spec.slots:
        chosen = [item for item in values.get(slot.key, ()) if item]
        if slot.source == "pack":
            pack_id = slot.pack_id if slot.binding == "fixed" else (
                chosen[0] if chosen else ""
            )
            if not pack_id:
                continue
            expanded = await lookup.expand_packs(session, actor, [pack_id])
            members = expanded.get(pack_id)
            if not members:
                raise BadRequest(f"槽位「{slot.label or slot.key}」的封装不存在或无权访问")
            blocked = [member.name or member.id for member in members if not member.readable]
            if blocked:
                raise BadRequest(
                    f"槽位「{slot.label or slot.key}」的封装成员不可读："
                    f"{'、'.join(blocked)}"
                )
            fills[slot.key] = FilledSlot(
                slot_key=slot.key,
                assets=tuple(
                    FilledAsset(
                        asset_id=member.id,
                        kind=str(member.kind),
                        role=member.role,
                        name=member.name,
                    )
                    for member in members
                ),
                pack_id=pack_id,
            )
            continue

        # `source=asset`：固定槽位取 spec 里的 id，用户槽位按挑选顺序。
        asset_ids = [slot.asset_id] if slot.binding == "fixed" and slot.asset_id else chosen
        if not asset_ids:
            continue
        refs = await lookup.get_many(session, asset_ids)
        missing = [item for item in asset_ids if item not in refs]
        if missing:
            raise BadRequest(f"槽位「{slot.label or slot.key}」的素材不存在：{'、'.join(missing)}")
        readable = await lookup.readable_ids(session, actor, asset_ids)
        denied = [item for item in asset_ids if item not in readable]
        if denied:
            raise BadRequest(f"槽位「{slot.label or slot.key}」的素材无权访问：{'、'.join(denied)}")
        fills[slot.key] = FilledSlot(
            slot_key=slot.key,
            assets=tuple(
                FilledAsset(
                    asset_id=item,
                    kind=str(refs[item].kind),
                    name=refs[item].name,
                )
                for item in asset_ids
            ),
        )
    return fills


def _compile_out(
    version: int,
    compiled: CompiledRequest,
    spec: TemplateSpec,
    fills: Mapping[str, FilledSlot],
) -> SpecCompileOut:
    slots: list[CompiledSlotOut] = []
    for slot in sorted(spec.slots, key=lambda item: item.position):
        tokens = compiled.slot_tokens.get(slot.key, ())
        if not tokens:
            continue
        assets = fills.get(slot.key)
        slots.append(
            CompiledSlotOut(
                key=slot.key,
                tokens=list(tokens),
                asset_ids=[asset.asset_id for asset in assets.assets] if assets else [],
            )
        )
    return SpecCompileOut(
        version=version,
        model=compiled.model,
        prompt=compiled.prompt,
        params=dict(compiled.params),
        reference_library_ids=list(compiled.reference_ids),
        text_library_ids=list(compiled.text_ids),
        slots=slots,
    )

