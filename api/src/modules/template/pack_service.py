"""专员草稿、不可变发布版本及受控公共预览。"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from core.exceptions import AppError, BadRequest, Conflict, NotFound
from core.media import MediaPack
from core.pack_roles import PACK_SCOPES, missing_role_labels, roles_for
from infra.archive import BlobStore, preview_key
from infra.media_catalog import MediaCatalog, catalog_lease, safe_id
from modules.template.replacement_schema import (
    PackDraftIn,
    PackManaged,
    PackManagedList,
    PackPublicList,
    ReplacementPack,
)

# 重试比对时忽略的字段：它们只描述「第几次」，不描述「改了什么」。
_VOLATILE = {"id", "owner_id", "version", "revision", "status", "published_at"}

# 抢版号的重试上限。正常只有一个赢家、零重试；有重试说明此刻正有并发发布，
# 连着被抢 5 次基本只可能是有人在刷接口，直接让它刷新重来。
_MAX_VERSION_RETRIES = 5


def _same_content(previous: MediaPack, content: dict[str, object]) -> bool:
    """比较归一化后的字段，而不是原始入参。

    `bindings` / `required_roles` 会被模型补齐（空用途落基准角色、必填永远含基准角色），
    直接拿 `payload.model_dump()` 跟存储对象比永远不相等，重试会被误判成「草稿已变化」。
    """
    candidate = MediaPack(id=previous.id, owner_id=previous.owner_id, **content)
    candidate.cover_reference_id = candidate.cover_reference_id or next(
        iter(candidate.reference_ids), None
    )
    return candidate.model_dump(exclude=_VOLATILE) == previous.model_dump(exclude=_VOLATILE)


class PackService:
    def __init__(self) -> None:
        self.catalog = MediaCatalog()
        self.storage = self.catalog.storage
        self.blobs = BlobStore(self.storage)

    def managed(self, pack: MediaPack) -> PackManaged:
        """带缺项清单返回：列表页据此提示「还差什么」，不用前端再猜一遍规则。"""
        return PackManaged.model_validate({**pack.model_dump(), "gaps": self.gaps(pack)})

    def drafts(self, owner: str) -> PackManagedList:
        rows: dict[str, MediaPack] = {}
        for folder in ("packs", "pack_drafts"):
            # 枚举走存储协议（`list_keys`），不在 `root()` 上 glob：
            # 后者等于要求「有本地根目录」，切远端存储后封装列表整块不可用（T17）。
            for key in self.storage.list_keys(f"catalog/{folder}", suffix=".json"):
                pack = MediaPack.model_validate(self.storage.read(key))
                if pack.owner_id == owner:
                    # 老包没有显式主图时沿用第一张，发布时仍验证完整性。
                    pack.cover_reference_id = pack.cover_reference_id or next(
                        iter(pack.reference_ids), None
                    )
                    rows[pack.id] = pack
        return PackManagedList(items=[self.managed(p) for p in rows.values()])

    def draft(self, uid: str, owner: str) -> MediaPack:
        key = f"catalog/pack_drafts/{safe_id(uid)}.json"
        pack = (
            MediaPack.model_validate(self.storage.read(key))
            if self.storage.exists(key)
            else self.catalog.pack(uid)
        )
        if pack.owner_id != owner:
            raise NotFound("封装草稿不存在")
        pack.cover_reference_id = pack.cover_reference_id or next(iter(pack.reference_ids), None)
        return pack

    def save(self, payload: PackDraftIn, owner: str) -> PackManaged:
        uid = payload.request_id.hex
        self._check_roles(payload)
        with catalog_lease(self.storage):
            draft_key = f"catalog/pack_drafts/{uid}.json"
            head_key = f"catalog/packs/{uid}.json"
            exists = self.storage.exists(draft_key) or self.storage.exists(head_key)
            previous = self.draft(uid, owner) if exists else None
            content = payload.model_dump(exclude={"request_id", "expected_revision"})
            if previous:
                if previous.revision == payload.expected_revision + 1 and _same_content(
                    previous, content
                ):
                    return self.managed(previous)
                if previous.revision != payload.expected_revision:
                    raise Conflict("草稿已变化，请刷新后核对")
            elif payload.expected_revision:
                raise Conflict("草稿不存在，请刷新")
            for ref in payload.reference_ids:
                media = self.catalog.media(ref)
                if media.owner_id != owner or media.deleted_at or media.category != payload.kind:
                    raise BadRequest("只能封装本人同类参考图片")
            pack = MediaPack(
                id=uid,
                owner_id=owner,
                **content,
                version=previous.version if previous else 0,
                revision=payload.expected_revision + 1,
            )
            self.storage.write(draft_key, pack.model_dump(mode="json", by_alias=True))
            return self.managed(pack)

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

    def gaps(self, pack: MediaPack) -> list[str]:
        """发布前还差什么；顺序就是封装者该补的顺序。"""
        gaps: list[str] = []
        if not pack.name.strip():
            gaps.append("对象名称")
        if not 1 <= len(pack.reference_ids) <= 10:
            gaps.append("1–10 张参考图")
        present = {binding.role for binding in pack.bindings}
        missing = missing_role_labels(pack.kind, list(present), pack.required_roles)
        if missing:
            gaps.append("缺少" + "、".join(missing))
        if pack.kind == "product" and not pack.supported_scopes:
            gaps.append("产品替换范围")
        return gaps

    def verify(self, pack: MediaPack) -> None:
        """字节还在不在。

        **不再重算 sha256**：素材 key 就是它的摘要（`blobs/ab/abcdef…`），
        key 存在即字节正确。原先这里每次发布都要把包内所有图片全读一遍，
        再用 `(path, mtime, size)` 缓存猜"文件是不是被换过"——那是可变存储的
        补丁。内容寻址之后，"字节变了"这件事在物理上不可能发生。
        """
        for media in self.catalog.references(pack):
            if not self.blobs.exists(media.path):
                raise BadRequest("素材字节缺失，请重新登记")

    def publish(self, uid: str, owner: str, revision: int) -> PackManaged:
        with catalog_lease(self.storage):
            draft = self.draft(uid, owner)
            if draft.revision != revision:
                raise Conflict("草稿已变化，请刷新后重新发布")
            head_key = f"catalog/packs/{safe_id(uid)}.json"
            head = self.catalog.pack(uid) if self.storage.exists(head_key) else None
            if head and head.revision == revision and head.status == "published":
                return self.managed(head)
            gaps = self.gaps(draft)
            if gaps:
                raise BadRequest("发布前请补齐：" + "；".join(gaps))
            self.verify(draft)
            draft.status = "published"
            draft.published_at = datetime.now(UTC)
            draft.version = self._claim_version(uid, (head.version if head else 0) + 1, draft)
            # head 是投影：并发下另一个实例可能已经写到更大的版号，不能用旧值盖回去
            self._write_head(head_key, draft)
            self.storage.write(
                f"catalog/pack_drafts/{uid}.json", draft.model_dump(mode="json", by_alias=True)
            )
            return self.managed(draft)

    def _claim_version(self, uid: str, start: int, draft: MediaPack) -> int:
        """抢一个没人占用的版号：版本文件的**原子创建**就是 CAS。

        为什么不是「读 head → 加一 → 写」：`catalog_lease` 在远端驱动下退化成
        本机锁，多实例时两个人都读到 `v2`，然后都写 `v3.json`——后者覆盖前者，
        留下「两份内容不同却同版本号」的包。已发布的版本按定义不可变，
        覆盖等于篡改（T20）。
        """
        version = start
        for _ in range(_MAX_VERSION_RETRIES):
            draft.version = version
            if self.storage.create(
                f"catalog/pack_versions/{safe_id(uid)}/{version}.json",
                draft.model_dump(mode="json", by_alias=True),
            ):
                return version
            version += 1
        raise Conflict("发布冲突过于频繁，请稍后刷新重试")

    def _write_head(self, head_key: str, draft: MediaPack) -> None:
        """写 head，但**只向前推进**：已有更大的版号时不覆盖。"""
        if self.storage.exists(head_key):
            current = MediaPack.model_validate(self.storage.read(head_key))
            if current.version > draft.version:
                return
        self.storage.write(head_key, draft.model_dump(mode="json", by_alias=True))

    def disable(self, uid: str, owner: str, revision: int) -> PackManaged:
        with catalog_lease(self.storage):
            head = self.catalog.pack(uid)
            if head.owner_id != owner:
                raise NotFound("发布对象不存在")
            if head.status == "disabled" and head.revision == revision + 1:
                return self.managed(head)
            draft = self.draft(uid, owner)
            if draft.revision != revision:
                raise Conflict("发布状态已变化，请刷新")
            head.status = "disabled"
            head.revision = revision + 1
            self.storage.write(
                f"catalog/packs/{safe_id(uid)}.json", head.model_dump(mode="json", by_alias=True)
            )
            draft.status = "disabled"
            draft.revision = head.revision
            self.storage.write(
                f"catalog/pack_drafts/{uid}.json", draft.model_dump(mode="json", by_alias=True)
            )
            return self.managed(draft)

    def public(self, pack: MediaPack) -> ReplacementPack:
        available = True
        try:
            self.verify(pack)
        except (AppError, OSError):
            available = False
        # 已发布但内容不全的包（旧版本发布过的历史数据）不放进可选项：
        # 否则用户点得进去，却卡在范围或提示词阶段，问题被推迟到更难解释的地方。
        if self.gaps(pack):
            available = False
        urls = [
            f"/api/v1/templates/replacement-packs/{pack.id}/preview/{uid}?version={pack.version}"
            for uid in pack.reference_ids
        ]
        cover = next(
            (
                url
                for uid, url in zip(pack.reference_ids, urls, strict=True)
                if uid == pack.cover_reference_id
            ),
            None,
        )
        return ReplacementPack.model_validate(
            {**pack.model_dump(), "available": available, "cover_url": cover, "preview_urls": urls}
        )

    def available(self) -> list[ReplacementPack]:
        rows = [
            MediaPack.model_validate(self.storage.read(key))
            for key in self.storage.list_keys("catalog/packs", suffix=".json")
        ]
        return [self.public(p) for p in rows if p.status == "published" and p.version > 0]

    def search(self, kind: str, keyword: str, page: int, page_size: int) -> PackPublicList:
        if kind not in ("all", "character", "product", "scene"):
            raise BadRequest("对象分类无效")
        rows = [
            MediaPack.model_validate(self.storage.read(key))
            for key in self.storage.list_keys("catalog/packs", suffix=".json")
        ]
        rows = [
            p
            for p in rows
            if p.status == "published"
            and p.version > 0
            and (kind == "all" or p.kind == kind)
            and keyword.strip().casefold() in p.name.casefold()
        ]
        rows.sort(
            key=lambda p: (p.published_at or datetime.min.replace(tzinfo=UTC), p.id), reverse=True
        )
        return PackPublicList(
            items=[self.public(p) for p in rows[(page - 1) * page_size : page * page_size]],
            total=len(rows),
            page=page,
            page_size=page_size,
        )

    def preview(self, uid: str, asset_id: str, version: int) -> tuple[Path, str]:
        pack = self.catalog.published(uid, version)
        if asset_id not in pack.reference_ids:
            raise NotFound("包内预览不存在")
        media = self.catalog.media(asset_id)
        if media.deleted_at or media.owner_id != pack.owner_id or media.category != pack.kind:
            raise NotFound("包内素材不可用")
        if media.preview_path:
            return self._materialized(media.preview_path), "image/jpeg"
        # 公共预览不开放原件下载：缺预览就现生成一份，生成失败宁可 404。
        derived = preview_key(media.sha256)
        try:
            created = self.blobs.derive(media.path, derived, kind=media.type)
        except OSError as exc:
            raise NotFound("包内预览不可用，请重新登记素材后再封装") from exc
        if not created:
            raise NotFound("包内预览不可用，请重新登记素材后再封装")
        return self._materialized(derived), "image/jpeg"

    def _materialized(self, key: str) -> Path:
        """交出可读路径。退出上下文是安全的：驱动承诺物化缓存不会被立即回收。"""
        with self.blobs.materialize(key) as path:
            return path
