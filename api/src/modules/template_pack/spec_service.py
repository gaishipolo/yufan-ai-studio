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

from collections.abc import Mapping, Sequence
from dataclasses import replace
from typing import Any, cast

from sqlalchemy import ColumnElement, and_, false, func, select, true, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from core.context import CurrentUser
from core.exceptions import BadRequest, Conflict, NotFound, StateChanged
from core.param_schema import resolve_params
from core.ports.library_lookup import get_library_lookup
from core.ports.model_generation import get_model_generation
from core.ports.template_usage import template_usage_counts
from core.template_spec import (
    CompiledRequest,
    FilledAsset,
    FilledSlot,
    InputSlot,
    TemplateSpec,
    always_filled_slot_keys,
    compile_spec,
    normalize_refs,
    normalize_text_refs,
    prompt_limit_for_capability,
    slot_ref_modes,
    slot_refs,
    strip_valid_refs,
)
from db.base import utcnow
from modules.template_pack.models import (
    TemplateSpec as SpecRow,
)
from modules.template_pack.models import (
    TemplateSpecSlot,
    TemplateSpecVersion,
)
from modules.template_pack.spec_catalog import (
    capability_label,
    effective_model_ids,
    model_capability,
    model_catalog,
)
from modules.template_pack.spec_gate import GateReport, review_draft, review_publish
from modules.template_pack.spec_schema import (
    CompiledSlotOut,
    SpecCompileIn,
    SpecCompileOut,
    SpecGroupIn,
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
        group=row.group_key or "",
        min_count=row.min_count,
        max_count=row.max_count,
        label=row.label,
        asset_id=row.asset_id,
        tag_ids=_slot_tag_ids(row),
        filled_text=row.filled_text or "",
        empty_text=row.empty_text or "",
        empty_text_from_anchor=row.empty_text_from_anchor,
        position=row.position,
    )


def _group_outs(slots: Sequence[TemplateSpecSlot]) -> list[SpecGroupIn]:
    """从发布快照的槽位行里提炼出「有哪些容器、各叫什么」。

    **顺序 = 首次出现的顺序**（`slots` 已按 `position` 排好），这样模板库
    画出来的容器顺序与槽位顺序一致。名字存组内每一行（冗余），取第一个非空的
    那个：正常写入永远是同值，取首个只是让「被手改过一行」的坏数据有确定行为，
    而不是随机挑一行。

    老版本没有 `group_label`（列是后加的，不回填）→ 这里给出空 label，
    前端按段序回退成「封装槽位 N」。
    """
    labels: dict[str, str] = {}
    for row in slots:
        key = row.group_key or ""
        if not key or key in labels:
            continue
        labels[key] = row.group_label or ""
    return [SpecGroupIn(key=key, label=label) for key, label in labels.items()]


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
        tag_ids=list(head.tag_ids or ()),
        usage_count=0,
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
                groups=_group_outs(slots),
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
        select(SpecRow).where(SpecRow.spec_key == key, SpecRow.deleted_at.is_(None))
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


async def _spec_outs_of(
    session: AsyncSession, heads: Sequence[SpecRow]
) -> list[TemplateSpecOut]:
    """列表用：按页内 id 批量取当前版本与槽位，不再逐行 2N 查询。

    只取「每个 head 的 current_version」对应的版本行；版本表可能有很多历史，
    宁可多取几行再在 Python 里按 (spec_id, current_version) 过滤，也不做
    `(spec_id, version) IN (...)` 这种方言敏感的行值比较。
    """
    wanted = [head for head in heads if head.current_version > 0]
    if not wanted:
        return [_spec_out(head, None, []) for head in heads]

    latest_by_spec: dict[str, TemplateSpecVersion] = {}
    version_rows = await session.scalars(
        select(TemplateSpecVersion).where(
            TemplateSpecVersion.spec_id.in_([head.id for head in wanted])
        )
    )
    current = {head.id: head.current_version for head in wanted}
    for version in version_rows:
        if version.version == current.get(version.spec_id):
            latest_by_spec[version.spec_id] = version

    slots_by_version: dict[str, list[TemplateSpecSlot]] = {}
    version_ids = [row.id for row in latest_by_spec.values()]
    if version_ids:
        slot_rows = await session.scalars(
            select(TemplateSpecSlot)
            .where(TemplateSpecSlot.version_id.in_(version_ids))
            .order_by(
                TemplateSpecSlot.version_id,
                TemplateSpecSlot.position,
                TemplateSpecSlot.slot_key,
            )
        )
        for slot in slot_rows:
            slots_by_version.setdefault(slot.version_id, []).append(slot)

    out: list[TemplateSpecOut] = []
    for head in heads:
        latest = latest_by_spec.get(head.id)
        slots = slots_by_version.get(latest.id, []) if latest is not None else []
        out.append(_spec_out(head, latest, slots))
    return out


# --------------------------------------------------------------------------- #
# 预检：结构（core） + 现实（模型目录 / 资产库）
# --------------------------------------------------------------------------- #
def _raise_first(report: GateReport) -> None:
    if report.blockers:
        blocker = report.blockers[0]
        raise BadRequest(blocker.message, details=blocker.details)


async def validate(
    session: AsyncSession, actor: CurrentUser, payload: TemplateSpecIn
) -> SpecValidateOut:
    """按发布级检查预演；不落库，只回 valid / fingerprint。"""
    report = await review_publish(session, actor, payload)
    _raise_first(report)
    return SpecValidateOut(valid=True, fingerprint=report.fingerprint)


async def save(
    session: AsyncSession, actor: CurrentUser, payload: TemplateSpecIn
) -> TemplateSpecOut:
    """保存草稿。同 key 同内容幂等；`revision` 不匹配则 409。"""
    report = await review_draft(session, actor, payload)
    _raise_first(report)
    fingerprint = report.fingerprint
    draft = payload.model_dump(mode="json", by_alias=True, exclude={"revision"})
    head = await _find(session, payload.key)

    if head is None:
        head = SpecRow(
            spec_key=payload.key,
            name=payload.name,
            description=payload.description,
            owner_id=actor.id,
            status="draft",
            draft=draft,
            revision=1,
            fingerprint=fingerprint,
            current_version=0,
        )
        session.add(head)
        await session.flush()
    else:
        if head.owner_id != actor.id:
            raise Conflict(f"配置 {payload.key} 属于其他用户")
        expected = payload.revision
        if expected is not None and expected != head.revision:
            raise StateChanged(
                f"配置已被更新（当前第 {head.revision} 次修订），请刷新后重试",
                details={"expectedRevision": expected, "currentRevision": head.revision},
            )
        if head.fingerprint == fingerprint:
            # 幂等：内容没变就不加修订号，也不把已发布状态打回草稿。
            out = await _spec_out_of(session, head)
            await session.commit()
            return out
        # 修订号校验必须和写入是同一条 SQL：读到的 revision 与随后的 UPDATE
        # 之间隔着 await，两个请求带同一个 revision 都能通过旧校验，后写者
        # 覆盖先写者且版本号只 +1（前端无法区分谁覆盖了谁）。
        conditions = [SpecRow.id == head.id]
        if expected is not None:
            conditions.append(SpecRow.revision == expected)
        result = await session.execute(
            update(SpecRow)
            .where(*conditions)
            .values(
                name=payload.name,
                description=payload.description,
                draft=draft,
                fingerprint=fingerprint,
                revision=SpecRow.revision + 1,
            )
        )
        if (cast(CursorResult[Any], result).rowcount or 0) == 0:
            current = await session.scalar(
                select(SpecRow.revision).where(SpecRow.id == head.id)
            )
            raise StateChanged(
                "配置已被更新，请刷新后重试",
                details={
                    "expectedRevision": expected,
                    "currentRevision": current,
                },
            )
        await session.refresh(head)

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
    report = await review_publish(session, actor, payload)
    _raise_first(report)
    snapshot = report.snapshot
    assert snapshot is not None

    latest = await _latest(session, head)
    fingerprint = report.fingerprint
    # 只比指纹不够：作者没改草稿、但文本资产改了正文，内联后的 prompt 也会变，
    # 那时必须发新版本，否则使用者拿到的还是旧文案。
    if (
        latest is not None
        and latest.fingerprint == fingerprint
        and latest.prompt == snapshot.prompt
        and (latest.random_prompt or "") == snapshot.random_prompt
    ):
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

    # 版本号抢占也必须是条件 UPDATE：两个并发发布会算出同一个
    # `current_version + 1`，后插入者撞唯一约束 → 500。先原子把 head 推进到
    # 新版本，抢不到的那一方重查：同一份草稿就按幂等返回它，否则明确 409。
    version = head.current_version + 1
    claimed = await session.execute(
        update(SpecRow)
        .where(
            SpecRow.id == head.id,
            SpecRow.current_version == head.current_version,
        )
        .values(current_version=version)
    )
    if (cast(CursorResult[Any], claimed).rowcount or 0) == 0:
        await session.refresh(head)
        latest_now = await _latest(session, head)
        if (
            latest_now is not None
            and latest_now.fingerprint == fingerprint
            and latest_now.prompt == snapshot.prompt
            and (latest_now.random_prompt or "") == snapshot.random_prompt
        ):
            out = await _spec_out_of(session, head)
            await session.commit()
            return SpecPublishOut(version=latest_now.version, spec=out)
        raise Conflict("模板正在被其他请求发布，请刷新后重试")

    row = TemplateSpecVersion(
        spec_id=head.id,
        version=version,
        model=snapshot.model,
        allowed_models=list(snapshot.allowed_models),
        params=dict(snapshot.params),
        prompt=snapshot.prompt,
        random_prompt=snapshot.random_prompt,
        fingerprint=fingerprint,
        published_by=actor.id,
    )
    session.add(row)
    await session.flush()  # 主键在 flush 后才有，槽位要用它
    # 容器名存组内每一行（冗余）：发布快照只有槽位行，没有「容器表」。
    # 只写组内第一行会依赖 position 顺序 —— 拖一下顺序就取不到名字了。
    group_labels = {group.key: group.label for group in snapshot.groups}
    for slot in snapshot.slots:
        session.add(
            TemplateSpecSlot(
                version_id=row.id,
                slot_key=slot.key,
                type=slot.type,
                binding=slot.binding,
                ref_mode=slot.ref_mode,
                group_key=slot.group,
                group_label=group_labels.get(slot.group, ""),
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
                empty_text_from_anchor=slot.empty_text_from_anchor,
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


async def set_tags(
    session: AsyncSession, actor: CurrentUser, key: str, tag_ids: Sequence[str]
) -> TemplateSpecOut:
    """给模板打标签（完整替换）。只改 head 的 tag_ids，不动 draft、不发版。

    只接受**叶子**标签：有下级的一级维度是分组，不能打在模板上（与素材库同
    口径）。孤立一级（没有下级的维度）仍可打 —— 删维度后提升上来的取值也
    是这个形状，拦掉会让正在用的词一夜之间选不了。
    """
    from modules.template_pack.tags import all_tags, children_of

    head = await _find(session, key)
    if head is None:
        raise NotFound("模板不存在")
    wanted = list(dict.fromkeys(tag_ids))
    rows = await all_tags(session)
    by_id = {row.id: row for row in rows}
    missing = [item for item in wanted if item not in by_id]
    if missing:
        raise BadRequest(f"标签不存在：{'、'.join(missing[:5])}")
    grouped = set(children_of(rows))
    invalid = [item for item in wanted if item in grouped]
    if invalid:
        names = "、".join(by_id[item].name for item in invalid[:5])
        raise BadRequest(f"这些是分组维度，不能打在模板上：{names}")
    head.tag_ids = wanted
    await session.commit()
    return await _spec_out_of(session, head)


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
async def get_out(session: AsyncSession, actor: CurrentUser, key: str) -> TemplateSpecOut:
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
    tag_ids: tuple[str, ...] = (),
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
            key
            for item in get_model_generation().models()
            if item.replacement
            for key in (item.id, *item.aliases)
        ]
        condition: ColumnElement[bool]
        if target == "replacement":
            condition = TemplateSpecVersion.model.in_(replaceable) if replaceable else false()
        else:
            condition = TemplateSpecVersion.model.notin_(replaceable) if replaceable else true()
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
        stmt = stmt.where(SpecRow.name.contains(needle) | SpecRow.spec_key.contains(needle))
        count_stmt = count_stmt.where(
            SpecRow.name.contains(needle) | SpecRow.spec_key.contains(needle)
        )
    order = (
        (SpecRow.deleted_at.desc(), SpecRow.id)
        if trashed
        else (SpecRow.updated_at.desc(), SpecRow.id)
    )
    if tag_ids:
        # 标签筛选在 Python 里做：tag_ids 是 JSON 列，跨维度 AND 用 SQL 表达
        # 要写方言相关的 JSON 查询（sqlite/pg 行为不一致），而模板数量本来就少。
        # 关键是 total 与 items 走**同一份**过滤后的列表，否则分页会错位。
        from modules.template_pack.tags import all_tags, group_by_dimension, spec_matches_groups

        rows_all = list((await session.scalars(stmt.order_by(*order))).all())
        groups = group_by_dimension(await all_tags(session), list(tag_ids))
        matching = [row for row in rows_all if spec_matches_groups(row, groups)] if groups else []
        total = len(matching)
        rows = matching[(page - 1) * page_size : page * page_size]
    else:
        total = int(await session.scalar(count_stmt) or 0)
        rows = list(
            (
                await session.scalars(
                    stmt.order_by(*order).limit(page_size).offset((page - 1) * page_size)
                )
            ).all()
        )
    items = await _spec_outs_of(session, rows)
    counts = await template_usage_counts([item.key for item in items])
    for item in items:
        item.usage_count = counts.get(item.key, 0)
    return TemplateSpecListOut(items=items, total=total)


# --------------------------------------------------------------------------- #
# 编译
# --------------------------------------------------------------------------- #
#: 使用者改写「未选时文案」的长度上限。与作者那一栏同口径
#: （`SpecSlotIn.empty_text` 的 `max_length`）：同一个槽位、同一段话，
#: 只是写的人不同，不该有两套限制。
MAX_SLOT_TEXT_OVERRIDE = 2000


def _apply_slot_text_overrides(
    spec: TemplateSpec,
    overrides: Mapping[str, str],
    slot_values: Mapping[str, Sequence[str]],
) -> TemplateSpec:
    """把使用者改写的「未选时文案」换进 spec。

    放在 `compile_spec` **之前**改 spec（与随机档换 `prompt` 同一手法、同一位置
    之后）：编译核心因此保持纯函数，同一 `(spec, fills)` 必得同一结果。
    传进来的 `spec.prompt` 已是**这次真正要编的那一段**（随机档已换过），
    所以「有没有插入点」查的就是它，不会出现「插入点在另一档里、改了也不生效」。

    五道 400 都在这里，都点明是哪一格、为什么不生效。静默收下的后果是
    「用户改的话没进提示词，页面看起来一切正常」—— 那比报错难查得多：

    1. 槽位不存在（拼错 key / 页面停在旧版本）
    2. 必填或固定槽位：没有「没选」这个状态，兜底文案本来就用不上
    3. 同一格既挑了素材又改了文案：两者都在回答「这一格是什么」，
       取谁都是替使用者做决定
    4. 提示词里没有插入点：只有单张引用 `{{slot:x}}` 会用兜底文案；
       整组 / 第 N 张已由结构校验拒，不是另一种插入点
    5. 文案为空或含 `{{`：空的到编译期必报错（且报的是「作者没写文案」，
       与眼前的情况对不上）；占位符放进去会在编译期炸成一句看不懂的话
    """
    if not overrides:
        return spec
    by_key = {slot.key: slot for slot in spec.slots}
    # 与发布闸同一个判据（`slot_ref_modes`）：只认**单张**引用。
    modes = slot_ref_modes(spec.prompt)
    replaced: dict[str, InputSlot] = {}
    for key, raw in overrides.items():
        text = (raw or "").strip()
        slot = by_key.get(key)
        name = (slot.label.strip() or key) if slot else key
        if slot is None:
            raise BadRequest(f"改文案的槽位不存在：{key}")
        if slot.binding == "fixed" or slot.min_count >= 1:
            raise BadRequest(
                f"「{name}」是必填槽位，没有「未选」这个状态，"
                "不能改未选时文案；要改请把它改成可选槽位"
            )
        if any(item for item in slot_values.get(key, ())):
            raise BadRequest(f"「{name}」既挑了素材又改了文案：一格只有一种说法，请二选一")
        if "" not in modes.get(key, set()):
            raise BadRequest(
                f"「{name}」在这份模板的提示词里没有插入点"
                "（要写成 {{slot:" + key + "}}），改的文案不会进提示词"
            )
        if not text:
            raise BadRequest(f"「{name}」改写的提示词不能为空")
        if len(text) > MAX_SLOT_TEXT_OVERRIDE:
            raise BadRequest(
                f"「{name}」改写的提示词 {len(text)} 字，超过上限 {MAX_SLOT_TEXT_OVERRIDE} 字"
            )
        # 改写里可以引用**必定有素材**的槽位（必填 / 已固定）：
        # 那段话提到的别格素材一定在，编号一定成立。规则与作者侧同一套
        # （core/template_spec/rules.py::_validate_slot_texts）。
        #
        # 先把作者写法（{{Mixed N}}）归一成内部写法再判 —— 改写框里
        # 预填的就是作者写法，不归一就认不出它引用了谁。
        if "{{" in text:
            normalized = normalize_text_refs(text, spec, where=f"「{name}」改写的提示词")
            refs = set(slot_refs(normalized))
            # 改写的是「未选时文案」—— 用的正是这一格没素材的时候，
            # 所以它不能引用自己（那一格此时没有编号可指）。规范 §12 不变量 25
            # 要求三处同口径：作者侧 `_validate_slot_texts` 已拦自引用，
            # 这里也要就地拦，不能靠更深的 validate_spec 兜底（那样错误
            # 文案说的是「未选时文案」，定位不到是这次改写造成的）。
            if key in refs:
                raise BadRequest(
                    f"「{name}」改写的提示词里引用了它自己："
                    "这段话用的正是这一格没挑素材的时候，那时没有编号可指"
                )
            forbidden = sorted(refs - always_filled_slot_keys(spec))
            if forbidden:
                raise BadRequest(
                    f"「{name}」改写的提示词里引用了「{'、'.join(forbidden)}」："
                    "只能引用必定有素材的槽位（必填 / 已固定）—— "
                    "可选槽位可能没人填，那时没有编号可指"
                )
            leftover = strip_valid_refs(normalized)
            if "{{" in leftover or "}}" in leftover:
                raise BadRequest(
                    f"「{name}」改写的提示词里有认不出来的占位符："
                    "这一段是纯文字，编号由提示词骨架决定"
                )
        replaced[key] = replace(slot, empty_text=text)
    if not replaced:
        return spec
    return replace(
        spec,
        slots=tuple(replaced.get(slot.key, slot) for slot in spec.slots),
    )


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
        raise BadRequest(f"模型不在该模板允许的清单里：{chosen}；可选：{'、'.join(effective)}")
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
    # 作者写的是 `{{Mixed N}}`，而下面两处判断（随机档换段之后仍然成立、
    # 改文案要查插入点）**都只认 `{{slot:key}}`**。换算做在入口一次
    # （`rules.normalize_refs`，幂等），`compile_spec` 里还会再调一次。
    #
    # 不换的话，「这一格在提示词里没有插入点」会把**所有**真实模板误判掉 ——
    # 现存模板清一色写 `{{Mixed 1}}`，它们其实都有插入点。
    spec = normalize_refs(spec)
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
    # 使用者改写的「未选时文案」：同样在编译前换进 spec。顺序放在随机档之后，
    # 这样查「有没有插入点」查的就是这次真正要编的那一段。
    spec = _apply_slot_text_overrides(spec, payload.slot_text_overrides, payload.slot_values)
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
    # 展开后的成品才是真正要发出去的那段，按**使用者选中的模型**所属能力判：
    # 同一模板的允许清单里可能有不同能力，选图就按图片上限。
    capability = model_capability(chosen)
    limit = prompt_limit_for_capability(capability)
    if len(compiled.prompt) > limit:
        raise BadRequest(
            f"编译后的提示词 {len(compiled.prompt)} 字，超过执行层上限 "
            f"{limit} 字（{capability_label(capability)}模型）；"
            "少填几张参考图或缩短描述"
        )
    return _compile_out(version_no, compiled, spec, fills)


def _checked_overrides(model_id: str, overrides: Mapping[str, Any]) -> dict[str, Any]:
    """校验使用者的运行时参数选择。

    只接受模型 schema 里标了 `userSelectable` 的键：其余键要么是作者写死的
    （模板参数），要么模型根本不认 —— 两种都不该由使用者在这里传。
    值按 schema 收敛（枚举必须落在 options 内），非法值当场 400。
    """
    catalog = model_catalog()
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
        # 漏了它读出来恒为 False（2026-11-27 补）。今天编译链不消费这个字段，
        # 但「读出来恒为 false」是埋雷：将来谁在编译路径上判它就会踩坑。
        empty_text_from_anchor=row.empty_text_from_anchor,
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
            if slot.binding == "fixed" and slot.asset_id in denied:
                # fixed 槽位在编译期必须等于 slot.asset_id（core/template_spec/compile.py
                # 的 _assets_of），复用者**无法**自己换一张 —— 所以这条不是
                # 「你没权限」那种自己能解决的事，得说清该找谁、怎么改。
                raise BadRequest(
                    f"槽位「{slot.label or slot.key}」是模板作者固定好的素材，"
                    f"但它对其他人不可见：{'、'.join(denied)}。"
                    "请让作者把这条素材移进案例池或改成团队可见后再用。"
                )
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
