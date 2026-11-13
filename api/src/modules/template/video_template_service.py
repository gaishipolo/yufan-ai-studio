"""视频模板的业务规则：封装、改提示词、抢蓝本、按原片反查。

原来这一整套在 `video_template_store.py` 里，和「读写 `catalog.json`」搅在一起，
于是每条业务规则都被迫写成「同步临界区 + 进线程池」。落表之后事务由数据库提供，
这里只剩下业务判断；表访问在 `video_template_repository.py`，媒体清单的纯计算在
`video_template_media.py`。

## 三条不能改的口径

1. **`templateKey` 是脚本重试边界**：同键同配置返回原行（幂等），同键异配置 409。
   `pinned` 不在比较范围内——它是独立管理状态，别人抢了蓝本不该让重试报冲突。
2. **改提示词只改这一条，不新建**：`find_for_run` 在同原片同类型下取最早创建那条，
   新建一条「改正版」永远排在后面等于没改，还会让一个原片挂着两份同类型模板。
3. **蓝本的唯一性由数据库保证**，不靠「先清后设」的应用层互斥：多 worker 时后者
   会被 `uq_video_templates_pinned` 拒绝，而不是静默双蓝本。
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from core.exceptions import BadRequest, Conflict, NotFound
from core.ports.media import get_asset_lookup
from modules.template.asset_source import resolve_names
from modules.template.models import TemplatePack, VideoTemplate
from modules.template.pack_roles import PACK_ROLES
from modules.template.repository import PackRepository
from modules.template.video_template_bindings import (
    allows_partial_slots,
    ensure_distinct_roles,
    order_reference_ids,
    place_source,
    reference_roles,
    source_position,
)
from modules.template.video_template_media import (
    apply_pack_roles,
    cited_slots,
    prompt_tokens,
    validate_media,
)
from modules.template.video_template_options import is_scene_text, validate_options
from modules.template.video_template_repository import VideoTemplateRepository
from modules.template.video_template_schema import (
    MAX_REFERENCE_IMAGES,
    VideoTemplateDraftIn,
    VideoTemplateListOut,
    VideoTemplateMedia,
    VideoTemplateOut,
)

__all__ = [
    "apply_out",
    "attach_source",
    "create",
    "find_for_run",
    "get_out",
    "list_out",
    "pin",
    "prefetch_names",
    "template_name",
    "update_prompt",
]

logger = logging.getLogger(__name__)

#: 判定「同键同配置」时参与比较的列。**不含** `id` / 时间戳 / `pinned`：
#: 前两个是库生成的，后者是独立管理状态。
_CONFIG_COLUMNS = (
    "name",
    "description",
    "prompt",
    "prompt_sha256",
    "source_video_asset_id",
    "source_video_path",
    "source_video_name",
    "source_duration_sec",
    "aspect_ratio",
    "pack_id",
    "pack_name",
    "pack_version",
    "default_kind",
    "media_order",
    "generation",
    "rules",
    "cover_url",
)


def _media_json(media: list[VideoTemplateMedia]) -> list[dict[str, Any]]:
    return [item.model_dump(mode="json", by_alias=True, exclude_none=True) for item in media]


def to_out(row: VideoTemplate) -> VideoTemplateOut:
    """行 → 出参。JSON 列在这里还原成模型，业务代码只接触出参模型。"""
    return VideoTemplateOut(
        id=row.id,
        template_key=row.template_key,
        name=row.name,
        description=row.description,
        prompt=row.prompt,
        prompt_sha256=row.prompt_sha256,
        source_video_name=row.source_video_name,
        source_video_asset_id=row.source_video_asset_id,
        source_video_path=row.source_video_path,
        source_duration_sec=row.source_duration_sec,
        aspect_ratio=row.aspect_ratio,
        pack_id=row.pack_id,
        pack_name=row.pack_name,
        pack_version=row.pack_version,
        default_kind=row.default_kind,  # type: ignore[arg-type]
        media_order=[VideoTemplateMedia.model_validate(item) for item in row.media_order],
        generation=dict(row.generation or {}),
        rules=dict(row.rules or {}),
        cover_url=row.cover_url,
        pinned=row.pinned,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def apply_out(row: VideoTemplate, out: VideoTemplateOut) -> None:
    """出参 → 行。改提示词那条路径复用同一份出参校验，避免两套口径。"""
    row.name = out.name
    row.description = out.description
    row.prompt = out.prompt
    row.prompt_sha256 = out.prompt_sha256
    row.source_video_name = out.source_video_name
    row.source_video_asset_id = out.source_video_asset_id
    row.source_video_path = out.source_video_path
    row.source_duration_sec = out.source_duration_sec
    row.aspect_ratio = out.aspect_ratio
    row.pack_id = out.pack_id
    row.pack_name = out.pack_name
    row.pack_version = out.pack_version
    row.default_kind = out.default_kind
    row.media_order = _media_json(out.media_order)
    row.generation = dict(out.generation)
    row.rules = dict(out.rules)
    row.cover_url = out.cover_url
    row.pinned = out.pinned


async def list_out(session: AsyncSession) -> VideoTemplateListOut:
    rows = await VideoTemplateRepository(session).list_all()
    items = [to_out(row) for row in rows]
    pinned_id = next((item.id for item in items if item.pinned), None)
    return VideoTemplateListOut(items=items, total=len(items), pinned_id=pinned_id)


async def get_out(session: AsyncSession, template_id: str) -> VideoTemplateOut:
    return to_out(await VideoTemplateRepository(session).get_or_404(template_id))


# --------------------------------------------------------------------------- #
# 媒体清单
# --------------------------------------------------------------------------- #
async def _resolve_media(
    payload: VideoTemplateDraftIn, session: AsyncSession, tokens: set[str]
) -> tuple[list[VideoTemplateMedia], str, int]:
    """媒体顺序优先用封装者显式给出的 `mediaOrder`，缺省才回落到素材包原序。

    显式顺序是「模板自己的顺序」：调用时 mixedList 按它拼，`{{Mixed 2}}` 才能
    稳稳指向三视图而不是主图。但**哪几张图**仍然只能来自素材包——数量或图片
    对不上就拒绝封装，避免提示词指向一张根本没插进接口的图。
    """
    pack_name = ""
    pack_version = 0
    if is_scene_text(payload.rules):
        reference_roles(payload.rules, payload.prompt, payload.default_kind)
        if payload.pack_id or payload.media_order:
            raise BadRequest("文字换场景不选择素材包，由服务端生成唯一原片输入")
        return [
            VideoTemplateMedia(
                token="{{Mixed 1}}",
                role="source_video",
                type="video",
                asset_id=payload.source_video_asset_id,
                name=payload.source_video_name,
                in_prompt="{{Mixed 1}}" in tokens,
            )
        ], "", 0

    required = reference_roles(payload.rules, payload.prompt, payload.default_kind)
    if required is not None:
        if payload.media_order or not payload.pack_id:
            raise BadRequest("用途绑定模式需提供已发布 packId，并由服务端生成完整媒体清单")
        pack = await session.get(TemplatePack, payload.pack_id)
        if pack is None or pack.deleted_at is not None or pack.status != "published":
            raise BadRequest("请选择已发布的替换素材包")
        refs = list(await PackRepository(session).references(pack.id))
        ids = [ref.asset_id for ref in refs]
        roles = {ref.asset_id: ref.role for ref in refs}
        ordered: list[str | None]
        if allows_partial_slots(required, payload.default_kind):
            ensure_distinct_roles(roles, required, payload.default_kind)
            # 一个包代表一个人物；第二张 front 也是该人物的辅助图，不能占下一人槽位。
            first = next(asset_id for asset_id in ids if roles[asset_id] == "front")
            ordered = [
                first,
                *([None] * (len(required) - 1)),
                *(asset_id for asset_id in ids if asset_id != first),
            ]
        else:
            ordered = list(order_reference_ids(ids, roles, required, payload.default_kind))
        names = await resolve_names(
            session, [(ref.asset_id, ref.asset_id_space) for ref in refs]
        )
        source_media = VideoTemplateMedia(
            token="",
            role="source_video",
            type="video",
            asset_id=payload.source_video_asset_id,
            name=payload.source_video_name,
            in_prompt=True,
        )
        image_media = []
        labels = PACK_ROLES.get(payload.default_kind, {})
        # 前 len(required) 项是模板声明的槽位；其余的包内图片是辅助参考，
        # 用途照包里的来（不参与槽位映射，但仍然上传）。
        for index, asset_id in enumerate(ordered):
            slot = required[index] if index < len(required) else ""
            role = roles[asset_id] if asset_id else slot
            image_media.append(
                VideoTemplateMedia(
                    token="",
                    role=role,
                    type="image",
                    asset_id=asset_id,
                    name=(
                        names.get(asset_id) or asset_id
                        if asset_id
                        else f"运行时选{labels.get(role, role)}"
                    ),
                )
            )
        media = place_source(source_media, image_media, payload.rules)
        for index, item in enumerate(media, 1):
            item.token = f"{{{{Mixed {index}}}}}"
            item.in_prompt = item.token in tokens
        return media, pack.name, pack.version

    if payload.media_order:
        media = [item.model_copy() for item in payload.media_order]
        if payload.pack_id:
            pack = await session.get(TemplatePack, payload.pack_id)
            if pack is not None:
                pack_name, pack_version = pack.name, pack.version
                refs = list(await PackRepository(session).references(pack.id))
                declared = sorted(
                    item.asset_id for item in media if item.type == "image" and item.asset_id
                )
                if declared != sorted(ref.asset_id for ref in refs):
                    raise BadRequest("媒体清单里的产品图必须与素材包完全一致：数量、图片都要对得上")
                apply_pack_roles(media, refs)
        return media, pack_name, pack_version

    if not payload.pack_id:
        raise BadRequest("请提供 packId 或显式给出 mediaOrder")
    pack = await session.get(TemplatePack, payload.pack_id)
    if pack is None or pack.deleted_at is not None or pack.status != "published":
        raise BadRequest("请选择已发布的替换素材包")
    refs = list(await PackRepository(session).references(pack.id))
    if not refs:
        raise BadRequest("该素材包没有参考图，无法封装视频模板")
    names = await resolve_names(
        session, [(ref.asset_id, ref.asset_id_space) for ref in refs]
    )

    media = [
        VideoTemplateMedia(
            token="{{Mixed 1}}",
            role="source_video",
            type="video",
            name=payload.source_video_name,
            file=payload.source_video_path,
            in_prompt=True,
        )
    ]
    for index, ref in enumerate(refs, 2):
        token = f"{{{{Mixed {index}}}}}"
        media.append(
            VideoTemplateMedia(
                token=token,
                role=ref.role,
                type="image",
                asset_id=ref.asset_id,
                name=names.get(ref.asset_id) or ref.asset_id,
                in_prompt=token in tokens,
            )
        )
    return media, pack.name, pack.version


# --------------------------------------------------------------------------- #
# 封装
# --------------------------------------------------------------------------- #
async def create(payload: VideoTemplateDraftIn, session: AsyncSession) -> VideoTemplateOut:
    if payload.rules.get("fixedReferences"):
        raise BadRequest("跨封装引用请通过模板提示词编辑接口绑定")
    validate_options(payload.prompt, payload.rules)
    if payload.pack_id:
        pack = await session.get(TemplatePack, payload.pack_id)
        if pack is None or pack.deleted_at is not None or pack.status != "published":
            raise BadRequest("请选择已发布的替换素材包")
        if pack.kind != payload.default_kind:
            raise BadRequest("封装处理类型必须与所选素材包类型一致")
    scene_text = is_scene_text(payload.rules)
    tokens = prompt_tokens(payload.prompt, scene_text)
    media, pack_name, pack_version = await _resolve_media(payload, session, tokens)
    images = validate_media(media, tokens, source_position(payload.rules), scene_text)

    duration = round(payload.source_duration_sec) if payload.source_duration_sec > 0 else 5
    # 与生成侧同一口径（`replacement_service` 把 duration 收敛到 2..15 秒）。
    # 封装时允许 30 秒、生成时静默跑 15 秒，等于模板上写的档位是假的。
    duration = max(2, min(15, duration))
    generation: dict[str, Any] = {
        "model": "wanx3.0",
        "modeType": "mixed2video",
        "count": 1,
        "ratio": payload.aspect_ratio,
        "resolution": "720P",
        "duration": duration,
        "enableSound": "on",
        "extendPrompt": 0,
        "infiniteSwitch": 0,
    }
    generation.update(payload.generation)
    configured = generation.get("duration")
    if isinstance(configured, (int, float)) and not isinstance(configured, bool):
        generation["duration"] = max(2, min(15, int(configured)))
    rules: dict[str, Any] = {
        "promptMode": "fixed_no_compilation",
        # 选中这张原片卡片时，替换配置默认切到该素材包对应的替换类型。
        # 前端会优先用 pack.kind 判定，这里作为包未加载时的兜底。
        "defaultKind": payload.default_kind,
        "preservePackReferenceOrder": True,
        "insertAllProductImages": True,
        "productImageCount": len(images),
        "promptTokenAllowlist": sorted(tokens),
        "mixedListOrderIsTokenOrder": True,
    }
    rules.update(payload.rules)

    candidate = VideoTemplate(
        id=uuid.uuid4().hex,
        template_key=payload.template_key or uuid.uuid4().hex[:12],
        name=payload.name,
        description=payload.description,
        prompt=payload.prompt,
        prompt_sha256=hashlib.sha256(payload.prompt.encode("utf-8")).hexdigest(),
        source_video_name=payload.source_video_name,
        source_video_asset_id=payload.source_video_asset_id,
        source_video_path=payload.source_video_path,
        source_duration_sec=payload.source_duration_sec,
        aspect_ratio=payload.aspect_ratio,
        pack_id=payload.pack_id,
        pack_name=pack_name,
        pack_version=pack_version,
        default_kind=payload.default_kind,
        media_order=_media_json(media),
        generation=generation,
        rules=rules,
        cover_url=payload.cover_url,
        pinned=payload.pinned,
    )

    repo = VideoTemplateRepository(session)
    # 幂等：同键同配置返回原行。检查与插入之间由唯一索引兜底——两个并发重试
    # 会有一个撞 `uq_video_templates_template_key`，此时重读一次即是原行。
    existing = await repo.get_by_key(candidate.template_key)
    if existing is not None:
        return _same_or_conflict(existing, candidate)
    # 先清别人的蓝本再插入：部分唯一索引是非延迟的，顺序反了会当场撞约束。
    if candidate.pinned:
        await repo.unpin_others(keep_id=None)
    try:
        await repo.add(candidate)
        await session.commit()
    except Exception:  # noqa: BLE001 - 只认「同键并发」，其余照抛
        await session.rollback()
        retried = await repo.get_by_key(candidate.template_key)
        if retried is not None:
            return _same_or_conflict(retried, candidate)
        raise
    return to_out(candidate)


def _same_or_conflict(existing: VideoTemplate, candidate: VideoTemplate) -> VideoTemplateOut:
    previous = {name: getattr(existing, name) for name in _CONFIG_COLUMNS}
    wanted = {name: getattr(candidate, name) for name in _CONFIG_COLUMNS}
    if previous != wanted:
        raise Conflict("模板稳定标识已被不同配置使用，请使用新的稳定标识")
    return to_out(existing)


# --------------------------------------------------------------------------- #
# 改提示词
# --------------------------------------------------------------------------- #
async def update_prompt(
    template_id: str,
    prompt: str,
    fixed_references: list[dict[str, Any]] | None = None,
    *,
    session: AsyncSession,
) -> VideoTemplateOut:
    """换掉固定提示词原文，槽位引用按新原文重算。

    **为什么只能原地改**：`find_for_run` 在同原片同类型下按创建时间取最早那条，
    新建一条「改正版」永远排在后面，等于没改；一条原片还会因此挂着两份同类型
    模板，排查时说不清跑的是哪份。所以改提示词只改这一条。

    保留 `bindings` 里没被新原文引用的槽位（不缩短）：那些图仍会完整插入接口，
    只是 `inPrompt=false`；只有新原文点名了更靠后的槽位时才向后扩展。
    """
    if not prompt.strip():
        raise BadRequest("提示词不能为空")
    repo = VideoTemplateRepository(session)
    row = await repo.get_or_404(template_id)
    template = to_out(row)
    rules = dict(template.rules)
    media = [item.model_copy() for item in template.media_order]
    base_count = int(rules.get("fixedBaseMediaCount", len(media)))
    fixed = fixed_references if fixed_references is not None else rules.get("fixedReferences", [])
    fixed_tokens = {ref["token"] for ref in fixed}
    expected = [f"{{{{Mixed {base_count + n + 1}}}}}" for n in range(len(fixed))]
    # base_count 里有 1 项是原片，真正参与上限的图片数是 base_count - 1 + 固定引用。
    # 与 prepare 端的 MAX_REFERENCE_IMAGES 用同一个常量，避免两处口径漂移。
    if (
        [ref["token"] for ref in fixed] != expected
        or base_count - 1 + len(fixed) > MAX_REFERENCE_IMAGES
    ):
        raise BadRequest(
            f"固定引用必须紧接模板媒体连续编号，最多{MAX_REFERENCE_IMAGES}张参考图片"
        )
    media = media[:base_count] + [
        VideoTemplateMedia(
            token=ref["token"],
            role="fixed",
            type="image",
            asset_id=ref["assetId"],
            name=ref["name"],
            in_prompt=ref["token"] in prompt,
        )
        for ref in fixed
    ]
    if fixed:
        rules.update(fixedReferences=fixed, fixedBaseMediaCount=base_count)
    else:
        rules.pop("fixedReferences", None)
        rules.pop("fixedBaseMediaCount", None)
    scene_text = is_scene_text(template.rules)
    if scene_text:
        validate_options(prompt, rules)
    numbers, scope = cited_slots(prompt, scene_text=scene_text)
    if not numbers and not scene_text:
        raise BadRequest("提示词至少要引用原片槽位")
    if not media:
        raise BadRequest("模板没有媒体清单，无法改写提示词")
    for number in numbers:
        if number > len(media):
            raise BadRequest(f"{{{{Mixed {number}}}}} 超出模板媒体数量 {len(media)}")

    bindings = rules.get("referenceBindings")
    if isinstance(bindings, dict) and bindings:
        declared = {str(token): str(role) for token, role in bindings.items()}
        video_slots = [
            number
            for number in range(1, len(declared) + 1)
            if declared.get(f"{{{{Mixed {number}}}}}") == "source_video"
        ]
    else:
        declared = {}
        video_slots = [n for n, item in enumerate(media, 1) if item.type == "video"]
    if not video_slots:
        raise BadRequest("模板没有原片槽位，无法声明视频引用")
    if not scene_text and not any(number in video_slots for number in numbers):
        raise BadRequest("提示词必须引用原片槽位")
    if not scene_text and not any(number not in video_slots for number in numbers):
        raise BadRequest("提示词必须至少点名一张参考图")

    if declared and not scene_text:
        dynamic_numbers = [
            number for number in numbers if f"{{{{Mixed {number}}}}}" not in fixed_tokens
        ]
        upper = max(max(dynamic_numbers, default=1), len(declared))
        for number in range(1, upper + 1):
            token = f"{{{{Mixed {number}}}}}"
            if token in declared:
                continue
            item = media[number - 1]
            role = "source_video" if item.type == "video" else item.role
            if not role:
                raise BadRequest(f"{token} 在媒体清单里没有用途，无法声明为图片槽位")
            declared[token] = role
        rules["referenceBindings"] = {
            f"{{{{Mixed {number}}}}}": declared[f"{{{{Mixed {number}}}}}"]
            for number in range(1, upper + 1)
        }
        rules["mixedListOrderIsTokenOrder"] = True

    scopes = [str(option) for option in (rules.get("scopeOptions") or [])]
    if scope and not scopes:
        raise BadRequest("提示词引用了 {{ReplacementScope}}，但模板没有声明可选范围")
    if scope:
        rules["scopeOptions"] = scopes
    else:
        # 新原文不再带范围参数，留着只会让配置面板出现一个点了没用的选择器。
        rules.pop("scopeOptions", None)
    rules["promptTokenAllowlist"] = [f"{{{{Mixed {number}}}}}" for number in sorted(numbers)]

    for number, item in enumerate(media, 1):
        item.in_prompt = number in numbers

    # 走模型校验再落库；prompt 使用 VerbatimText，逐字保留画布测试的原文，
    # 摘要也按同一份原文计算，不附加图片用途或描述。
    candidate = template.model_copy(
        update={"prompt": prompt, "media_order": media, "rules": rules}
    )
    updated = VideoTemplateOut.model_validate(candidate.model_dump())
    updated = updated.model_copy(
        update={"prompt_sha256": hashlib.sha256(updated.prompt.encode("utf-8")).hexdigest()}
    )
    if declared:
        # 用创建时同一套校验兜底：越界引用、多人换脸槽位规则都不许在这里绕过。
        reference_roles(rules, updated.prompt, updated.default_kind)
    apply_out(row, updated)
    await repo.save()
    await session.commit()
    return to_out(row)


# --------------------------------------------------------------------------- #
# 蓝本 / 反查 / 绑定原片
# --------------------------------------------------------------------------- #
async def pin(template_id: str, session: AsyncSession) -> VideoTemplateOut:
    """设为固定蓝本：全库只保留一个，后续封装以它为复制样板。"""
    repo = VideoTemplateRepository(session)
    row = await repo.get_or_404(template_id)
    await repo.unpin_others(keep_id=row.id)
    row.pinned = True
    await repo.save()
    await session.commit()
    return to_out(row)


async def find_for_run(
    source_id: str,
    pack_id: str | None,
    kind: str | None = None,
    template_id: str | None = None,
    *,
    session: AsyncSession,
) -> VideoTemplateOut | None:
    """按原片 asset id 找已封装的视频模板。

    模板按**原片**绑定，`packId` 只是「选中卡片时默认带出的那个包」：
    换包照样命中同一条模板，`replacement_service` 再按模板声明的用途
    （front / multiview / detail）从当前包里取图，于是提示词不用改，
    `{{Mixed 2}}` 永远是所选包的正面主图。同一原片若封了多条模板，
    优先取与本次包 id 一致的那条，其次固定蓝本。
    普通原片（0507 高端姐）没有绑定，行为完全不变。
    """
    if not source_id or (not pack_id and not template_id):
        return None
    repo = VideoTemplateRepository(session)
    if template_id:
        selected = await repo.get(template_id)
        if (
            selected is None
            or selected.source_video_asset_id != source_id
            or (kind is not None and selected.default_kind != kind)
        ):
            raise BadRequest("所选封装功能不存在或与原片、处理类型不匹配，请重新选择")
        return to_out(selected)
    bound = list(await repo.bound_for_source(source_id, kind))
    if not bound:
        return None
    exact = [row for row in bound if row.pack_id == pack_id]
    matches = exact or bound
    matches.sort(key=lambda row: (not row.pinned, row.created_at))
    return to_out(matches[0])


async def attach_source(
    template_id: str, asset_id: str, session: AsyncSession
) -> VideoTemplateOut:
    """把样片上传后的 source 资产绑定到模板，让它出现在「全部模板」卡片流里。

    换的是**这条模板的样片**本身，所以文件名与时长跟着资产走：只写 `assetId`
    会让封装管理里继续显示上一支片的名字和秒数，生成档位也还按旧时长取整
    （6 秒的片按 5 秒出，白丢一秒）。档位只在它确实等于旧时长取整时才跟着改
    ——手调过的值不覆盖。
    """
    lookup = await get_asset_lookup().get_many(session, [asset_id])
    asset = lookup.get(asset_id)
    if asset is None or asset.deleted_at:
        raise NotFound("样片原片不存在或已删除")
    if asset.category != "source" or asset.type != "video":
        raise BadRequest("请选择已归档的 source 视频原片")
    repo = VideoTemplateRepository(session)
    row = await repo.get_or_404(template_id)
    previous = float(row.source_duration_sec or 0)
    row.source_video_asset_id = asset_id
    if asset.name:
        row.source_video_name = asset.name
    duration = float(asset.duration or 0)
    if duration > 0:
        row.source_duration_sec = duration
        generation = dict(row.generation or {})
        current = generation.get("duration")
        derived = max(2, min(15, round(previous))) if previous > 0 else None
        if isinstance(current, int) and derived is not None and current == derived:
            generation["duration"] = max(2, min(15, round(duration)))
            row.generation = generation
    await repo.save()
    await session.commit()
    return to_out(row)


# --------------------------------------------------------------------------- #
# 老档案补模板名
# --------------------------------------------------------------------------- #
#: `{template_id: name}`。只在 `prefetch_names()` 里写，`template_name()` 只读。
#: 名字没有改名接口（`update_prompt` 只动提示词），所以缓存不需要失效逻辑；
#: 真加了改名接口，这里必须一起改，否则档案列表会一直显示旧名字。
_NAME_CACHE: dict[str, str] = {}


async def prefetch_names(session: AsyncSession, template_ids: list[str]) -> None:
    """批量预热模板名缓存，给一次列表渲染里的 N 条老档案共用。

    **为什么要预热而不是逐个查**：`_run_out` 是同步的出参投影，一个列表页里
    每条老档案都要反查一次名字。让投影变 async 会把整条列表链路改成 async 传染；
    在列表入口预热一次、投影只读缓存，两边都不用将就。
    """
    wanted = [item for item in dict.fromkeys(template_ids) if item and item not in _NAME_CACHE]
    if not wanted:
        return
    _NAME_CACHE.update(await VideoTemplateRepository(session).names(wanted))


def template_name(template_id: str) -> str | None:
    """缓存里没有就返回 `None`——展示层自己降级，别让一条老档案打崩整页。"""
    return _NAME_CACHE.get(template_id)


def reset_name_cache() -> None:
    """仅测试用。"""
    _NAME_CACHE.clear()
