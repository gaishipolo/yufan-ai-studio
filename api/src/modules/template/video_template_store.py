"""视频模板目录：本地 JSON 持久化 + 封装校验。

**为什么是文件而不是新表**：本机 `api/var` 已是运行时状态目录；当前部署的
PostgreSQL `alembic_version` 指向本地迁移链里不存在的 revision
（`b4e2d8f1a630`），跑 `alembic upgrade head` 会失败。为了让模板库可以先
用起来，这轮只新增一个目录，不改数据库 schema；后续迁移链对齐后把
`_read/_write` 换成仓储即可，对外契约不变。

写入用「同目录临时文件 + os.replace」：Windows/SMB 上直接覆盖会在覆盖瞬间
出现路径短暂不存在，读者会拿到半个文件或 FileNotFoundError。

**异步纪律**（`CODE_STYLE.md` §2.3）：HTTP 入口是 `async def`，而读改写盘是同步的。
所以异步入口只做「算好要写什么」，真正的 `_read/_write` 一律
`await asyncio.to_thread(...)` 交给线程池 —— 目录虽然在几百 KB 内，
但 SMB/机械盘上一次 `os.replace` 就足以卡住整个事件循环。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import tempfile
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from core.exceptions import BadRequest, Conflict, NotFound
from core.ports.media import get_asset_lookup
from modules.template.models import TemplatePack
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
from modules.template.video_template_options import is_scene_text, validate_options
from modules.template.video_template_schema import (
    VideoTemplateDraftIn,
    VideoTemplateListOut,
    VideoTemplateMedia,
    VideoTemplateOut,
)

# .../api/src/modules/template/ -> parents[3] = .../api
_ROOT = Path(__file__).resolve().parents[3]
CATALOG_PATH = _ROOT / "var" / "video_templates" / "catalog.json"
_CATALOG_VERSION = 1

# 临界区统一在这一把锁里：**读改写整段**不能在锁外拆开，否则两个封装并发时
# 后写的会把前一条整个覆盖（目录是「读全量 → 追加 → 写全量」）。
# 它是进程内锁，只在单实例部署下成立；多 worker / 多机时目录会丢更新，
# 迁移链对齐后应换成仓储表 + 行锁（见模块 docstring）。
_LOCK = threading.Lock()
_TOKEN_RE = re.compile(r"\{\{([^{}]+)\}\}")
_SLOT_RE = re.compile(r"^Mixed (\d+)$")


def _now() -> datetime:
    return datetime.now(tz=UTC)


def catalog_stamp() -> float:
    """目录文件的代次（mtime），给进程内缓存做失效判据。

    读不到文件就返回 0：缓存条目仍然可用（调用方会自己降级），
    不要因为目录被删就让一次列表请求 500。
    """
    try:
        return CATALOG_PATH.stat().st_mtime
    except OSError:
        return 0.0


def _read() -> dict[str, Any]:
    if not CATALOG_PATH.is_file():
        return {"version": _CATALOG_VERSION, "items": []}
    with CATALOG_PATH.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict) or not isinstance(data.get("items"), list):
        raise BadRequest("视频模板目录文件损坏，请检查 var/video_templates/catalog.json")
    return data


def _write(data: dict[str, Any]) -> None:
    CATALOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(
        prefix="catalog-", suffix=".json", dir=CATALOG_PATH.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(temp_name, CATALOG_PATH)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def _as_out(record: dict[str, Any]) -> VideoTemplateOut:
    """把目录里的 camelCase JSON 还原成出参模型（时间字段转 datetime）。"""
    data = dict(record)
    for field in ("createdAt", "updatedAt"):
        value = data.get(field)
        if isinstance(value, str):
            data[field] = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return VideoTemplateOut.model_validate(data)


def _serialize(template: VideoTemplateOut) -> dict[str, Any]:
    return template.model_dump(mode="json", by_alias=True, exclude_none=True)


def list_out() -> VideoTemplateListOut:
    """全量返回，不分页。

    模板是「封装配置」而不是业务流水：一个原片通常只有几条，全量一次返回
    换来的好处是前端选中卡片时不必再翻页补数据。**这个前提有量级**——
    如果哪天真到几百条，先换仓储表再谈分页，而不是在这个 JSON 目录上
    加 limit/offset（每次请求仍要读全文件，分页只省了序列化）。
    """
    with _LOCK:
        records = list(_read()["items"])
    items = [_as_out(record) for record in records]
    # 固定蓝本永远排在最前，其余按创建时间倒序
    items.sort(key=lambda item: (not item.pinned, -item.created_at.timestamp()))
    pinned_id = next((item.id for item in items if item.pinned), None)
    return VideoTemplateListOut(items=items, total=len(items), pinned_id=pinned_id)


def get_out(template_id: str) -> VideoTemplateOut:
    with _LOCK:
        records = list(_read()["items"])
    for record in records:
        if record.get("id") == template_id:
            return _as_out(record)
    raise NotFound("视频模板不存在")


def _prompt_tokens(prompt: str, scene_text: bool = False) -> set[str]:
    found = _TOKEN_RE.findall(prompt)
    if not found and not scene_text:
        raise BadRequest("提示词缺少 {{Mixed N}} 占位符")
    tokens: set[str] = set()
    for raw in found:
        if raw == "ReplacementScope" or (scene_text and raw == "ScenePrompt"):
            continue
        match = _SLOT_RE.match(raw)
        if not match:
            raise BadRequest(f"非法占位符 {{{{{raw}}}}}：只允许 {{{{Mixed N}}}}")
        number = int(match.group(1))
        if number < 1:
            raise BadRequest("占位符编号从 1 开始")
        tokens.add(f"{{{{Mixed {number}}}}}")
    return tokens


def _apply_pack_roles(media: list[VideoTemplateMedia], refs: list[Any]) -> None:
    """用途以素材包为准：显式清单只决定顺序，不决定「这张是主图还是三视图」。

    封装界面不传 role（传了也未必与包一致）。若照抄空 role，运行时按用途取图
    会全部落空，「换一个产品包、提示词不用改」就退化成按 asset_id 死配。
    """
    by_asset = {ref.asset_id: ref.role for ref in refs}
    for item in media:
        if item.type == "image" and item.asset_id in by_asset:
            item.role = by_asset[item.asset_id]


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
        return [VideoTemplateMedia(token="{{Mixed 1}}", role="source_video", type="video",
                   asset_id=payload.source_video_asset_id, name=payload.source_video_name,
                   in_prompt="{{Mixed 1}}" in tokens)], "", 0
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
        lookup = await get_asset_lookup().get_many(session, ids)
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
                        getattr(lookup.get(asset_id), "name", "") or asset_id
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
                _apply_pack_roles(media, refs)
        return media, pack_name, pack_version

    if not payload.pack_id:
        raise BadRequest("请提供 packId 或显式给出 mediaOrder")
    pack = await session.get(TemplatePack, payload.pack_id)
    if pack is None or pack.deleted_at is not None or pack.status != "published":
        raise BadRequest("请选择已发布的替换素材包")
    refs = list(await PackRepository(session).references(pack.id))
    if not refs:
        raise BadRequest("该素材包没有参考图，无法封装视频模板")
    lookup = await get_asset_lookup().get_many(session, [ref.asset_id for ref in refs])

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
        row = lookup.get(ref.asset_id)
        media.append(
            VideoTemplateMedia(
                token=token,
                role=ref.role,
                type="image",
                asset_id=ref.asset_id,
                name=getattr(row, "name", "") or ref.asset_id,
                in_prompt=token in tokens,
            )
        )
    return media, pack.name, pack.version


def _validate_media(
    media: list[VideoTemplateMedia], tokens: set[str], source_index: int = 0,
    video_only: bool = False,
) -> list[VideoTemplateMedia]:
    if not media:
        raise BadRequest("媒体清单不能为空")
    source_token = f"{{{{Mixed {source_index + 1}}}}}"
    if (
        source_index >= len(media)
        or media[source_index].type != "video"
        or sum(item.type == "video" for item in media) != 1
    ):
        raise BadRequest(f"{source_token} 必须是唯一的完整原片")
    for index, item in enumerate(media, 1):
        expected = f"{{{{Mixed {index}}}}}"
        if item.token != expected:
            raise BadRequest(f"第 {index} 项的 token 应为 {expected}，实际 {item.token}")
    for token in tokens:
        number = int(_SLOT_RE.match(token[2:-2]).group(1))  # type: ignore[union-attr]
        if number > len(media):
            raise BadRequest(f"{token} 超出 mixedList 长度 {len(media)}")
    if source_token not in tokens and not video_only:
        raise BadRequest(f"提示词必须引用 {source_token} 原片")
    if not video_only and not any(item.type == "image" and item.token in tokens for item in media):
        raise BadRequest("提示词必须至少点名一张参考图")
    marked = {item.token for item in media if item.in_prompt}
    if marked != tokens:
        raise BadRequest(f"inPrompt 标记 {sorted(marked)} 与提示词引用 {sorted(tokens)} 不一致")
    images = [item for item in media if item.type == "image"]
    if not images and not video_only:
        raise BadRequest("至少要有 1 张参考图")
    # 空槽位（`assetId` 为空）是「运行时由第 N 张人物卡填」的声明，不是真图，
    # 不能参与去重：否则两个空槽会被判成「重复图片」。
    asset_ids = [item.asset_id for item in images if item.asset_id]
    if len(set(asset_ids)) != len(asset_ids):
        raise BadRequest("参考图不能重复")
    return images


async def create(payload: VideoTemplateDraftIn, session: AsyncSession) -> VideoTemplateOut:
    validate_options(payload.prompt, payload.rules)
    if payload.pack_id:
        pack = await session.get(TemplatePack, payload.pack_id)
        if pack is None or pack.deleted_at is not None or pack.status != "published":
            raise BadRequest("请选择已发布的替换素材包")
        if pack.kind != payload.default_kind:
            raise BadRequest("封装处理类型必须与所选素材包类型一致")
    scene_text = is_scene_text(payload.rules)
    tokens = _prompt_tokens(payload.prompt, scene_text)
    media, pack_name, pack_version = await _resolve_media(payload, session, tokens)
    images = _validate_media(media, tokens, source_position(payload.rules), scene_text)

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

    now = _now()
    template = VideoTemplateOut(
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
        media_order=media,
        generation=generation,
        rules=rules,
        cover_url=payload.cover_url,
        pinned=payload.pinned,
        created_at=now,
        updated_at=now,
    )
    record = _serialize(template)
    # 写盘进线程池：临界区整段（读 → 改 → 写）必须在一起，所以整段一起交出去。
    persisted = await asyncio.to_thread(_append_record, record, payload.pinned)
    return _as_out(persisted)


def _append_record(record: dict[str, Any], pinned: bool) -> dict[str, Any]:
    """同步临界区：追加一条并（可选）抢占固定蓝本。只从线程池调用。"""
    with _LOCK:
        data = _read()
        # 稳定键是脚本重试边界：检查与追加必须在同一临界区内。
        # pinned 是独立管理状态：其他模板抢占蓝本后，重试创建不能报冲突或抢回。
        ignored = {"id", "createdAt", "updatedAt", "pinned"}
        configuration = {key: value for key, value in record.items() if key not in ignored}
        for existing in data["items"]:
            if existing.get("templateKey") == record["templateKey"]:
                previous = {key: value for key, value in existing.items() if key not in ignored}
                if previous != configuration:
                    raise Conflict("模板稳定标识已被不同配置使用，请使用新的稳定标识")
                return dict(existing)
        if pinned:
            for existing in data["items"]:
                existing["pinned"] = False
        data["items"].append(record)
        _write(data)
        return dict(record)


_SCOPE_TOKEN = "ReplacementScope"


def _cited_slots(prompt: str, *, scene_text: bool = False) -> tuple[list[int], bool]:
    """提示词点名了哪些槽位（`Mixed N` 的 N），以及是否引用可选范围参数。

    与 `_prompt_tokens` 的差别只有一处：改提示词允许 `{{ReplacementScope}}`，
    它在运行文案里替换成用户选的范围，不算媒体槽位。
    """
    numbers: list[int] = []
    scope = False
    for raw in _TOKEN_RE.findall(prompt):
        if scene_text and raw == "ScenePrompt":
            continue
        if raw == _SCOPE_TOKEN:
            scope = True
            continue
        match = _SLOT_RE.match(raw)
        if not match:
            raise BadRequest(
                f"非法占位符 {{{{{raw}}}}}：只允许 {{{{Mixed N}}}} 或 {{{{ReplacementScope}}}}"
            )
        number = int(match.group(1))
        if number < 1:
            raise BadRequest("占位符编号从 1 开始")
        if number not in numbers:
            numbers.append(number)
    return numbers, scope


def update_prompt(template_id: str, prompt: str) -> VideoTemplateOut:
    """换掉固定提示词原文，槽位引用按新原文重算。

    **为什么只能原地改**：`find_for_run` 在同原片同类型下按创建时间取最早那条，
    新建一条「改正版」永远排在后面，等于没改；一条原片还会因此挂着两份同类型
    模板，排查时说不清跑的是哪份。所以改提示词只改这一条。

    保留 `bindings` 里没被新原文引用的槽位（不缩短）：那些图仍会完整插入接口，
    只是 `inPrompt=false`；只有新原文点名了更靠后的槽位时才向后扩展。
    """
    if not prompt.strip():
        raise BadRequest("提示词不能为空")
    with _LOCK:
        data = _read()
        index = next(
            (i for i, item in enumerate(data["items"]) if item.get("id") == template_id),
            -1,
        )
        if index < 0:
            raise NotFound("视频模板不存在")
        record = data["items"][index]
        template = _as_out(record)
        scene_text = is_scene_text(template.rules)
        if scene_text:
            validate_options(prompt, template.rules)
        numbers, scope = _cited_slots(prompt, scene_text=scene_text)
        if not numbers and not scene_text:
            raise BadRequest("提示词至少要引用原片槽位")
        media = [item.model_copy() for item in template.media_order]
        if not media:
            raise BadRequest("模板没有媒体清单，无法改写提示词")
        for number in numbers:
            if number > len(media):
                raise BadRequest(f"{{{{Mixed {number}}}}} 超出模板媒体数量 {len(media)}")

        rules = dict(template.rules)
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
            upper = max(max(numbers), len(declared))
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
        rules["promptTokenAllowlist"] = [
            f"{{{{Mixed {number}}}}}" for number in sorted(numbers)
        ]

        for number, item in enumerate(media, 1):
            item.in_prompt = number in numbers

        # 走一遍模型校验再落盘：`CamelModel` 会 strip 首尾空白，`model_copy` 不会。
        # 少了这步，目录里的 prompt 与读出来看到的差一个换行，sha256 也就跟着对不上。
        candidate = template.model_copy(
            update={
                "prompt": prompt,
                "media_order": media,
                "rules": rules,
                "updated_at": _now(),
            }
        )
        updated = VideoTemplateOut.model_validate(candidate.model_dump())
        updated = updated.model_copy(
            update={
                "prompt_sha256": hashlib.sha256(
                    updated.prompt.encode("utf-8")
                ).hexdigest()
            }
        )
        if declared:
            # 用创建时同一套校验兜底：越界引用、多人换脸槽位规则都不许在这里绕过。
            reference_roles(rules, updated.prompt, updated.default_kind)
        data["items"][index] = _serialize(updated)
        _write(data)
    return updated


def pin(template_id: str) -> VideoTemplateOut:
    """设为固定蓝本：全库只保留一个，后续封装以它为复制样板。"""
    with _LOCK:
        data = _read()
        target: dict[str, Any] | None = None
        for record in data["items"]:
            if record.get("id") == template_id:
                target = record
                break
        if target is None:
            raise NotFound("视频模板不存在")
        for record in data["items"]:
            record["pinned"] = record is target
        target["updatedAt"] = _now().isoformat().replace("+00:00", "Z")
        _write(data)
    return _as_out(target)


def find_for_run(
    source_id: str,
    pack_id: str | None,
    kind: str | None = None,
    template_id: str | None = None,
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
    with _LOCK:
        records = list(_read()["items"])
    if template_id:
        selected = next((record for record in records if record.get("id") == template_id), None)
        if (
            selected is None
            or selected.get("sourceVideoAssetId") != source_id
            or (kind is not None and selected.get("defaultKind", "product") != kind)
        ):
            raise BadRequest("所选封装功能不存在或与原片、处理类型不匹配，请重新选择")
        return _as_out(selected)
    bound = [
        record
        for record in records
        if record.get("sourceVideoAssetId") == source_id
        and (kind is None or record.get("defaultKind", "product") == kind)
    ]
    exact = [record for record in bound if record.get("packId") == pack_id]
    matches = exact or bound
    if not matches:
        return None
    matches.sort(key=lambda record: (not record.get("pinned"), record.get("createdAt") or ""))
    return _as_out(matches[0])


async def attach_source(template_id: str, asset_id: str, session: AsyncSession) -> VideoTemplateOut:
    """把样片上传后的 source 资产绑定到模板，让它出现在「全部模板」卡片流里。"""
    lookup = await get_asset_lookup().get_many(session, [asset_id])
    asset = lookup.get(asset_id)
    if asset is None or asset.deleted_at:
        raise NotFound("样片原片不存在或已删除")
    if asset.category != "source" or asset.type != "video":
        raise BadRequest("请选择已归档的 source 视频原片")
    target = await asyncio.to_thread(
        _bind_source_asset, template_id, asset_id, asset.name, asset.duration
    )
    return _as_out(target)


def _bind_source_asset(
    template_id: str, asset_id: str, asset_name: str, asset_duration: float
) -> dict[str, Any]:
    """同步临界区：把 source 资产写回模板。只从线程池调用。

    换的是**这条模板的样片**本身，所以文件名与时长跟着资产走：只写 `assetId`
    会让封装管理里继续显示上一支片的名字和秒数，生成档位也还按旧时长取整
    （6 秒的片按 5 秒出，白丢一秒）。

    档位只在它确实等于旧时长取整时才跟着改——手调过的值不覆盖。
    """
    with _LOCK:
        data = _read()
        target = next((record for record in data["items"] if record.get("id") == template_id), None)
        if target is None:
            raise NotFound("视频模板不存在")
        previous = float(target.get("sourceDurationSec") or 0)
        target["sourceVideoAssetId"] = asset_id
        if asset_name:
            target["sourceVideoName"] = asset_name
        duration = float(asset_duration or 0)
        if duration > 0:
            target["sourceDurationSec"] = duration
            generation = dict(target.get("generation") or {})
            current = generation.get("duration")
            derived = max(2, min(15, round(previous))) if previous > 0 else None
            if isinstance(current, int) and derived is not None and current == derived:
                generation["duration"] = max(2, min(15, round(duration)))
                target["generation"] = generation
        target["updatedAt"] = _now().isoformat().replace("+00:00", "Z")
        _write(data)
        return dict(target)
