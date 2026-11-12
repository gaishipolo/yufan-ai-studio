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
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from core.exceptions import AppError, BadRequest, Conflict, NotFound
from core.logging import get_logger
from core.ports.media import get_asset_lookup
from infra.archive import BlobStore, get_archive_storage, preview_key
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
        self._check_roles(payload)
        await self._check_references(payload, owner)

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

        bindings = aligned_bindings(payload.kind, payload.reference_ids, payload.bindings)
        required = aligned_required_roles(payload.kind, payload.required_roles)
        await self.repo.replace_references(
            pack.id,
            [
                (asset_id, position, binding.role, binding.description)
                for position, (asset_id, binding) in enumerate(
                    zip(payload.reference_ids, bindings, strict=True)
                )
            ],
        )
        pack.cover_asset_id = payload.cover_reference_id
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
            and self._same_as_version(pack, refs, frozen)
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
    def _check_roles(self, payload: PackDraftIn) -> None:
        """用途写错不该被静默改成基准用途：那会让封装者以为设置生效了。"""
        allowed = roles_for(payload.kind)
        unknown = sorted({b.role for b in payload.bindings if b.role and b.role not in allowed})
        if unknown:
            raise BadRequest(f"图片用途无效：{'、'.join(unknown)}")
        if payload.bindings and len(payload.bindings) != len(payload.reference_ids):
            raise BadRequest("图片用途需与参考图一一对应，请重新勾选")
        extra_required = [role for role in payload.required_roles if role not in allowed]
        if extra_required:
            raise BadRequest(f"必备用途无效：{'、'.join(sorted(set(extra_required)))}")
        if payload.kind != "product" and payload.supported_scopes:
            raise BadRequest("只有产品替换需要设置替换范围")
        bad_scopes = [scope for scope in payload.supported_scopes if scope not in PACK_SCOPES]
        if bad_scopes:
            raise BadRequest(f"替换范围无效：{'、'.join(bad_scopes)}")

    async def _check_references(self, payload: PackDraftIn, owner: str) -> None:
        """参考图必须是本人的、同类的、未删除的图片。

        走 `AssetLookup` 注入口而不是直连素材表：模板不认识 `assets` 的 schema，
        这是 `EXTENDING.md` §5.1 的红线。查不到实现时抛 503，不静默放行。
        """
        if not payload.reference_ids:
            return
        media = await get_asset_lookup().get_many(self.session, payload.reference_ids)
        for asset_id in payload.reference_ids:
            row = media.get(asset_id)
            if (
                row is None
                or row.deleted_at
                or row.owner_id != owner
                or row.category != payload.kind
            ):
                raise BadRequest("只能封装本人同类参考图片")

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
        media = await get_asset_lookup().get_many(
            self.session, [ref.asset_id for ref in refs]
        )
        for ref in refs:
            row = media.get(ref.asset_id)
            if row is None or not self.blobs.exists(row.path):
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
            names = await get_asset_lookup().get_many(self.session, shaped.reference_ids)
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
                "reference_names": [names[item].name if item in names else "素材不可用"
                                    for item in shaped.reference_ids],
            }
        )

    # ------------------------------------------------------------------ #
    # 预览
    # ------------------------------------------------------------------ #
    async def preview(self, pack_id: str, asset_id: str, version: int) -> tuple[Path, str]:
        """返回 `(可读路径, content-type)`。缺预览就现生成一份，生成失败宁可 404。"""
        pack = await self.repo.get_or_404(pack_id)
        frozen = await self.repo.get_version(pack.id, version)
        if frozen is None:
            raise NotFound("发布版本不存在")
        if asset_id not in frozen.reference_ids:
            raise NotFound("包内预览不存在")
        media = await get_asset_lookup().get_many(self.session, [asset_id])
        row = media.get(asset_id)
        if row is None or row.deleted_at or row.owner_id != pack.owner_id:
            raise NotFound("包内素材不可用")
        if row.preview_path:
            return self._materialized(row.preview_path), "image/jpeg"
        # 公共预览不开放原件下载：缺预览就现生成一份，生成失败宁可 404。
        derived = preview_key(row.sha256)
        try:
            created = self.blobs.derive(row.path, derived, kind=row.type)
        except OSError as exc:
            raise NotFound("包内预览不可用，请重新登记素材后再封装") from exc
        if not created:
            raise NotFound("包内预览不可用，请重新登记素材后再封装")
        return self._materialized(derived), "image/jpeg"

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
        self, pack: TemplatePack, refs: Sequence[TemplatePackReference], frozen: TemplatePackVersion
    ) -> bool:
        """当前草稿是否与这份已发布版本内容相同。

        用途、必填、范围、说明文字全都要比：只比名称会让「补了一张参考图之后
        再点发布」被当成重发，那次修改就永远发不出去，而界面显示「发布成功」。

        名称不参与比较——版本行里没有 name 列（名称在 head 上，改名不需要新版本，
        它也不改变生成行为）。
        """
        bindings = [{"role": ref.role, "description": ref.description} for ref in refs]
        return (
            list(frozen.reference_ids) == [ref.asset_id for ref in refs]
            and list(frozen.bindings) == bindings
            and list(frozen.required_roles) == list(pack.required_roles)
            and list(frozen.supported_scopes) == list(pack.supported_scopes)
            and frozen.features == pack.features
            and frozen.instructions == pack.instructions
            and frozen.wardrobe_mode == pack.wardrobe_mode
        )
