"""素材服务：上传（去重 + 缩略图）、列表、详情、删除、取文件。

## 几个刻意的决定

**上传走服务端中转，不用预签名直传。** 预签名要把文件类型、大小、摘要校验都搬到
前端或桶策略里，而本地驱动根本不支持预签名（`LocalStorage.presigned_upload_url`
显式抛错）。中转一次换来的是：类型白名单、50MB 上限、sha256 去重、缩略图派生
四件事都在同一条代码路径上，不依赖存储后端。

**去重按 `(owner_id, sha256)`。** 同一账号重复上传同一文件直接返回旧记录，
连对象都不再写一份；跨账号不去重（各人一份，互不影响删除）。

**先写对象、后落行，失败回滚对象。** 反过来会出现「行存在但文件不存在」——
列表能渲染、点开 404，用户以为素材坏了。反过来是「对象存在但没行」，那只是
占空间，清理任务能扫掉。

**本服务只管 `source != 'archive'` 的行。** `assets` 表同时存归档素材
（`source='archive'`，字节在归档盘，见 `archive_service`），但两边的 `storage_key`
是不同的命名空间、取文件端点也不同。这里一律按「归档行不存在」处理：否则列表会
混进一批 `fileUrl` 必然 404 的卡片，删除也会去错存储里删。
"""

from __future__ import annotations

import asyncio
import hashlib
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import Select, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import settings
from core.context import CurrentUser
from core.exceptions import BadRequest
from core.logging import get_logger
from core.ports.assets import ask_asset_references
from core.response import PageParams
from db.repository import BaseRepository
from infra.media_probe import EXTENSIONS, MediaInfo, create_preview, inspect
from infra.storage.base import ObjectStorage, StorageError
from modules.asset.errors import AssetInUse, AssetNotFound, FileTooLarge, FileTypeNotAllowed
from modules.asset.models import Asset
from modules.asset.schema import (
    AssetCounts,
    AssetListOut,
    AssetListQuery,
    AssetOut,
    AssetSource,
)

logger = get_logger(__name__)

# 个人创作素材的上限。归档盘的原片另有一套 300MB 上限：素材库是"随手取用的
# 成品素材"，再大的文件该进归档库，否则列表与备份都会被拖慢。
MAX_ASSET_BYTES = 50 * 1024 * 1024


class AssetService:
    def __init__(self, session: AsyncSession, storage: ObjectStorage) -> None:
        self.session = session
        self.storage = storage
        self.repo = BaseRepository(session, Asset)

    # ------------------------------------------------------------------ 读
    async def list_assets(
        self, actor: CurrentUser, query: AssetListQuery, page: PageParams
    ) -> AssetListOut:
        counts = await self._counts(actor, query)
        stmt = self._scoped_query(actor, query, with_type=True).order_by(
            Asset.created_at.desc(), Asset.id.desc()
        )
        rows, total = await self.repo.paginate(stmt, page)
        return AssetListOut(
            items=[self._to_out(row) for row in rows],
            total=total,
            page=page.page,
            page_size=page.page_size,
            counts=counts,
        )

    async def get_asset(self, actor: CurrentUser, asset_id: str) -> AssetOut:
        return self._to_out(await self._owned(actor, asset_id))

    async def resolve_file(
        self, actor: CurrentUser, asset_id: str, *, cover: bool = False
    ) -> tuple[Path | None, str | None, str]:
        """取文件：返回 `(本地路径, 重定向地址, MIME)`，二选一非空。

        本地驱动给路径（`FileResponse` 支持 Range，视频能拖进度条），远程驱动给
        预签名地址（字节不经过应用进程）。两条路都不把私有素材的直链写进列表接口。
        """
        row = await self._owned(actor, asset_id)
        key = self._file_key(row, cover=cover)
        if not key:
            raise AssetNotFound("此素材没有缩略图")
        mime = "image/jpeg" if cover else row.mime_type

        path = self.storage.local_path(key)
        if path is not None:
            return path, None, mime
        return None, await self.storage.presigned_download_url(key), mime

    # ------------------------------------------------------------------ 写
    async def upload(
        self, actor: CurrentUser, chunks: AsyncIterator[bytes], filename: str
    ) -> AssetOut:
        ext = Path(filename).suffix.lower()
        mime = EXTENSIONS.get(ext)
        if mime is None:
            raise FileTypeNotAllowed()

        temporary = await asyncio.to_thread(self._temporary)
        try:
            total, sha256 = await self._receive(temporary, chunks)
            # 探测放在去重之前：类型伪装（改后缀的可执行文件、扩展名与内容不符的图片）
            # 连"重复上传"的资格都没有，不能因为摘要撞上了就放行。
            info = await asyncio.to_thread(inspect, temporary, ext)

            existing = await self._find_by_digest(actor.id, sha256)
            if existing is not None:
                return self._to_out(existing)

            return await self._store(
                actor=actor,
                source=temporary,
                filename=filename,
                ext=ext,
                mime=mime,
                info=info,
                sha256=sha256,
                size_bytes=total,
            )
        finally:
            await asyncio.to_thread(temporary.unlink, missing_ok=True)

    async def delete_asset(self, actor: CurrentUser, asset_id: str) -> None:
        row = await self._owned(actor, asset_id)

        references = await ask_asset_references([asset_id])
        if references.get(asset_id):
            raise AssetInUse(details={"references": list(references[asset_id])})

        keys = [key for key in (row.storage_key, row.cover_key) if key]
        await self.repo.delete(row)
        await self.session.commit()

        # 先提交再删对象：删对象失败只是留个孤儿（可清理），
        # 反过来则是"文件没了但列表还在"——用户点开 404，比占空间糟得多。
        for key in keys:
            try:
                await self.storage.delete(key)
            except StorageError as exc:  # pragma: no cover - 只有存储后端故障时触发
                logger.warning("素材对象删除失败，留待清理：%s（%s）", key, exc)

    # ------------------------------------------------------------------ 内部
    async def _owned(self, actor: CurrentUser, asset_id: str) -> Asset:
        row = await self.repo.get(asset_id)
        # 归档行（source='archive'）的字节在归档盘，不在对象存储：用本模块的
        # `resolve_file` 去取会拼出 /static/library/... 这种必然 404 的地址。
        # 它们有自己的取文件端点（/assets/archive/{id}/file），这里一律当不存在。
        if row is None or row.owner_id != actor.id or row.source == AssetSource.ARCHIVE:
            raise AssetNotFound()
        return row

    async def _receive(self, target: Path, chunks: AsyncIterator[bytes]) -> tuple[int, str]:
        """把请求体落到临时文件，边写边算摘要（大文件不进内存）。"""
        total = 0
        digest = hashlib.sha256()
        stream = await asyncio.to_thread(target.open, "xb")
        try:
            async for chunk in chunks:
                if not chunk:
                    continue
                total += len(chunk)
                if total > MAX_ASSET_BYTES:
                    raise FileTooLarge()
                digest.update(chunk)
                await asyncio.to_thread(stream.write, chunk)
        finally:
            await asyncio.to_thread(stream.close)

        if total == 0:
            raise BadRequest("文件不能为空")
        return total, digest.hexdigest()

    async def _store(
        self,
        *,
        actor: CurrentUser,
        source: Path,
        filename: str,
        ext: str,
        mime: str,
        info: MediaInfo,
        sha256: str,
        size_bytes: int,
    ) -> AssetOut:
        asset_id = uuid.uuid4().hex
        stamp = datetime.now(tz=UTC)
        kind = mime.split("/", 1)[0]
        key = f"assets/{actor.id}/{stamp:%Y/%m}/{asset_id}{ext}"
        cover_key = ""

        await self.storage.put_file(key, source, mime)
        try:
            cover_key = await self._store_cover(asset_id, kind, source, key)
            row = Asset(
                id=asset_id,
                name=filename[:200],
                type=kind,
                mime_type=mime,
                size_bytes=size_bytes,
                storage_key=key,
                cover_key=cover_key,
                sha256=sha256,
                source=AssetSource.UPLOAD,
                width=info["width"],
                height=info["height"],
                duration=info["duration"],
                has_audio=info["hasAudio"],
                owner_id=actor.id,
            )
            await self.repo.add(row)
            await self.session.commit()
            return self._to_out(row)
        except IntegrityError:
            # 并发上传同一文件：唯一约束是最后一道闸，谁先提交谁留下。
            # 这里不能直接抛 500 —— 对用户来说就是「我又传了一次同一个文件」。
            await self.session.rollback()
            await self._discard(key, cover_key)
            existing = await self._find_by_digest(actor.id, sha256)
            if existing is not None:
                return self._to_out(existing)
            raise
        except Exception:
            await self.session.rollback()
            await self._discard(key, cover_key)
            raise

    async def _store_cover(self, asset_id: str, kind: str, source: Path, key: str) -> str:
        """派生缩略图；失败不算上传失败（列表退回原图/占位，素材本身可用）。"""
        if kind == "audio":
            return ""
        target = await asyncio.to_thread(self._temporary, ".jpg")
        try:
            ok = await asyncio.to_thread(create_preview, source, target, kind)
            if not ok:
                logger.warning("缩略图生成失败，素材仍然入库：%s", asset_id)
                return ""
            cover_key = f"{key.rsplit('.', 1)[0]}.cover.jpg"
            await self.storage.put_file(cover_key, target, "image/jpeg")
            return cover_key
        except (OSError, ValueError) as exc:
            logger.warning("缩略图生成异常，素材仍然入库：%s（%s）", asset_id, exc)
            return ""
        finally:
            await asyncio.to_thread(target.unlink, missing_ok=True)

    async def _discard(self, *keys: str) -> None:
        for key in keys:
            if not key:
                continue
            try:
                await self.storage.delete(key)
            except StorageError:  # pragma: no cover - 清理失败不影响主流程
                logger.warning("回滚已写入的对象失败：%s", key)

    async def _find_by_digest(self, owner_id: str, sha256: str) -> Asset | None:
        stmt = select(Asset).where(
            Asset.owner_id == owner_id,
            Asset.sha256 == sha256,
            Asset.source != AssetSource.ARCHIVE,
        )
        return await self.session.scalar(stmt.limit(1))

    def _temporary(self, suffix: str = ".upload") -> Path:
        folder = Path(settings.storage_local_root) / "incoming"
        folder.mkdir(parents=True, exist_ok=True)
        return folder / (uuid.uuid4().hex + suffix)

    def _scoped_query(
        self, actor: CurrentUser, query: AssetListQuery, *, with_type: bool = False
    ) -> Select[Any]:
        # 归档素材与个人素材同表但不同存储：列表各自限定来源，
        # 否则归档行会带着一个必然 404 的 fileUrl 混进「我的素材」。
        stmt = select(Asset).where(
            Asset.owner_id == actor.id, Asset.source != AssetSource.ARCHIVE
        )
        keyword = query.keyword.strip()
        if keyword:
            stmt = stmt.where(Asset.name.ilike(f"%{keyword}%"))
        if query.category is not None:
            stmt = stmt.where(Asset.category == query.category)
        if with_type and query.type != "all":
            stmt = stmt.where(Asset.type == query.type)
        return stmt

    async def _counts(self, actor: CurrentUser, query: AssetListQuery) -> AssetCounts:
        sub = self._scoped_query(actor, query).subquery()
        rows = (
            await self.session.execute(select(sub.c.type, func.count()).group_by(sub.c.type))
        ).all()
        buckets = {str(kind): int(total) for kind, total in rows}
        return AssetCounts(
            all=sum(buckets.values()),
            image=buckets.get("image", 0),
            video=buckets.get("video", 0),
            audio=buckets.get("audio", 0),
        )

    @staticmethod
    def _file_key(row: Asset, *, cover: bool) -> str:
        """定位要读的对象 key。

        封面优先用托管缩略图；没有缩略图时，图片就用原图当封面（列表不该为了
        一个 40KB 的小图再派生一份），视频/音频则没有封面可言。
        """
        if not cover:
            return row.storage_key
        if row.cover_key:
            return row.cover_key
        return row.storage_key if row.type == "image" else ""

    def _to_out(self, row: Asset) -> AssetOut:
        cover_url = ""
        cover_key = self._file_key(row, cover=True)
        if cover_key:
            # 托管封面的地址**指向本服务**，不指向存储驱动：`url_for` 给的是
            # `/static/<key>`（本地驱动）或桶地址（S3 私有桶），两者浏览器都取不到
            # ——`/static` 根本没有挂载，私有桶直连会被拒。而这个接口带鉴权、
            # 且换驱动不变，客户端只需在请求头带上令牌。
            cover_url = f"{settings.api_prefix}/assets/{row.id}/cover"
        elif row.cover_url:
            cover_url = row.cover_url

        return AssetOut(
            id=row.id,
            name=row.name,
            type=row.type,
            mime_type=row.mime_type,
            size_bytes=row.size_bytes,
            width=row.width,
            height=row.height,
            duration=row.duration,
            has_audio=row.has_audio,
            category=row.category,
            source=row.source,
            cover_url=cover_url,
            file_url=f"{settings.api_prefix}/assets/{row.id}/file",
            ref_count=row.ref_count,
            created_at=row.created_at,
        )


__all__ = ["MAX_ASSET_BYTES", "AssetService"]
