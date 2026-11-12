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
import re
import tempfile
from collections.abc import AsyncIterator, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased
from sqlalchemy.sql.elements import ColumnElement

from core.config import settings
from core.context import CurrentUser
from core.exceptions import AppError, BadRequest, NotFound
from core.ports.library_references import ask_library_references, library_references_ready
from core.response import PageParams
from core.serving import Served
from infra.media_probe import (
    EXTENSIONS,
    MAX_BYTES,
    MAX_TEXT_BYTES,
    PREVIEW_VERSION,
    TEXT_EXTENSIONS,
    create_browser_preview,
    create_preview,
    decode_text,
    inspect,
    text_excerpt,
)
from infra.storage.base import ObjectStorage, StorageError
from modules.library import access
from modules.library.errors import (
    AssetNotEditable,
    LibraryFileTooLarge,
    LibraryFileTypeNotAllowed,
    LibraryTextTooLarge,
    PackHasNoOriginal,
)
from modules.library.models import (
    LibraryAsset,
    LibraryAssetGeneration,
    LibraryAssetTag,
    LibraryPackItem,
    LibraryShareRequest,
    LibraryTag,
    LibraryTextVersion,
)
from modules.library.schema import (
    AssetPatchIn,
    AssetSort,
    LibraryArchiveOut,
    LibraryAssetDetailOut,
    LibraryAssetOut,
    LibraryAssetPage,
    LibraryFacets,
    LibraryGenerationOut,
    LibraryGroupRef,
    LibraryTab,
    LibraryTagOut,
    PackItemOut,
    ShareHistoryItem,
    UploadKind,
)
from modules.library.tags import expand_tag_ids, set_asset_tags

__all__ = ["LibraryService"]

logger = logging.getLogger(__name__)

#: 主字节前缀。与旧归档盘（`blobs/`）彻底分开：删 v2 模块不会误删旧库字节，
#: 反过来旧库的 `prune-blobs` 也扫不到这里。
BLOB_PREFIX = "library/blobs"
PREVIEW_PREFIX = "library/previews"

_STATES = {"draft", "active", "trash"}

#: 「像扩展名」的判断：带字母，1-5 位。`v1.2` 的 `.2` 不算（见 `keep_extension`）。
_SUFFIX_SHAPE = re.compile(r"^\.[A-Za-z][A-Za-z0-9]{0,4}$")


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
    if mime.startswith("audio/"):
        return "audio"
    if mime.startswith("text/"):
        return "text"
    return "image"


def blob_key(digest: str, extension: str) -> str:
    return f"{BLOB_PREFIX}/{digest[:2]}/{digest}{extension}"


def preview_key(digest: str) -> str:
    return f"{PREVIEW_PREFIX}/{digest[:2]}/{digest}.jpg"


def clip_key(digest: str) -> str:
    """浏览器可播的视频派生流，按摘要命名，同一份原片只派生一次。

    文件名带 `PREVIEW_VERSION`：转码参数一变，所有缓存（浏览器 / CDN / 桶）
    必须一起失效。旧库的 `clip_key` 与前端 `?v=hd-v2` 用的是同一个常量，
    v2 不另起一套版本号，否则将来模板库切过来会出现「新旧两版预览混着放」。
    """
    return f"{PREVIEW_PREFIX}/{digest[:2]}/{digest}.{PREVIEW_VERSION}.mp4"


def keep_extension(original: str, storage_key: str | None, requested: str) -> str:
    """改名只改主体，扩展名跟着记录走。

    前端输入框里本来就不带扩展名，但直接调 API 时可能带。两种情况都按
    「用户写的后缀不算数」处理：先剥掉，再拼回这条记录自己的后缀。否则把
    `a.png` 改成 `a.mp4` 会让库里的字节（仍是 PNG）与名字（说是 MP4）对不上：
    详情页的类型徽标还是「图片」，下载下来的文件却叫 `.mp4`，播放器一打开就是坏的。

    后缀先看 `name`，再退到 `storage_key`（导入的历史数据 name 可能被清过）。
    两者都没有（封装、人工建的行）就不加——别给「春季素材包」硬塞一个 `.png`。
    """
    cleaned = requested.strip()
    suffix = Path(cleaned).suffix
    # 只有「像扩展名」的那一段才剥：带字母、1-5 位。`v1.2` 的 `.2` 没有字母，
    # 那是名字的一部分，剥掉就成了 `v1`——比不改名更糟。
    if suffix and _SUFFIX_SHAPE.match(suffix):
        cleaned = cleaned[: -len(suffix)]
    cleaned = cleaned.strip(" .") or "未命名"
    fixed = Path(original).suffix or Path(storage_key or "").suffix
    return f"{cleaned}{fixed}" if fixed else cleaned


def snapshot_out(row: LibraryAssetGeneration) -> LibraryGenerationOut:
    """生成溯源快照 → 出参。入库时冻结，这里只做字段搬运，不联表、不补算。"""
    return LibraryGenerationOut(
        source_module=row.source_module,
        source_task_id=row.source_task_id,
        model=row.model,
        model_id=row.model_id,
        prompt=row.prompt,
        size=row.size,
        count=row.count,
        duration_sec=row.duration_sec,
        aspect_ratio=row.aspect_ratio,
        style_preset=row.style_preset,
        seed=row.seed,
        reference_asset_ids=list(row.reference_asset_ids or []),
        pack_id=row.pack_id,
        pack_version=row.pack_version,
        prompt_spec=dict(row.prompt_spec or {}),
        configuration=dict(row.configuration or {}),
        external_id=row.external_id,
        credits=row.credits,
        generated_at=row.generated_at,
    )


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
        origins: Sequence[str] = (),
        hide_packed_members: bool = True,
        exclude_kinds: Sequence[str] = (),
    ) -> LibraryAssetPage:
        """列表 + 分面。

        可见集按 `library` 与 `state` 两组条件拼：`state=draft/trash` 时**忽略**
        `library`（它们不在任何一个可见范围内，只属于本人），避免出现
        「草稿箱 + 团队」这种拼起来的空集让人以为权限出了问题。

        `hide_packed_members`：active 列表里不单列「已装进封装」的成员（见
        `access.hidden_by_pack_clause`）。选择器传 `False`——封装的成员本来就要
        能被挑去生成，按列表口径把成员藏起来，会让那 3 个原素材在选择器里消失。
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
            if hide_packed_members:
                query = query.where(access.hidden_by_pack_clause(actor, groups, library))
        elif state == "draft":
            # 代审范围：持 draft:review 的人能看到所有人的草稿；否则只看自己的
            if not actor.has(access.PERM_DRAFT_REVIEW):
                query = query.where(LibraryAsset.owner_id == actor.id)
        else:
            if not actor.has(access.PERM_DELETE_ANY):
                query = query.where(LibraryAsset.owner_id == actor.id)

        if kind:
            query = query.where(LibraryAsset.kind == kind)
        if origins:
            # 多值而不是单值：模板库要同时取「切换后新传的 template_source」
            # 与「并存期从旧库导入的 imported」，漏一个就少一半原片。
            # 角标（facets）仍按全入口算——它是侧栏六个入口的总数，不该被
            # 调用方的一次筛选悄悄改掉。
            query = query.where(LibraryAsset.origin.in_(list(origins)))
        if exclude_kinds:
            # 「排除某几类」必须落在 SQL 里：在 Python 里滤掉 items 之后
            # `total` 还是原值，前端会算出多一页，最后一页点进去是空的。
            query = query.where(LibraryAsset.kind.not_in(list(exclude_kinds)))
        if keyword:
            query = query.where(LibraryAsset.name.ilike(f"%{keyword}%"))
        if tag_ids:
            # 选中一级 = 展开成「自身 + 旗下二级」，与标签栏的计数口径一致。
            # 仍是 OR：`asset_id IN (...)` 天然按资产去重，一张图同时挂了父子
            # 两个标签也只会出现一次。
            expanded = await expand_tag_ids(self.session, tag_ids)
            query = query.where(
                LibraryAsset.id.in_(
                    select(LibraryAssetTag.asset_id).where(
                        LibraryAssetTag.tag_id.in_(expanded)
                    )
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
        # 一次查完这一页的归属、组名与标签，不按行循环（24 行就是 24 次往返）
        group_map = await access.asset_group_ids(self.session, page_ids)
        owner_map = await access.owner_names(
            self.session, [row.owner_id for row in rows]
        )
        tag_map = await self._tags_of(page_ids)
        return LibraryAssetPage(
            items=[
                self._to_out(
                    row,
                    owner_name=owner_map.get(row.owner_id),
                    group_ids=group_map.get(row.id, []),
                    tags=tag_map.get(row.id, []),
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
        """详情：列表项 + 详情页六个区要用的东西（设计 §7.2）。

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
        tags = (await self._tags_of([row.id])).get(row.id, [])
        group_ids = await access.asset_group_ids(self.session, [row.id])
        names = await access.group_names_for(self.session, group_ids.get(row.id, []))
        owner_map = await access.owner_names(self.session, [row.owner_id])
        generation = await self.session.get(LibraryAssetGeneration, row.id)
        references = await self._references(row)
        return LibraryAssetDetailOut(
            **self._to_out(
                row,
                owner_name=owner_map.get(row.owner_id),
                group_ids=group_ids.get(row.id, []),
                tags=tags,
            ).model_dump(by_alias=False),
            groups=[
                LibraryGroupRef(id=group_id, name=names.get(group_id, ""))
                for group_id in group_ids.get(row.id, [])
            ],
            generation=snapshot_out(generation) if generation else None,
            pack_items=await self._pack_items(row) if row.kind == "pack" else [],
            references=references,
            share_history=await self._share_history(row),
            archive=self._archive(row, actor, references),
            reviewed_by=row.reviewed_by,
            reviewed_at=row.reviewed_at,
            reject_reason=row.reject_reason,
            trashed_from_state=row.trashed_from_state,
        )

    async def _pack_items(self, pack: LibraryAsset) -> list[PackItemOut]:
        """封装的成员。**成员行可能已经被物理删除**——建封装时它们是引用关系，
        删除一方要过 `library-references`，理论上到不了这里；但历史数据/人工
        SQL 都可能留下断链，取不到就跳过，别让详情页整体 500。"""
        items = (
            await self.session.scalars(
                select(LibraryPackItem)
                .where(LibraryPackItem.pack_id == pack.id)
                .order_by(LibraryPackItem.position)
            )
        ).all()
        if not items:
            return []
        rows = {
            row.id: row
            for row in (
                await self.session.scalars(
                    select(LibraryAsset).where(
                        LibraryAsset.id.in_([item.asset_id for item in items])
                    )
                )
            ).all()
        }
        result: list[PackItemOut] = []
        for item in items:
            row = rows.get(item.asset_id)
            if row is None:
                continue
            result.append(
                PackItemOut(
                    asset_id=row.id,
                    name=row.name,
                    kind=row.kind,
                    state=row.state,
                    position=item.position,
                    role=item.role,
                    description=item.description,
                    size_bytes=row.size_bytes,
                    cover_url=self.storage.url_for(row.cover_key) if row.cover_key else None,
                )
            )
        return result

    async def _references(self, row: LibraryAsset) -> list[str] | None:
        """谁在用它。`None` 表示**不可判定**（来源不齐），不是「没人用」。

        详情页据此决定是「可以彻底删除」还是「暂时查不了」——把两者都说成
        空数组，会让用户在一个装配故障的实例上删掉正在被引用的素材。

        判据是 `library_references_ready()`（声明 ⊆ 实现）而不是「有没有人注册」：
        注册了却漏声明，某个来源会被静默忽略，这种实例「查得动」但答案不完整。
        只读展示传 `require_complete=False` —— 删除路径（`lifecycle.purge`）才需要
        抛 503，这里抛了会连详情页都打不开。
        """
        if not library_references_ready():
            return None
        references = await ask_library_references(
            self.session, [row.id], require_complete=False
        )
        return list(references.get(row.id, []))

    async def _share_history(self, row: LibraryAsset) -> list[ShareHistoryItem]:
        """可见性变更历史。最多 20 条：再久远的共享记录属于审计，不属于详情页。"""
        requests = (
            await self.session.scalars(
                select(LibraryShareRequest)
                .where(LibraryShareRequest.asset_id == row.id)
                .order_by(LibraryShareRequest.created_at.desc())
                .limit(20)
            )
        ).all()
        if not requests:
            return []
        user_ids = {request.applicant_id for request in requests}
        user_ids.update(request.reviewer_id for request in requests if request.reviewer_id)
        names = await access.owner_names(self.session, sorted(user_ids))
        group_names = await access.group_names_for(
            self.session, [request.group_id for request in requests if request.group_id]
        )
        return [
            ShareHistoryItem(
                id=request.id,
                action=request.action,
                target=request.target,
                group_id=request.group_id,
                group_name=group_names.get(request.group_id, ""),
                status=request.status,
                applicant_id=request.applicant_id,
                applicant_name=names.get(request.applicant_id),
                reviewer_id=request.reviewer_id,
                reviewer_name=names.get(request.reviewer_id or ""),
                reason=request.reason,
                note=request.note,
                decided_at=request.decided_at,
                created_at=request.created_at,
            )
            for request in requests
        ]

    def _archive(
        self, row: LibraryAsset, actor: CurrentUser, references: list[str] | None
    ) -> LibraryArchiveOut | None:
        """归档信息只给所有者与管理员：存储键与幂等键对外没有意义，只暴露内部结构。"""
        if row.owner_id != actor.id and not actor.has(access.PERM_DELETE_ANY):
            return None
        return LibraryArchiveOut(
            storage_key=row.storage_key,
            sha256=row.sha256 or "",
            source_key=row.source_key or "",
            size_bytes=row.size_bytes,
            reference_count=len(references or []),
        )

    async def file(
        self, asset_id: str, actor: CurrentUser, *, preview: bool = False
    ) -> Served:
        row = await access.load_visible(self.session, asset_id, actor)
        if row.state == "trash":
            # 「删除」必须真的拿不到文件，否则回收站只是前端的一个标签页。
            # 恢复后立刻可用，所以这不是永久拒绝。
            raise NotFound("回收站内的资产不提供预览与下载")
        if preview and row.kind == "text":
            # 文本没有缩略图，卡片按 `textExcerpt` 画文字卡。这里直接说清楚，
            # 别让前端对每张文字卡发一次注定 404 的 `/thumbnail`。
            raise NotFound("文本资产没有缩略图")
        key = row.cover_key if preview else row.storage_key
        if not preview and row.kind == "pack":
            # 封装只有从成员借来的封面，没有自己的原文件。用 404 会让用户
            # 以为记录被删了；这里明确说「换个入口」。前端已改成对封装
            # 直接走打包下载，这条是给直接调 API 的人兜底。
            raise PackHasNoOriginal()
        if not key:
            # 两种取件共用一个函数，提示必须分开：以前无论是下载原文件还是
            # 取缩略图，都报「没有缩略图」，把排查方向带偏了。
            raise NotFound("此资产没有缩略图" if preview else "此资产没有原文件")
        is_text = not preview and row.kind == "text"
        return await self._serve(
            key,
            # `.md` 也按纯文本给：渲染口径是「Markdown 不转 HTML」，
            # 让浏览器拿到 `text/markdown` 只会诱发各种嗅探差异。
            "text/plain; charset=utf-8" if is_text else (
                "image/jpeg" if preview else row.mime_type
            ),
            filename=None if preview else row.name,
            # 缩略图给 `<img>` 直接引用；原文件一律同源转发（见 `_serve` 的注释）
            proxy=not preview,
            size=None if preview else row.size_bytes,
            headers={"X-Content-Type-Options": "nosniff"} if is_text else None,
        )

    async def preview(self, asset_id: str, actor: CurrentUser) -> Served:
        """受控公共预览：视频给派生流，图片 / 音频给原文件。

        与旧 `/assets/{id}/preview` 同口径。能不能看只由 `load_visible` 一家
        说了算（共享到小组 / 团队、封装成员都算可见），这里不再写第二份
        「有没有 shared」的判断——两处判定迟早漂移，漂移的方向一定是越权。
        """
        row = await access.load_visible(self.session, asset_id, actor)
        if row.state == "trash":
            raise NotFound("回收站内的资产不提供预览与下载")
        if row.kind == "video":
            return await self.stream(asset_id, actor)
        return await self.file(asset_id, actor)

    async def stream(self, asset_id: str, actor: CurrentUser) -> Served:
        """浏览器兼容视频预览：H.264 8-bit 派生流，绝不改动原始归档文件。

        归档原片可能是 10-bit HEVC / H.264 High10，Chrome 的 `<video>` 解不了；
        旧库 `/assets/{id}/stream` 就是为此存在的，v2 不能只补个「同源转发原片」
        就算对齐。派生按摘要命名，同一份字节只转一次。

        转码失败时**本人**回退原文件，**别人**一律 400：把别人共享的原片整份
        当「预览」送出去，等于绕过「只给派生预览」这层收窄。
        """
        row = await access.load_visible(self.session, asset_id, actor)
        if row.state == "trash":
            raise NotFound("回收站内的资产不提供预览与下载")
        if row.kind != "video":
            raise NotFound("此资产没有视频预览")
        digest = row.sha256 or ""
        derived = clip_key(digest) if re.fullmatch(r"[0-9a-f]{64}", digest) else ""
        ready = bool(derived) and await self.storage.exists(derived)
        if derived and not ready:
            ready = await self._derive_clip(row, derived)
        if not ready:
            if row.owner_id != actor.id:
                raise BadRequest("公共预览生成失败，请稍后重试；原件不会作为预览返回")
            logger.warning("浏览器预览派生失败，回退原文件：%s", asset_id)
            return await self.file(asset_id, actor)
        # 与 `/file` 一样走同源转发：前端既可能 `<video src>` 直接引用，
        # 也可能 XHR 取 blob 再 createObjectURL，307 到桶会踩 CORS。
        return await self._serve(derived, "video/mp4", proxy=True)

    async def _derive_clip(self, row: LibraryAsset, derived: str) -> bool:
        """分块物化原片 → ffmpeg 转码 → 写回派生 key，全程不整块读进内存。"""
        if not row.storage_key:
            return False
        with tempfile.TemporaryDirectory(prefix="library-clip-") as folder:
            source = Path(folder) / f"source{Path(row.storage_key).suffix or '.mp4'}"
            try:
                with source.open("wb") as handle:
                    async for chunk in self.storage.iter_chunks(row.storage_key):
                        handle.write(chunk)
            except StorageError:
                logger.warning("原片字节读取失败，跳过派生：%s", row.id)
                return False
            target = Path(folder) / "clip.mp4"
            if not await asyncio.to_thread(create_browser_preview, source, target):
                return False
            await self.storage.put_file(derived, target, "video/mp4")
            return True

    # ------------------------------------------------------------------ 写

    async def patch(
        self, asset_id: str, payload: AssetPatchIn, actor: CurrentUser
    ) -> LibraryAssetDetailOut:
        """改名 / 换标签。**不改可见性**：共享必须走申请单，否则审批就成了摆设。"""
        row = await access.load_visible(self.session, asset_id, actor)
        if row.owner_id != actor.id and not actor.has(access.PERM_DELETE_ANY):
            raise NotFound("资产不存在")
        # 草稿可以改（它还没进库），回收站不行：删除的语义是「冻结后再决定」，
        # 能继续改名换标签意味着 purge 前的写入没有业务含义，而 admin 还能
        # 静默改别人回收站里的内容。
        if row.state == "trash":
            raise AssetNotEditable()
        self._check_version(row, payload.expected_version)
        if payload.name is not None:
            # 名字里的格式后缀由**文件本身**决定，不由用户输入决定
            row.name = keep_extension(row.name, row.storage_key, payload.name)
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
        """流式上传。临时文件在 http 请求体内写满即落盘，300MB 不进内存。

        `source_key` 由客户端传，只表达「我重复传同一个文件别落两条」，没有
        跨用户语义。但 `uq_library_assets_source_key` 是**全局**唯一、
        `_by_source_key()` 也不带 owner：不隔离的话，别人用同一个 key 上传会
        直接命中**你的**行——他的文件被静默丢弃，返回的 id 还是你的。
        加 actor 前缀把上传的命名空间按人隔开；生成侧（`module:taskId`）的 key
        不进这条路径，不受影响。
        """
        extension = Path(filename).suffix.lower()
        mime = EXTENSIONS.get(extension)
        if mime is None or _kind_of(mime) != kind.value:
            raise LibraryFileTypeNotAllowed()
        # 文本走独立的一档上限：正文会被整份读进内存做注入，300MB 的
        # 「提示词」既没意义也会把 worker 打爆。这里先卡请求体，探测里再卡一次
        # 规范化后的字节（BOM 剥离不改变上限，但两道都要有）。
        text_upload = kind is UploadKind.TEXT
        if text_upload and extension not in TEXT_EXTENSIONS:
            raise LibraryFileTypeNotAllowed()
        scoped_key = f"upload:{actor.id}:{source_key}" if source_key else None
        with tempfile.TemporaryDirectory(prefix="library-upload-") as folder:
            temporary = Path(folder) / f"source{extension}"
            size = await self._receive(
                temporary,
                chunks,
                limit=MAX_TEXT_BYTES if text_upload else MAX_BYTES,
                too_large=LibraryTextTooLarge if text_upload else LibraryFileTooLarge,
            )
            return await self._ingest(
                temporary,
                filename=filename,
                extension=extension,
                mime=mime,
                size=size,
                kind=kind.value,
                actor=actor,
                origin=origin,
                source_key=scoped_key,
            )

    # ------------------------------------------------------------------ 内部

    async def _receive(
        self,
        target: Path,
        chunks: AsyncIterator[bytes],
        *,
        limit: int = MAX_BYTES,
        too_large: type[AppError] = LibraryFileTooLarge,
    ) -> int:
        total = 0
        stream = await asyncio.to_thread(target.open, "xb")
        try:
            async for chunk in chunks:
                if not chunk:
                    continue
                total += len(chunk)
                if total > limit:
                    raise too_large(details={"maxBytes": limit})
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
        is_text = kind == "text"
        if is_text:
            # `inspect` 会剥掉 BOM 并**原地回写**规范化后的字节，所以读回的大小
            # 与请求体大小不同是预期结果：内容寻址的 key 必须以规范化后的字节为准，
            # 否则同一段文字带不带 BOM 会存成两份，sha256 与编辑器正文也对不上。
            size = measured
        elif measured != size:
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
        if kind not in {"audio", "text"}:
            cover_key = await self._ensure_preview(path, digest, kind)
        # 文本正文同时进 `library_text_versions`：注入口只读这张表就能拿到
        # 「当前第几版、正文是什么」，不必为每条引用再开一次对象存储的读。
        text_content = (
            decode_text(await asyncio.to_thread(path.read_bytes)) if is_text else ""
        )

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
            text_excerpt=text_excerpt(text_content) if is_text else "",
            text_version=1,
            owner_id=actor.id,
            visibility="personal",
            state="active",
            state_version=1,
            source_key=source_key or None,
            created_at=datetime.now(UTC),
        )
        try:
            self.session.add(row)
            if is_text:
                # `UUIDMixin.id` 是 flush 期默认值，构造完还拿不到；先 flush
                # 再插版本行，两次写仍在同一个事务里（commit 一起提交）。
                await self.session.flush()
                self.session.add(
                    LibraryTextVersion(
                        asset_id=row.id,
                        version=1,
                        content=text_content,
                        sha256=digest,
                        size_bytes=size,
                        note="上传" if origin == "upload" else "站内新建",
                        created_by=actor.id,
                    )
                )
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

    async def _serve(
        self,
        key: str,
        mime: str,
        *,
        filename: str | None = None,
        proxy: bool = False,
        size: int | None = None,
        headers: dict[str, str] | None = None,
    ) -> Served:
        path = self.storage.local_path(key)
        if path is not None:
            return Served.local(path, mime, filename=filename, headers=headers)
        if proxy:
            # 原文件这条路的消费方全是 XHR：详情页播放音视频也是 `fetch` 成 blob
            # 再 `createObjectURL`，下载原件同样是 blob。`307` 到对象存储会让浏览器
            # 发起跨域请求，目标不回 CORS 头就被拦成
            # 「blocked by CORS policy」（MinIO 的 `PutBucketCors` 当前是 NotImplemented）。
            # 同源转发不依赖任何桶配置。
            return Served.stream(
                self.storage.iter_chunks(key),
                mime,
                filename=filename,
                size=size or None,
                headers=headers,
            )
        # 缩略图走 `<img>` / `<video poster>`：不受 CORS 限制，交给对象存储
        # 省掉一次中转，Range 也由它处理。
        return Served.remote(
            await self.storage.presigned_download_url(key), mime, headers=headers
        )

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

        四个可见范围**各自按自己的入口口径算**，与列表用的是同一对条件
        （`tab_clause` + `hidden_by_pack_clause(tab)`）。不能图省事用一次
        「全量可见集」的 group by 分发四个数字：我把一条共享到小组的素材装进
        **自己的个人封装**时，小组列表不该隐藏它（个人封装在小组入口不可见），
        而全量口径会隐藏——角标于是显示 0，点进去却有 1 条。

        管理员（`draft:review` / `delete:any`）看到的草稿与回收站角标是全量的，
        否则「待我审核」的入口显示 0，而实际有一堆在等。
        """
        facets = LibraryFacets()

        async def visible_count(tab: LibraryTab | None) -> int:
            scope = (
                access.active_visible_clause(actor, groups)
                if tab is None
                else access.tab_clause(actor, groups, tab)
            )
            query = (
                select(func.count())
                .select_from(LibraryAsset)
                .where(
                    LibraryAsset.state == "active",
                    LibraryAsset.deleted_at.is_(None),
                    scope,
                    # 与列表同口径：成员不单列，角标也不该把它算进去，
                    # 否则「个人 4」点进去只看到 1 条。
                    access.hidden_by_pack_clause(actor, groups, tab),
                )
            )
            return int(await self.session.scalar(query) or 0)

        facets.all = await visible_count(None)
        facets.personal = await visible_count(LibraryTab.PERSONAL)
        facets.group = await visible_count(LibraryTab.GROUP)
        facets.team = await visible_count(LibraryTab.TEAM)

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

    async def _tags_of(self, asset_ids: Sequence[str]) -> dict[str, list[LibraryTagOut]]:
        """一页资产的标签，一次查完。

        列表页要显示标签，但卡片上只有名字没有 id —— 逐条拉详情才能拿到标签，
        24 条就是 24 次请求。这里按 `asset_id IN (...)` 一次取回，再在内存里分组；
        分组用 `setdefault` 保持「同一资产内按标签名排序」。
        """
        if not asset_ids:
            return {}
        # 顺手取父名：卡片要显示「产品 / 3030」，而卡片拿不到整份词表，
        # 为了一个名字让前端再拉一次 `/library/tags` 不值当。
        parent = aliased(LibraryTag)
        rows = await self.session.execute(
            select(
                LibraryAssetTag.asset_id,
                LibraryTag.id,
                LibraryTag.name,
                LibraryTag.parent_id,
                parent.name,
            )
            .join(LibraryTag, LibraryTag.id == LibraryAssetTag.tag_id)
            .outerjoin(parent, parent.id == LibraryTag.parent_id)
            .where(LibraryAssetTag.asset_id.in_(list(asset_ids)))
            .order_by(LibraryTag.name)
        )
        grouped: dict[str, list[LibraryTagOut]] = {}
        for asset_id, tag_id, name, parent_id, parent_name in rows:
            grouped.setdefault(asset_id, []).append(
                LibraryTagOut(
                    id=tag_id,
                    name=name,
                    parent_id=parent_id or None,
                    parent_name=parent_name,
                    depth=2 if parent_id else 1,
                )
            )
        return grouped

    def _to_out(
        self,
        row: LibraryAsset,
        *,
        owner_name: str | None = None,
        group_ids: Sequence[str] = (),
        tags: Sequence[LibraryTagOut] = (),
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
            url=f"{settings.api_prefix}/library/assets/{row.id}/file",
            preview_url=(
                f"{settings.api_prefix}/library/assets/{row.id}/thumbnail"
                if row.cover_key
                else None
            ),
            stream_url=(
                f"{settings.api_prefix}/library/assets/{row.id}/stream"
                if row.kind == "video"
                else None
            ),
            width=row.width or None,
            height=row.height or None,
            group_ids=list(group_ids),
            tags=list(tags),
            trash_reason=row.trash_reason,
            trash_expires_at=row.trash_expires_at,
            text_excerpt=(row.text_excerpt or None) if row.kind == "text" else None,
            text_version=row.text_version if row.kind == "text" else 0,
            text_url=(
                f"{settings.api_prefix}/library/assets/{row.id}/text/raw"
                if row.kind == "text"
                else None
            ),
            created_at=row.created_at,
            updated_at=row.updated_at,
        )
