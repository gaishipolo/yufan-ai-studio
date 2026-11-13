"""封装（参考素材包）的业务规则：草稿、不可变发布版本、受控公共预览。

数据从「归档盘的 JSON 目录」搬到数据库之后，四件事的写法变了，语义没变：

| 原来 | 现在 |
|---|---|
| `storage.list_keys("catalog/packs")` 再逐个 `read` | 一条带索引的 `select` |
| `storage.create()` 的条件写做版号 CAS | `UNIQUE(pack_id, version)` 约束 |
| `catalog_lease()` 共享盘文件锁 | 数据库事务（不再需要跨实例锁文件） |
| `catalog/assets/*.json` 镜像查素材 | `AssetLookup` 注入口查 `assets` 表 |

**保留不变的两条判据**（都很容易在重构时被"优化"掉）：

1. `gaps()` 的顺序就是封装者该补的顺序，前端直接把它当待办清单渲染；
2. 已发布版本不可变——`publish()` 只新增 version 行，绝不改历史行。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from core.context import CurrentUser
from core.exceptions import AppError, BadRequest, Conflict, NotFound
from core.logging import get_logger
from core.ports.library_lookup import (
    LibraryAssetKind,
    LibraryAssetState,
    get_library_lookup,
)
from core.ports.media import get_asset_lookup
from core.serving import Served
from infra import get_storage
from infra.archive import BlobStore, get_archive_storage, preview_key
from modules.template.asset_source import resolve_assets, resolve_names
from modules.template.id_space import (
    ASSET_ID_SPACE_V1,
    ASSET_ID_SPACE_V2,
    CURRENT_ASSET_ID_SPACE,
)
from modules.template.models import TemplatePack, TemplatePackReference, TemplatePackVersion
from modules.template.pack_roles import (
    PACK_SCOPES,
    missing_role_labels,
    roles_for,
)
from modules.template.repository import PackRepository
from modules.template.schema import (
    PackDraftIn,
    PackManaged,
    PackManagedList,
    PackPublicList,
    ReplacementPack,
    aligned_bindings,
    aligned_required_roles,
)

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class _DraftRefs:
    """草稿里「用哪几张图」的归一化结果：id 列表 + 空间 + 封面。

    请求侧是两个字段（`referenceIds` / `referenceLibraryIds`），到业务层合成一份，
    后面的校验、落库、发布都只认它——各写一遍「用哪个字段」迟早漏一处。
    """

    ids: list[str]
    space: str
    cover: str | None


class PackService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repo = PackRepository(session)
        self.storage = get_archive_storage()
        self.blobs = BlobStore(self.storage)

    # ------------------------------------------------------------------ #
    # 读
    # ------------------------------------------------------------------ #
    async def managed(self, pack: TemplatePack) -> PackManaged:
        """带缺项清单返回：列表页据此提示「还差什么」，不用前端再猜一遍规则。

        `revision` 与 `status` **必须显式带上**：`_shape()` 出的是对外只读形状
        （`ReplacementPack` 没有这两个字段），漏了它们 `PackManaged` 会用默认值
        （`0` / `"draft"`）。症状是前端保存一次后拿到 `revision: 0`，第二次保存
        带 `expectedRevision: 0` 立刻 409「草稿已变化」，而刚保存的那次其实成功了；
        已发布的包也会一直显示成草稿态，按钮停在「发布」。
        """
        refs = await self.repo.references(pack.id)
        return PackManaged.model_validate(
            {
                **self._shape(pack, refs).model_dump(),
                "revision": pack.revision,
                "status": pack.status,
                "gaps": self.gaps(pack, refs),
            }
        )

    async def drafts(self, owner: str) -> PackManagedList:
        packs = await self.repo.list_owned(owner)
        return PackManagedList(items=[await self.managed(pack) for pack in packs])

    async def draft(self, pack_id: str, owner: str) -> PackManaged:
        pack = await self.repo.get_or_404(pack_id, owner=owner)
        return await self.managed(pack)

    async def available(self) -> list[ReplacementPack]:
        packs = await self.repo.list_published()
        rows = []
        for pack in packs:
            refs = await self.repo.references(pack.id)
            rows.append(await self._public(pack, refs))
        return rows

    async def search(self, kind: str, keyword: str, page: int, page_size: int) -> PackPublicList:
        if kind not in ("all", "character", "product", "scene"):
            raise BadRequest("对象分类无效")
        packs = await self.repo.list_published(kind)
        needle = keyword.strip().casefold()
        matched = [pack for pack in packs if not needle or needle in pack.name.casefold()]
        window = matched[(page - 1) * page_size : page * page_size]
        items = [await self._public(pack, await self.repo.references(pack.id)) for pack in window]
        return PackPublicList(items=items, total=len(matched), page=page, page_size=page_size)

    # ------------------------------------------------------------------ #
    # 写
    # ------------------------------------------------------------------ #
    async def save(self, payload: PackDraftIn, owner: str) -> PackManaged:
        """保存草稿。`request_id` 就是封装 id——幂等边界落在主键上。"""
        draft = self._draft_refs(payload)
        self._check_roles(payload, len(draft.ids))
        await self._check_references(draft, payload.kind, owner)

        pack = await self.repo.get(payload.request_id.hex)
        if pack is not None and pack.deleted_at is not None:
            raise NotFound("封装不存在")
        if pack is not None and pack.owner_id != owner:
            raise NotFound("封装不存在")

        if pack is None:
            if payload.expected_revision:
                raise Conflict("草稿不存在，请刷新")
            pack = TemplatePack(
                id=payload.request_id.hex,
                kind=payload.kind,
                owner_id=owner,
                revision=1,
            )
            await self.repo.add(pack)
        else:
            # 重试同一份内容不该被当成「别人改过了」：上一版遇到前端重发时
            # revision 已经 +1，直接比对会报 409，用户看到的是「保存失败」而
            # 实际已经保存成功。内容和 revision 同时命中才认作重试。
            if pack.revision != payload.expected_revision:
                if pack.revision == payload.expected_revision + 1 and self._same_content(
                    pack, payload
                ):
                    return await self.managed(pack)
                raise Conflict("草稿已变化，请刷新后核对")
            pack.revision = payload.expected_revision + 1
            pack.kind = payload.kind

        pack.name = payload.name
        pack.features = payload.features
        pack.instructions = payload.instructions
        pack.wardrobe_mode = payload.wardrobe_mode

        bindings = aligned_bindings(payload.kind, draft.ids, payload.bindings)
        required = aligned_required_roles(payload.kind, payload.required_roles)
        await self.repo.replace_references(
            pack.id,
            draft.space,
            [
                (asset_id, position, binding.role, binding.description)
                for position, (asset_id, binding) in enumerate(
                    zip(draft.ids, bindings, strict=True)
                )
            ],
        )
        pack.cover_asset_id = draft.cover
        # 封面与引用同空间：写入时定死，之后不随 `CURRENT_ASSET_ID_SPACE` 翻转。
        pack.cover_asset_id_space = draft.space
        # 待发布状态：发布时整块冻结进版本行（见 `publish`）。
        pack.required_roles = required
        pack.supported_scopes = list(payload.supported_scopes)

        await self.repo.save(pack)
        await self.session.commit()
        return await self.managed(pack)

    async def publish(self, pack_id: str, owner: str, revision: int) -> PackManaged:
        """发布：校验缺项与字节 → 抢一个版号 → 写不可变快照。"""
        pack = await self.repo.get_or_404(pack_id, owner=owner)
        if pack.revision != revision:
            raise Conflict("草稿已变化，请刷新后重新发布")

        refs = await self.repo.references(pack.id)
        reference_space = self._reference_space(refs)
        # 已经按**这份内容**发布过就返回成功：发布接口是「幂等地让当前内容对外可见」，
        # 超时重发与双击都不该再占一个版号。
        #
        # 这里比的是内容而不是 revision——`publish` 不动 revision（它改的是 version），
        # 所以「revision 相同」既可能是重发，也可能是「发布后又点了发布」。旧实现
        # 把这段幂等判断写在 `revision 不等` 分支里，条件自相矛盾，永远进不去，
        # 于是每点一次就多一个完全相同的版本。
        frozen = await self.repo.get_version(pack.id, pack.version) if pack.version else None
        if (
            pack.status == "published"
            and frozen is not None
            and self._same_as_version(pack, refs, frozen, reference_space)
        ):
            return await self.managed(pack)

        gaps = self.gaps(pack, refs)
        if gaps:
            raise BadRequest("发布前请补齐：" + "；".join(gaps))
        await self._verify_bytes(refs)

        bindings = [{"role": ref.role, "description": ref.description} for ref in refs]
        reference_ids = [ref.asset_id for ref in refs]
        required = list(pack.required_roles) or aligned_required_roles(pack.kind, [])
        scopes = list(pack.supported_scopes)

        def build(version: int) -> TemplatePackVersion:
            return TemplatePackVersion(
                pack_id=pack.id,
                version=version,
                reference_ids=reference_ids,
                # 空间来自实际引用行，不能读「此刻的默认值」：那会把 dual-read
                # 期间已有的 v1 草稿在切换后发布成一份假冒的 v2 冻结版本。
                reference_ids_space=reference_space,
                bindings=bindings,
                required_roles=list(required),
                supported_scopes=list(scopes),
                features=pack.features,
                instructions=pack.instructions,
                wardrobe_mode=pack.wardrobe_mode,
                published_at=datetime.now(UTC),
            )

        pack.version = await self.repo.create_version(pack.id, pack.version + 1, build)
        pack.status = "published"
        pack.published_at = datetime.now(UTC)
        await self.repo.save(pack)
        await self.session.commit()
        return await self.managed(pack)

    async def disable(self, pack_id: str, owner: str, revision: int) -> PackManaged:
        pack = await self.repo.get_or_404(pack_id, owner=owner)
        if pack.status == "disabled" and pack.revision == revision + 1:
            return await self.managed(pack)
        if pack.revision != revision:
            raise Conflict("发布状态已变化，请刷新")
        pack.status = "disabled"
        pack.revision = revision + 1
        await self.repo.save(pack)
        await self.session.commit()
        return await self.managed(pack)

    # ------------------------------------------------------------------ #
    # 校验
    # ------------------------------------------------------------------ #
    @staticmethod
    def _reference_space(refs: Sequence[TemplatePackReference]) -> str:
        """一次发布只能冻结一个 id 空间；空草稿沿用当前写入空间。"""
        spaces = {ref.asset_id_space for ref in refs}
        if len(spaces) > 1:
            raise BadRequest("同一封装不能混用不同空间的素材")
        return next(iter(spaces), CURRENT_ASSET_ID_SPACE)

    @staticmethod
    def _draft_refs(payload: PackDraftIn) -> _DraftRefs:
        """按**字段名**判空间，两套字段互斥。

        不写「哪个优先」的规则：两套 id 形状相同，允许同时填之后再来一次
        「优先取 v2」，就等于让一次手滑的请求悄悄换成另一侧的图。
        """
        legacy = list(payload.reference_ids)
        library = list(payload.reference_library_ids)
        if legacy and library:
            raise BadRequest("同一封装不能同时使用 referenceIds 与 referenceLibraryIds")
        if library:
            if payload.cover_reference_id:
                raise BadRequest("referenceLibraryIds 只能配 coverLibraryId")
            return _DraftRefs(
                ids=library, space=ASSET_ID_SPACE_V2, cover=payload.cover_library_id
            )
        if payload.cover_library_id:
            raise BadRequest("coverLibraryId 只能配 referenceLibraryIds")
        # 旧字段跟随「当前写入空间」：这是迁移开关（翻转常量即整体切到 v2），
        # 不是兜底。落库后每行都带自己的空间，不会再被翻转重新解释。
        return _DraftRefs(
            ids=legacy, space=CURRENT_ASSET_ID_SPACE, cover=payload.cover_reference_id
        )

    def _check_roles(self, payload: PackDraftIn, reference_count: int) -> None:
        """用途写错不该被静默改成基准用途：那会让封装者以为设置生效了。"""
        allowed = roles_for(payload.kind)
        unknown = sorted({b.role for b in payload.bindings if b.role and b.role not in allowed})
        if unknown:
            raise BadRequest(f"图片用途无效：{'、'.join(unknown)}")
        if payload.bindings and len(payload.bindings) != reference_count:
            raise BadRequest("图片用途需与参考图一一对应，请重新勾选")
        extra_required = [role for role in payload.required_roles if role not in allowed]
        if extra_required:
            raise BadRequest(f"必备用途无效：{'、'.join(sorted(set(extra_required)))}")
        if payload.kind != "product" and payload.supported_scopes:
            raise BadRequest("只有产品替换需要设置替换范围")
        bad_scopes = [scope for scope in payload.supported_scopes if scope not in PACK_SCOPES]
        if bad_scopes:
            raise BadRequest(f"替换范围无效：{'、'.join(bad_scopes)}")

    async def _check_references(self, draft: _DraftRefs, kind: str, owner: str) -> None:
        """v1 保留本人/同类/未删除；v2 只认可读的已采纳图片。

        v2 没有 `category`，人物/产品/场景由封装的槽位绑定表达。小组与封装成员
        能否读由 `readable_ids()` 统一回答，这里不反推标签或重写共享规则。
        """
        if not draft.ids:
            return
        space = draft.space
        assets = await resolve_assets(
            self.session,
            [(asset_id, space) for asset_id in draft.ids],
            actor=CurrentUser(id=owner, name=owner),
        )
        for asset_id in draft.ids:
            if space == ASSET_ID_SPACE_V1:
                row = assets.v1.get(asset_id)
                if (
                    row is None
                    or row.deleted_at
                    or row.owner_id != owner
                    or row.category != kind
                ):
                    raise BadRequest("只能封装本人同类参考图片")
            elif space == ASSET_ID_SPACE_V2:
                row_v2 = assets.v2.get(asset_id)
                if (
                    row_v2 is None
                    or row_v2.state != LibraryAssetState.ACTIVE
                    or row_v2.kind != LibraryAssetKind.IMAGE
                    or asset_id not in assets.readable_v2
                ):
                    raise BadRequest("只能封装可读的已采纳参考图片")
            else:
                raise BadRequest("素材空间无效")

    def gaps(self, pack: TemplatePack, refs: Sequence[TemplatePackReference]) -> list[str]:
        """发布前还差什么；顺序就是封装者该补的顺序。"""
        gaps: list[str] = []
        if not pack.name.strip():
            gaps.append("对象名称")
        if not 1 <= len(refs) <= 10:
            gaps.append("1–10 张参考图")
        present = {ref.role for ref in refs}
        missing = missing_role_labels(pack.kind, list(present), list(pack.required_roles))
        if missing:
            gaps.append("缺少" + "、".join(missing))
        if pack.kind == "product" and not pack.supported_scopes:
            gaps.append("产品替换范围")
        return gaps

    async def _verify_bytes(self, refs: Sequence[TemplatePackReference]) -> None:
        """字节还在不在。

        **不重算 sha256**：素材 key 就是它的摘要（`blobs/ab/abcdef…`），key 存在
        即字节正确。以前每次发布都要把包内所有图片全读一遍，再用
        `(path, mtime, size)` 缓存猜「文件是不是被换过」——那是可变存储的补丁。
        """
        assets = await resolve_assets(
            self.session, [(ref.asset_id, ref.asset_id_space) for ref in refs]
        )
        for ref in refs:
            if ref.asset_id_space == ASSET_ID_SPACE_V1:
                row = assets.v1.get(ref.asset_id)
                present = row is not None and self.blobs.exists(row.path)
            elif ref.asset_id_space == ASSET_ID_SPACE_V2:
                row_v2 = assets.v2.get(ref.asset_id)
                present = (
                    row_v2 is not None
                    and bool(row_v2.storage_key)
                    and await get_storage().exists(row_v2.storage_key)
                )
            else:
                present = False
            if not present:
                raise BadRequest("素材字节缺失，请重新登记")

    # ------------------------------------------------------------------ #
    # 出参
    # ------------------------------------------------------------------ #
    def _shape(
        self, pack: TemplatePack, refs: Sequence[TemplatePackReference]
    ) -> ReplacementPack:
        reference_ids = [ref.asset_id for ref in refs]
        bindings = aligned_bindings(
            pack.kind,
            reference_ids,
            [{"role": ref.role, "description": ref.description} for ref in refs],
        )
        return ReplacementPack(
            id=pack.id,
            kind=pack.kind,  # type: ignore[arg-type]
            name=pack.name,
            reference_ids=reference_ids,
            reference_id_space=refs[0].asset_id_space if refs else "",
            cover_reference_id=(
                pack.cover_asset_id
                if pack.cover_asset_id in reference_ids
                else (reference_ids[0] if reference_ids else None)
            ),
            wardrobe_mode=pack.wardrobe_mode,  # type: ignore[arg-type]
            features=pack.features,
            instructions=pack.instructions,
            version=pack.version,
            bindings=bindings,
            required_roles=aligned_required_roles(pack.kind, list(pack.required_roles)),
            supported_scopes=list(pack.supported_scopes),
        )

    async def _public(
        self, pack: TemplatePack, refs: Sequence[TemplatePackReference]
    ) -> ReplacementPack:
        """对外可选项：附预览地址，并如实标注「能不能用」。

        已发布但内容不全的包（字节丢了、用途缺了）标 `available=False`，仍然出现
        在列表里但不可选。直接隐藏会让运营以为「发布没生效」，如实标注至少能解释。
        """
        shaped = self._shape(pack, refs)
        available = True
        try:
            # 素材名查询与字节校验都是「能不能解释这个包」的一环，必须一起降级。
            # 放在 try 外时，DB 抖动或异常 reference_ids 会让整个素材目录 500。
            names = await resolve_names(
                self.session, [(ref.asset_id, ref.asset_id_space) for ref in refs]
            )
            await self._verify_bytes(refs)
        except (AppError, OSError, SQLAlchemyError):
            names = {}
            available = False
        if self.gaps(pack, refs):
            available = False
        urls = [
            f"/api/v1/templates/replacement-packs/{pack.id}/preview/{asset_id}"
            f"?version={pack.version}"
            for asset_id in shaped.reference_ids
        ]
        return shaped.model_copy(
            update={
                "available": available,
                "cover_url": urls[0] if urls else None,
                "preview_urls": urls,
                "reference_names": [names.get(item, "素材不可用")
                                    for item in shaped.reference_ids],
            }
        )

    # ------------------------------------------------------------------ #
    # 预览
    # ------------------------------------------------------------------ #
    async def preview(self, pack_id: str, asset_id: str, version: int) -> Served:
        """包内预览：**由「已发布版本」授权**，不是「当前用户对资产库条目有读权限」。

        v1 读旧素材表 + 归档盘派生预览；v2 直接取库自己的缩略图（`cover_key`），
        没有缩略图就退回原件。两条路都不做资产库授权检查——这份图已经随
        「已发布版本」对外了，再加一道 `readable_ids()` 会让「能看模板、看不了包」
        的矛盾状态出现（共享审批与模板可见范围本来就是两件事）。

        也不把 v2 重定向到 `/library/assets/{id}/thumbnail`：那个端点会做资产库
        授权，非成员拿到的是 403 而不是这张图。
        """
        pack = await self.repo.get_or_404(pack_id)
        frozen = await self.repo.get_version(pack.id, version)
        if frozen is None:
            raise NotFound("发布版本不存在")
        if asset_id not in frozen.reference_ids:
            raise NotFound("包内预览不存在")
        if frozen.reference_ids_space == ASSET_ID_SPACE_V2:
            return await self._library_preview(asset_id)
        if frozen.reference_ids_space != ASSET_ID_SPACE_V1:
            raise NotFound("包内预览暂不可用")
        media = await get_asset_lookup().get_many(self.session, [asset_id])
        row = media.get(asset_id)
        if row is None or row.deleted_at or row.owner_id != pack.owner_id:
            raise NotFound("包内素材不可用")
        if row.preview_path:
            return Served.local(self._materialized(row.preview_path), "image/jpeg")
        # 公共预览不开放原件下载：缺预览就现生成一份，生成失败宁可 404。
        derived = preview_key(row.sha256)
        try:
            created = self.blobs.derive(row.path, derived, kind=row.type)
        except OSError as exc:
            raise NotFound("包内预览不可用，请重新登记素材后再封装") from exc
        if not created:
            raise NotFound("包内预览不可用，请重新登记素材后再封装")
        return Served.local(self._materialized(derived), "image/jpeg")

    async def _library_preview(self, asset_id: str) -> Served:
        """v2 包内预览的字节来源：优先缩略图，其次原件。"""
        ref = (await get_library_lookup().get_many(self.session, [asset_id])).get(asset_id)
        if (
            ref is None
            or ref.state != LibraryAssetState.ACTIVE
            or ref.kind != LibraryAssetKind.IMAGE
        ):
            raise NotFound("包内素材不可用")
        key = ref.cover_key or ref.storage_key
        if not key:
            raise NotFound("包内素材不可用")
        mime = ref.mime_type or "image/jpeg"
        storage = get_storage()
        local = storage.local_path(key)
        if local is not None:
            return Served.local(local, mime)
        if not await storage.exists(key):
            raise NotFound("包内素材不可用")
        # 远端驱动不把字节搬进进程：签名地址交给浏览器直接取（`<img>` 不走 CORS）。
        return Served.remote(await storage.presigned_download_url(key), mime)

    def _materialized(self, key: str) -> Path:
        """交出可读路径。退出上下文是安全的：驱动承诺物化缓存不会被立即回收。"""
        with self.blobs.materialize(key) as path:
            return path

    def _same_content(self, pack: TemplatePack, payload: PackDraftIn) -> bool:
        """重发判定：比的是**归一化后**的字段，不是原始入参。

        `bindings` / `required_roles` 会被补齐（空用途落基准角色、必填永远含基准
        角色），直接拿 `payload.bundle()` 与 head 比永远不相等，重试会被误判成
        「草稿已变化」。
        """
        return (
            pack.kind == payload.kind
            and pack.name == payload.name
            and pack.features == payload.features
            and pack.instructions == payload.instructions
            and pack.wardrobe_mode == payload.wardrobe_mode
        )

    def _same_as_version(
        self,
        pack: TemplatePack,
        refs: Sequence[TemplatePackReference],
        frozen: TemplatePackVersion,
        reference_space: str,
    ) -> bool:
        """当前草稿是否与这份已发布版本内容相同。

        用途、必填、范围、说明文字全都要比：只比名称会让「补了一张参考图之后
        再点发布」被当成重发，那次修改就永远发不出去，而界面显示「发布成功」。

        名称不参与比较——版本行里没有 name 列（名称在 head 上，改名不需要新版本，
        它也不改变生成行为）。
        """
        bindings = [{"role": ref.role, "description": ref.description} for ref in refs]
        return (
            frozen.reference_ids_space == reference_space
            and list(frozen.reference_ids) == [ref.asset_id for ref in refs]
            and list(frozen.bindings) == bindings
            and list(frozen.required_roles) == list(pack.required_roles)
            and list(frozen.supported_scopes) == list(pack.supported_scopes)
            and frozen.features == pack.features
            and frozen.instructions == pack.instructions
            and frozen.wardrobe_mode == pack.wardrobe_mode
        )
