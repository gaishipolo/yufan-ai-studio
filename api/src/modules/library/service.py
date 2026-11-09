"""资产库 v2 服务（M2：三层可见集 + 改名换标签 + 取件）。

拆分边界（M2 之后）：

- 本文件：**读**（列表 / 分面 / 详情 / 取件）与**上传**、改名换标签；
- `sharing.py`：共享 / 撤销审批、组长映射；
- `lifecycle.py`：草稿与回收站状态机、删除矩阵（含 3.4.1 窄口）；
- `tags.py`：标签 CRUD 与资产打标；
- `bulk.py`：批量写，逐条复用上面三个的方法。

不把它们塞进一个类，是因为「删除矩阵」与「共享审批」各自都有并发与版本校验，
合在一起只会让 review 时看不出「这一行属于哪条规则」。

## 三条不能省的规则（M1 起沿用）

1. **元数据探测在落库之前**：损坏的文件不能先入库再报错。
2. **`sha256` 是内容寻址的 key**：秒传 = 同 owner + 同 sha + 同 kind 且未删除。
3. **幂等键 `source_key` 先查**：生成侧轮询会重复回调，第二次必须返回同一行。
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import tempfile
from collections.abc import AsyncIterator, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from core.context import CurrentUser
from core.exceptions import NotFound
from core.response import PageParams
from core.serving import Served
from infra.media_probe import EXTENSIONS, MAX_BYTES, create_preview, inspect
from infra.storage.base import ObjectStorage
from modules.library import access
from modules.library.errors import LibraryFileTooLarge, LibraryFileTypeNotAllowed
from modules.library.models import (
    LibraryAsset,
    LibraryAssetGeneration,
    LibraryAssetTag,
    LibraryTag,
)
from modules.library.schema import (
    AssetPatchIn,
    AssetSort,
    LibraryAssetDetailOut,
    LibraryAssetOut,
    LibraryAssetPage,
    LibraryFacets,
    LibraryGenerationOut,
    LibraryGroupRef,
    LibraryTab,
    LibraryTagOut,
    UploadKind,
)
from modules.library.tags import set_asset_tags

__all__ = ["LibraryService"]

logger = logging.getLogger(__name__)

#: 主字节前缀。与旧归档盘（`blobs/`）彻底分开：删 v2 模块不会误删旧库字节，
#: 反过来旧库的 `prune-blobs` 也扫不到这里。
BLOB_PREFIX = "library/blobs"
PREVIEW_PREFIX = "library/previews"

_STATES = {"draft", "active", "trash"}


def _digest_and_size(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            size += len(chunk)
            digest.update(chunk)
    return digest.hexdigest(), size


def _kind_of(mime: str) -> str:
    if mime.startswith("video/"):
        return "video"
    return "audio" if mime.startswith("audio/") else "image"


def blob_key(digest: str, extension: str) -> str:
    return f"{BLOB_PREFIX}/{digest[:2]}/{digest}{extension}"


def preview_key(digest: str) -> str:
    return f"{PREVIEW_PREFIX}/{digest[:2]}/{digest}.jpg"


class LibraryService:
    def __init__(self, session: AsyncSession, storage: ObjectStorage) -> None:
        self.session = session
        self.storage = storage

    # ------------------------------------------------------------------ 读

    async def list_assets(
        self,
        actor: CurrentUser,
        params: PageParams,
        *,
        library: LibraryTab = LibraryTab.ALL,
        state: str = "active",
        kind: str | None = None,
        tag_ids: list[str] | None = None,
        keyword: str = "",
        sort: AssetSort = AssetSort.CREATED_DESC,
    ) -> LibraryAssetPage:
        """列表 + 分面。

        可见集按 `library` 与 `state` 两组条件拼：`state=draft/trash` 时**忽略**
        `library`（它们不在任何一个可见范围内，只属于本人），避免出现
        「草稿箱 + 团队」这种拼起来的空集让人以为权限出了问题。
        """
        if state not in _STATES:
            state = "active"
        groups = await access.all_groups(self.session, actor)

        query = select(LibraryAsset).where(LibraryAsset.state == state)
        if state == "active":
            query = query.where(
                LibraryAsset.deleted_at.is_(None),
                access.tab_clause(actor, groups, library),
            )
        elif state == "draft":
            # 代审范围：持 draft:review 的人能看到所有人的草稿；否则只看自己的
            if not actor.has(access.PERM_DRAFT_REVIEW):
                query = query.where(LibraryAsset.owner_id == actor.id)
        else:
            if not actor.has(access.PERM_DELETE_ANY):
                query = query.where(LibraryAsset.owner_id == actor.id)

        if kind:
            query = query.where(LibraryAsset.kind == kind)
        if keyword:
            query = query.where(LibraryAsset.name.ilike(f"%{keyword}%"))
        if tag_ids:
            query = query.where(
                LibraryAsset.id.in_(
                    select(LibraryAssetTag.asset_id).where(LibraryAssetTag.tag_id.in_(tag_ids))
                )
            )

        total = await self.session.scalar(select(func.count()).select_from(query.subquery()))
        order = self._order(sort)
        rows = (
            await self.session.scalars(
                query.order_by(*order).offset(params.offset).limit(params.limit)
            )
        ).all()
        page_ids = [row.id for row in rows]
        # 一次查完这一页的归属与组名，不按行循环（24 行就是 24 次往返）
        group_map = await access.asset_group_ids(self.session, page_ids)
        owner_map = await access.owner_names(
            self.session, [row.owner_id for row in rows]
        )
        return LibraryAssetPage(
            items=[
                self._to_out(
                    row,
                    owner_name=owner_map.get(row.owner_id),
                    group_ids=group_map.get(row.id, []),
                )
                for row in rows
            ],
            total=int(total or 0),
            page=params.page,
            page_size=params.page_size,
            facets=await self._facets(actor, groups),
        )

    async def facets(self, actor: CurrentUser) -> LibraryFacets:
        return await self._facets(actor, await access.all_groups(self.session, actor))

    async def detail(
        self, asset_id: str, actor: CurrentUser, *, require_visible: bool = True
    ) -> LibraryAssetDetailOut:
        """详情。

        `require_visible=False` 只给「刚做完授权动作」的调用方用（草稿代审、
        驳回、恢复）：动作本身已经判过权限，但动作之后的状态可能让操作人
        看不见这条资产——审核人采纳草稿后它变回「别人的个人库资产」，
        再走一遍可见性判定就会 404，把一次成功的审核报成失败。
        """
        row = (
            await access.load_visible(self.session, asset_id, actor)
            if require_visible
            else await access.load_asset(self.session, asset_id)
        )
        tags = (
            await self.session.scalars(
                select(LibraryTag)
                .join(LibraryAssetTag, LibraryAssetTag.tag_id == LibraryTag.id)
                .where(LibraryAssetTag.asset_id == row.id)
                .order_by(LibraryTag.name)
            )
        ).all()
        group_ids = await access.asset_group_ids(self.session, [row.id])
        names = await access.group_names_for(self.session, group_ids.get(row.id, []))
        owner_map = await access.owner_names(self.session, [row.owner_id])
        generation = await self.session.get(LibraryAssetGeneration, row.id)
        return LibraryAssetDetailOut(
            **self._to_out(
                row,
                owner_name=owner_map.get(row.owner_id),
                group_ids=group_ids.get(row.id, []),
            ).model_dump(by_alias=False),
            tags=[LibraryTagOut(id=tag.id, name=tag.name) for tag in tags],
            groups=[
                LibraryGroupRef(id=group_id, name=names.get(group_id, ""))
                for group_id in group_ids.get(row.id, [])
            ],
            generation=(
                LibraryGenerationOut(
                    source_module=generation.source_module,
                    source_task_id=generation.source_task_id,
                    model=generation.model,
                    model_id=generation.model_id,
                    prompt=generation.prompt,
                    size=generation.size,
                    count=generation.count,
                    duration_sec=generation.duration_sec,
                    aspect_ratio=generation.aspect_ratio,
                    style_preset=generation.style_preset,
                    seed=generation.seed,
                    reference_asset_ids=list(generation.reference_asset_ids or []),
                    pack_id=generation.pack_id,
                    pack_version=generation.pack_version,
                    prompt_spec=dict(generation.prompt_spec or {}),
                    configuration=dict(generation.configuration or {}),
                    external_id=generation.external_id,
                    credits=generation.credits,
                    generated_at=generation.generated_at,
                )
                if generation
                else None
            ),
        )

    async def file(
        self, asset_id: str, actor: CurrentUser, *, preview: bool = False
    ) -> Served:
        row = await access.load_visible(self.session, asset_id, actor)
        if row.state == "trash":
            # 「删除」必须真的拿不到文件，否则回收站只是前端的一个标签页。
            # 恢复后立刻可用，所以这不是永久拒绝。
            raise NotFound("回收站内的资产不提供预览与下载")
        key = row.cover_key if preview else row.storage_key
        if not key:
            raise NotFound("此资产没有缩略图")
        return await self._serve(
            key,
            "image/jpeg" if preview else row.mime_type,
            filename=None if preview else row.name,
        )

    # ------------------------------------------------------------------ 写

    async def patch(
        self, asset_id: str, payload: AssetPatchIn, actor: CurrentUser
    ) -> LibraryAssetDetailOut:
        """改名 / 换标签。**不改可见性**：共享必须走申请单，否则审批就成了摆设。"""
        row = await access.load_visible(self.session, asset_id, actor)
        if row.owner_id != actor.id and not actor.has(access.PERM_DELETE_ANY):
            raise NotFound("资产不存在")
        self._check_version(row, payload.expected_version)
        if payload.name is not None:
            row.name = payload.name
        if payload.tag_ids is not None:
            await set_asset_tags(self.session, row.id, payload.tag_ids)
        row.state_version += 1
        await self.session.commit()
        return await self.detail(asset_id, actor)

    async def upload(
        self,
        chunks: AsyncIterator[bytes],
        filename: str,
        *,
        kind: UploadKind,
        actor: CurrentUser,
        origin: str = "upload",
        source_key: str | None = None,
    ) -> LibraryAssetOut:
        """流式上传。临时文件在 http 请求体内写满即落盘，300MB 不进内存。"""
        extension = Path(filename).suffix.lower()
        mime = EXTENSIONS.get(extension)
        if mime is None or _kind_of(mime) != kind.value:
            raise LibraryFileTypeNotAllowed()
        with tempfile.TemporaryDirectory(prefix="library-upload-") as folder:
            temporary = Path(folder) / f"source{extension}"
            size = await self._receive(temporary, chunks)
            return await self._ingest(
                temporary,
                filename=filename,
                extension=extension,
                mime=mime,
                size=size,
                kind=kind.value,
                actor=actor,
                origin=origin,
                source_key=source_key,
            )

    # ------------------------------------------------------------------ 内部

    async def _receive(self, target: Path, chunks: AsyncIterator[bytes]) -> int:
        total = 0
        stream = await asyncio.to_thread(target.open, "xb")
        try:
            async for chunk in chunks:
                if not chunk:
                    continue
                total += len(chunk)
                if total > MAX_BYTES:
                    raise LibraryFileTooLarge(details={"maxBytes": MAX_BYTES})
                await asyncio.to_thread(stream.write, chunk)
        finally:
            await asyncio.to_thread(stream.close)
        if total == 0:
            raise LibraryFileTypeNotAllowed("文件不能为空")
        return total

    async def _ingest(
        self,
        path: Path,
        *,
        filename: str,
        extension: str,
        mime: str,
        size: int,
        kind: str,
        actor: CurrentUser,
        origin: str,
        source_key: str | None,
    ) -> LibraryAssetOut:
        info = await asyncio.to_thread(inspect, path, extension)
        digest, measured = await asyncio.to_thread(_digest_and_size, path)
        if measured != size:
            # 写盘与读回不一致只可能是并发截断/磁盘故障；宁可拒绝，也别落一条
            # 「大小字段与字节不符」的行——它会让配额、下载包大小全部失真。
            raise LibraryFileTypeNotAllowed("文件在写入过程中发生变化，请重试")

        if source_key:
            existing = await self._by_source_key(source_key)
            if existing is not None:
                return self._to_out(existing)
        duplicate = await self._find_duplicate(actor.id, digest, kind)
        if duplicate is not None:
            return self._to_out(duplicate)

        key = blob_key(digest, extension)
        await self.storage.put_file(key, path, mime)
        cover_key = ""
        if kind != "audio":
            cover_key = await self._ensure_preview(path, digest, kind)

        row = LibraryAsset(
            name=Path(filename).name[:200] or f"{kind}{extension}",
            kind=kind,
            origin=origin,
            mime_type=mime,
            size_bytes=size,
            sha256=digest,
            storage_key=key,
            cover_key=cover_key,
            width=int(info["width"]),
            height=int(info["height"]),
            duration=float(info["duration"]),
            has_audio=bool(info["hasAudio"]),
            owner_id=actor.id,
            visibility="personal",
            state="active",
            state_version=1,
            source_key=source_key or None,
            created_at=datetime.now(UTC),
        )
        try:
            self.session.add(row)
            await self.session.commit()
        except IntegrityError:
            # 并发上传同一份文件 / 同一个 sourceKey：唯一索引兜底。回滚后按同样
            # 口径再查一次——索引拦下了、这里又查不到，说明口径不一致，那就把
            # IntegrityError 原样抛出去，别伪装成成功。
            await self.session.rollback()
            existing = await self._find_duplicate(actor.id, digest, kind)
            if existing is None and source_key:
                existing = await self._by_source_key(source_key)
            if existing is None:
                raise
            return self._to_out(existing)
        return self._to_out(row)

    async def _ensure_preview(self, source: Path, digest: str, kind: str) -> str:
        key = preview_key(digest)
        with tempfile.TemporaryDirectory(prefix="library-cover-") as folder:
            target = Path(folder) / "cover.jpg"
            ok = await asyncio.to_thread(create_preview, source, target, kind)
            if not ok or not target.is_file():
                logger.info("资产没有生成缩略图（音轨或派生失败）：%s", digest[:12])
                return ""
            await self.storage.put_file(key, target, "image/jpeg")
        return key

    async def _serve(self, key: str, mime: str, *, filename: str | None = None) -> Served:
        path = self.storage.local_path(key)
        if path is not None:
            return Served.local(path, mime, filename=filename)
        # 远端驱动不把字节搬进进程：预签名地址由对象存储处理 Range。
        return Served.remote(await self.storage.presigned_download_url(key), mime)

    async def _find_duplicate(self, owner_id: str, digest: str, kind: str) -> LibraryAsset | None:
        return await self.session.scalar(
            select(LibraryAsset).where(
                LibraryAsset.owner_id == owner_id,
                LibraryAsset.sha256 == digest,
                LibraryAsset.kind == kind,
                LibraryAsset.deleted_at.is_(None),
            )
        )

    async def _by_source_key(self, source_key: str) -> LibraryAsset | None:
        return await self.session.scalar(
            select(LibraryAsset).where(LibraryAsset.source_key == source_key)
        )

    async def _facets(
        self, actor: CurrentUser, groups: Sequence[str]
    ) -> LibraryFacets:
        """六个角标。

        前四个按 `visibility` 分组一次算完；草稿与回收站各一条 count。
        管理员（`draft:review` / `delete:any`）看到的角标是全量的——否则
        「待我审核」的入口显示 0，而实际有一堆在等。
        """
        facets = LibraryFacets()
        rows = (
            await self.session.execute(
                select(LibraryAsset.visibility, func.count())
                .where(
                    LibraryAsset.state == "active",
                    LibraryAsset.deleted_at.is_(None),
                    access.active_visible_clause(actor, groups),
                )
                .group_by(LibraryAsset.visibility)
            )
        ).all()
        for visibility, count in rows:
            value = int(count)
            facets.all += value
            if visibility in ("personal", "group", "team"):
                setattr(facets, visibility, getattr(facets, visibility) + value)

        draft_query = select(func.count()).where(LibraryAsset.state == "draft")
        if not actor.has(access.PERM_DRAFT_REVIEW):
            draft_query = draft_query.where(LibraryAsset.owner_id == actor.id)
        facets.drafts = int(await self.session.scalar(draft_query) or 0)

        trash_query = select(func.count()).where(LibraryAsset.state == "trash")
        if not actor.has(access.PERM_DELETE_ANY):
            trash_query = trash_query.where(LibraryAsset.owner_id == actor.id)
        facets.trash = int(await self.session.scalar(trash_query) or 0)
        return facets

    def _order(self, sort: AssetSort) -> tuple[ColumnElement[Any], ColumnElement[Any]]:
        # 排序键必须以主键收尾：created_at 同秒的行（批量导入）否则顺序不稳定，
        # 分页会出现「第 2 页重复第 1 页的最后一条」。
        if sort is AssetSort.CREATED_ASC:
            return (LibraryAsset.created_at.asc(), LibraryAsset.id.asc())
        if sort is AssetSort.NAME:
            return (LibraryAsset.name.asc(), LibraryAsset.id.asc())
        if sort is AssetSort.SIZE_DESC:
            return (LibraryAsset.size_bytes.desc(), LibraryAsset.id.desc())
        return (LibraryAsset.created_at.desc(), LibraryAsset.id.desc())

    def _check_version(self, row: LibraryAsset, expected: int) -> None:
        """乐观锁。`expected=0` 表示不校验——单页操作没必要为了一个数字失败一次。"""
        if expected and row.state_version != expected:
            from core.exceptions import Conflict

            raise Conflict(
                "资产已被修改，请刷新后重试",
                details={"expectedVersion": expected, "stateVersion": row.state_version},
            )

    def _to_out(
        self,
        row: LibraryAsset,
        *,
        owner_name: str | None = None,
        group_ids: Sequence[str] = (),
    ) -> LibraryAssetOut:
        return LibraryAssetOut(
            id=row.id,
            name=row.name,
            kind=row.kind,
            origin=row.origin,
            mime_type=row.mime_type or None,
            size_bytes=row.size_bytes,
            duration_sec=row.duration or None,
            has_audio=row.has_audio,
            owner_id=row.owner_id,
            owner_name=owner_name,
            visibility=row.visibility,
            state=row.state,
            state_version=row.state_version,
            cover_url=self.storage.url_for(row.cover_key) if row.cover_key else None,
            group_ids=list(group_ids),
            trash_reason=row.trash_reason,
            trash_expires_at=row.trash_expires_at,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )
