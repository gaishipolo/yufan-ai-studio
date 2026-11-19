"""模板规格的服务层：草稿保存、发布冻结、编译取请求体。

## 三条约束（都能在测试里找到对应断言）

1. **发布即冻结**：`template_spec_versions` 只插入不更新；同一份草稿重复发布
   返回同一个版本号（幂等），改了草稿才会 +1。
2. **只存 id 不存结构**：槽位记的是 `library_assets.id`，素材的字节与清单都不进
   模板，模板不替素材库描述"这个对象有哪些视图"。
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
from typing import Any

from sqlalchemy import ColumnElement, and_, false, func, select, true
from sqlalchemy.ext.asyncio import AsyncSession

from core.context import CurrentUser
from core.exceptions import BadRequest, Conflict, NotFound
from core.param_schema import resolve_params
from core.ports.library_lookup import get_library_lookup
from core.ports.model_generation import ModelSummary, get_model_generation
from core.template_spec import (
    EXECUTION_PROMPT_LIMIT,
    MAX_SLOT_REFS,
    CompiledRequest,
    FilledAsset,
    FilledSlot,
    InputSlot,
    TemplateSpec,
    compile_spec,
    normalize_refs,
    slot_ref_modes,
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
                min_count=slot.min_count,
                max_count=slot.max_count,
                label=slot.label,
                asset_id=slot.asset_id,
                tag_ids=tuple(slot.tag_ids),
                filled_text=slot.filled_text,
                empty_text=slot.empty_text,
                position=slot.position,
            )
            for slot in payload.slots
        ),
        attributes=dict(payload.attributes),
        demo_asset_id=payload.demo_asset_id,
    )


def fingerprint_of(payload: TemplateSpecIn) -> str:
    """草稿摘要：按 key 排序的规范 JSON 的 sha256。

    `revision` 不参与：它只是"第几次保存"，同一份内容重发不该产生新指纹。
    """
    data = payload.model_dump(mode="json", by_alias=True, exclude={"revision"})
    return hashlib.sha256(
        json.dumps(data, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def _slot_tag_ids(row: TemplateSpecSlot) -> list[str]:
    """读一个槽位的候选集标签。

    新行写在 `tag_ids`（JSON 列表）；老行只有 `tag_id`（一个）—— 读的时候退回
    它，**老模板才能继续用**（迁移不做回填，见 `d9e4f1a83c07`）。

    回退写在**读侧**而不是靠迁移回填：回填要动数据、还要处理 JSON 列的方言
    差异；而"读的时候退回旧的"一次就够 —— 老模板照样能用，只是作者下次保存、
    再发布之后才会写成新形状。
    """
    ids = [item for item in (row.tag_ids or []) if item]
    if ids:
        return ids
    return [row.tag_id] if row.tag_id else []


def _slot_out(row: TemplateSpecSlot) -> SpecSlotOut:
    return SpecSlotOut(
        key=row.slot_key,
        type=row.type,  # type: ignore[arg-type]
        binding=row.binding,  # type: ignore[arg-type]
        ref_mode=row.ref_mode,  # type: ignore[arg-type]
        min_count=row.min_count,
        max_count=row.max_count,
        label=row.label,
        asset_id=row.asset_id,
        tag_ids=_slot_tag_ids(row),
        filled_text=row.filled_text or "",
        empty_text=row.empty_text or "",
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
        deleted_at=head.deleted_at,
        created_at=head.created_at,
        updated_at=head.updated_at,
        draft=draft,
        demo_asset_id=(draft.demo_asset_id if draft is not None else None),
        latest=(
            SpecVersionOut(
                version=latest.version,
                model=latest.model,
                allowed_models=list(latest.allowed_models or []),
                params=dict(latest.params or {}),
                prompt=latest.prompt,
                # 前端判「随机档可不可选」只看这一个字段（`hasRandomPrompt`）。
                # 漏映射时它的表现是**永远空**，于是模板明明配了，档位却置灰。
                random_prompt=latest.random_prompt or "",
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


async def _find_any(session: AsyncSession, key: str) -> SpecRow | None:
    """连回收站一起找：恢复入口要能摸到已软删的行。"""
    return await session.scalar(select(SpecRow).where(SpecRow.spec_key == key))


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
    """发布前判「这份模板跑得起来吗」：提示词长度 + 素材候选集够不够明确。

    提示词部分只是**早提醒**：`{{slot:key*}}` 展开后可能比原文更长，真正的判据在
    `compile_for()` 里按展开后的成品长度算。两处同一个常量，改上限只改
    `EXECUTION_PROMPT_LIMIT`。
    """
    # 只认一种写法：作者写的 `{{Mixed N}}` 先换算成 key，
    # 不然后面按 prompt 判「哪些可选槽位被单张引用」会一个都判不到。
    spec = normalize_refs(spec)
    total = len(spec.prompt)
    if total > EXECUTION_PROMPT_LIMIT:
        raise BadRequest(
            f"提示词共 {total} 字，超过执行层上限 {EXECUTION_PROMPT_LIMIT} 字"
            "（通用生成任务的 prompt 上限）"
        )

    # 模板库的填表区按「标签 · 槽位名」分组展示候选素材，所以**要让用户填的
    # 图片/视频槽位必须圈至少一个标签**：没有标签时那一段只能退化成"全部素材"，
    # 素材一多就没法挑（这正是这一轮要消灭的形态）。
    #
    # 只要求 `binding=user`：固定槽位的内容作者已经选死了，模板使用者根本不挑，
    # 逼作者再填一个标签只是多一步。文本/音频也不要求 —— 它们走各自的列表
    # 选择器，没有"按标签分组"的概念。
    missing = [
        slot.label.strip() or slot.key
        for slot in spec.slots
        if slot.type in ("image", "video") and slot.binding == "user" and not slot.tag_ids
    ]
    if missing:
        raise BadRequest(
            "这些槽位要圈标签（模板库按标签分组让用户挑素材）："
            + "、".join(missing)
        )

    _check_optional_slots_have_fallback(spec)


def _check_optional_slots_have_fallback(spec: TemplateSpec) -> None:
    """**可选**槽位（`min_count == 0`）被**单张引用**时，必须有「未选时文案」。

    可选槽位不填素材也能编译 —— 那时提示词里这一段由「未选时文案」接管。
    没有它的话，使用者在模板库不填素材就会在点生成时被 400 打回，而那句话
    与「我刚才没选素材」看不出关系。

    只看**单张引用**（`{{slot:x}}`）：三种写法的「没填」语义不同（见
    `core/template_spec/compile.py`）—— 整组引用（`{{slot:x*}}`）没填时降级
    为空（既有语义，不报错），第 N 张（`{{slot:x.N}}`）没填时必然报错
    （没有「第 N 张」这回事，兜底文案套不上去）。所以**只有单张引用**
    才需要兜底文案 —— 把整组引用也算进来，会把一堆本来好用的模板拦在发布外。

    放在**发布**这一道闸（而不是编译）：作者在封装页把槽位改成可选、还没写
    兜底文案时，草稿能存（`validate_spec` 也被保存草稿调用，拦了会逼作者
    先想清楚才能存草稿），但**发布出去之前**必须写好 —— 发布意味着「别人会
    拿去用」，别人不该替作者填这个坑。

    只看**被引用**的可选槽位：没被引用的槽位填不填都不影响提示词，
    要求它写兜底文案是白加一步。

    **两段提示词一起看**：随机档用 `random_prompt` 整段替换主提示词，而随机档
    同样不传可选素材 —— 只在主提示词上查，会放行「随机档引用了可选槽位却没写
    兜底」的模板，使用者只在切到随机档那一次被 400 打回。
    """
    modes = slot_ref_modes(spec.prompt)
    if spec.random_prompt.strip():
        for key, ref_modes in slot_ref_modes(spec.random_prompt).items():
            modes.setdefault(key, set()).update(ref_modes)
    missing = [
        slot.label.strip() or slot.key
        for slot in spec.slots
        if slot.key in modes
        # 只有「单张引用」需要兜底：整组引用降级为空，第 N 张必然报错。
        and "" in modes[slot.key]
        and slot.min_count == 0
        and slot.binding == "user"
        and not slot.empty_text.strip()
    ]
    if missing:
        raise BadRequest(
            "这些可选槽位被提示词引用了，要写「未选时文案」"
            "（使用者不填素材时这一段怎么说）："
            + "、".join(missing)
        )


def effective_model_ids(
    fixed_model: str, allowed_models: Sequence[str]
) -> tuple[str, ...]:
    """这份模板能跑的全部模型：**固定模型 + 作者声明的允许清单**（去重、保序）。

    固定模型永远在可选项里：它是模板自己的模型，作者没把它写进清单也不该让
    使用者无路可走。清单是「再加几款让使用者挑」的声明，所以是并集而不是替换。
    """
    ids = [fixed_model]
    for model_id in allowed_models:
        if model_id not in ids:
            ids.append(model_id)
    return tuple(ids)


async def _check_model(spec: TemplateSpec) -> None:
    """模型必须存在，参数键必须落在它的 `param_schema` 内。

    不做这一步的话，发布成功、用户在模板库填完表、点提交才被 422 —— 那时候
    用户已经贴不上原因了（"前几天还好好的"）。

    清单里的**每一款**都要过同一道闸：使用者挑的是清单里的某一款，挑到哪一款
    都不该被拒 —— 否则「清单里第三款一选就报错」这种坑要等使用者自己踩到。
    """
    catalog = {item.id: item for item in get_model_generation().models()}
    for model_id in effective_model_ids(spec.model, spec.allowed_models):
        model = catalog.get(model_id)
        if model is None:
            raise BadRequest(f"模型不存在或未启用：{model_id}")
        _check_model_params(spec, model)


def _check_model_params(spec: TemplateSpec, model: ModelSummary) -> None:
    """一款模型的参数与参考素材上限能不能承接这份模板。"""
    schema = dict(model.param_schema or {})
    unknown = sorted(set(spec.params) - set(schema))
    if unknown:
        raise BadRequest(
            f"模型 {model.id} 不支持参数 {'、'.join(unknown)}；"
            f"可用：{'、'.join(sorted(schema)) or '（无）'}"
        )
    # 标了 `userSelectable` 的参数**由使用模板的人选**，作者不许写死。
    # 写死 = 作者替每个使用者决定了要不要花积分，而积分是用的人的钱。
    # 这类参数由模板库在填表时呈现，使用者的选择不落进 spec.params。
    locked = sorted(
        key
        for key in spec.params
        if isinstance(schema.get(key), dict) and schema[key].get("userSelectable")
    )
    if locked:
        labels = "、".join(
            str(schema[key].get("label") or key) for key in locked  # type: ignore[index]
        )
        raise BadRequest(
            f"{labels} 由使用模板的人来选，封装时不用设置；"
            "它会在模板库填表时让用户自己选",
            details={"userSelectableParams": locked},
        )
    mixed = [slot for slot in spec.slots if slot.ref_mode == "mixed"]
    # 固定槽位是确定的（1 个），用户槽位按上限算 —— 超了现在就报，别等提交。
    upper = sum(
        1 if slot.binding == "fixed" else slot.max_count for slot in mixed
    )
    if upper > MAX_SLOT_REFS:
        raise BadRequest(f"参与编号的素材上限 {upper} 个，超过 {MAX_SLOT_REFS}")
    # 与模型的承接能力比：**不能只判 `max_references == 0`**。只判 0 的话，
    # 「最多接 4 张」的模型配了 5 个槽位照样发布成功，使用者填满才在提交时
    # 被 400 打回 —— 那时作者已经把模板发出去了。
    if upper > model.max_references:
        if model.max_references == 0:
            raise BadRequest(f"模型 {model.id} 不接受参考素材，去掉需要编号的槽位")
        raise BadRequest(
            f"模型 {model.id} 最多接 {model.max_references} 张参考素材，"
            f"这份模板按上限算要 {upper} 张（固定槽位 1 张 + 用户槽位的张数）"
        )


async def _check_fixed_refs(
    session: AsyncSession, actor: CurrentUser, spec: TemplateSpec
) -> None:
    """固定槽位在发布时必须**现在就能解释**：资产可读。"""
    lookup = get_library_lookup()
    asset_ids = [slot.asset_id for slot in spec.slots if slot.asset_id]
    if asset_ids:
        readable = await lookup.readable_ids(session, actor, asset_ids)
        missing = [item for item in asset_ids if item not in readable]
        if missing:
            raise BadRequest(f"固定素材不可用或无权访问：{'、'.join(missing)}")


async def _check_demo_ref(
    session: AsyncSession, actor: CurrentUser, spec: TemplateSpec
) -> None:
    """展示案例必须**现在就能读**。

    为什么这一条不能省：`demoAssetId` 是删除保护的依据（见
    `SpecDemoCases`）。零校验时，写一个**别人的私有 id** 进去就能让那条
    资产从此永远 purge 不掉（409 说「仍被引用」），而作者根本不知道自己
    占了别人的东西；那个 id 甚至可能是随手编的，引用检查永远查无此人。

    读不到就拒绝保存（fail-closed）：宁可保存不进去，也不能留一条查不出来的
    引用 —— 后者会让 owner 在回收站里卡死，且没有任何线索能定位到是谁占的。
    """
    asset_id = spec.demo_asset_id
    if not asset_id:
        return
    lookup = get_library_lookup()
    readable = await lookup.readable_ids(session, actor, [asset_id])
    if asset_id not in readable:
        raise BadRequest("展示案例不可用或无权访问")


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
    await _check_demo_ref(session, actor, spec)
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
        #
        # **但状态要恢复**：停用之后重新发布，语义就是「重新上线」。
        # 这里若只 return 不动 status，接口会给 200 而记录仍是 disabled ——
        # 界面拿到成功、模板却还在下架状态，用户以为启用过了。
        # （真撞过：草稿一个字都没改就想把停用的模板放回去，走了这条幂等分支。）
        #
        # `published_at` 不动：它记的是**这个版本首次发布**的时间，
        # 这次没有新版本，改它会让「发布于何时」说谎。
        if head.status != "published":
            head.status = "published"
            head.disabled_at = None
            await session.flush()
        out = await _spec_out_of(session, head)
        await session.commit()
        return SpecPublishOut(version=latest.version, spec=out)

    version = head.current_version + 1
    row = TemplateSpecVersion(
        spec_id=head.id,
        version=version,
        model=spec.model,
        allowed_models=list(spec.allowed_models),
        params=dict(spec.params),
        prompt=spec.prompt,
        random_prompt=spec.random_prompt,
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
                binding=slot.binding,
                ref_mode=slot.ref_mode,
                min_count=slot.min_count,
                max_count=slot.max_count,
                label=slot.label,
                asset_id=slot.asset_id,
                tag_ids=list(slot.tag_ids),
                # 老列也填上（记第一个标签）：降级回旧代码时它还能看懂这一行，
                # 代价只是「多标签」退化成「第一个标签」。
                tag_id=slot.tag_ids[0] if slot.tag_ids else "",
                filled_text=slot.filled_text,
                empty_text=slot.empty_text,
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


async def delete(session: AsyncSession, actor: CurrentUser, key: str) -> TemplateSpecOut:
    """移进回收站（软删）。历史版本与已生成任务不受影响，可恢复。

    为什么不硬删：`template_spec_versions` 记的是「当时的提示词原文」，硬删等于
    让历史任务的溯源断掉；而模板本身是资产，误删的代价比留一行大得多。
    """
    head = await _find(session, key)
    if head is None:
        raise NotFound(f"配置不存在：{key}")
    if head.owner_id != actor.id:
        raise Conflict(f"配置 {key} 属于其他用户")
    head.deleted_at = utcnow()
    await session.flush()
    out = await _spec_out_of(session, head)
    await session.commit()
    return out


async def restore(session: AsyncSession, actor: CurrentUser, key: str) -> TemplateSpecOut:
    """从回收站恢复。key 是唯一约束，所以不能出现「新行占了旧 key」的情况。"""
    head = await _find_any(session, key)
    if head is None:
        raise NotFound(f"配置不存在：{key}")
    if head.owner_id != actor.id:
        raise Conflict(f"配置 {key} 属于其他用户")
    if head.deleted_at is None:
        raise Conflict(f"配置 {key} 不在回收站里")
    head.deleted_at = None
    await session.flush()
    out = await _spec_out_of(session, head)
    await session.commit()
    return out


# --------------------------------------------------------------------------- #
# 读
# --------------------------------------------------------------------------- #
async def get_out(
    session: AsyncSession, actor: CurrentUser, key: str
) -> TemplateSpecOut:
    """规格详情。

    **已发布的是公开的**（模板库要给所有人用），**草稿与停用是作者私有的**：
    草稿里存的是提示词原文与固定素材 id，不是给外人看的。越权读到别人的草稿
    等于把人家的创作过程与素材清单公开了。

    越权时回 404 而不是 403 —— 403 会确认「这个 key 存在」，等于给 id 空间
    开了个枚举口。
    """
    head = await _find(session, key)
    if head is None:
        raise NotFound(f"配置不存在：{key}")
    if head.status != "published" and head.owner_id != actor.id:
        raise NotFound(f"配置不存在：{key}")
    return await _spec_out_of(session, head)


async def list_out(
    session: AsyncSession,
    actor: CurrentUser,
    *,
    status: str = "",
    keyword: str = "",
    published_only: bool = False,
    target: str = "all",
    trashed: bool = False,
    page: int = 1,
    page_size: int = 20,
) -> TemplateSpecListOut:
    """`target` 按**模型声明**筛：`replacement` 只留能跑替换链的，`generation` 反过来。

    判据来自 `GET /models` 的 `replacement`（驱动声明），**不看槽位组合** ——
    靠"有 fixed video 槽 + 有多图槽"猜，普通多图模板会被误判成可替换模板。

    ## 可见范围

    - `published_only=True`（模板库）：**公开**。已发布是给人用的。
    - 其余（本人列表 / 回收站）：**只有作者**。草稿里是提示词原文与固定素材
      id，回收站里是作者自己删掉的东西 —— 都不是给别人看的。

    过滤落在 SQL 里：`total` 与 `items` 必须是同一个口径，分开算就会翻出
    「共 12 条、第 2 页空的」这种页。
    """
    alive = SpecRow.deleted_at.is_(None) if not trashed else SpecRow.deleted_at.is_not(None)
    stmt = select(SpecRow).where(alive)
    count_stmt = select(func.count()).select_from(SpecRow).where(alive)
    if not published_only:
        stmt = stmt.where(SpecRow.owner_id == actor.id)
        count_stmt = count_stmt.where(SpecRow.owner_id == actor.id)
    if trashed:
        # 回收站只按删除时间排，不认 status / target：已经删掉的东西再筛状态没有意义。
        pass
    elif published_only:
        stmt = stmt.where(SpecRow.status == "published")
        count_stmt = count_stmt.where(SpecRow.status == "published")
    elif status:
        stmt = stmt.where(SpecRow.status == status)
        count_stmt = count_stmt.where(SpecRow.status == status)
    if target != "all" and not trashed:
        replaceable = [
            item.id
            for item in get_model_generation().models()
            if item.replacement
        ]
        condition: ColumnElement[bool]
        if target == "replacement":
            condition = (
                TemplateSpecVersion.model.in_(replaceable) if replaceable else false()
            )
        else:
            condition = (
                TemplateSpecVersion.model.notin_(replaceable) if replaceable else true()
            )
        # 只跟「当前发布版本」 join：草稿里的模型不算数，停用后重新编辑过也一样。
        join_on = and_(
            TemplateSpecVersion.spec_id == SpecRow.id,
            TemplateSpecVersion.version == SpecRow.current_version,
        )
        stmt = stmt.join(TemplateSpecVersion, join_on).where(condition)
        count_stmt = count_stmt.join(TemplateSpecVersion, join_on).where(condition)
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
    order = (
        (SpecRow.deleted_at.desc(), SpecRow.id)
        if trashed
        else (SpecRow.updated_at.desc(), SpecRow.id)
    )
    rows = await session.scalars(
        stmt.order_by(*order)
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
    allowed = tuple(row.allowed_models or ())
    effective = effective_model_ids(row.model, allowed)
    # 使用者挑的模型必须在清单里：静默改成固定模型等于「用户选的东西没生效」，
    # 而页面看起来一切正常（模型名还显示着他选的那款）。
    chosen = (payload.model or row.model).strip()
    if chosen not in effective:
        raise BadRequest(
            f"模型不在该模板允许的清单里：{chosen}；"
            f"可选：{'、'.join(effective)}"
        )
    spec = TemplateSpec(
        key=head.spec_key,
        name=head.name,
        model=chosen,
        allowed_models=allowed,
        params=dict(row.params or {}),
        prompt=row.prompt,
        random_prompt=row.random_prompt or "",
        slots=tuple(_slot_core(item) for item in slots),
    )
    # 「随机」档：整段替换成作者写的随机档提示词。放在 compile_spec **之前**
    # 改 spec（而不是改 compile），编译核心因此保持纯函数：同一 (spec, fills)
    # 必得同一结果。
    #
    # 没配就**拒绝**，不静默回退主提示词 —— 回退的样子是「随机档和均衡档出来
    # 一模一样」，使用者会以为随机坏了却查不出原因。前端也会把这一档置灰，
    # 这里是最后一道。
    if payload.use_random_prompt:
        if not spec.random_prompt.strip():
            raise BadRequest("该模板未配置随机档提示词，不能用随机档生成")
        spec = replace(spec, prompt=spec.random_prompt)
    fills = await _fills(session, actor, spec, payload.slot_values)
    compiled = compile_spec(spec, fills)
    # 使用者对「运行时参数」的选择（计费模式等）：只在编译结果里生效，
    # **不落进 spec.params** —— 那是作者的部分，模板版本不该被谁用了一次改掉。
    # `CompiledRequest.params` 是 Mapping（不可变），所以整份重建而不是就地改。
    if payload.param_overrides:
        compiled = replace(
            compiled,
            params={
                **compiled.params,
                **_checked_overrides(chosen, payload.param_overrides),
            },
        )
    # 展开后的成品才是真正要发出去的那段：`{{slot:key*}}` 一次展开可能多出几十字。
    if len(compiled.prompt) > EXECUTION_PROMPT_LIMIT:
        raise BadRequest(
            f"编译后的提示词 {len(compiled.prompt)} 字，超过执行层上限 "
            f"{EXECUTION_PROMPT_LIMIT} 字；少填几张参考图或缩短描述"
        )
    return _compile_out(version_no, compiled, spec, fills)


def _checked_overrides(model_id: str, overrides: Mapping[str, Any]) -> dict[str, Any]:
    """校验使用者的运行时参数选择。

    只接受模型 schema 里标了 `userSelectable` 的键：其余键要么是作者写死的
    （模板参数），要么模型根本不认 —— 两种都不该由使用者在这里传。
    值按 schema 收敛（枚举必须落在 options 内），非法值当场 400。
    """
    catalog = {item.id: item for item in get_model_generation().models()}
    model = catalog.get(model_id)
    if model is None:
        raise BadRequest(f"模型不存在或未启用：{model_id}")
    schema = dict(model.param_schema or {})

    unknown = sorted(key for key in overrides if key not in schema)
    if unknown:
        raise BadRequest(
            f"模型 {model_id} 没有这些参数：{'、'.join(unknown)}",
            details={"unknownParams": unknown},
        )
    locked = sorted(
        key
        for key in overrides
        if not (isinstance(schema.get(key), dict) and schema[key].get("userSelectable"))
    )
    if locked:
        raise BadRequest(
            f"这些参数由模板作者设置，使用时不能改：{'、'.join(locked)}",
            details={"lockedParams": locked},
        )

    # 值的收敛交给 `core.param_schema.resolve_params` —— 与生图、任务受理
    # 那两条链**同一份**代码。两份实现一旦漂移，症状是「同一个参数在两条
    # 路径上接受不同的值」。
    #
    # 只把使用者真选了的键喂进去：schema 里那些**没选**的参数如果也传进去，
    # `resolve_params` 会把它们的默认值补齐 —— 那等于使用者替作者做了决定。
    subset = {key: dict(schema[key]) for key in overrides}  # type: ignore[misc]
    return dict(resolve_params(model_id, subset, overrides))
def _slot_core(row: TemplateSpecSlot) -> InputSlot:
    return InputSlot(
        key=row.slot_key,
        type=row.type,  # type: ignore[arg-type]
        binding=row.binding,  # type: ignore[arg-type]
        ref_mode=row.ref_mode,  # type: ignore[arg-type]
        min_count=row.min_count,
        max_count=row.max_count,
        label=row.label,
        asset_id=row.asset_id,
        tag_ids=tuple(_slot_tag_ids(row)),
        filled_text=row.filled_text or "",
        empty_text=row.empty_text or "",
        position=row.position,
    )


async def _fills(
    session: AsyncSession,
    actor: CurrentUser,
    spec: TemplateSpec,
    values: Mapping[str, Sequence[str]],
) -> dict[str, FilledSlot]:
    """把「用户填的 id」翻译成编译需要的 `FilledSlot`。

    素材事实只从端口取：单资产走 `get_many` / `readable_ids`。这一层不查表、
    不猜类型。
    """
    lookup = get_library_lookup()
    fills: dict[str, FilledSlot] = {}
    for slot in spec.slots:
        chosen = [item for item in values.get(slot.key, ()) if item]
        # 固定槽位取 spec 里的 id，用户槽位按挑选顺序。
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

