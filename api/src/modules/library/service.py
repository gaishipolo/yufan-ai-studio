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
from collections.abc import AsyncIterator, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased
from sqlalchemy.sql.elements import ColumnElement

from core.audit import record as audit
from core.config import settings
from core.context import CurrentUser
from core.exceptions import AppError, BadRequest, Conflict, NotFound
from core.ports.library_references import ask_library_references, library_references_ready
from core.response import PageParams
from core.serving import Served
from infra.archive.blobs import normalize_suffix
from infra.media_probe import (
    EXTENSIONS,
    LARGE_PREVIEW_VERSION,
    MAX_BYTES,
    MAX_TEXT_BYTES,
    PREVIEW_VERSION,
    TEXT_EXTENSIONS,
    create_browser_preview,
    create_large_preview,
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
    PackMemberOut,
    PackOut,
    PackSummaryOut,
    ShareHistoryItem,
    UploadKind,
)
from modules.library.tags import (
    add_asset_tags,
    all_tags,
    set_asset_tags,
    tag_filter_clause,
    tag_ids_by_asset,
    tag_tree_index,
)

__all__ = ["LibraryService"]

logger = logging.getLogger(__name__)

#: 主字节前缀。与旧归档盘（`blobs/`）彻底分开：删 v2 模块不会误删旧库字节，
#: 反过来旧库的 `prune-blobs` 也扫不到这里。
BLOB_PREFIX = "library/blobs"
PREVIEW_PREFIX = "library/previews"

def _packed_member_clause() -> ColumnElement[bool]:
    """「这条素材被某个**还在库里的**封装收录」。

    成员不再单列（2026-11-19 用户拍板）：一条图进了包，就只在包里出现，
    个人库里不再单列 —— 否则同一张图既要挑「包」又要挑「图」，包的意义减半。

    落在 **SQL** 里而不是滤 `items`：`total` 与 `items` 必须同口径，
    否则最后一页点进去是空的、侧栏角标与列表也对不上（与 `exclude_kinds` 同理）。

    **要判包本身的状态**：包被移入回收站之后，它自己只在回收站里，而成员又被
    这一条排除在个人库之外 —— 那几张图会「凭空消失」，用户得进回收站才找得到。
    包不在库（回收站 / 已彻底删）时，成员就该回到个人库。
    """
    pack = aliased(LibraryAsset)
    return (
        select(LibraryPackItem.id)
        .join(pack, pack.id == LibraryPackItem.pack_id)
        .where(
            LibraryPackItem.asset_id == LibraryAsset.id,
            # 只看 state：`deleted_at` 是「移入回收站的时间」，不是「彻底删除」——
            # 拿它当「还在不在」用，会把回收站里的包也一起排掉。
            pack.state == "active",
        )
        .exists()
    )


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
    """`library/blobs/ab/abcdef…{ext}`：前两位分桶，避免单目录几十万文件。

    扩展名过 `normalize_suffix`（与归档层**同一个函数**）：这个字符串会被拼进
    对象 key，而 key 有可能落成文件路径。上游现在确实用白名单挡住了（`upload`
    先 `Path(filename).suffix` 再查 `EXTENSIONS`），但「函数本身挡住」和「调用方
    恰好挡住」是两件事 —— 后者换一个调用点就失效，而失效的样子是路径穿越
    或两条内容相同的素材落到两个不同的 key 上（`.PNG` vs `.png`）。
    """
    return f"{BLOB_PREFIX}/{digest[:2]}/{digest}{normalize_suffix(extension)}"


def preview_key(digest: str) -> str:
    return f"{PREVIEW_PREFIX}/{digest[:2]}/{digest}.jpg"


def large_preview_key(digest: str) -> str:
    """悬浮大图的存放位置：与封面同前缀、文件名带尺寸档。

    带 `LARGE_PREVIEW_VERSION` 是为了将来调尺寸时旧缓存自动失效 ——
    与 `clip_key` 同一个道理：参数一变，所有缓存必须一起换代。
    """
    return (
        f"{PREVIEW_PREFIX}/{digest[:2]}/{digest}.large-{LARGE_PREVIEW_VERSION}.jpg"
    )


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
    两者都没有（人工建的行）就不加——别给「春季素材包」硬塞一个 `.png`。
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
        reference_library_ids=list(row.reference_library_ids or []),
        # 老行可能没有这一列，`or []` 兜住 NULL（JSON 列在 SQLite 上可能是 None）
        pack_id=row.pack_id,
        pack_version=row.pack_version,
        prompt_spec=dict(row.prompt_spec or {}),
        configuration=dict(row.configuration or {}),
        external_id=row.external_id,
        credits=row.credits,
        generated_at=row.generated_at,
    )


#: 能当「模板库展示案例」的媒体类型。音频没有封面、文本没有画幅，卡片上
#: 显示不出来 —— 两条入口（`upload` 带 purpose、`set_purpose` 改用途）都必须
#: 过这道闸，否则就能造出一个「全员可读但谁也看不到东西」的案例。
CASE_KINDS = frozenset({"image", "video"})


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
        exclude_kinds: Sequence[str] = (),
        #: 资产库列表要藏起「已被封装收录」的成员（成员不单列）；选择器**不藏**。
        #:
        #: 两者不能共用一个口径：列表是「看我的库」，成员挤在这里只会让同一张图
        #: 出现两次；选择器是「挑素材去用」，藏掉就等于那几张图选不到了 ——
        #: 模板库里**没进封装**的槽位都是按素材挑的，藏了会立刻表现为
        #: 「我上传的图不见了」。
        exclude_packed_members: bool = True,
        purpose: str | None = None,
        exclude_purposes: Sequence[str] = (access.PURPOSE_CASE,),
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
        if purpose:
            query = query.where(LibraryAsset.purpose == purpose)
        if exclude_purposes and state == "active":
            # 案例默认不进「全部 / 个人 / 小组 / 团队」：它们是模板的展示素材，
            # 有自己的池子。排除同样落在 SQL 里，理由与 `exclude_kinds` 一样
            # （`total` 与 `items` 必须同口径，否则翻页翻到空页）。
            #
            # **只对 active 生效**：草稿箱与回收站是「我自己的东西」，一条被删掉的案例
            # 必须还能在回收站里找回来。若是还排除案例，则「删了就消失」，
            # 而角标又把它算进去了（`facets.trash` 不按用途过滤）。
            query = query.where(LibraryAsset.purpose.not_in(list(exclude_purposes)))
        if state == "active" and exclude_packed_members:
            # 成员不单列：进了包的素材只在包里出现。
            query = query.where(~_packed_member_clause())
        if keyword:
            query = query.where(LibraryAsset.name.ilike(f"%{keyword}%"))
        if tag_ids:
            # 标签筛选：**组内 OR、组间 AND**（两级维度模型）。
            # 选中一级 = 它的全部取值；选二级 = 该取值本身。
            # 跨维度取交集：选「画面类型/正面图」+「产品/6036」= 既是 6036
            # 又是正面图，不是两者的并集。
            clause = tag_filter_clause(await all_tags(self.session), tag_ids)
            if clause is not None:
                query = query.where(clause)

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
        count_map = await self._pack_member_counts(
            [row.id for row in rows if row.kind == "pack"]
        )
        # 「这张图在哪个包里」：列表把成员藏起来了，只有选择器看得到它们，
        # 所以只在**不藏成员**的那条路径上查一次，别给资产库列表白加一次 join。
        pack_of = await self._pack_of(page_ids) if not exclude_packed_members else {}
        return LibraryAssetPage(
            items=[
                self._to_out(
                    row,
                    owner_name=owner_map.get(row.owner_id),
                    group_ids=group_map.get(row.id, []),
                    tags=tag_map.get(row.id, []),
                    member_count=count_map.get(row.id, 0),
                    pack_of=pack_of,
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
        # 包没有自己的字节：`url` 指向的是一份不存在的文件，不能当媒体预览，
        # 详情页的「媒体区」只能靠成员列表。
        members = (
            (await self._pack_members([row.id])).get(row.id, [])
            if row.kind == "pack"
            else []
        )
        member_tags = await tag_ids_by_asset(self.session, [m.id for m in members])
        return LibraryAssetDetailOut(
            **self._to_out(
                row,
                owner_name=owner_map.get(row.owner_id),
                group_ids=group_ids.get(row.id, []),
                tags=tags,
                member_count=len(members),
            ).model_dump(by_alias=False),
            groups=[
                LibraryGroupRef(id=group_id, name=names.get(group_id, ""))
                for group_id in group_ids.get(row.id, [])
            ],
            generation=snapshot_out(generation) if generation else None,
            references=references,
            share_history=await self._share_history(row),
            archive=self._archive(row, actor, references),
            reviewed_by=row.reviewed_by,
            reviewed_at=row.reviewed_at,
            reject_reason=row.reject_reason,
            trashed_from_state=row.trashed_from_state,
            pack_items=[
                PackMemberOut(
                    id=member.id,
                    name=member.name,
                    kind=member.kind,
                    cover_url=(
                        self.storage.url_for(member.cover_key) if member.cover_key else None
                    ),
                    tag_ids=list(member_tags.get(member.id, ())),
                    deleted=member.deleted_at is not None,
                )
                for member in members
            ],
        )

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

    async def large(self, asset_id: str, actor: CurrentUser) -> Served:
        """悬浮大图：长边 1280 的派生 JPEG，**按摘要缓存**。

        为什么要有这一档：封面只有 360×240，网格里是几十像素的方块，
        产品图的走线、缝线、logo 全看不清。用户得「先看清再选」，
        所以另备一档大图 —— 悬浮时才取，点一下仍然是选中。

        **为什么不是直接给原图**：原图平均 2.6MB、最大 9.5MB，
        在网格里来回悬停就是上百 MB。缩到 1280 长边是几百 KB，
        够看清细节又不至于拖垮页面。

        **派生是惰性的、且按内容摘要缓存**：第一次悬浮才生成，
        同一份原图（摘要相同）永远复用同一个 key —— 换一条资产记录指向
        同一份字节时，第二次直接命中缓存。

        派生失败不抛 500：原图可能真的坏了，那属于「这张素材没有预览」，
        明确回 404，前端退回显示缩略图。
        """
        row = await access.load_visible(self.session, asset_id, actor)
        if row.state == "trash":
            raise NotFound("回收站内的资产不提供预览与下载")
        if row.kind == "text":
            raise NotFound("文本资产没有大图预览")
        if row.kind == "audio":
            raise NotFound("音频资产没有大图预览")

        digest = row.sha256 or ""
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            # 没有摘要就派生不了（key 是内容寻址的）。这种情况极少 ——
            # 正常入库都会算摘要 —— 但回退到封面比报错好。
            if not row.cover_key:
                raise NotFound("此资产没有大图预览")
            return await self._serve(row.cover_key, "image/jpeg")

        derived = large_preview_key(digest)
        if not await self.storage.exists(derived):
            await self._derive_large_preview(row, derived)
        if not await self.storage.exists(derived):
            raise NotFound("此资产没有大图预览")
        # 大图由前端 XHR 取 blob（`<img>` 直链要跨域的话会被桶的 CORS 拦），
        # 所以走同源转发，不依赖任何桶配置。
        return await self._serve(derived, "image/jpeg", proxy=True)

    async def _derive_large_preview(self, row: LibraryAsset, derived: str) -> bool:
        """从原图派生大图并写回存储。全程不把原图整块读进内存。"""
        if not row.storage_key:
            return False
        with tempfile.TemporaryDirectory(prefix="library-large-") as folder:
            source = Path(folder) / f"source{Path(row.storage_key).suffix or '.png'}"
            try:
                with source.open("wb") as handle:
                    async for chunk in self.storage.iter_chunks(row.storage_key):
                        handle.write(chunk)
            except StorageError:
                logger.warning("原图字节读取失败，跳过大图派生：%s", row.id)
                return False
            target = Path(folder) / "large.jpg"
            if not await asyncio.to_thread(create_large_preview, source, target):
                return False
            await self.storage.put_file(derived, target, "image/jpeg")
            return True

    async def preview(self, asset_id: str, actor: CurrentUser) -> Served:
        """受控公共预览：视频给派生流，图片 / 音频给原文件。

        与旧 `/assets/{id}/preview` 同口径。能不能看只由 `load_visible` 一家
        说了算（共享到小组 / 团队都算可见），这里不再写第二份
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

    async def set_purpose(
        self, asset_id: str, purpose: str, actor: CurrentUser, expected_version: int = 0
    ) -> LibraryAssetOut:
        """把一条素材移入案例池（`purpose='case'`），或移回普通素材（空串）。

        「选已有素材当案例」走这里，**不复制字节**：模板引用的是同一个 id，
        作者在资产库里换封面、改名，模板卡片立刻跟着变；也不会多出一份存储。

        规则：

        - 只能改自己的：别人的素材即使可见（团队库/案例池）也不能被悄悄改成案例，
          那等于替人决定了它的用途。返回 404 而不是 403 —— 不确认 id 的存在性。
        - 只有图片/视频能当案例：音频没有封面、文本没有画幅，卡片上显示不出来。
        - 幂等：已经是目标用途就直接返回，不涨 `state_version`（前端确认框点两次
          不该把版本推高两次）。
        """
        row = await access.load_visible(self.session, asset_id, actor)
        if row.owner_id != actor.id and not actor.has(access.PERM_DELETE_ANY):
            raise NotFound("资产不存在")
        if row.state != "active":
            raise BadRequest("只有库内的素材可以设为案例")
        self._check_version(row, expected_version)
        target = purpose.strip().lower()
        if target not in {"", access.PURPOSE_CASE}:
            raise BadRequest(f"不支持的用途：{purpose}")
        if target == access.PURPOSE_CASE and row.kind not in CASE_KINDS:
            raise BadRequest("案例只支持图片或视频")
        if row.purpose == target:
            return self._to_out(row, owner_name=None)
        row.purpose = target
        row.state_version += 1
        await self.session.commit()
        await audit(
            "library.asset.purpose",
            actor_id=actor.id,
            target_type="library_asset",
            target_id=row.id,
            detail={"purpose": target, "ownerId": row.owner_id},
        )
        return self._to_out(row)

    async def upload(
        self,
        chunks: AsyncIterator[bytes],
        filename: str,
        *,
        kind: UploadKind,
        actor: CurrentUser,
        origin: str = "upload",
        source_key: str | None = None,
        purpose: str = "",
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
        # 直接以「案例」上传时也要卡媒体类型：`purpose=case` 对**所有登录用户**
        # 放行，一个音频/文本案例就是一条人人可读却没有任何呈现形式的东西。
        if purpose.strip().lower() == access.PURPOSE_CASE and kind.value not in CASE_KINDS:
            raise BadRequest("案例只支持图片或视频")
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
                purpose=purpose,
            )

    # ------------------------------------------------------------------ 封装
    async def create_pack(
        self, actor: CurrentUser, *, name: str, asset_ids: Sequence[str]
    ) -> PackOut:
        """把一组素材打成一个封装（一行 `kind=pack` 的资产）。

        **成员仍然是自己的资产行**：这里只记归属，不复制字节、不改成员的
        标签/可见性/回收站状态。所以「删包不删成员」，成员也不能同时属于两个包
        之外的任何容器概念。

        包自动继承成员的**共同标签**：一份封装的成员都是同一件产品，`6036`
        这种标签对整包都有意义；而 `正面图 / 三视图` 是成员各自独有的，**不继承**
        —— 那正是取图时区分成员的依据，打进包里反而会让整包看起来「有所有角色」。
        """
        ids = [item for item in dict.fromkeys(asset_ids) if item]
        readable = await self._validate_pack_members(ids, actor)
        common = await self._common_tags(ids)

        pack = LibraryAsset(
            name=name.strip()[:200] or "未命名封装",
            kind="pack",
            origin="upload",
            mime_type="",
            size_bytes=0,
            storage_key="",
            cover_key=self._pack_cover(ids, readable),
            owner_id=actor.id,
            visibility="personal",
            state="active",
            purpose="",
            state_version=1,
            created_at=datetime.now(UTC),
        )
        self.session.add(pack)
        await self.session.flush()  # 主键在 flush 之后才有

        for position, asset_id in enumerate(ids):
            self.session.add(
                LibraryPackItem(pack_id=pack.id, asset_id=asset_id, position=position)
            )

        await add_asset_tags(self.session, pack.id, sorted(common))

        await self.session.commit()
        return await self.get_pack(actor, pack.id)

    async def update_pack(
        self,
        actor: CurrentUser,
        pack_id: str,
        *,
        name: str | None = None,
        asset_ids: Sequence[str] | None = None,
    ) -> PackOut:
        """改封装：名字、成员。

        **只传要改的那个**（`None` = 不动），两个操作因此可以独立发生，
        不必让调用方把另一份也原样回传一遍。

        换成员之后包身上的标签**重算成新成员的共同标签**（完全替换）：包上的
        标签是「成员共同标签的投影」，不是一份独立维护的数据。增量维护会留下
        一堆「上一次的共同标签」，而它们已经不再共同了 —— 模板库正是靠这个
        投影判断「这个包能不能填那一格」。
        """
        row = await self.session.scalar(
            select(LibraryAsset).where(
                LibraryAsset.id == pack_id, LibraryAsset.kind == "pack"
            )
        )
        # 别人的包一律 404：既不说「存在但没权限」，也不泄露别人的库结构。
        if (
            row is None
            or row.state != "active"
            or (row.owner_id != actor.id and not actor.has(access.PERM_DELETE_ANY))
        ):
            raise NotFound("封装不存在")

        if name is not None:
            row.name = name.strip()[:200] or "未命名封装"

        if asset_ids is not None:
            ids = [item for item in dict.fromkeys(asset_ids) if item]
            readable = await self._validate_pack_members(
                ids, actor, exclude_pack_id=row.id
            )
            common = await self._common_tags(ids)

            # 整体替换：先清旧成员再写新的，顺序也一起换掉。
            await self.session.execute(
                delete(LibraryPackItem).where(LibraryPackItem.pack_id == row.id)
            )
            for position, asset_id in enumerate(ids):
                self.session.add(
                    LibraryPackItem(pack_id=row.id, asset_id=asset_id, position=position)
                )
            row.cover_key = self._pack_cover(ids, readable)
            # 标签**完全替换**（不是追加）：不再共同的必须掉下去
            await set_asset_tags(self.session, row.id, sorted(common))

        row.state_version = (row.state_version or 1) + 1
        await self.session.commit()
        return await self.get_pack(actor, row.id)

    async def _validate_pack_members(
        self,
        ids: Sequence[str],
        actor: CurrentUser,
        *,
        exclude_pack_id: str | None = None,
    ) -> dict[str, LibraryAsset]:
        """建包 / 改成员共用的一份校验。返回可读的成员行。

        `exclude_pack_id` 是「别把自己算成冲突」：改成员时，本来就在这个包里
        的那些素材当然还属于它，不该被唯一性检查拦下来。
        """
        if not ids:
            raise BadRequest("封装至少要含一张素材")

        readable = await access.readable_rows(self.session, ids, actor)
        missing = [item for item in ids if item not in readable]
        if missing:
            raise BadRequest(f"这些素材不存在或无权访问：{'、'.join(missing[:5])}")
        if any(row.kind == "pack" for row in readable.values()):
            raise BadRequest("封装不能嵌套：成员只能是素材，不能再是封装")

        # 一张图只能属于一个包。不拦的话，「6036 黑」和「6 月新品」都含同一张
        # 正面图，改一个包的成员会连带影响另一个包的取图 —— 而使用者以为
        # 两个包互不相干。选择器会把这些素材标灰，这里是最后一道闸。
        # 连**回收站里的包**一起看：那些包还能恢复，恢复之后同一张图就属于两个包了。
        # 报错必须说清它在回收站，否则用户翻遍资产库也找不到那个包。
        taken = await self._pack_of(ids, include_trashed=True)
        if exclude_pack_id:
            taken = {
                key: value for key, value in taken.items() if value[0] != exclude_pack_id
            }
        if taken:
            labels = "、".join(
                f"「{pack_name}」" + ("（在回收站里）" if state == "trash" else "")
                for _, pack_name, state in list(taken.values())[:3]
            )
            raise Conflict(f"这些素材已经属于封装{labels}，一张素材只能进一个封装")
        return readable

    async def _common_tags(self, ids: Sequence[str]) -> set[str]:
        """成员**共有**的标签。空集 = 打不成一个封装（这里直接报错）。

        成员必须至少共有一个标签：封装的意思是「同一件东西的几个视角」
        （同一产品、同一颜色），而这件事在系统里唯一能判的信号就是标签 ——
        `6036` 这类产品编号是成员共同带的，`正面图 / 三视图` 是各自独有的。
        一组没有共同标签的图，打出来的包在模板库选择器里也没有依据归到任何
        槽位（那里正是靠「成员标签 ∩ 槽位候选标签」判断能不能填）。

        放在建行**之前**：先落一行包再报错，会留下一条没有成员关系的空壳。
        """
        tags = await tag_ids_by_asset(self.session, ids)
        common = set(tags.get(ids[0], []))
        for asset_id in ids[1:]:
            common &= set(tags.get(asset_id, []))
        if not common:
            raise BadRequest(
                "这些素材没有共同的标签，打不成一个封装："
                "先给它们打上同一个标签（比如产品编号）再来"
            )
        return common

    @staticmethod
    def _pack_cover(ids: Sequence[str], readable: dict[str, LibraryAsset]) -> str:
        """封面取第一个**有封面**的图片/视频成员的封面。

        包没有自己的字节，卡片上那张图就是成员的封面 —— 取不到就留空，
        前端会退回类型图标，不会画一个破图。
        """
        return next(
            (
                readable[item].cover_key
                for item in ids
                if readable[item].cover_key and readable[item].kind in {"image", "video"}
            ),
            "",
        )

    async def get_pack(self, actor: CurrentUser, pack_id: str) -> PackOut:
        row = await self.session.scalar(
            select(LibraryAsset).where(
                LibraryAsset.id == pack_id, LibraryAsset.kind == "pack"
            )
        )
        if row is None or row.deleted_at is not None or row.state != "active":
            raise NotFound("封装不存在")
        members = (await self._pack_members([row.id])).get(row.id, [])
        tags = await tag_ids_by_asset(self.session, [member.id for member in members])
        return self._pack_out(row, members, tags)

    async def list_packs(
        self,
        actor: CurrentUser,
        *,
        tag_ids: Sequence[str] | None = None,
        keyword: str = "",
        mode: str = "any",
    ) -> list[PackSummaryOut]:
        """选择器用的包列表。

        `tag_ids` 是**槽位的候选标签**。
        - `mode=any`（默认）：与包内成员标签有**交集**就返回 —— 「这个包能不能填
          这一格」。单槽位选择器用这一档。
        - `mode=all`：成员标签的并集必须**覆盖全部** `tag_ids` —— 「这个包能不能
          把这一组格子都填上」。封装容器用这一档：容器里有几格，包就得几张对得上，
          否则点下去只填一半。

        容器传的是**具体的二级标签**（如 6036 + 正面图 + 三视图）：一级标签是维度，
        素材身上不会挂它，放进 `all` 里会把所有包都排除掉。
        """
        groups = await access.all_groups(self.session, actor)
        query = select(LibraryAsset).where(
            LibraryAsset.kind == "pack",
            LibraryAsset.state == "active",
            LibraryAsset.deleted_at.is_(None),
            access.tab_clause(actor, groups, LibraryTab.ALL),
        )
        if keyword:
            query = query.where(LibraryAsset.name.ilike(f"%{keyword}%"))
        rows = list(
            (await self.session.scalars(query.order_by(LibraryAsset.created_at.desc()))).all()
        )
        if not rows:
            return []

        members = await self._pack_members([row.id for row in rows])
        tags = await tag_ids_by_asset(
            self.session, [member.id for group in members.values() for member in group]
        )
        wanted = set(tag_ids or ())
        result: list[PackSummaryOut] = []
        for row in rows:
            group = members.get(row.id, [])
            # 判据是「这个包能不能填那一格」，而**已进回收站的成员不参与分发**
            # （前端分发时跳过 `deleted`）。所以这里也必须只看还在库里的成员：
            # 否则包里唯一那张「细节图」删了之后，这个包仍然出现在「细节图」
            # 那一格的网格里，点下去一格都填不上。
            # 卡片上的「N 张」仍按成员总数算（详情里会把已删的那张照列并标出来）。
            active = [member for member in group if member.state == "active"]
            union = self._union_tags(active, tags)
            if wanted:
                hit = wanted & set(union)
                if not hit or (mode == "all" and hit != wanted):
                    continue
            result.append(
                PackSummaryOut(
                    id=row.id,
                    name=row.name,
                    cover_url=self.storage.url_for(row.cover_key) if row.cover_key else None,
                    member_count=len(group),
                    tag_ids=union,
                )
            )
        return result

    async def _pack_member_counts(self, pack_ids: Sequence[str]) -> dict[str, int]:
        """一批包的成员数（列表卡片要显示「N 张」）。**不 join 资产行**：
        只数行数，一条 `group by` 就够。"""
        wanted = [item for item in dict.fromkeys(pack_ids) if item]
        if not wanted:
            return {}
        rows = (
            await self.session.execute(
                select(LibraryPackItem.pack_id, func.count())
                .where(LibraryPackItem.pack_id.in_(wanted))
                .group_by(LibraryPackItem.pack_id)
            )
        ).all()
        return {pack_id: int(count) for pack_id, count in rows}

    async def _pack_of(
        self, asset_ids: Sequence[str], *, include_trashed: bool = False
    ) -> dict[str, tuple[str, str, str]]:
        """素材 id → (所属封装 id, 封装名, 封装状态)。**一张图只属于一个包**，
        所以这里可以放心用 1:1 的字典。

        `include_trashed` 的两处用途**结论相反**，不能共用一个口径：

        - 界面标「已在封装「X」里」只该看**在库**的包（`False`）—— 包进了回收站，
          这几张图在个人库里已经能正常挑了，还标一句「已在封装里」会让人以为选不了；
        - 建包的唯一性校验要连**回收站里的包**一起看（`True`）：那些包还能恢复，
          恢复之后同一张图就属于两个包了。这时报错要说清它在回收站里，
          否则用户翻遍资产库也找不到那个包。
        """
        wanted = [item for item in dict.fromkeys(asset_ids) if item]
        if not wanted:
            return {}
        query = (
            select(
                LibraryPackItem.asset_id,
                LibraryAsset.id,
                LibraryAsset.name,
                LibraryAsset.state,
            )
            .join(LibraryAsset, LibraryAsset.id == LibraryPackItem.pack_id)
            .where(LibraryPackItem.asset_id.in_(wanted))
        )
        if not include_trashed:
            query = query.where(LibraryAsset.state == "active")
        rows = (await self.session.execute(query)).all()
        return {
            asset_id: (pack_id, pack_name, state)
            for asset_id, pack_id, pack_name, state in rows
        }

    async def _pack_members(
        self, pack_ids: Sequence[str]
    ) -> dict[str, list[LibraryAsset]]:
        """一批包各自的成员，按 `position` 排好。

        **不过滤被删的成员**：包静默少一张比露出来更糟 —— 使用者会以为
        「这个包本来就这样」，而取图时会当场报缺，两处对不上。
        """
        wanted = [item for item in dict.fromkeys(pack_ids) if item]
        if not wanted:
            return {}
        rows = (
            await self.session.execute(
                select(LibraryPackItem, LibraryAsset)
                .join(LibraryAsset, LibraryAsset.id == LibraryPackItem.asset_id)
                .where(LibraryPackItem.pack_id.in_(wanted))
                .order_by(LibraryPackItem.pack_id, LibraryPackItem.position)
            )
        ).all()
        result: dict[str, list[LibraryAsset]] = {}
        for item, asset in rows:
            result.setdefault(item.pack_id, []).append(asset)
        return result

    @staticmethod
    def _union_tags(
        members: Sequence[LibraryAsset], tags: Mapping[str, Sequence[str]]
    ) -> list[str]:
        """成员标签的并集，**保序去重**（先出现先保留）。"""
        union: list[str] = []
        seen: set[str] = set()
        for member in members:
            for tag_id in tags.get(member.id, ()):
                if tag_id and tag_id not in seen:
                    seen.add(tag_id)
                    union.append(tag_id)
        return union

    def _pack_out(
        self,
        row: LibraryAsset,
        members: Sequence[LibraryAsset],
        tags: Mapping[str, Sequence[str]],
    ) -> PackOut:
        return PackOut(
            id=row.id,
            name=row.name,
            cover_url=self.storage.url_for(row.cover_key) if row.cover_key else None,
            member_count=len(members),
            tag_ids=self._union_tags(members, tags),
            members=[
                PackMemberOut(
                    id=member.id,
                    name=member.name,
                    kind=member.kind,
                    cover_url=(
                        self.storage.url_for(member.cover_key) if member.cover_key else None
                    ),
                    tag_ids=list(tags.get(member.id, ())),
                    deleted=member.deleted_at is not None,
                )
                for member in members
            ],
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
        purpose: str = "",
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
            purpose=purpose,
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

        四个可见范围**各自按自己的入口口径算**，与列表用的是同一条
        `tab_clause`。不能图省事用一次「全量可见集」的 group by 分发四个数字：
        我把一条共享到小组的素材放进**自己的个人库**时，小组列表不该因此少算它，
        而全量口径会算——角标于是显示 0，点进去却有 1 条。

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
                    # 案例不算进任何入口的角标：它们在案例池里，不属于「个人 / 小组 /
                    # 团队 / 全部」。算进来的话，作者打开侧栏会看到自己多了几十条
                    # 素材，点进去却一条也找不到（列表默认排除案例）。
                    LibraryAsset.purpose != access.PURPOSE_CASE,
                    # 角标与列表同口径：进了包的成员不单列，也不单独计数。
                    ~_packed_member_clause(),
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
        # 父名给的是**祖先路径**（「产品 / 3030」）：三级标签要显示整条链，
        # 只给一层父名不够。层数也是算出来的（库里只有 parent_id）。
        # 卡片拿不到整份词表，为了一个名字让前端再拉一次 `/library/tags` 不值当。
        rows = await self.session.execute(
            select(
                LibraryAssetTag.asset_id,
                LibraryTag.id,
                LibraryTag.name,
                LibraryTag.parent_id,
            )
            .join(LibraryTag, LibraryTag.id == LibraryAssetTag.tag_id)
            .where(LibraryAssetTag.asset_id.in_(list(asset_ids)))
            .order_by(LibraryTag.name)
        )
        _parent, depth, path = tag_tree_index(await all_tags(self.session))
        grouped: dict[str, list[LibraryTagOut]] = {}
        for asset_id, tag_id, name, parent_id in rows:
            grouped.setdefault(asset_id, []).append(
                LibraryTagOut(
                    id=tag_id,
                    name=name,
                    parent_id=parent_id or None,
                    parent_name=path.get(parent_id) if parent_id else None,
                    depth=depth.get(tag_id, 1),
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
        member_count: int = 0,
        pack_of: Mapping[str, tuple[str, str, str]] | None = None,
    ) -> LibraryAssetOut:
        return LibraryAssetOut(
            id=row.id,
            name=row.name,
            kind=row.kind,
            purpose=row.purpose,
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
            member_count=member_count,
            # 这条素材被哪个封装收录（只在选择器里看得见 —— 列表里成员已被藏掉）
            pack_id=(pack_of or {}).get(row.id, (None, "", ""))[0],
            pack_name=(pack_of or {}).get(row.id, (None, "", ""))[1] or None,
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
