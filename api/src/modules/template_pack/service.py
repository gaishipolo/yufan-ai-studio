"""封装模板的业务规则。

一条封装模板 = 参考视频 + 固定提示词 + 一组槽位。
槽位二选一：`fixed`（钉死素材）或 `filtered`（声明准入规则）。

## 四条不能改的口径

1. **`templateKey` 是脚本重试边界**：同键同配置返回原行（幂等），
   同键异配置 409。`status` 不在比较范围内 —— 它是独立管理状态。
2. **槽位编号由 `position` 决定，不由提示词决定**。提示词只引用编号，
   编号指向谁看槽位表。两者由 service 对账，对不上就 400。
3. **筛选条件只收窄不放宽**。候选池 = 用户可读 ∩ 满足筛选条件 ∩ `active`。
4. **发布后不能改内容**。已发布的模板可能已被别人引用，原地改会让
   「模板当时长什么样」与「现在长什么样」对不上。要改就发新版本。
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from core.context import CurrentUser
from core.exceptions import BadRequest
from core.ports.library_lookup import (
    LibraryAssetKind,
    LibraryAssetState,
    get_library_lookup,
)
from modules.template_pack.errors import TemplatePackConflict, TemplatePackStateError
from modules.template_pack.models import (
    STATUS_DISABLED,
    STATUS_DRAFT,
    STATUS_PUBLISHED,
    TemplatePackRow,
    TemplatePackSlotRow,
)
from modules.template_pack.repository import TemplatePackRepository
from modules.template_pack.schema import (
    MAX_REFERENCE_IMAGES,
    TemplatePackCandidateItem,
    TemplatePackCandidateListOut,
    TemplatePackDraftIn,
    TemplatePackListOut,
    TemplatePackOut,
    TemplatePackSlotIn,
    TemplatePackSlotOut,
)
from modules.template_pack.slot_rules import (
    FilteredSlot,
    cited_tokens,
    parse_filtered_slot,
    token_position,
)

__all__ = [
    "apply_out",
    "candidates",
    "disable",
    "list_out",
    "publish",
    "save",
    "to_out",
    "update_prompt",
    "verify_picks",
]

#: 参与「同键同配置」比较的列。**不含** `id` / 时间戳 / `status`：
#: 前两个是库生成的，后者是独立管理状态。
_CONFIG_COLUMNS = (
    "name",
    "description",
    "prompt",
    "prompt_sha256",
    "source_video_asset_id",
    "source_duration_sec",
    "aspect_ratio",
    "cover_url",
)


def _slot_out(
    slot: TemplatePackSlotRow, names: dict[str, str], tag_names: dict[str, str]
) -> TemplatePackSlotOut:
    return TemplatePackSlotOut(
        position=slot.position,
        token=slot.token,
        slot_kind=slot.slot_kind,  # type: ignore[arg-type]
        label=slot.label,
        asset_id=slot.asset_id or None,
        media_type=slot.media_type,
        asset_name=names.get(slot.asset_id, "") if slot.asset_id else "",
        filter_kind=slot.filter_kind,
        filter_tag_ids=list(slot.filter_tag_ids or []),
        filter_tag_names=[
            tag_names.get(tag_id, "") for tag_id in (slot.filter_tag_ids or [])
        ],
        filter_tag_match=slot.filter_tag_match,
        required=slot.required,
    )


async def _asset_names(
    session: AsyncSession, slots: list[TemplatePackSlotRow]
) -> dict[str, str]:
    """钉死素材的显示名。

    名字**以库里的为准**：界面上写的是库里那一行，前端手里的可能是旧的。
    """
    refs = [
        slot.asset_id
        for slot in slots
        if slot.slot_kind == "fixed" and slot.asset_id
    ]
    if not refs:
        return {}
    rows = await get_library_lookup().get_many(session, refs)
    return {asset_id: row.name for asset_id, row in rows.items()}


async def to_out(
    session: AsyncSession,
    row: TemplatePackRow,
    slots: list[TemplatePackSlotRow] | None = None,
) -> TemplatePackOut:
    """行 → 出参。JSON 列在这里还原成模型，业务代码只接触出参模型。"""
    repo = TemplatePackRepository(session)
    slot_rows = list(await repo.slots(row.id)) if slots is None else slots
    names = await _asset_names(session, slot_rows)
    return TemplatePackOut(
        id=row.id,
        template_key=row.template_key,
        name=row.name,
        description=row.description,
        prompt=row.prompt,
        prompt_sha256=row.prompt_sha256,
        source_video_asset_id=row.source_video_asset_id,
        source_duration_sec=row.source_duration_sec,
        aspect_ratio=row.aspect_ratio,
        cover_url=row.cover_url,
        status=row.status,  # type: ignore[arg-type]
        owner_id=row.owner_id,
        slots=[_slot_out(slot, names, {}) for slot in slot_rows],
        published_at=row.published_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def apply_out(row: TemplatePackRow, out: TemplatePackOut) -> None:
    """出参 → 行。出参与回写必须一一对应，少一列就是有损往返。"""
    row.name = out.name
    row.description = out.description
    row.prompt = out.prompt
    row.prompt_sha256 = out.prompt_sha256
    row.source_video_asset_id = out.source_video_asset_id
    row.source_duration_sec = out.source_duration_sec
    row.aspect_ratio = out.aspect_ratio
    row.cover_url = out.cover_url


# --------------------------------------------------------------------------- #
# 槽位校验
# --------------------------------------------------------------------------- #
def _validate_slots(
    payload_slots: list[TemplatePackSlotIn], prompt: str
) -> list[TemplatePackSlotIn]:
    """槽位与提示词的对账。

    三条必须成立：

    1. **第 1 个槽位是参考视频**，且是 `fixed`。参考视频是模板的一部分，
       不是用户能换的输入。
    2. **编号连续**且与 `position` 对齐。作者写 `{{Mixed 3}}` 却只声明了
       两个槽位，运行时那一句就没有对应素材 —— 编译期拦住，别等生成失败。
    3. **提示词引用了不存在的编号** → 400。同理。
    """
    if not payload_slots:
        raise BadRequest("至少要有一个槽位")
    ordered = sorted(payload_slots, key=lambda item: token_position(item.token))
    for index, slot in enumerate(ordered, 1):
        number = token_position(slot.token)
        if number != index:
            raise BadRequest(
                f"槽位编号必须从 1 起连续：第 {index} 个槽位写的是 {{{{Mixed {number}}}}}"
            )
    first = ordered[0]
    if first.slot_kind != "fixed":
        raise BadRequest("第一个槽位必须是参考视频（固定素材）")
    if not first.asset_id:
        raise BadRequest("第一个槽位必须绑定参考视频")
    if first.media_type != "video":
        raise BadRequest("第一个槽位的素材类别必须是视频")
    cited = cited_tokens(prompt)
    available = {token_position(item.token) for item in ordered}
    missing = [number for number in cited if number not in available]
    if missing:
        raise BadRequest(
            f"提示词引用了没有对应槽位的编号：{'、'.join(f'{{{{Mixed {n}}}}}' for n in missing)}"
        )
    images = sum(
        1
        for item in ordered[1:]
        if item.media_type == "image" or item.filter_kind == "image"
    )
    if images > MAX_REFERENCE_IMAGES:
        raise BadRequest(f"参考图片最多 {MAX_REFERENCE_IMAGES} 张")
    return ordered


async def _validate_source(
    session: AsyncSession, payload: TemplatePackDraftIn
) -> dict[str, Any]:
    """参考视频必须真实存在、是视频。

    「不存在」与「不可读」合并成同一句：后者不该告诉调用方这条素材确实存在
    （与 `modules/template` 的 `_library_source` 同一口径）。
    """
    asset_id = payload.source_video_asset_id
    if not asset_id:
        raise BadRequest("请选择参考视频")
    rows = await get_library_lookup().get_many(session, [asset_id])
    row = rows.get(asset_id)
    if row is None:
        raise BadRequest("参考视频不存在，请重新选择")
    if row.kind != LibraryAssetKind.VIDEO:
        raise BadRequest("参考视频必须是视频素材")
    fields: dict[str, Any] = {}
    if row.duration_ms:
        fields["source_duration_sec"] = row.duration_ms / 1000
    return fields


def _build_slot_rows(
    template_id: str, ordered: list[TemplatePackSlotIn]
) -> list[TemplatePackSlotRow]:
    """出入参 → 行。筛选条件在这里解析并归一化。"""
    rows: list[TemplatePackSlotRow] = []
    for index, item in enumerate(ordered, 1):
        row = TemplatePackSlotRow(
            template_id=template_id,
            position=index,
            slot_kind=item.slot_kind,
            token=item.token,
            label=item.label,
            required=item.required,
        )
        if item.slot_kind == "fixed":
            if not item.asset_id:
                raise BadRequest(f"{item.token} 是固定素材槽位，必须指定素材")
            row.asset_id = item.asset_id
            row.media_type = item.media_type
        else:
            parsed = parse_filtered_slot(
                filter_kind=item.filter_kind,
                filter_tag_ids=list(item.filter_tag_ids),
                filter_tag_match=item.filter_tag_match,
            )
            row.filter_kind = parsed.filter_kind
            row.filter_tag_ids = list(parsed.filter_tag_ids)
            row.filter_tag_match = parsed.filter_tag_match
            row.media_type = parsed.filter_kind
        rows.append(row)
    return rows


# --------------------------------------------------------------------------- #
# 保存 / 发布 / 停用
# --------------------------------------------------------------------------- #
async def save(
    payload: TemplatePackDraftIn,
    session: AsyncSession,
    *,
    actor: CurrentUser,
) -> TemplatePackOut:
    """保存草稿。幂等：同键同配置返回原行，同键异配置 409。

    ## 幂等口径与 `modules/template` 对齐

    「同键同配置返回原行、同键异配置冲突」是项目里已有的约定
    （`video_template_service._same_or_conflict`）。这里跟着走，而不是
    让同键覆盖 —— 覆盖会让脚本重试悄悄改掉别人已经引用过的模板。

    **槽位参与比较**：筛选条件是模板的业务契约，只比主体字段的话，
    「改了筛选的标签」会被判成同配置而返回原行 —— 筛选条件被静默丢弃，
    而用户以为改成功了。所以槽位一起比。
    """
    ordered = _validate_slots(list(payload.slots), payload.prompt)
    source_fields = await _validate_source(session, payload)
    repo = TemplatePackRepository(session)

    template_key = payload.template_key or uuid.uuid4().hex[:12]
    existing = await repo.get_by_key(template_key)
    if existing is not None:
        if existing.owner_id != actor.id:
            # 别人的模板：连「存在」都不该确认，否则能拿 key 探测别人的命名空间。
            raise TemplatePackConflict("该稳定标识已被占用")
        if existing.status != STATUS_DRAFT:
            # 已发布 / 已停用：内容锁定，重试只能拿到原行，不能改。
            return await _same_or_conflict(
                existing, payload, ordered, source_fields, session
            )
        await _apply_draft(existing, payload, ordered, source_fields)
        await repo.replace_slots(existing.id, _build_slot_rows(existing.id, ordered))
        await session.commit()
        return await to_out(session, existing)

    row = TemplatePackRow(
        id=uuid.uuid4().hex,
        template_key=template_key,
        name=payload.name,
        description=payload.description,
        prompt=payload.prompt,
        prompt_sha256=hashlib.sha256(payload.prompt.encode("utf-8")).hexdigest(),
        source_video_asset_id=payload.source_video_asset_id,
        source_duration_sec=source_fields.get(
            "source_duration_sec", payload.source_duration_sec
        ),
        aspect_ratio=payload.aspect_ratio,
        cover_url=payload.cover_url,
        status=STATUS_DRAFT,
        owner_id=actor.id,
    )
    await repo.add(row)
    await repo.replace_slots(row.id, _build_slot_rows(row.id, ordered))
    await session.commit()
    return await to_out(session, row)


def _config_of(
    payload: TemplatePackDraftIn,
    ordered: list[TemplatePackSlotIn],
    source_fields: dict[str, Any],
) -> dict[str, Any]:
    """一份配置的完整指纹。**主体字段 + 槽位**一起算。

    槽位这一段的取值必须与 `_build_slot_rows` **写库时用的值一模一样**，
    否则同一个配置存进去再读出来比不上，幂等重试会被误判成「异配置」。
    两处必须同步的地方：

    - `filtered` 槽位的 `media_type` 存的是 **`filter_kind`**（不是入参里
      那个默认的 `"image"`）。入参的 `mediaType` 对筛选槽位没有意义，
      拿它去比会把 `filterKind=video` 的槽位判成「变了」。
    - `filter_tag_ids` **去重后**再比（`parse_filtered_slot` 会去重）。
      拿带重复的入参去比已去重的库值，永远比不上。
    """
    return {
        "name": payload.name,
        "description": payload.description,
        "prompt": payload.prompt,
        "prompt_sha256": hashlib.sha256(payload.prompt.encode("utf-8")).hexdigest(),
        "source_video_asset_id": payload.source_video_asset_id,
        "source_duration_sec": source_fields.get(
            "source_duration_sec", payload.source_duration_sec
        ),
        "aspect_ratio": payload.aspect_ratio,
        "cover_url": payload.cover_url,
        "slots": [
            (
                item.token,
                item.slot_kind,
                item.label,
                item.asset_id or "",
                item.media_type if item.slot_kind == "fixed" else (item.filter_kind or ""),
                item.filter_kind or "",
                tuple(dict.fromkeys(tag_id for tag_id in item.filter_tag_ids if tag_id)),
                item.filter_tag_match,
                item.required,
            )
            for item in ordered
        ],
    }


def _row_config_of(
    row: TemplatePackRow, slots: list[TemplatePackSlotRow]
) -> dict[str, Any]:
    """库里那一行的配置指纹。字段与 `_config_of` 一一对应。"""
    return {
        "name": row.name,
        "description": row.description,
        "prompt": row.prompt,
        "prompt_sha256": row.prompt_sha256,
        "source_video_asset_id": row.source_video_asset_id,
        "source_duration_sec": float(row.source_duration_sec or 0),
        "aspect_ratio": row.aspect_ratio,
        "cover_url": row.cover_url,
        "slots": [
            (
                slot.token,
                slot.slot_kind,
                slot.label,
                slot.asset_id or "",
                slot.media_type,
                slot.filter_kind or "",
                tuple(slot.filter_tag_ids or []),
                slot.filter_tag_match,
                bool(slot.required),
            )
            for slot in slots
        ],
    }


async def _same_or_conflict(
    row: TemplatePackRow,
    payload: TemplatePackDraftIn,
    ordered: list[TemplatePackSlotIn],
    source_fields: dict[str, Any],
    session: AsyncSession,
) -> TemplatePackOut:
    """已发布 / 已停用的模板遇到同键重试：同配置返原行，异配置冲突。

    为什么已发布的不返回 409 而是「同配置返回原行」：脚本重试时配置不会
    变，返回原行是幂等的正确表现；而内容被改过（异配置）说明调用方拿
    已发布的 key 干别的事，那是冲突。
    """
    repo = TemplatePackRepository(session)
    slots = list(await repo.slots(row.id))
    if _row_config_of(row, slots) != _config_of(payload, ordered, source_fields):
        raise TemplatePackConflict("该稳定标识已被另一份配置使用，请换一个")
    return await to_out(session, row, slots)


async def _apply_draft(
    row: TemplatePackRow,
    payload: TemplatePackDraftIn,
    ordered: list[TemplatePackSlotIn],
    source_fields: dict[str, Any],
) -> None:
    """把草稿更新到新配置。

    草稿是私有工作区，改配置是允许的（已发布的才锁）。`templateKey` 相同
    但配置变了不算冲突 —— 那是作者自己改的，不是脚本重试。
    """
    apply_out(
        row,
        TemplatePackOut(
            id=row.id,
            template_key=row.template_key,
            name=payload.name,
            description=payload.description,
            prompt=payload.prompt,
            prompt_sha256=hashlib.sha256(payload.prompt.encode("utf-8")).hexdigest(),
            source_video_asset_id=payload.source_video_asset_id,
            source_duration_sec=source_fields.get(
                "source_duration_sec", payload.source_duration_sec
            ),
            aspect_ratio=payload.aspect_ratio,
            cover_url=payload.cover_url,
            status=row.status,  # type: ignore[arg-type]
            owner_id=row.owner_id,
        ),
    )


async def publish(
    pack_id: str, session: AsyncSession, *, actor: CurrentUser
) -> TemplatePackOut:
    """发布：进入模板库，别人可以取用。

    发布前要确认参考视频还在（作者可能在上传后把它删了）。
    """
    repo = TemplatePackRepository(session)
    row = await repo.get_or_404(pack_id)
    if row.owner_id != actor.id:
        raise BadRequest("只能发布自己创建的封装模板")
    if row.status == STATUS_PUBLISHED:
        return await to_out(session, row)
    if row.status == STATUS_DISABLED:
        raise TemplatePackStateError("该封装模板已停用，请新建一个再发布")
    slots = list(await repo.slots(row.id))
    if not slots:
        raise BadRequest("该封装模板没有槽位，不能发布")
    await _validate_source(
        session,
        TemplatePackDraftIn(
            name=row.name,
            prompt=row.prompt,
            sourceVideoAssetId=row.source_video_asset_id,
            slots=[
                TemplatePackSlotIn(
                    token=slot.token,
                    slotKind=slot.slot_kind,  # type: ignore[arg-type]
                    label=slot.label,
                    assetId=slot.asset_id or None,
                    mediaType=slot.media_type,  # type: ignore[arg-type]
                    filterKind=slot.filter_kind or None,  # type: ignore[arg-type]
                    filterTagIds=list(slot.filter_tag_ids or []),
                    filterTagMatch=slot.filter_tag_match,  # type: ignore[arg-type]
                    required=slot.required,
                )
                for slot in slots
            ],
        ),
    )
    row.status = STATUS_PUBLISHED
    row.published_at = datetime.now(tz=UTC)
    await repo.save()
    await session.commit()
    return await to_out(session, row)


async def disable(
    pack_id: str, session: AsyncSession, *, actor: CurrentUser
) -> TemplatePackOut:
    """停用：保留历史，但不再对外。"""
    repo = TemplatePackRepository(session)
    row = await repo.get_or_404(pack_id)
    if row.owner_id != actor.id:
        raise BadRequest("只能停用自己创建的封装模板")
    if row.status == STATUS_DISABLED:
        return await to_out(session, row)
    row.status = STATUS_DISABLED
    await repo.save()
    await session.commit()
    return await to_out(session, row)


async def list_out(
    session: AsyncSession,
    *,
    actor: CurrentUser,
    owner_id: str = "",
    status: str = "",
) -> TemplatePackListOut:
    """列表。作者看自己的全部；别人只看已发布的。

    **`owner_id` 只认「本人」**：`owner_id` 非空且是别人时一律当没传，
    走「只看已发布」。

    **`status` 不能单独放开草稿**：客户端传 `status=draft` 且不传 `ownerId`，
    会列出**所有人**的草稿 —— 草稿是私有工作区，里面有提示词原文与筛选条件。
    所以「非本人的草稿」这条路径根本不存在：看草稿必须同时是本人。
    """
    repo = TemplatePackRepository(session)
    effective_owner = owner_id if owner_id == actor.id else ""
    if effective_owner:
        rows = list(await repo.list_all(owner_id=effective_owner, status=status))
    elif status and status != STATUS_PUBLISHED:
        # 没有 owner 上下文时，只有「已发布」是公共的。其余状态一律按公共集处理。
        rows = list(await repo.list_all(status=STATUS_PUBLISHED))
    else:
        rows = list(await repo.list_all(status=STATUS_PUBLISHED))
    slots_map = await repo.slots_of([row.id for row in rows])
    items = [await to_out(session, row, slots_map.get(row.id, [])) for row in rows]
    return TemplatePackListOut(items=items, total=len(items))


async def update_prompt(
    pack_id: str,
    prompt: str,
    session: AsyncSession,
    *,
    actor: CurrentUser,
) -> TemplatePackOut:
    """改固定提示词原文。

    **只能改未发布的**。已发布的模板可能已被引用，原地改会让引用它的人
    看到的提示词与作者写的不一样。
    """
    if not prompt.strip():
        raise BadRequest("提示词不能为空")
    repo = TemplatePackRepository(session)
    row = await repo.get_or_404(pack_id)
    if row.owner_id != actor.id:
        raise BadRequest("只能修改自己创建的封装模板")
    if row.status != STATUS_DRAFT:
        raise TemplatePackStateError("该封装模板已发布，不能再修改内容")
    slots = list(await repo.slots(row.id))
    cited = cited_tokens(prompt)
    available = {token_position(slot.token) for slot in slots}
    missing = [number for number in cited if number not in available]
    if missing:
        raise BadRequest(
            f"提示词引用了没有对应槽位的编号：{'、'.join(f'{{{{Mixed {n}}}}}' for n in missing)}"
        )
    if not cited:
        raise BadRequest("提示词至少要引用一个槽位")
    row.prompt = prompt
    row.prompt_sha256 = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    await repo.save()
    await session.commit()
    return await to_out(session, row, slots)


# --------------------------------------------------------------------------- #
# 候选池与运行时复核
# --------------------------------------------------------------------------- #
async def candidates(
    pack_id: str,
    token: str,
    session: AsyncSession,
    *,
    actor: CurrentUser,
    limit: int = 100,
) -> TemplatePackCandidateListOut:
    """某个筛选槽位的合规素材池。

    **筛选只收窄不放宽**：候选 = actor 可读 ∩ 满足筛选条件 ∩ `active`。
    这三条判据都在 `LibraryLookup.find_ids` 里（`state=active` 硬过滤、
    标签 OR 展开到二级、`readable_rows` 可见性），本模块不重写一遍。
    """
    repo = TemplatePackRepository(session)
    row = await repo.get_or_404(pack_id)
    if row.status != STATUS_PUBLISHED and row.owner_id != actor.id:
        raise BadRequest("该封装模板未发布")
    slots = list(await repo.slots(row.id))
    target = next((item for item in slots if item.token == token), None)
    if target is None:
        raise BadRequest(f"槽位 {token} 不存在")
    if target.slot_kind != "filtered":
        raise BadRequest("该槽位是固定素材，没有可选范围")
    parsed = parse_filtered_slot(
        filter_kind=target.filter_kind,
        filter_tag_ids=list(target.filter_tag_ids or []),
        filter_tag_match=target.filter_tag_match,
    )
    ids = await get_library_lookup().find_ids(
        session,
        actor,
        kind=LibraryAssetKind(parsed.filter_kind),
        tag_ids=parsed.filter_tag_ids,
        limit=limit,
    )
    rows = await get_library_lookup().get_many(session, ids)
    items = [
        TemplatePackCandidateItem(
            asset_id=asset_id,
            name=row.name,
            kind=row.kind.value,
            cover_url=row.cover_key,
            size_bytes=row.size_bytes,
            width=row.width,
            height=row.height,
        )
        for asset_id, row in rows.items()
        if asset_id in ids
    ]
    return TemplatePackCandidateListOut(
        token=target.token,
        label=target.label,
        filter_kind=parsed.filter_kind,
        filter_tag_ids=list(parsed.filter_tag_ids),
        items=items,
        total=len(items),
    )


async def verify_picks(
    pack_id: str,
    picks: dict[str, str],
    session: AsyncSession,
    *,
    actor: CurrentUser,
) -> dict[str, str]:
    """运行时复核：用户选的素材是否仍满足该槽位的筛选条件。

    **前端过滤只是 UI 便利，接口可以被直接调用。** 筛选条件是模板的业务
    契约，越权提交等于用错素材生成 —— 所以这里必须服务端再判一次。

    返回 `{token: asset_id}`，只含通过复核的槽位。

    ## 为什么逐个判，而不是「把整个池子捞出来再查表里有没有」

    `LibraryLookup.find_ids` 内部有 `_MAX_CANDIDATES = 500` 的翻页上限
    （它要判可见性，可读性压不进一条 SQL）。拿它做复核的话：合规素材
    超过 500 条时，排在窗口外的合法选择会被**误判为不合规** —— 用户明明
    选的是合规素材，却被告知「不符合要求」。这是把「池子太大」当成
    「素材不对」。

    所以这里反过来做：**先看这条素材本身**（它是什么类别、是否在库、是否可读），
    再看**它挂的标签**是否满足筛选条件。判的是「这一条」，与池子大小无关。
    """
    repo = TemplatePackRepository(session)
    row = await repo.get_or_404(pack_id)
    slots = list(await repo.slots(row.id))
    slot_by_token = {item.token: item for item in slots}
    filtered = {item.token: item for item in slots if item.slot_kind == "filtered"}
    if not filtered:
        return {}
    unknown = [token for token in picks if token not in slot_by_token]
    if unknown:
        raise BadRequest(f"模板里没有这些槽位：{'、'.join(unknown)}")
    checked: dict[str, str] = {}
    for token, asset_id in picks.items():
        slot = filtered.get(token)
        if slot is None:
            continue
        if not asset_id:
            if slot.required:
                raise BadRequest(f"槽位 {token}「{slot.label or token}」必须选择素材")
            continue
        parsed = parse_filtered_slot(
            filter_kind=slot.filter_kind,
            filter_tag_ids=list(slot.filter_tag_ids or []),
            filter_tag_match=slot.filter_tag_match,
        )
        if not await _asset_matches_filter(session, actor, asset_id, parsed):
            raise BadRequest(
                f"所选素材不符合槽位「{slot.label or token}」的要求，请重新选择"
            )
        checked[token] = asset_id
    return checked


async def _asset_matches_filter(
    session: AsyncSession,
    actor: CurrentUser,
    asset_id: str,
    parsed: FilteredSlot,
) -> bool:
    """这一条素材满不满足这条筛选条件。

    四条判据，逐条都要成立：

    1. **存在且在库**（`state=active`）。草稿与回收站里的不是可用素材。
    2. **actor 现在可读**。可见性判据只有资产库那一份（`readable_rows`），
       这里不重写。
    3. **类别对得上**。筛选说「图片」就不能选视频。
    4. **标签对得上**：挂了任一指定标签（标签已按「一级展开成自身+直属二级」
       的口径算过）。

    第 4 条的判定方式：用 `find_ids` 按**单个标签**各查一次。选中的可能是
    一级标签（要连它的二级一起算），所以一次查一个标签、任一命中即通过 ——
    这正是 OR 语义。

    注意这里不用 `find_ids` 一次捞整个池子再查表：池子有 `_MAX_CANDIDATES`
    上限，捞不全时合法选择会被误拒（见 `verify_picks` 的说明）。
    """
    ref = (await get_library_lookup().get_many(session, [asset_id])).get(asset_id)
    if ref is None:
        return False
    if ref.state != LibraryAssetState.ACTIVE:
        return False
    if ref.kind.value != parsed.filter_kind:
        return False
    readable = await get_library_lookup().readable_ids(session, actor, [asset_id])
    if asset_id not in readable:
        return False
    if not parsed.filter_tag_ids:
        # 没指定标签 = 只按类别筛。
        return True
    for tag_id in parsed.filter_tag_ids:
        hit = await get_library_lookup().find_ids(
            session,
            actor,
            kind=LibraryAssetKind(parsed.filter_kind),
            tag_ids=[tag_id],
            limit=1,
        )
        if asset_id in hit:
            return True
    return False
