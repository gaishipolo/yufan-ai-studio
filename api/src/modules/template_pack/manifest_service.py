"""manifest 配置的预检 / 幂等保存 / 发布。

## 从 CLI 搬到服务层，哪些补偿逻辑被删掉了

原 `template_packager` 的复杂度几乎都在跟**本地文件**搏斗：算文件 sha256、
防「预检后文件被换」、按 `{api, owner, key}` 找 journal 进度文件、`.lock` 独占、
每步 atomic checkpoint、`uuid5` 派生幂等键。素材改成资产库 id 之后：

- 字节变了就是新的 `library_assets.id`，**内容去重退化成 id 去重**；
- 幂等由 `uq_template_manifests_key` + `fingerprint` 兜住，跨进程成立；
- 「远端被手工改过」靠 `published_pack_id` / `published_pack_version` 回执比对，
  不再每轮拉远端列表逐字段 diff。

所以本地文件校验、journal、锁、HTTP 客户端整套都没有搬进来。

## 保留下来的能力

严格入参（`extra="forbid"`）、用途词表 / 必备用途 / 支持范围、
`{{Mixed N}}` 编号与视频槽位、bindings 按用途绑定、mediaOrder 完整性、
文字换场景、prompt 选项校验、内容指纹幂等、复用已发布包、发布版本回执。

## 本阶段边界（C1）

发布只做**配置层**的状态流转与版本冻结，并通过 `core.ports.template_catalog`
对外提供已发布数据。把它投影成 `/templates` 旧目录里的 `video_templates` 行
属于阶段 C2（①② 表迁进本模块之后，同模块内直写，不需要跨模块端口）。
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy import delete as sa_delete
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.context import CurrentUser
from core.exceptions import BadRequest, Conflict, NotFound
from core.library_assets import resolve_assets
from core.ports.library_lookup import (
    LibraryAssetKind,
    LibraryAssetState,
    get_library_lookup,
)
from core.template_rules import MAX_REFERENCE_IMAGES
from core.template_rules.bindings import (
    allows_partial_slots,
    ensure_distinct_roles,
    order_reference_ids,
    reference_roles,
)
from core.template_rules.options import validate_options
from core.template_rules.roles import BASE_ROLE, PACK_ROLES, PACK_SCOPES
from db.base import utcnow
from modules.template_pack import video_template_service
from modules.template_pack.manifest_schema import (
    MAX_PROMPT,
    ManifestDraftIn,
    ManifestFunctionIn,
    ManifestListOut,
    ManifestOut,
    ManifestPackIn,
    ManifestPublishOut,
    ManifestReferenceIn,
    ManifestValidateOut,
    ManifestVideoIn,
    PublishedTemplateOut,
)
from modules.template_pack.models import (
    TemplateManifest,
    TemplateManifestFunction,
    TemplateManifestPack,
    TemplateManifestReference,
    TemplatePack,
    TemplatePackVersion,
)
from modules.template_pack.pack_service import PackService
from modules.template_pack.schema import PackBinding, PackDraftIn
from modules.template_pack.video_template_schema import (
    VideoTemplateDraftIn,
    VideoTemplateMedia,
)

#: 原 CLI 对本地 mp4 的唯一硬限制。资产库里 `size_bytes` 是已探测值，直接用。
MAX_VIDEO_BYTES = 300 * 1024 * 1024

_MIXED = re.compile(r"\{\{([^{}]+)\}\}")


# ---------------------------------------------------------------------------
# 内容指纹
# ---------------------------------------------------------------------------


def fingerprint_of(spec: ManifestDraftIn) -> str:
    """配置摘要：只由结构与 asset id 决定，不含机器路径 / 文件字节。

    `exclude_none=True` 让「未提供」与「显式 null」等价 —— 前端表单里这两种
    写法指的是同一份配置，不该因此判成 409。
    """
    data = spec.model_dump(mode="json", by_alias=True, exclude_none=True)
    payload = json.dumps(data, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# 预检
# ---------------------------------------------------------------------------


def _validate_structure(spec: ManifestDraftIn) -> None:
    """纯规则校验：不碰数据库、不碰网络（规则来自 core/template_rules）。"""
    packs = {pack.key: pack for pack in spec.packs}
    if len(packs) != len(spec.packs):
        raise BadRequest("素材包 key 不能重复")
    if len({fn.key for fn in spec.functions}) != len(spec.functions):
        raise BadRequest("功能 key 不能重复")

    for pack in spec.packs:
        if not pack.references:
            if not pack.pack_id:
                raise BadRequest(f"素材包 {pack.key} 复用时必须指定已发布包的 packId")
            if (
                pack.name
                or pack.required_roles
                or pack.supported_scopes
                or pack.wardrobe_mode != "preserve"
            ):
                raise BadRequest(f"复用包 {pack.key} 不能覆盖已发布包的配置")
            continue
        if pack.pack_id or not pack.name.strip():
            raise BadRequest(f"素材包 {pack.key} 新建需要名称与参考图，不能同时提供 packId")
        ref_keys = [ref.key for ref in pack.references]
        if len(set(ref_keys)) != len(ref_keys):
            raise BadRequest(f"素材包 {pack.key} 的参考图 key 不能重复")
        # 资产库是内容寻址：同一份字节只有一个 id，重复引用即同图重复。
        if len({ref.asset_id for ref in pack.references}) != len(pack.references):
            raise BadRequest(f"素材包 {pack.key} 不能重复使用同一张图片")
        roles = {ref.role for ref in pack.references}
        if not roles <= PACK_ROLES[pack.kind].keys():
            raise BadRequest(f"素材包 {pack.key} 的图片用途不属于该类型")
        if not ({BASE_ROLE[pack.kind]} | set(pack.required_roles)) <= roles:
            raise BadRequest(f"素材包 {pack.key} 缺少必备用途")
        if not set(pack.supported_scopes) <= PACK_SCOPES.keys():
            raise BadRequest(f"素材包 {pack.key} 的支持范围无效")
        if pack.kind == "product" and not pack.supported_scopes:
            raise BadRequest("产品素材包必须声明 supportedScopes")

    for function in spec.functions:
        if len(f"{spec.name} · {function.name}") > 160:
            raise BadRequest("卡片名称与功能名称组合后不能超过 160 字")
        _validate_function(function, packs)


def _validate_function(function: ManifestFunctionIn, packs: dict[str, ManifestPackIn]) -> None:
    text_scene = function.scene_prompt_default is not None
    prompt = function.prompt
    if not prompt.strip() or len(prompt) > MAX_PROMPT:
        raise BadRequest("提示词不能为空且不能超过 20000 字")

    rules: dict[str, Any] = {
        "workbenchLabel": function.name,
        "scopeOptions": list(function.scope_options),
    }
    if text_scene:
        rules.update(
            inputMode="video_scene_text",
            scenePromptDefault=function.scene_prompt_default,
        )
    validate_options(prompt, rules)

    if text_scene:
        if (
            function.kind != "scene"
            or function.pack
            or function.media_order
            or function.scope_options
        ):
            raise BadRequest(
                "文字换场景需指定 kind=scene，不提供素材包、mediaOrder 或替换范围"
            )
        if function.bindings not in (None, {"{{Mixed 1}}": "source_video"}):
            raise BadRequest("文字换场景只有原视频输入")
        # 原 packager 在这里把绑定补成「只有原片」，下游不必再判空。
        if function.bindings is None:
            function.bindings = {"{{Mixed 1}}": "source_video"}
        return

    if function.pack not in packs:
        raise BadRequest(f"功能引用的素材包不存在：{function.pack}")
    pack = packs[function.pack or ""]
    if function.kind is not None and function.kind != pack.kind:
        raise BadRequest("功能类型与素材包类型不匹配")

    keys = [ref.key for ref in pack.references]
    if function.bindings is not None:
        if function.media_order:
            raise BadRequest("bindings 与 mediaOrder 不能同时声明")
        declared = reference_roles(
            {"referenceBindings": function.bindings}, prompt, pack.kind
        )
        if pack.references:
            roles = {ref.key: ref.role for ref in pack.references}
            if allows_partial_slots(declared or [], pack.kind):
                ensure_distinct_roles(roles, declared or [], pack.kind)
            else:
                order_reference_ids(keys, roles, declared or [], pack.kind)
    elif not pack.references:
        raise BadRequest("复用已有包时请声明 bindings，按用途绑定提示词编号")

    if function.media_order and sorted(function.media_order) != sorted(keys):
        raise BadRequest("mediaOrder 必须完整且不重复地列出当前包的参考图 key")

    numbers: list[int] = []
    for token in _MIXED.findall(prompt):
        if token == "ReplacementScope":
            continue
        if not re.fullmatch(r"Mixed [1-9]\d*", token):
            raise BadRequest(f"非法提示词引用：{token}")
        numbers.append(int(token[6:]))
    video_number = (
        next(
            int(token[8:-2])
            for token, role in (function.bindings or {}).items()
            if role == "source_video"
        )
        if function.bindings
        else 1
    )
    if video_number not in numbers or not any(n != video_number for n in numbers):
        raise BadRequest("提示词必须引用声明的视频槽位及至少一张参考图")
    upper = len(function.bindings) if function.bindings is not None else len(keys) + 1
    if numbers and max(numbers) > upper:
        raise BadRequest("提示词 Mixed 编号超出素材数量")
    if len(keys) + 1 > MAX_REFERENCE_IMAGES:
        raise BadRequest("参考图数量超过上限")
    # 与旧 manifest 同款：没显式排 mediaOrder 就按包里参考图的顺序。
    # 在校验里补而不是保存时补，指纹才会把这份「默认顺序」算进去。
    if function.bindings is None and not function.media_order:
        function.media_order = list(keys)


async def _validate_media(
    session: AsyncSession, actor: CurrentUser, spec: ManifestDraftIn
) -> None:
    """素材一律来自资产库：存在、active、可读、类型对、大小合规。

    可读性判据复用 `LibraryLookup.readable_ids`（唯一一份在 `library/access.py`），
    不在这里重写 —— 重写漂移的方向总是「多给权限」。
    """
    ids = {spec.video.asset_id}
    for pack in spec.packs:
        ids.update(ref.asset_id for ref in pack.references)
    lookup = get_library_lookup()
    refs = await lookup.get_many(session, sorted(ids))
    readable = await lookup.readable_ids(session, actor, sorted(ids))

    def need(asset_id: str, kind: LibraryAssetKind) -> None:
        ref = refs.get(asset_id)
        if ref is None:
            raise BadRequest(f"素材不存在：{asset_id}")
        if ref.kind != kind:
            raise BadRequest(f"素材 {asset_id} 类型不是 {kind.value}")
        if ref.state != LibraryAssetState.ACTIVE:
            raise BadRequest(f"素材 {asset_id} 不在可用状态")
        if asset_id not in readable:
            raise BadRequest(f"素材 {asset_id} 无权访问")
        if not ref.size_bytes:
            raise BadRequest(f"素材 {asset_id} 内容为空")

    need(spec.video.asset_id, LibraryAssetKind.VIDEO)
    if refs[spec.video.asset_id].size_bytes > MAX_VIDEO_BYTES:
        raise BadRequest("原片最大 300MB")
    for pack in spec.packs:
        for ref in pack.references:
            need(ref.asset_id, LibraryAssetKind.IMAGE)


async def _reused_pack_versions(
    session: AsyncSession, pack_ids: set[str]
) -> dict[str, int]:
    """`packId` 指向**已发布的运行素材包**，返回 `{pack_id: 包自己的发布版本}`。

    口径与原 packager 一致：它从 `/replacement-packs/available` 挑包，拿到的是
    运行行的 id 与版本。DB 化后这一步退化成两次查询（head + 冻结版本行）。

    **必须是运行行，不是配置行**：投影出来的 `video_templates.pack_id` 与替换链
    用的都是 `template_packs.id`；配置行 id 在运行链路里从不出现，拿它当 `packId`
    会让「预检通过、投影时查不到包」。手工用封装编辑器建的包也只在运行表里，
    用配置行就永远复用不了它们。

    同时要求**冻结版本行存在**：只看 head 会被「手工删过历史版本」骗过。
    查不到返回空字典而不是抛异常 —— 预检把它翻译成 400，发布时用它填回执，
    两种调用方对「缺失」的措辞不同。
    """
    if not pack_ids:
        return {}
    rows = (
        await session.execute(
            select(TemplatePack.id, TemplatePack.version).where(
                TemplatePack.id.in_(sorted(pack_ids)),
                TemplatePack.status == "published",
                TemplatePack.deleted_at.is_(None),
                TemplatePack.version > 0,
            )
        )
    ).all()
    heads = {str(pack_id): int(version) for pack_id, version in rows}
    if not heads:
        return {}
    frozen = {
        (str(pack_id), int(version))
        for pack_id, version in (
            await session.execute(
                select(TemplatePackVersion.pack_id, TemplatePackVersion.version).where(
                    TemplatePackVersion.pack_id.in_(sorted(heads))
                )
            )
        ).all()
    }
    return {
        pack_id: version
        for pack_id, version in heads.items()
        if (pack_id, version) in frozen
    }


async def validate(
    session: AsyncSession, actor: CurrentUser, spec: ManifestDraftIn
) -> ManifestValidateOut:
    """只预检不落库，返回将用于幂等判定的指纹。"""
    _validate_structure(spec)
    await _validate_media(session, actor, spec)
    wanted = {pack.pack_id for pack in spec.packs if pack.pack_id}
    if wanted:
        found = await _reused_pack_versions(session, wanted)
        missing = sorted(wanted - found.keys())
        if missing:
            raise BadRequest(f"复用的素材包不存在或尚未发布：{', '.join(missing)}")
        if any(version <= 0 for version in found.values()):
            raise BadRequest("复用的素材包没有已发布版本")
    return ManifestValidateOut(fingerprint=fingerprint_of(spec))


# ---------------------------------------------------------------------------
# 读写
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class _Bundle:
    head: TemplateManifest
    packs: list[TemplateManifestPack]
    references: dict[str, list[TemplateManifestReference]]
    functions: list[TemplateManifestFunction]


async def _bundle(session: AsyncSession, head: TemplateManifest) -> _Bundle:
    packs = list(
        (
            await session.scalars(
                select(TemplateManifestPack)
                .where(TemplateManifestPack.manifest_id == head.id)
                .order_by(TemplateManifestPack.position)
            )
        ).all()
    )
    pack_ids = [row.id for row in packs]
    refs: dict[str, list[TemplateManifestReference]] = {pid: [] for pid in pack_ids}
    if pack_ids:
        rows = (
            await session.scalars(
                select(TemplateManifestReference)
                .where(TemplateManifestReference.pack_id.in_(pack_ids))
                .order_by(TemplateManifestReference.position)
            )
        ).all()
        for row in rows:
            refs[row.pack_id].append(row)
    functions = list(
        (
            await session.scalars(
                select(TemplateManifestFunction)
                .where(TemplateManifestFunction.manifest_id == head.id)
                .order_by(TemplateManifestFunction.position)
            )
        ).all()
    )
    return _Bundle(head, packs, refs, functions)


def _to_out(bundle: _Bundle) -> ManifestOut:
    head = bundle.head
    packs: list[ManifestPackIn] = []
    for row in bundle.packs:
        packs.append(
            ManifestPackIn(
                key=row.pack_key,
                name=row.name,
                kind=row.kind,  # type: ignore[arg-type]
                pack_id=row.reuse_pack_id or None,
                references=[
                    ManifestReferenceIn(**_reference_out(ref))
                    for ref in bundle.references.get(row.id, [])
                ],
                required_roles=list(row.required_roles),
                supported_scopes=list(row.supported_scopes),
                wardrobe_mode=row.wardrobe_mode,  # type: ignore[arg-type]
            )
        )
    functions = [
        ManifestFunctionIn(
            key=row.function_key,
            name=row.name,
            pack=row.pack_key or None,
            kind=row.kind or None,  # type: ignore[arg-type]
            scene_prompt_default=row.scene_prompt_default,
            prompt=row.prompt,
            media_order=list(row.media_order),
            bindings=dict(row.bindings) if row.bindings else None,
            scope_options=list(row.scope_options),
            aspect_ratio=row.aspect_ratio,  # type: ignore[arg-type]
            duration=row.duration,
        )
        for row in bundle.functions
    ]
    return ManifestOut(
        id=head.id,
        key=head.template_key,
        name=head.name,
        description=head.description,
        owner_id=head.owner_id,
        video=ManifestVideoIn(asset_id=head.video_asset_id, shared=head.video_shared),
        status=head.status,
        version=head.version,
        revision=head.revision,
        fingerprint=head.fingerprint,
        published_at=head.published_at,
        created_at=head.created_at,
        updated_at=head.updated_at,
        packs=packs,
        functions=functions,
    )


def _reference_out(row: TemplateManifestReference) -> dict[str, Any]:
    return {
        "key": row.ref_key,
        "asset_id": row.asset_id,
        "role": row.role,
        "description": row.description,
    }


async def _find(session: AsyncSession, key: str) -> TemplateManifest | None:
    return await session.scalar(
        select(TemplateManifest).where(
            TemplateManifest.template_key == key,
            TemplateManifest.deleted_at.is_(None),
        )
    )


async def get_out(session: AsyncSession, actor: CurrentUser, key: str) -> ManifestOut:
    head = await _find(session, key)
    if head is None:
        raise NotFound(f"配置不存在：{key}")
    # 草稿是作者私有工作区。不引入 `:read:all` 这种「声明了没有判定点」的
    # 权限码（`test_permission_coverage.py` 的红线），直接按 owner 收窄。
    if head.status == "draft" and head.owner_id != actor.id:
        raise NotFound(f"配置不存在：{key}")
    return _to_out(await _bundle(session, head))


async def list_out(
    session: AsyncSession,
    actor: CurrentUser,
    *,
    status_filter: str = "",
    mine_only: bool = False,
) -> ManifestListOut:
    stmt = select(TemplateManifest).where(TemplateManifest.deleted_at.is_(None))
    if status_filter:
        stmt = stmt.where(TemplateManifest.status == status_filter)
    if mine_only:
        stmt = stmt.where(TemplateManifest.owner_id == actor.id)
    else:
        # 别人的草稿不进列表；已发布 / 停用的历史允许查看。
        stmt = stmt.where(
            (TemplateManifest.owner_id == actor.id)
            | (TemplateManifest.status != "draft")
        )
    rows = list(
        (await session.scalars(stmt.order_by(TemplateManifest.created_at.desc()))).all()
    )
    items = [_to_out(await _bundle(session, row)) for row in rows]
    return ManifestListOut(items=items, total=len(items))


async def _clear_children(session: AsyncSession, head: TemplateManifest) -> None:
    pack_ids = select(TemplateManifestPack.id).where(
        TemplateManifestPack.manifest_id == head.id
    )
    await session.execute(
        sa_delete(TemplateManifestReference).where(
            TemplateManifestReference.pack_id.in_(pack_ids)
        )
    )
    await session.execute(
        sa_delete(TemplateManifestPack).where(
            TemplateManifestPack.manifest_id == head.id
        )
    )
    await session.execute(
        sa_delete(TemplateManifestFunction).where(
            TemplateManifestFunction.manifest_id == head.id
        )
    )


async def _write_children(
    session: AsyncSession, head: TemplateManifest, spec: ManifestDraftIn
) -> None:
    for index, pack in enumerate(spec.packs):
        row = TemplateManifestPack(
            manifest_id=head.id,
            pack_key=pack.key,
            name=pack.name,
            kind=pack.kind,
            reuse_pack_id=pack.pack_id or "",
            required_roles=list(pack.required_roles),
            supported_scopes=list(pack.supported_scopes),
            wardrobe_mode=pack.wardrobe_mode,
            position=index,
        )
        session.add(row)
        # UUIDMixin 的主键 default 在 flush 时才生效；子行要拿 row.id。
        await session.flush()
        for position, ref in enumerate(pack.references):
            session.add(
                TemplateManifestReference(
                    pack_id=row.id,
                    ref_key=ref.key,
                    asset_id=ref.asset_id,
                    role=ref.role,
                    description=ref.description,
                    position=position,
                )
            )
            await session.flush()
    for index, function in enumerate(spec.functions):
        text_scene = function.scene_prompt_default is not None
        session.add(
            TemplateManifestFunction(
                manifest_id=head.id,
                function_key=function.key,
                name=function.name,
                pack_key=function.pack or "",
                kind=function.kind or "",
                prompt=function.prompt,
                prompt_sha256=hashlib.sha256(function.prompt.encode("utf-8")).hexdigest(),
                scene_prompt_default=function.scene_prompt_default,
                input_mode="video_scene_text" if text_scene else "",
                media_order=list(function.media_order),
                bindings=dict(function.bindings) if function.bindings else None,
                scope_options=list(function.scope_options),
                aspect_ratio=function.aspect_ratio,
                duration=function.duration,
                position=index,
            )
        )
        await session.flush()


async def save(
    session: AsyncSession, actor: CurrentUser, spec: ManifestDraftIn
) -> ManifestOut:
    """保存草稿：**同 key 同配置返回原行，同 key 异配置在已发布时 409**。

    草稿允许整体替换（配置还没对外，改了不算破坏历史）；已发布的配置按
    「终态不可变」处理，要改就换 key 发新版本 —— 与 `template_packs` 的
    `version` / `revision` 分工是同一套口径。
    """
    checked = await validate(session, actor, spec)
    head = await _find(session, spec.key)
    if head is not None:
        if head.owner_id != actor.id:
            raise Conflict(f"配置 {spec.key} 属于其他用户")
        if head.fingerprint == checked.fingerprint:
            return _to_out(await _bundle(session, head))
        if head.status != "draft":
            raise Conflict(f"配置 {spec.key} 已发布，内容不可再改；请换 key 发新版本")
        await _clear_children(session, head)
        head.revision += 1
    else:
        head = TemplateManifest(template_key=spec.key, owner_id=actor.id, status="draft")
        session.add(head)

    # 所有 NOT NULL 列在**第一次 flush 之前**填满：先 add 再分批赋值会让
    # flush 撞上 `NOT NULL constraint failed`，还被框架兜成 409 看不出真因。
    head.name = spec.name
    head.description = spec.description
    head.video_asset_id = spec.video.asset_id
    head.video_shared = spec.video.shared
    head.fingerprint = checked.fingerprint
    await session.flush()  # 主键 default 在这里生效，子行要用 head.id
    await _write_children(session, head, spec)
    await session.flush()
    result = _to_out(await _bundle(session, head))
    # `db.session.get_session()` 只在异常时回滚，**提交由 service 显式控制**。
    # 漏 commit 的症状很隐蔽：接口 201，紧接着的读全 404。
    await session.commit()
    return result


# ---------------------------------------------------------------------------
# 发布投影：manifest → 运行表
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _PackShot:
    key: str
    kind: str
    name: str
    reuse_pack_id: str
    wardrobe_mode: str
    required_roles: tuple[str, ...]
    supported_scopes: tuple[str, ...]
    #: `(ref_key, asset_id, role, description)`
    refs: tuple[tuple[str, str, str, str], ...]


@dataclass(frozen=True, slots=True)
class _FunctionShot:
    key: str
    name: str
    pack_key: str
    kind: str
    prompt: str
    scene_prompt_default: str | None
    media_order: tuple[str, ...]
    bindings: dict[str, str] | None
    scope_options: tuple[str, ...]
    aspect_ratio: str
    duration: int | None


@dataclass(frozen=True, slots=True)
class _Shot:
    key: str
    name: str
    owner_id: str
    video_asset_id: str
    fingerprint: str
    packs: tuple[_PackShot, ...]
    functions: tuple[_FunctionShot, ...]


def _shot(bundle: _Bundle) -> _Shot:
    """把 ORM 行摊成纯值。

    **必须在第一次 commit 之前拍**：投影复用 `PackService` / `create`，它们各自
    提交，`expire_on_commit` 会让 ORM 属性变成需要重新加载（异步会话里读一下就
    炸 MissingGreenlet）。摊平之后，后面只碰自己手里的值。
    """
    head = bundle.head
    return _Shot(
        key=head.template_key,
        name=head.name,
        owner_id=head.owner_id,
        video_asset_id=head.video_asset_id,
        fingerprint=head.fingerprint,
        packs=tuple(
            _PackShot(
                key=row.pack_key,
                kind=row.kind,
                name=row.name,
                reuse_pack_id=row.reuse_pack_id,
                wardrobe_mode=row.wardrobe_mode,
                required_roles=tuple(row.required_roles or ()),
                supported_scopes=tuple(row.supported_scopes or ()),
                refs=tuple(
                    (ref.ref_key, ref.asset_id, ref.role, ref.description)
                    for ref in bundle.references.get(row.id, [])
                ),
            )
            for row in bundle.packs
        ),
        functions=tuple(
            _FunctionShot(
                key=row.function_key,
                name=row.name,
                pack_key=row.pack_key,
                kind=row.kind,
                prompt=row.prompt,
                scene_prompt_default=row.scene_prompt_default,
                media_order=tuple(row.media_order or ()),
                bindings=dict(row.bindings) if row.bindings else None,
                scope_options=tuple(row.scope_options or ()),
                aspect_ratio=row.aspect_ratio,
                duration=row.duration,
            )
            for row in bundle.functions
        ),
    )


def _namespace(owner_id: str, key: str) -> Any:
    """与离线 packager 完全一致的命名空间。

    **必须沿用**：素材包 id 与 `video_templates.template_key` 都从这里派生。
    换算法等于把 CLI 时期已经发布出去的模板当成另一条记录，工作台里会重复出现。
    """
    return uuid5(NAMESPACE_URL, f"yufan-packager:{owner_id}:{key}")


async def _project(
    session: AsyncSession, actor: CurrentUser, shot: _Shot
) -> tuple[dict[str, tuple[str, str, str, int]], dict[str, str]]:
    """把一份配置投影成运行表，返回 `{包 key: (id, name, kind, version)}` 与
    `{功能 key: 模板 id}`。

    **不是单事务**：复用 `PackService.save/publish` 与 `video_template_service.create`
    （它们各自提交，那是 HTTP 接口的实现）。失败可重跑：包 id 与模板
    `template_key` 都由 `(owner, manifest key, 子 key)` 派生，同配置重入返回原行
    （异配置由 `_same_or_conflict` 判 409）。这与离线 packager 的 journal 语义一致，
    比「一个横跨十几张表的大事务」更抗中断。
    """
    namespace = _namespace(shot.owner_id, shot.key)
    service = PackService(session)
    packs: dict[str, tuple[str, str, str, int]] = {}
    for pack in shot.packs:
        if pack.reuse_pack_id:
            row = await session.get(TemplatePack, pack.reuse_pack_id)
            if row is None:
                raise BadRequest(f"素材包 {pack.key} 复用的包已不存在")
            packs[pack.key] = (row.id, row.name, row.kind, row.version)
            continue
        managed = await service.save(
            PackDraftIn(
                request_id=uuid5(namespace, "pack:" + pack.key),
                kind=pack.kind,
                name=pack.name,
                reference_library_ids=[ref[1] for ref in pack.refs],
                cover_library_id=pack.refs[0][1] if pack.refs else None,
                wardrobe_mode=pack.wardrobe_mode,
                bindings=[
                    PackBinding(role=role, description=description)
                    for _key, _asset, role, description in pack.refs
                ],
                # 基准用途永远必填：原 packager 在这里补齐，下游不重复判断。
                required_roles=list(
                    dict.fromkeys([BASE_ROLE[pack.kind], *pack.required_roles])
                ),
                supported_scopes=list(pack.supported_scopes),
            ),
            shot.owner_id,
        )
        if managed.status != "published":
            managed = await service.publish(managed.id, shot.owner_id, managed.revision)
        packs[pack.key] = (managed.id, managed.name, managed.kind, managed.version)

    media_assets = [shot.video_asset_id, *[ref[1] for pack in shot.packs for ref in pack.refs]]
    resolved = await resolve_assets(session, media_assets, actor=actor)
    names = {asset_id: row.name for asset_id, row in resolved.v2.items()}
    refs_by_pack = {pack.key: {ref[0]: ref for ref in pack.refs} for pack in shot.packs}

    templates: dict[str, str] = {}
    for function in shot.functions:
        scene_text = function.scene_prompt_default is not None
        if scene_text:
            pack_id, pack_kind = None, "scene"
        else:
            pack_id, _name, pack_kind, _version = packs[function.pack_key]
        rules: dict[str, Any] = {
            "workbenchLabel": function.name,
            "scopeOptions": list(function.scope_options),
            "packagingFingerprint": shot.fingerprint,
        }
        if function.bindings is not None:
            rules["referenceBindings"] = dict(function.bindings)
        if scene_text:
            rules.update(
                inputMode="video_scene_text",
                scenePromptDefault=function.scene_prompt_default,
            )
        media: list[VideoTemplateMedia] = []
        if function.bindings is None and not scene_text:
            media.append(
                VideoTemplateMedia(
                    token="{{Mixed 1}}",
                    role="source_video",
                    type="video",
                    asset_id=shot.video_asset_id,
                    name=names.get(shot.video_asset_id, ""),
                    in_prompt=True,
                )
            )
            refs = refs_by_pack.get(function.pack_key, {})
            for index, key in enumerate(function.media_order, 2):
                ref = refs[key]
                token = f"{{{{Mixed {index}}}}}"
                media.append(
                    VideoTemplateMedia(
                        token=token,
                        role=ref[2],
                        type="image",
                        asset_id=ref[1],
                        name=names.get(ref[1], ""),
                        in_prompt=token in function.prompt,
                    )
                )
        out = await video_template_service.create(
            VideoTemplateDraftIn(
                template_key=f"ai_{namespace.hex}_{function.key}",
                name=f"{shot.name} · {function.name}",
                prompt=function.prompt,
                source_video_asset_id=shot.video_asset_id,
                aspect_ratio=function.aspect_ratio,
                pack_id=pack_id,
                default_kind=pack_kind,  # type: ignore[arg-type]
                media_order=media,
                generation=(
                    {"duration": function.duration} if function.duration is not None else {}
                ),
                rules=rules,
            ),
            session,
            actor=actor,
        )
        templates[function.key] = out.id
    return packs, templates


async def publish(
    session: AsyncSession, actor: CurrentUser, key: str
) -> ManifestPublishOut:
    """发布：状态流转 + 版本号 +1，并给每个 function 生成工作台回执。"""
    head = await _find(session, key)
    if head is None:
        raise NotFound(f"配置不存在：{key}")
    if head.owner_id != actor.id:
        raise Conflict(f"配置 {key} 属于其他用户")
    if head.status != "draft":
        raise Conflict(f"配置 {key} 不是草稿状态")
    bundle = await _bundle(session, head)
    if not bundle.functions:
        raise BadRequest("没有可发布的功能")

    # 先摊平再投影：投影里会多次 commit（复用 PackService / create 的既有语义），
    # 之后再读 ORM 属性就是「过期行 + 异步会话」，会炸 MissingGreenlet。
    shot = _shot(bundle)
    packs, template_ids = await _project(session, actor, shot)

    head.status = "published"
    head.version += 1
    head.published_at = utcnow()
    version = head.version

    # 发布回执（packager 的 journal 语义）：本次真正对外的是哪几行、第几版。
    # 这两列现在指向**投影出来的运行行** —— 重跑时可以直接和远端比对。
    for pack_row in bundle.packs:
        pack_id, _name, _kind, pack_version = packs[pack_row.pack_key]
        pack_row.published_pack_id = pack_id
        pack_row.published_pack_version = pack_version
    for fn_row in bundle.functions:
        fn_row.published_template_id = template_ids[fn_row.function_key]
    await session.commit()

    templates = [
        PublishedTemplateOut(
            function_key=fn.key,
            template_id=template_ids[fn.key],
            template_key=f"{shot.key}:{fn.key}",
            name=f"{shot.name} · {fn.name}",
            workbench_path=(
                f"/templates?sourceId={shot.video_asset_id}"
                f"&videoTemplateId={template_ids[fn.key]}&kind={fn.kind or 'product'}"
            ),
        )
        for fn in shot.functions
    ]
    stored = await _find(session, key)
    if stored is None:  # pragma: no cover - 刚提交过，只可能是并发删除
        raise NotFound(f"配置不存在：{key}")
    return ManifestPublishOut(
        manifest=_to_out(await _bundle(session, stored)),
        version=version,
        templates=templates,
    )


async def disable(session: AsyncSession, actor: CurrentUser, key: str) -> ManifestOut:
    """停用：保留历史、不再对外。不是删除 —— 模板是资产。"""
    head = await _find(session, key)
    if head is None:
        raise NotFound(f"配置不存在：{key}")
    if head.owner_id != actor.id:
        raise Conflict(f"配置 {key} 属于其他用户")
    if head.status != "published":
        raise Conflict(f"配置 {key} 不是已发布状态")
    head.status = "disabled"
    await session.flush()
    result = _to_out(await _bundle(session, head))
    await session.commit()
    return result


__all__ = [
    "disable",
    "fingerprint_of",
    "get_out",
    "list_out",
    "publish",
    "save",
    "validate",
]
