"""归档素材编排：登记、列表、取文件；索引在 DB，字节在归档存储。

## 与 `AssetService` 的分工

两个 service 共用 `assets` 表，但用的是两套**物理通路**（配置分别是
`STORAGE_DRIVER` 与 `ARCHIVE_DRIVER`，两者都能跑本地盘或 S3）：

- `AssetService`：对象存储，服务端中转上传，缩略图按需派生，文件走
  `FileResponse` 或预签名跳转。
- 本文件：归档存储，内容寻址 blob（`blobs/ab/<sha256>`）、300MB 上限，
  供替换模板的 `prepare` 读原始视频，取件在远端驱动下走 307 预签名。

所以两边的 `storage_key` 不是同一个命名空间，靠 `source` 列区分。
列表接口也各自限定来源，不会把对方的行渲染成 404 的封面。

## 三个可见性层级

| 谁在问 | 能看什么 | 入口 |
|---|---|---|
| 本人 | 自己的全部（含回收站、未分享） | `owned()` |
| 任何人 | 未删除、且「自己的」或「别人已分享的原片」 | `visible()` |
| 列表 | 按 `scope` 分池：mine / public / trash | `list_visible()` |

`owned()` 与 `visible()` 不能合并：删除、改名只能动自己的记录，
而预览、详情要放行公共原片。合在一起就得在每个调用点补 if。

## 镜像是派生投影，不是第二份真相

替换模板的 worker 是同步代码，够不着异步 ORM，只能读 `catalog/assets/{id}.json`。
所以镜像必须存在，但**DB 才是唯一事实来源**：业务事务先 `commit`，之后才投影；
投影失败不回滚事务、也不让请求 500，`modules/asset/mirror.py` 的周期对账会按
DB 重放（镜像带 `stateVersion`，版本相同就跳过），并清掉没有对应 DB 行的孤儿镜像。
`app rebuild-mirrors` 可以任何时候全量重建。

## 列表不依赖归档存储在线

列表读 DB，存储不可达也不影响「库里有多少素材、占多少空间」；取文件、上传、
导入仍然要求存储可用。归档已切 key 语义，没有 `root` 字段可退化。
"""

from __future__ import annotations

import asyncio
import hashlib
import tempfile
import uuid
from collections.abc import AsyncIterator, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import TypeAlias

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from core import audit
from core.config import settings
from core.context import CurrentUser
from core.exceptions import BadRequest, Conflict, Forbidden, NotFound
from core.logging import get_logger
from core.media import MediaCategory
from core.ports.assets import ask_asset_references
from core.serving import Served
from infra.archive import (
    ArchiveStorage,
    BlobStore,
    clip_key,
    get_archive_storage,
    preview_key,
    within,
)
from infra.media_probe import (
    EXTENSIONS,
    MAX_BYTES,
)
from infra.media_probe import (
    inspect as probe_media,
)
from modules.asset.archive_purge import purge_trash_asset
from modules.asset.archive_repository import CATEGORIES, ArchiveAssetRepository
from modules.asset.archive_schema import (
    ArchiveCategory,
    ArchiveChange,
    ArchivedAsset,
    ArchiveList,
    ArchiveScope,
    ArchiveState,
    AssetQuota,
    ImportAsset,
)
from modules.asset.blob_repository import BlobRepository
from modules.asset.errors import AssetInUse, AssetQuotaExceeded, FileTypeNotAllowed
from modules.asset.library_service import INBOX_NAME, LibraryService, ensure_tree
from modules.asset.mirror import MirrorProjector, project_after_commit
from modules.asset.models import Asset
from modules.asset.schema import AssetSource
from modules.asset.subject_grants import allows, published_grants
from modules.asset.tag_service import TagService

logger = get_logger(__name__)

#: 允许的排序方式。白名单而不是透传：`order_by(getattr(Asset, sort))` 那种写法
#: 等于把列名交给客户端。
SORTS = ("created_desc", "created_asc", "name_asc")

#: 二级「媒体类型」分面的白名单。系统判定值只有三个（认不出来时不进分面），
#: 不透传客户端字符串：它进 SQL 比较，而「随便传个 `image%`」一旦生效
#: 就是一次全表扫。
MEDIA_TYPES = ("image", "video", "audio")

# 取件结果：`(本地路径, 重定向地址, MIME)`，前两项二选一非空。
# 与 `AssetService.resolve_file` 同形状，路由层的出参逻辑才能共用一份。
Serve: TypeAlias = Served


class ArchiveAssetService:
    def __init__(self, session: AsyncSession, storage: ArchiveStorage | None = None) -> None:
        self.session = session
        self.storage = storage or get_archive_storage()
        self.blobs = BlobStore(self.storage)
        self.repo = ArchiveAssetRepository(session)
        self.blob_repo = BlobRepository(session)

    # ------------------------------------------------------------------ 读
    async def owned(self, asset_id: str, owner: str) -> Asset:
        """本人记录：回收站里的也要能取（恢复操作要读它）。"""
        row = await self.repo.get(asset_id)
        if row.owner_id != owner:
            raise NotFound("素材不存在")
        return row

    async def visible(self, asset_id: str, owner: str) -> Asset:
        """公共可见：本人未删除的，或别人已分享的未删除原片。"""
        row = await self.repo.get(asset_id)
        shared_source = row.category == "source" and row.shared
        if row.deleted_at is not None or (row.owner_id != owner and not shared_source):
            raise NotFound("素材不存在或已停止共享")
        return row

    async def list_assets(
        self,
        owner: str,
        category: ArchiveCategory = "all",
        scope: ArchiveScope = "mine",
        keyword: str = "",
        state: ArchiveState = "all",
        page: int = 1,
        page_size: int = 200,
        team_id: str = "",
        tag_ids: Sequence[str] = (),
        sort: str = "created_desc",
        visible_to_all: bool = False,
        category_id: str = "",
        media_type: str = "",
    ) -> ArchiveList:
        if category not in (*CATEGORIES, "all"):
            raise BadRequest("素材分类无效")
        if sort not in SORTS:
            raise BadRequest("排序方式无效")
        if media_type and media_type not in MEDIA_TYPES:
            raise BadRequest("媒体类型无效")
        if scope == "team":
            # 团队库的可见性在这里判，不在 SQL 里判：`visibility='team'` 只说明
            # "这条属于某个团队"，不说明"你是谁"。
            if not team_id:
                raise BadRequest("请先选择团队库")
            visible = await LibraryService(self.session).teams_for(
                owner, visible_to_all=visible_to_all
            )
            if team_id not in {team.id for team, _, _ in visible}:
                raise Forbidden("无权访问该团队库")

        # 标签 id 先解析：未知 id 要 422 而不是静默忽略（忽略会让结果比用户以为的宽），
        # 已合并的 id 要跳到新 id（保存视图里沉淀的旧 id 仍能筛出东西）。
        resolved_tags = await TagService(self.session).resolve(tag_ids)
        rows, total, used_bytes = await self.repo.list_visible(
            owner,
            category,
            scope,
            keyword,
            state,
            page,
            page_size,
            tuple(resolved_tags),
            sort,
            team_id,
            category_id,
            media_type,
        )
        personal_quota = max(0, settings.archive_quota_bytes_per_user)
        team_quota = max(0, settings.archive_quota_bytes_per_team)
        personal_used = await self.repo.used_by_owner(owner)
        team_used = (
            await self.repo.distinct_used(visibility="team", team_id=team_id) if team_id else 0
        )
        quota = AssetQuota(
            personal_used=personal_used,
            personal_quota=personal_quota,
            team_used=team_used,
            team_quota=team_quota,
            draft_used=await self.repo.draft_used(),
            trash_used=await self.repo.distinct_used(
                visibility="personal", owner_id=owner, deleted=True
            ),
            physical_used=await self.repo.physical_used(),
            physical_quota=max(0, settings.archive_quota_bytes_total),
        )
        # 出参的下载判定要看「这张图在已发布主体里被允许下载吗」。一次查一批，
        # 不按行查：列表一页 200 条时逐条查等于给这个接口加 200 次往返
        grants = await published_grants(self.session, [row.id for row in rows])
        return ArchiveList(
            items=[self.to_out(row, owner, grants=grants) for row in rows],
            total=total,
            used_bytes=used_bytes,
            quota_bytes=personal_quota,
            owner_used_bytes=personal_used,
            quota=quota,
            page=page,
            page_size=page_size,
        )

    async def detail(self, asset_id: str, owner: str) -> ArchivedAsset:
        """详情：本人读自己的任何状态（含回收站，否则没法拿版本号去恢复），
        别人只能读已分享且未删除的原片。"""
        row = await self.repo.get(asset_id)
        if row.owner_id != owner and (
            row.deleted_at is not None or not (row.category == "source" and row.shared)
        ):
            raise NotFound("素材不存在或已停止共享")
        return self.to_out(row, owner, grants=await published_grants(self.session, [row.id]))

    async def preview(self, asset_id: str, owner: str) -> Serve:
        """受控公共预览：图片给原图，视频给浏览器可播的派生流。"""
        row = await self.visible(asset_id, owner)
        if row.type == "video":
            return await self.stream(asset_id, owner)
        return await self._serve(row.storage_key, row.mime_type)

    async def file(self, asset_id: str, owner: str, *, preview: bool = False) -> Serve:
        row = await self.visible(asset_id, owner) if preview else await self.owned(asset_id, owner)
        if not preview and row.deleted_at is not None:
            raise NotFound("素材已移入回收站")
        if preview and row.type == "video" and not row.cover_key:
            # 视频没有派生封面时现场截一帧：列表用 `<img>` 取缩略图，喂不了 mp4
            derived = preview_key(_require_digest(row))
            if await asyncio.to_thread(self._create_preview, row.storage_key, derived, row.type):
                return await self._serve(derived, "image/jpeg")
        key = row.cover_key if preview else row.storage_key
        if not key:
            raise NotFound("此素材没有缩略图")
        return await self._serve(key, "image/jpeg" if preview else row.mime_type)

    async def stream(self, asset_id: str, owner: str) -> Serve:
        """悬停/页面预览用的视频流；原片字节始终由 `file` 提供，不混用。"""
        row = await self.visible(asset_id, owner)
        if row.type != "video":
            raise NotFound("此素材没有视频预览")
        derived = clip_key(_require_digest(row))
        if await asyncio.to_thread(self._derive_clip, row.storage_key, derived):
            return await self._serve(derived, "video/mp4")
        if row.owner_id != owner:
            # 别人的原片不能当预览回出去：派生失败就是失败，不能退化成原片
            raise BadRequest("公共预览生成失败，请稍后重试；原件不会作为预览返回")
        logger.warning("browser preview failed, fallback to original: %s", asset_id)
        return await self._serve(row.storage_key, row.mime_type)

    # ------------------------------------------------------------------ 写
    async def import_asset(self, payload: ImportAsset, owner: str) -> ArchivedAsset:
        source = await asyncio.to_thread(
            within, await asyncio.to_thread(self.storage.root) / "inbox", payload.relative_path
        )
        if not source.is_file():
            raise BadRequest("请先将文件放入 Z 盘归档根目录的 inbox 文件夹")
        return await self.register(source, source.name, payload.category, owner)

    async def import_for(self, payload: ImportAsset, actor: CurrentUser) -> ArchivedAsset:
        if not actor.has("asset:reference:manage"):
            raise Forbidden("inbox 登记需要素材专员权限")
        return await self.import_asset(payload, actor.id)

    async def upload(
        self,
        chunks: AsyncIterator[bytes],
        filename: str,
        category: MediaCategory,
        owner: str,
    ) -> ArchivedAsset:
        temporary = await asyncio.to_thread(self._temporary)
        try:
            await self._receive(temporary, chunks)
            return await self.register(temporary, filename, category, owner)
        finally:
            await asyncio.to_thread(temporary.unlink, missing_ok=True)

    async def upload_for(
        self,
        chunks: AsyncIterator[bytes],
        filename: str,
        category: MediaCategory,
        actor: CurrentUser,
    ) -> ArchivedAsset:
        # 原片谁都能传（替换任务的输入就是原片），人物/产品/场景/音频属于受管参考素材
        if category != "source" and not actor.has("asset:reference:manage"):
            raise Forbidden("参考素材上传需要素材专员权限")
        return await self.upload(chunks, filename, category, actor.id)

    async def register(
        self, source: Path, filename: str, category: MediaCategory, owner: str
    ) -> ArchivedAsset:
        """登记一份**已经在盘上**的文件（上传的临时文件、inbox 里的文件、迁移脚本）。

        复制进归档目录、派生缩略图、落 DB 行、写 JSON 镜像，四件事要么全成要么全不成。
        """
        return await self._ingest(source, filename, category, owner)

    async def purge(self, asset_id: str, owner: str) -> None:
        """清空回收站：删行、删镜像、删没人再引用的字节（A2）。"""
        row = await self.owned(asset_id, owner)
        await purge_trash_asset(self.session, row, self.storage, actor_id=owner)

    async def change_action(
        self, asset_id: str, owner: str, payload: ArchiveChange, field: str
    ) -> ArchivedAsset:
        """三个 PUT 共用一条写路径；`field` 声明这一次允许改哪个字段。"""
        values = payload.model_dump(exclude_none=True)
        if set(values) != {"expected_version", field}:
            raise BadRequest("请仅提交当前操作对应的字段")
        return await self.change(asset_id, owner, payload)

    async def change(self, asset_id: str, owner: str, payload: ArchiveChange) -> ArchivedAsset:
        """改名 / 分享 / 移入回收站。

        并发保护靠 `state_version` 乐观锁，不用文件锁：索引在 DB，一次更新就是一个
        事务；归档盘上的 JSON 只是镜像，不需要跨进程串行化。
        """
        row = await self.owned(asset_id, owner)
        if payload.expected_version != row.state_version:
            # 响应丢失后的相同状态重试只返回事实，不再次改写时间和版本
            same = (
                (payload.name is None or payload.name.strip() == row.name)
                and (payload.shared is None or payload.shared == row.shared)
                and (payload.deleted is None or payload.deleted == (row.deleted_at is not None))
            )
            if same:
                return self.to_out(row, owner)
            raise Conflict("素材状态已变化，请刷新后重新操作")
        if payload.name is not None:
            if not payload.name.strip():
                raise BadRequest("名称不能为空")
            row.name = payload.name.strip()[:200]
        if payload.shared is not None:
            if row.category != "source" or row.deleted_at is not None:
                raise BadRequest("只能分享未删除的本人原片")
            row.shared = payload.shared
            row.shared_at = datetime.now(UTC) if payload.shared else None
        if payload.deleted is not None:
            if payload.deleted:
                # 归档素材与对象存储的素材共用 `assets.id`，删除保护不能只在
                # `AssetService.delete_asset()` 做：这里的回收站标记同样会让
                # 已发布的封装、任务详情里的参考图失效。
                references = await ask_asset_references([asset_id])
                if references.get(asset_id):
                    raise AssetInUse(details={"references": list(references[asset_id])})
            elif row.sha256 and row.category:
                # 恢复要过唯一索引（owner + sha256 + category，只看未删除行）这一关：
                # 「删除一份、又传了同一个文件」之后点恢复，库里就会并列两条同一份
                # 内容，索引直接拒掉——不先查的话客户端收到的是 500 而不是可读冲突。
                twin = await self._find_by_digest(owner, row.sha256, row.category)
                if twin is not None and twin.id != row.id:
                    raise Conflict(
                        "已存在内容相同的素材，无法恢复；请先删除列表中那一条，或直接使用它"
                    )
            if payload.deleted:
                await self.blob_repo.release(row.blob_id)
            else:
                # 恢复：字节还在（软删不删字节），把引用加回来。
                # 这一步漏掉的症状是「回收站清空时把别人还在用的字节删了」，
                # 而它只在「恢复过一次 + 之后彻底删除」这个组合下才炸。
                if row.blob_id:
                    blob = await self.blob_repo.get(row.blob_id)
                    if blob is not None:
                        await self.blob_repo.retain(blob)
            row.deleted_at = datetime.now(UTC) if payload.deleted else None
            # 移入回收站即撤回分享：留着 shared 会让它继续出现在公共池
            row.shared = False
            row.shared_at = None
        row.state_version += 1
        # DB 先提交，镜像后投影：镜像不是事务的一部分，也不可能回滚。
        # 投影失败由周期对账按 stateVersion 重放，不会留下「列表已删、模板还能引用」。
        await self.repo.save(row)
        await self.session.commit()
        await project_after_commit(row, self.storage)
        action = _change_action(payload)
        if action:
            await audit.record(
                action,
                actor_id=owner,
                target_type="asset",
                target_id=asset_id,
                detail={
                    "name": row.name,
                    "stateVersion": row.state_version,
                    "shared": row.shared,
                    "deleted": row.deleted_at is not None,
                },
            )
        return self.to_out(row, owner)

    # ------------------------------------------------------------------ 内部
    async def _ingest(
        self, source: Path, filename: str, category: MediaCategory, owner: str
    ) -> ArchivedAsset:
        ext = Path(filename).suffix.lower()
        # 扩展名先自己判一遍，而不是等 `probe_media`：它属于基础设施层，只能报
        # 通用 `BadRequest`（40000），业务侧的 `FileTypeNotAllowed`（40051）就到
        # 不了前端。两套素材接口合并时这个码差点静默消失——前端本来靠它单独提示
        # 「格式不支持」，退化成 40000 之后只能显示一句通用错误。
        if ext not in EXTENSIONS:
            raise FileTypeNotAllowed()
        # 类型由**内容**判定，扩展名只在认不出来时兜底（M1-E）。
        declared = _kind_of(EXTENSIONS[ext])
        sniffed = await asyncio.to_thread(_sniff, source)
        kind = sniffed[0] if sniffed else declared
        if kind != declared:
            # 不拒绝：用户可能只是改错了名字，而文件本身是好的。改名的图不会
            # 变成视频——下面按 `kind` 探测、按 `kind` 记 MIME，分类校验也按它判，
            # 所以「把 png 改名成 mp4 传进原片库」照样会被挡下。
            logger.warning("扩展名与内容不符：%s 声明 %s，内容像 %s", filename, declared, kind)
        mime = _mime_for(kind, EXTENSIONS[ext], sniffed[1] if sniffed else "")
        _ensure_category(category, kind)
        # 探测按**内容**分派（`kind=`）：拿被改错的名字决定走图片还是音视频分支，
        # 会让一张改名成 `.mp4` 的 PNG 报「音视频文件无法解析」，而真正的原因是
        # 文件名写错了。探测失败就是 400（基础设施层抛业务异常），不要吞掉——
        # 吞掉的结果是入库一条打不开的「图片」，错误延迟到生成时才暴露。
        info = await asyncio.to_thread(probe_media, source, ext, kind=kind)

        digest, size_bytes = await asyncio.to_thread(_digest_and_size, source)
        existing = await self._find_by_digest(owner, digest, category)
        if existing is not None:
            # 同一个人把同一份文件重复登记到**同一个分类**：返回已有记录，
            # 连对象都不再复制一份。分类不同则各自一条 —— 一张图同时当人物参考
            # 和产品参考是正常用法，不该被"内容相同"合并掉。
            return self.to_out(existing, owner)

        quota = max(0, settings.archive_quota_bytes_per_user)
        if quota:
            # 与 used_by_owner + 插入共处一个事务临界区，避免并发登记都判成「还够」
            await self.repo.lock_owner_for_quota(owner)
            used = await self.repo.used_by_owner(owner)
            # 同一份字节在别的分类已经有一份：这次**不增加账面占用**。
            # 不判这一下的话，「复制到另一个分类」会被当成新增计费，
            # 而那恰恰是复用机制要鼓励的动作（A6 的全部意义）。
            incoming = (
                0 if await self._digest_exists(owner, digest) else size_bytes
            )
            if used + incoming > quota:
                raise AssetQuotaExceeded(
                    details={
                        "usedBytes": used,
                        "quotaBytes": quota,
                        "incomingBytes": incoming,
                    }
                )

        # 物理上限单独判，且不是「账面 + 本次」：回收站里的字节账面上已释放、
        # 物理还在。两个都检查才会出现「账面有余、物理已满」——
        # 那种情况下的正确提示是「清空回收站可释放」，不是「你超标了」。
        physical_quota = max(0, settings.archive_quota_bytes_total)
        if physical_quota:
            physical = await self.repo.physical_used()
            existing_blob = await self.blob_repo.by_sha256(digest)
            physical_incoming = 0 if existing_blob is not None else size_bytes
            if physical + physical_incoming > physical_quota:
                raise AssetQuotaExceeded(
                    details={
                        "usedBytes": physical,
                        "quotaBytes": physical_quota,
                        "incomingBytes": physical_incoming,
                        "scope": "physical",
                    }
                )

        uid = uuid.uuid4().hex
        # 字节按 sha256 落库（`blobs/ab/abcdef…`）：位置由内容决定，不由登记时间决定。
        # 于是同一份文件被两个人传、或同一人传到两个分类，物理上只有一份；
        # 而"这份字节还是不是当初那份"也不再需要每次读出来重算——key 就是答案。
        key, created = await asyncio.to_thread(self.blobs.ensure, source, digest, ext)
        cover_key = preview_key(digest)

        has_preview = await asyncio.to_thread(self._create_preview, key, cover_key, kind)
        if not has_preview:
            logger.info("归档素材没有缩略图（音轨或派生失败）：%s", uid)

        # 字节事实与归属记录分开写：同一份内容只有一条 blob，多条素材指向它。
        # `ensure` 在并发下用 savepoint 兜住唯一冲突，不会把外层事务一起回滚。
        blob, _ = await self.blob_repo.ensure(
            sha256=digest,
            size_bytes=size_bytes,
            mime_type=mime,
            storage_key=key,
            preview_key=cover_key if has_preview else "",
        )
        blob.ref_count = (blob.ref_count or 0) + 1

        # 分类树的位置：个人库按需建树（首次上传的人不会在注册时白建 6 行）。
        # 用途与旧分类不是一一对应——`audio` 归到「参考素材」，因为音频不是用途。
        tree = await ensure_tree(self.session, "personal", owner, commit=False)
        category_id = tree.get(_PURPOSE_BY_CATEGORY.get(category, ""), "") or tree.get(
            INBOX_NAME, ""
        )

        row = Asset(
            id=uid,
            name=Path(filename).name[:200],
            type=kind,
            media_type=kind,
            mime_type=mime,
            size_bytes=size_bytes,
            storage_key=key,
            cover_key=cover_key if has_preview else "",
            sha256=digest,
            category=category,
            category_id=category_id,
            visibility="personal",
            blob_id=blob.id,
            source=AssetSource.ARCHIVE,
            owner_id=owner,
            created_at=datetime.now(UTC),
            width=info["width"],
            height=info["height"],
            duration=info["duration"],
            has_audio=info["hasAudio"],
            # 上传件就是素材：直接 `adopted`，且合规状态是 `approved`——
            # 列级默认值是 `pending`（AI 产物要审核），上传件必须显式写这一句，
            # 漏了的表现是"上传的图不能进主体"，而提示只说"合规未通过"。
            origin="upload",
            stage="adopted",
            compliance_status="approved",
            adopted_at=datetime.now(UTC),
        )
        try:
            await self.repo.add(row)
            await self.session.commit()
        except IntegrityError:
            # 并发登记同一份文件：部分唯一索引（owner + sha256 + category，
            # 且只看未删除行）兜底。回滚后按摘要取已有记录返回——用户拿到的是
            # 「本来就在库里的那条」，不是一条报错。
            #
            # 索引与 `_find_by_digest` 的口径必须一字不差，否则会出现
            # 「索引拦下了、这里又查不到」→ 直接把 IntegrityError 抛给客户端。
            await self.session.rollback()
            await asyncio.to_thread(self._discard_blob, key, created)
            await asyncio.to_thread(self._discard_mirror, uid)
            existing = await self._find_by_digest(owner, digest, category)
            if existing is not None:
                return self.to_out(existing, owner)
            raise
        except Exception:
            await self.session.rollback()
            await asyncio.to_thread(self._discard_blob, key, created)
            await asyncio.to_thread(self._discard_mirror, uid)
            raise
        # 提交成功后才写镜像；失败交由 `asset.mirror` 周期任务按 DB 重放。
        await project_after_commit(row, self.storage)
        return self.to_out(row, owner)

    async def _digest_exists(self, owner: str, digest: str) -> bool:
        """该 owner 是否已有一份同摘要的**活跃**素材（任意分类）。

        与 `_find_by_digest` 的差别是**不带分类**：这里只回答「账面要不要加钱」，
        和「这次算不算重复登记」是两件事。同一个人把同一张图分别放进产品图与
        人物形象，会得到两条记录、一份字节、一次计费——三者都对。
        """
        return bool(
            await self.session.scalar(
                select(Asset.id)
                .where(
                    Asset.owner_id == owner,
                    Asset.sha256 == digest,
                    Asset.source == AssetSource.ARCHIVE,
                    Asset.deleted_at.is_(None),
                )
                .limit(1)
            )
        )

    async def _find_by_digest(self, owner: str, digest: str, category: str) -> Asset | None:
        """同 owner、同摘要、同分类的**活跃**记录。

        `deleted_at IS NULL` 不能少：否则用户把一份素材移进回收站、再上传同一个
        文件时，这里会命中那条已删除的行并原样返回——接口回 201，返回体里带着
        `deletedAt`，而「我的素材」列表默认不含回收站，看起来就是「上传成功但
        文件不见了」。之前靠 `source != 'archive'` 的部分唯一索引完全没兜住这个
        （索引对所有行都不生效），所以只能在这里判。
        """
        stmt = select(Asset).where(
            Asset.owner_id == owner,
            Asset.sha256 == digest,
            Asset.category == category,
            Asset.source == AssetSource.ARCHIVE,
            Asset.deleted_at.is_(None),
        )
        return await self.session.scalar(stmt.limit(1))

    async def _receive(self, target: Path, chunks: AsyncIterator[bytes]) -> None:
        """把请求体流式落盘：300MB 原片不进内存。"""
        total = 0
        stream = await asyncio.to_thread(target.open, "xb")
        try:
            async for chunk in chunks:
                if not chunk:
                    continue
                total += len(chunk)
                if total > MAX_BYTES:
                    raise BadRequest("文件不能超过 300MB")
                await asyncio.to_thread(stream.write, chunk)
        finally:
            await asyncio.to_thread(stream.close)
        if total == 0:
            raise BadRequest("文件不能为空")

    def _materialized(self, key: str) -> Path:
        """取出可读路径。

        上下文在这里就退出了，所以**依赖驱动承诺的「退出后缓存仍可读」**：
        本地驱动交出的就是归档里的原路径，远端驱动把字节留在物化缓存里等淘汰。
        不这样做的话，`FileResponse` 拿到的路径会在响应发出前失效。
        """
        with self.blobs.materialize(key) as path:
            return path

    async def _serve(self, key: str, mime: str) -> Serve:
        """取件：远端驱动给预签名地址（307），本地驱动给路径（`FileResponse`）。

        视频原片 300MB。远端驱动下先 `materialize` 再 `FileResponse`，等于把
        「拖进度条」变成「先下完整个文件」；而预签名让对象存储自己处理 Range，
        字节也不经过应用进程（T19）。

        预签名地址不校验存在性，所以远端分支要先 HEAD 一次：素材被删掉之后
        该 404，而不是 307 到一个 403/NoSuchKey 的桶地址。
        """
        url = await asyncio.to_thread(self.storage.download_url, key)
        if url is None:
            path = await asyncio.to_thread(self._materialized, key)
            return Served.local(path, mime)
        if not await asyncio.to_thread(self.storage.exists, key):
            raise NotFound("素材字节不存在，请重新登记")
        return Served.remote(url, mime)

    def _create_preview(self, key: str, cover_key: str, kind: str) -> bool:
        """派生静帧封面（图片缩放 / 视频截帧）。"""
        return self.blobs.derive(key, cover_key, kind=kind)

    def _derive_clip(self, key: str, target_key: str) -> bool:
        """派生浏览器可播的转码流。"""
        return self.blobs.derive(key, target_key, kind="video", clip=True)

    async def _write_mirror(self, row: Asset) -> None:
        """兼容入口：幂等投影一行。写路径统一走 `project_after_commit()`。"""
        projector = MirrorProjector(self.storage)
        await asyncio.to_thread(projector.write, projector.snapshot(row))

    def _discard_mirror(self, asset_id: str) -> None:
        MirrorProjector(self.storage).remove(asset_id)

    def _discard_blob(self, key: str, created: bool) -> None:
        """登记失败不留半条记录，但**也不删字节**。

        内容寻址下同一个 key 可能正被别的记录共享（同一份文件被另一个人登记、
        或同一人登记到了另一个分类），删它等于打断别人。留一份没人引用的字节
        只是磁盘占用，`app prune-blobs` 能扫出来；删错则是数据丢失。
        """
        if created:
            logger.info("登记失败，已写入的字节留待回收：%s", key)

    def _temporary(self) -> Path:
        """上传中转文件放本机临时目录，不再借用归档盘。

        归档盘可能是只读挂载或远端桶，而中转文件只在本进程内活几秒；
        放本机也能让 300MB 的流式落盘不等网络。
        """
        folder = Path(tempfile.gettempdir()) / "yufan-archive"
        folder.mkdir(parents=True, exist_ok=True)
        return folder / (uuid.uuid4().hex + ".upload")

    def to_out(
        self, row: Asset, owner: str, *, grants: dict[str, set[str]] | None = None
    ) -> ArchivedAsset:
        """DB 行 → 出参，并**按调用者裁剪**。

        别人的素材只回预览地址：原件路径（Z 盘相对 key）与摘要不外露，
        否则公共池等于把归档盘目录结构发给了所有账号。

        `grants` 是**已发布主体**给这张图的授权（见 `subject_grants.py`）：
        缺席表示它不是任何主体的成员，没有对外承诺，按归属判定即可。
        `download` 是**逐成员**判的（木桶不压整体），所以这里只看它自己的授权——
        同组里别的成员不给下载，不影响这张。
        """
        item = ArchivedAsset.model_validate(self.repo.to_media(row).model_dump())
        item.editable = row.owner_id == owner
        item.download_allowed = item.editable and allows(grants or {}, row.id, "download")
        # 生命周期字段不在 `StoredMedia` 里（那是跨模块的媒体词汇），
        # 它们是素材域自己的事实，所以在这里补
        item.origin = row.origin or "upload"
        item.stage = row.stage or "adopted"
        item.recipe_id = row.recipe_id or ""
        item.compliance_status = row.compliance_status or "approved"
        item.adopted_at = row.adopted_at
        item.category_id = row.category_id or ""
        if not item.editable:
            item.path = ""
            item.sha256 = ""
            item.url = f"{settings.api_prefix}/assets/{row.id}/preview"
        return item


def _require_digest(row: Asset) -> str:
    """派生 key 由摘要决定：缺摘要就没法定位字节，也没法定位它的缓存。"""
    if not row.sha256:
        raise NotFound("素材摘要缺失，请重新登记")
    return row.sha256


def _change_action(payload: ArchiveChange) -> str:
    """三个 PUT 各自的审计动作名，便于按动作检索而不是翻日志。"""
    if payload.name is not None:
        return "asset.archive.rename"
    if payload.shared is not None:
        return "asset.archive.share" if payload.shared else "asset.archive.unshare"
    if payload.deleted is not None:
        return "asset.archive.trash" if payload.deleted else "asset.archive.restore"
    return ""


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


def _mime_for(kind: str, declared_mime: str, sniffed_mime: str = "") -> str:
    """决定记哪个 MIME：内容签名 > 扩展名。

    冲突时保留原 MIME 会让浏览器按扩展名解释字节（把 PNG 当 MP4 播），
    结果是「预览一直转圈」，而不是「提示格式不支持」——后者才是可行动的。

    三级优先：签名认出了具体格式就用它；只认出粗类型（图片/视频/音频）时
    给一个该类型的保守默认值；两样都没有（扩展名认不出来才会走到这里）时
    保留声明值。
    """
    if sniffed_mime:
        return sniffed_mime
    if _kind_of(declared_mime) == kind:
        return declared_mime
    return {
        "image": "image/png",
        "video": "video/mp4",
        "audio": "audio/wav",
    }.get(kind, declared_mime)


# 文件头签名 → (类型, MIME)。**按内容判类型，不按扩展名**：扩展名是客户端说的，
# 内容是真的。
#
# 这不是防恶意（真要防得做完整解析），是防「用户把 png 改名成 mp4 传进原片库」：
# 那条路会让替换模板在运行时拿到一个解不开的「视频」，而错误信息指向 provider，
# 排查方向从一开始就是错的。读 16 字节的代价可以忽略。
#
# 带上 MIME 是为了让改名文件记对类型：JPEG 被改名成 `.mp4` 时，按扩展名记
# `video/mp4` 会让浏览器把字节当视频解；按「图片」这个粗类型记 `image/png`
# 同样不对——它会拿 PNG 解码器读 JPEG 字节。签名能分到格式这一层，就别停在类型。
_SIGNATURES: tuple[tuple[bytes, str, str], ...] = (
    (b"\x89PNG\r\n\x1a\n", "image", "image/png"),
    (b"\xff\xd8\xff", "image", "image/jpeg"),
    (b"GIF87a", "image", "image/gif"),
    (b"GIF89a", "image", "image/gif"),
    (b"II*\x00", "image", "image/tiff"),  # TIFF
    (b"ID3", "audio", "audio/mpeg"),
    (b"\xff\xfb", "audio", "audio/mpeg"),
    (b"\xff\xf3", "audio", "audio/mpeg"),
    (b"OggS", "audio", "audio/ogg"),
    (b"fLaC", "audio", "audio/flac"),
)


def _sniff(path: Path) -> tuple[str, str] | None:
    """按文件头判 `(类型, MIME)`。认不出来返回 `None`，由扩展名兜底。

    **认不出不等于错**：容器格式太多（mkv、avi、heic…），扩展名仍然是有效的
    补充信息。只有「认出来了且与扩展名冲突」才值得干预。
    """
    try:
        with path.open("rb") as stream:
            head = stream.read(16)
    except OSError:  # pragma: no cover - 文件刚被删或权限问题
        return None
    if len(head) < 12:
        return None
    for magic, kind, mime in _SIGNATURES:
        if head.startswith(magic):
            return kind, mime
    riff = head[:4] == b"RIFF"
    if riff and head[8:12] == b"WEBP":
        return "image", "image/webp"
    if riff and head[8:12] == b"WAVE":
        return "audio", "audio/wav"
    if riff and head[8:12] == b"AVI ":
        return "video", "video/x-msvideo"
    # MP4 / MOV / M4A 家族：第 4–8 字节是 `ftyp`，brand 在 8–12
    if head[4:8] == b"ftyp":
        if head[8:11] in {b"M4A", b"M4B"}:
            return "audio", "audio/mp4"
        return "video", "video/mp4"
    return None


def _sniff_kind(path: Path) -> str | None:
    """只要类型时用这个（对外只说「是图还是视频」的地方）。"""
    found = _sniff(path)
    return found[0] if found else None


def _ensure_category(category: MediaCategory, kind: str) -> None:
    """分类与媒体类型必须匹配：人物库里混进一段音频，替换模板会在运行时炸。"""
    expected = {
        "source": "video",
        "character": "image",
        "product": "image",
        "scene": "image",
        "audio": "audio",
    }[category]
    if kind != expected:
        raise BadRequest("原片需为 MP4，人物/产品/场景需为图片，音频分类需为音频")


# 旧分类（业务类型）→ 新分类树上的用途。
# `audio` 归「参考素材」而不是单开一类：音频不是**用途**，它是媒体类型分面。
# 把「音效」做成一级分类，会让「这条素材属于哪个用途」这个问题对音频无解。
_PURPOSE_BY_CATEGORY: dict[str, str] = {
    "source": "参考素材",
    "character": "人物形象",
    "product": "产品图",
    "scene": "场景背景",
    "audio": "参考素材",
}
