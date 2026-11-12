"""资产库 v2 路由，统一前缀 `/library`。

**与现役 `/assets` 完全隔离**：表前缀、权限码、注入口、前端路径全部独立，
切换时只改前端模块的 `load()` 一行，后端两套可以同时在线。

路径顺序有讲究：`/assets/bulk`、`/assets/upload` 必须声明在
`/assets/{asset_id}` **之前**。FastAPI 按声明顺序匹配，写在后面会被
`{asset_id}` 吃掉，然后返回「资产不存在」——这种错在 OpenAPI 里看不出来，
只有真的发一次请求才会发现。

M2 交付：三层可见集、共享与撤销审批、删除矩阵（含 §3.4.1 窄口）、草稿与回收站、
标签、批量写、选择器。封装与批量下载在 M3。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, status
from fastapi.responses import Response

from api.deps import PageDep, rate_limit
from core.config import settings
from core.ports.library_ingest import IngestRequest
from core.ports.library_lookup import LibraryAssetKind, LibraryAssetState
from core.response import ApiResponse
from modules.library.dependencies import (
    BulkDep,
    BulkServiceDep,
    DownloadServiceDep,
    GenerationIngestDep,
    LibraryCreateDep,
    LibraryDeleteDep,
    LibraryDraftDep,
    LibraryIngestDep,
    LibraryReadDep,
    LibraryServiceDep,
    LibraryShareApproveDep,
    LibraryShareDep,
    LibraryTagManageDep,
    LibraryUpdateDep,
    LifecycleServiceDep,
    PackServiceDep,
    ShareServiceDep,
    TagServiceDep,
    TextServiceDep,
)
from modules.library.schema import (
    AssetPatchIn,
    AssetSort,
    BulkIn,
    BulkOut,
    DownloadIn,
    DownloadJobOut,
    DraftDecisionIn,
    FromGenerationIn,
    GroupLeadersIn,
    IngestOut,
    LibraryAssetDetailOut,
    LibraryAssetOrigin,
    LibraryAssetOut,
    LibraryAssetPage,
    LibraryFacets,
    LibraryGroupOut,
    LibrarySelectionPage,
    LibraryTab,
    LibraryTagOut,
    LibraryTagStatOut,
    PackIn,
    PackPatchIn,
    ShareDecisionIn,
    ShareRequestIn,
    ShareRequestOut,
    TagIn,
    TagParentIn,
    TextAssetIn,
    TextAssetOut,
    TextAssetRestoreIn,
    TextAssetUpdateIn,
    TextAssetVersionOut,
    UploadKind,
)

router = APIRouter(prefix="/library", tags=["资产库"])


# --------------------------------------------------------------------------- #
# 浏览
# --------------------------------------------------------------------------- #
@router.get("/assets", summary="资产库列表（分页 + 分面）")
async def list_assets(
    actor: LibraryReadDep,
    page: PageDep,
    service: LibraryServiceDep,
    library: LibraryTab = LibraryTab.ALL,
    state: LibraryAssetState = LibraryAssetState.ACTIVE,
    kind: LibraryAssetKind | None = None,
    tag_ids: Annotated[list[str] | None, Query(alias="tagIds")] = None,
    keyword: str = Query(default="", max_length=100),
    sort: AssetSort = AssetSort.CREATED_DESC,
    origin: Annotated[list[LibraryAssetOrigin] | None, Query()] = None,
) -> ApiResponse[LibraryAssetPage]:
    """`state=draft|trash` 时忽略 `library`：草稿与回收站不在任何可见范围内。

    代审 / 管理员看全量靠权限点（`library:draft:review` / `library:delete:any`），
    不靠一个 `mine=false` 参数——参数能被任何人传，权限点不能。
    """
    return ApiResponse.ok(
        await service.list_assets(
            actor,
            page,
            library=library,
            state=state.value,
            kind=kind.value if kind else None,
            tag_ids=tag_ids,
            keyword=keyword,
            sort=sort,
            origins=[item.value for item in origin] if origin else [],
        )
    )


@router.get("/facets", summary="资产库分面统计")
async def facets(actor: LibraryReadDep, service: LibraryServiceDep) -> ApiResponse[LibraryFacets]:
    """给「不加载列表也要显示角标」的页面用（侧栏、首页卡片）。"""
    return ApiResponse.ok(await service.facets(actor))


@router.get("/selection", summary="选择器：只返回库内资产")
async def selection(
    actor: LibraryReadDep,
    page: PageDep,
    service: LibraryServiceDep,
    kind: LibraryAssetKind | None = None,
    tag_ids: Annotated[list[str] | None, Query(alias="tagIds")] = None,
    keyword: str = Query(default="", max_length=100),
    include_packs: Annotated[bool, Query(alias="includePacks")] = True,
    origin: Annotated[list[LibraryAssetOrigin] | None, Query()] = None,
) -> ApiResponse[LibrarySelectionPage]:
    """生成页 / 工作台挑素材用。

    **硬过滤 `state=active`**：草稿与回收站永远不出现在选择器里。这不是一个
    可选的筛选参数——调用方忘了传就会把用户删掉的东西塞进生成任务。
    """
    result = await service.list_assets(
        actor,
        page,
        library=LibraryTab.ALL,
        state="active",
        kind=kind.value if kind else None,
        tag_ids=tag_ids,
        keyword=keyword,
        # 模板库切换后靠 `origin` 只挑原片视频；与列表用同一组枚举值，
        # 免得两处各写一份字符串字面量、改一处漏一处。
        origins=[item.value for item in origin] if origin else [],
        # 选择器要能挑到封装里的原素材：封装的成员在这里不隐藏（列表里隐藏是
        # 为了「封装后只剩一条」，选择器恰恰相反，挑的是要用的那条素材）。
        hide_packed_members=False,
        # 排除封装要落在 SQL 里：这里再 `items = [x for x ...]` 滤一遍的话，
        # `total` 仍是含封装的数，前端算出多一页。
        exclude_kinds=() if include_packs else ("pack",),
    )
    return ApiResponse.ok(
        LibrarySelectionPage(
            items=result.items,
            total=result.total,
            page=result.page,
            page_size=result.page_size,
        )
    )


# --------------------------------------------------------------------------- #
# 上传
# --------------------------------------------------------------------------- #
@router.post(
    "/assets/upload",
    status_code=status.HTTP_201_CREATED,
    summary="上传图片 / 音频 / 文本（流式，按内容秒传）",
    dependencies=[
        # 上传是重 IO：一次按 3 次计入额度，与旧素材库同一档
        Depends(
            rate_limit("library.upload", limit=settings.rate_limit_default_per_min, cost=3)
        )
    ],
)
async def upload(
    request: Request,
    actor: LibraryCreateDep,
    service: LibraryServiceDep,
    filename: Annotated[str, Query(min_length=1, max_length=200)],
    kind: UploadKind = UploadKind.IMAGE,
    # 120 而不是 160：服务层会给它加 `upload:<actorId>:` 前缀（40 字符）做
    # 按人隔离，列宽是 160，加起来刚好。
    source_key: Annotated[str | None, Query(alias="sourceKey", max_length=120)] = None,
) -> ApiResponse[LibraryAssetOut]:
    """只收图片与音频：视频走 `/source-videos`（模板库专用）。

    分开的理由不是「类型不同」，而是**来源语义不同**：这个接口是用户自己挑文件，
    视频上传属于模板库的参考素材流程，它需要 `origin=template_source` 才能在
    切换后继续喂给模板封装。混成一个接口会让来源变成前端传参，写错了没人发现。
    """
    return ApiResponse.ok(
        await service.upload(
            request.stream(),
            filename,
            kind=kind,
            actor=actor,
            origin="upload",
            source_key=source_key,
        )
    )


@router.post(
    "/source-videos",
    status_code=status.HTTP_201_CREATED,
    summary="上传模板库参考视频（只收视频）",
    dependencies=[
        Depends(
            rate_limit(
                "library.source_videos",
                limit=settings.rate_limit_default_per_min,
                cost=3,
            )
        )
    ],
)
async def upload_source_video(
    request: Request,
    actor: LibraryCreateDep,
    service: LibraryServiceDep,
    filename: Annotated[str, Query(min_length=1, max_length=200)],
    # 120 而不是 160：服务层会给它加 `upload:<actorId>:` 前缀（40 字符）做
    # 按人隔离，列宽是 160，加起来刚好。
    source_key: Annotated[str | None, Query(alias="sourceKey", max_length=120)] = None,
) -> ApiResponse[LibraryAssetOut]:
    """等价于旧 `POST /assets/upload?category=source`，只是路径与来源不同。

    模板库前端切换时只改 `archive.api.ts` 里一个 URL 常量，界面一行不动。
    """
    return ApiResponse.ok(
        await service.upload(
            request.stream(),
            filename,
            kind=UploadKind.VIDEO,
            actor=actor,
            origin="template_source",
            source_key=source_key,
        )
    )


# --------------------------------------------------------------------------- #
# 批量写（必须声明在 /assets/{asset_id} 之前）
# --------------------------------------------------------------------------- #
@router.post(
    "/assets/bulk",
    summary="批量写：逐条判定，部分失败返回明细",
    dependencies=[Depends(rate_limit("library.bulk", limit=20, cost=2))],
)
async def bulk_write(
    payload: BulkIn, actor: BulkDep, service: BulkServiceDep
) -> ApiResponse[BulkOut]:
    return ApiResponse.ok(await service.run(payload, actor))


@router.post(
    "/assets/from-generation",
    status_code=status.HTTP_201_CREATED,
    summary="生成结果入库（落草稿箱，按 sourceKey 幂等）",
    dependencies=[Depends(rate_limit("library.generation_ingest", limit=60, cost=2))],
)
async def ingest_generated(
    payload: FromGenerationIn,
    response: Response,
    actor: LibraryIngestDep,
    service: GenerationIngestDep,
) -> ApiResponse[IngestOut]:
    """`state=draft` / `visibility=personal` 由服务层强制，入参给不了。

    命中幂等（同一个 `sourceKey`，或同一份字节已在该 owner 的库里）时返回
    **200 + `created=false`**：调用方据此知道「这条已经入过库了」，不必再关联一次。
    """
    result = await service.run(
        IngestRequest(
            source_module=payload.source_module,
            source_task_id=payload.source_task_id,
            source_key=payload.source_key,
            kind=LibraryAssetKind(payload.kind.value),
            owner_id=actor.id,
            name=payload.name,
            storage_key=payload.storage_key,
            archive_key=payload.archive_key,
            source_url=payload.source_url,
            mime_type=payload.mime_type,
            size_bytes=payload.size_bytes,
            generation=payload.generation.model_dump(by_alias=False),
            tag_ids=payload.tag_ids,
        )
    )
    if not result.created:
        response.status_code = status.HTTP_200_OK
    return ApiResponse.ok(IngestOut(asset_id=result.asset_id, created=result.created))


@router.patch(
    "/assets/{asset_id}",
    summary="改名 / 换标签（不含可见性）",
    dependencies=[Depends(rate_limit("library.asset_write", limit=60))],
)
async def patch_asset(
    asset_id: str,
    payload: AssetPatchIn,
    actor: LibraryUpdateDep,
    service: LibraryServiceDep,
) -> ApiResponse[LibraryAssetDetailOut]:
    return ApiResponse.ok(await service.patch(asset_id, payload, actor))


@router.delete(
    "/assets/{asset_id}",
    summary="移入回收站（软删，可恢复）",
    dependencies=[Depends(rate_limit("library.asset_delete", limit=60))],
)
async def delete_asset(
    asset_id: str,
    actor: LibraryDeleteDep,
    service: LifecycleServiceDep,
    revoke_own_group_share: Annotated[
        bool,
        Query(
            alias="revokeOwnGroupShare",
            description="组长的窄口：本人所有、仅共享给本组时，一步撤销共享并删除",
        ),
    ] = False,
    reason: str = Query(default="", max_length=200),
) -> ApiResponse[None]:
    """删除矩阵见设计 §3.4。

    已共享返回 409 `SHARED_ASSET_NOT_DELETABLE`，`details.canRevokeAndDelete`
    告诉前端要不要把按钮换成「撤销共享并删除」。判定与执行共用同一个条件，
    前端不自己算。
    """
    await service.delete(
        asset_id,
        actor,
        revoke_own_group_share=revoke_own_group_share,
        reason=reason,
    )
    return ApiResponse.ok(None)


@router.get("/assets/{asset_id}", summary="资产详情（含标签与生成参数）")
async def asset_detail(
    asset_id: str, actor: LibraryReadDep, service: LibraryServiceDep
) -> ApiResponse[LibraryAssetDetailOut]:
    return ApiResponse.ok(await service.detail(asset_id, actor))


@router.get("/assets/{asset_id}/file", summary="下载或预览原文件")
async def asset_file(
    asset_id: str, actor: LibraryReadDep, service: LibraryServiceDep
) -> Response:
    return (await service.file(asset_id, actor)).response()


@router.get("/assets/{asset_id}/thumbnail", summary="读取缩略图")
async def asset_thumbnail(
    asset_id: str, actor: LibraryReadDep, service: LibraryServiceDep
) -> Response:
    return (await service.file(asset_id, actor, preview=True)).response()


@router.get("/assets/{asset_id}/stream", summary="浏览器兼容视频预览（派生流，不改原片）")
async def asset_stream(
    asset_id: str, actor: LibraryReadDep, service: LibraryServiceDep
) -> Response:
    """模板库悬停/播放用的取件口。旧库在 `/assets/{id}/stream`，v2 补在同位置。

    `/file` 给的是原件，`/stream` 给的是 H.264 8-bit 派生版，两者不能互相代替：
    10-bit 原片 Chrome 解不了，而把原件当「预览」发给别人等于放行整份下载。
    """
    return (await service.stream(asset_id, actor)).response()


@router.get("/assets/{asset_id}/preview", summary="受控公共预览（视频给派生流）")
async def asset_preview(
    asset_id: str, actor: LibraryReadDep, service: LibraryServiceDep
) -> Response:
    """旧库把「非本人的公共原片」的 url 改写成 `/assets/{id}/preview`；v2 补同款。

    可见性仍由 `load_visible` 判定，这个端点不额外发权限，只负责「视频别给原件」。
    """
    return (await service.preview(asset_id, actor)).response()


# --------------------------------------------------------------------------- #
# 文本资产（站内新建 / 在线编辑 / 版本历史）
#
# 路径都挂在 `/assets/{asset_id}/text...` 下：它仍是同一条资产行，共享、
# 标签、回收站、取件走的是同一套接口，不另起一套 id 空间。
# --------------------------------------------------------------------------- #
@router.post(
    "/text-assets",
    status_code=status.HTTP_201_CREATED,
    summary="站内新建文本资产（直接写正文，不传文件）",
    dependencies=[Depends(rate_limit("library.text_write", limit=60))],
)
async def create_text_asset(
    payload: TextAssetIn, actor: LibraryCreateDep, service: TextServiceDep
) -> ApiResponse[TextAssetOut]:
    """`.txt` / `.md`、UTF-8、≤1MB。正文按纯文本保存，Markdown 不转 HTML。"""
    return ApiResponse.ok(await service.create(payload, actor))


@router.get("/assets/{asset_id}/text", summary="读取文本正文与当前版本")
async def read_text_asset(
    asset_id: str, actor: LibraryReadDep, service: TextServiceDep
) -> ApiResponse[TextAssetOut]:
    return ApiResponse.ok(await service.read(asset_id, actor))


@router.put(
    "/assets/{asset_id}/text",
    summary="在线编辑文本（追加一版，可带 expectedVersion 防覆盖）",
    dependencies=[Depends(rate_limit("library.text_write", limit=60))],
)
async def update_text_asset(
    asset_id: str,
    payload: TextAssetUpdateIn,
    actor: LibraryUpdateDep,
    service: TextServiceDep,
) -> ApiResponse[TextAssetOut]:
    return ApiResponse.ok(await service.update(asset_id, payload, actor))


@router.get(
    "/assets/{asset_id}/text/versions", summary="文本版本历史（默认不带正文）"
)
async def list_text_versions(
    asset_id: str, actor: LibraryReadDep, service: TextServiceDep
) -> ApiResponse[list[TextAssetVersionOut]]:
    return ApiResponse.ok(await service.versions(asset_id, actor))


@router.get("/assets/{asset_id}/text/versions/{number}", summary="读取某一历史版本正文")
async def read_text_version(
    asset_id: str, number: int, actor: LibraryReadDep, service: TextServiceDep
) -> ApiResponse[TextAssetVersionOut]:
    return ApiResponse.ok(await service.version(asset_id, number, actor))


@router.post(
    "/assets/{asset_id}/text/restore",
    summary="恢复历史版本（写成新的一版，不改历史行）",
    dependencies=[Depends(rate_limit("library.text_write", limit=60))],
)
async def restore_text_version(
    asset_id: str,
    payload: TextAssetRestoreIn,
    actor: LibraryUpdateDep,
    service: TextServiceDep,
) -> ApiResponse[TextAssetOut]:
    return ApiResponse.ok(await service.restore(asset_id, payload, actor))


@router.get("/assets/{asset_id}/text/raw", summary="纯文本取件（text/plain + nosniff）")
async def raw_text_asset(
    asset_id: str, actor: LibraryReadDep, service: TextServiceDep
) -> Response:
    return (await service.raw(asset_id, actor)).response()


# --------------------------------------------------------------------------- #
# 草稿
# --------------------------------------------------------------------------- #
@router.post(
    "/drafts/{asset_id}/approve",
    summary="草稿采纳：进入个人素材库",
    dependencies=[Depends(rate_limit("library.draft", limit=60))],
)
async def approve_draft(
    asset_id: str,
    payload: DraftDecisionIn,
    actor: LibraryDraftDep,
    service: LifecycleServiceDep,
) -> ApiResponse[LibraryAssetDetailOut]:
    return ApiResponse.ok(await service.approve_draft(asset_id, payload, actor))


@router.post(
    "/drafts/{asset_id}/reject",
    summary="草稿驳回：进回收站，可恢复回草稿",
    dependencies=[Depends(rate_limit("library.draft", limit=60))],
)
async def reject_draft(
    asset_id: str,
    payload: DraftDecisionIn,
    actor: LibraryDraftDep,
    service: LifecycleServiceDep,
) -> ApiResponse[LibraryAssetDetailOut]:
    return ApiResponse.ok(await service.reject_draft(asset_id, payload, actor))


# --------------------------------------------------------------------------- #
# 回收站
# --------------------------------------------------------------------------- #
@router.post(
    "/trash/{asset_id}/restore",
    summary="从回收站恢复（强制回到个人库，不恢复共享）",
    dependencies=[Depends(rate_limit("library.trash", limit=60))],
)
async def restore_asset(
    asset_id: str, actor: LibraryDeleteDep, service: LifecycleServiceDep
) -> ApiResponse[LibraryAssetDetailOut]:
    return ApiResponse.ok(await service.restore(asset_id, actor))


@router.delete(
    "/trash/{asset_id}",
    summary="彻底删除（不可逆，先过引用检查）",
    dependencies=[Depends(rate_limit("library.trash", limit=30, cost=2))],
)
async def purge_asset(
    asset_id: str, actor: LibraryDeleteDep, service: LifecycleServiceDep
) -> ApiResponse[None]:
    """被封装 / 下载任务引用时返回 409 `TRASH_ENTRY_REFERENCED` 并列出引用方。

    不做批量彻底删除：不可逆的动作用「勾选十条点一下」来触发太危险。
    """
    await service.purge(asset_id, actor)
    return ApiResponse.ok(None)


# --------------------------------------------------------------------------- #
# 封装
# --------------------------------------------------------------------------- #
@router.post(
    "/packs",
    status_code=status.HTTP_201_CREATED,
    summary="建封装（成员必须是我看得见且在库的）",
    dependencies=[Depends(rate_limit("library.pack", limit=30))],
)
async def create_pack(
    payload: PackIn, actor: LibraryCreateDep, service: PackServiceDep
) -> ApiResponse[LibraryAssetDetailOut]:
    """封装是一行 `kind=pack` 的资产行 + 有序成员，**不复制成员**。

    解散封装不影响任何成员；下载时封装会被展开成成员逐个进包。
    """
    return ApiResponse.ok(await service.create(payload, actor))


@router.get("/packs/{pack_id}", summary="封装详情（含成员）")
async def pack_detail(
    pack_id: str, actor: LibraryReadDep, service: LibraryServiceDep
) -> ApiResponse[LibraryAssetDetailOut]:
    return ApiResponse.ok(await service.detail(pack_id, actor))


@router.patch(
    "/packs/{pack_id}",
    summary="改封装：改名 / 整体替换成员 / 换封面 / 换标签",
    dependencies=[Depends(rate_limit("library.pack", limit=30))],
)
async def patch_pack(
    pack_id: str,
    payload: PackPatchIn,
    actor: LibraryUpdateDep,
    service: PackServiceDep,
) -> ApiResponse[LibraryAssetDetailOut]:
    return ApiResponse.ok(await service.update(pack_id, payload, actor))


@router.delete(
    "/packs/{pack_id}",
    summary="解散封装（进回收站，成员不受影响）",
    dependencies=[Depends(rate_limit("library.pack", limit=30))],
)
async def delete_pack(
    pack_id: str, actor: LibraryDeleteDep, service: PackServiceDep
) -> ApiResponse[None]:
    await service.delete(pack_id, actor)
    return ApiResponse.ok(None)


# --------------------------------------------------------------------------- #
# 批量下载
# --------------------------------------------------------------------------- #
@router.post(
    "/downloads",
    status_code=status.HTTP_202_ACCEPTED,
    summary="发起批量打包（异步，返回任务）",
    dependencies=[Depends(rate_limit("library.download", limit=10, cost=2))],
)
async def create_download(
    payload: DownloadIn, actor: LibraryReadDep, service: DownloadServiceDep
) -> ApiResponse[DownloadJobOut]:
    """**可见即可下载**：能看见的 `state=active` 条目都能进包。

    草稿与回收站里的 id 不整单失败，而是在 `skipped` 里逐条给原因。
    超限（100 项 / 2GB）返回 413，`details` 带 `limit` 与 `actual`。
    """
    return ApiResponse.ok(await service.create(payload, actor))


@router.get("/downloads", summary="我最近发起的打包任务")
async def list_downloads(
    actor: LibraryReadDep, service: DownloadServiceDep
) -> ApiResponse[list[DownloadJobOut]]:
    return ApiResponse.ok(await service.list_jobs(actor))


@router.get("/downloads/{job_id}", summary="打包任务状态")
async def download_status(
    job_id: str, actor: LibraryReadDep, service: DownloadServiceDep
) -> ApiResponse[DownloadJobOut]:
    return ApiResponse.ok(await service.get(job_id, actor))


@router.get("/downloads/{job_id}/file", summary="取打包好的 zip")
async def download_file(
    job_id: str, actor: LibraryReadDep, service: DownloadServiceDep
) -> Response:
    return (await service.file(job_id, actor)).response()


# --------------------------------------------------------------------------- #
# 共享
# --------------------------------------------------------------------------- #
@router.post(
    "/share-requests",
    status_code=status.HTTP_201_CREATED,
    summary="发起共享 / 撤销共享申请",
    dependencies=[Depends(rate_limit("library.share", limit=60))],
)
async def create_share_request(
    payload: ShareRequestIn, actor: LibraryShareDep, service: ShareServiceDep
) -> ApiResponse[ShareRequestOut]:
    """`action=revoke` 必须带 `reason`（400 `REVOKE_REASON_REQUIRED`）。

    申请人自己就是**该目标**的审批人时直接通过并记自审（全站码
    `library:share:approve`，或目标对应的 `library:share:approve:team` /
    `library:share:approve:group`），否则会出现「自己申请、等自己批」的死锁。
    """
    return ApiResponse.ok(await service.request(payload, actor))


@router.get("/share-requests", summary="申请单列表（我提交的 / 待我审核）")
async def list_share_requests(
    actor: LibraryReadDep,
    service: ShareServiceDep,
    scope: str = Query(default="mine", pattern="^(mine|pending)$"),
) -> ApiResponse[list[ShareRequestOut]]:
    return ApiResponse.ok(await service.list_requests(actor, scope=scope))


@router.post(
    "/share-requests/{request_id}/approve",
    summary="通过申请",
    dependencies=[Depends(rate_limit("library.share", limit=60))],
)
async def approve_share_request(
    request_id: str,
    payload: ShareDecisionIn,
    actor: LibraryReadDep,
    service: ShareServiceDep,
) -> ApiResponse[ShareRequestOut]:
    """审批资格按申请目标范围在服务层判（全站 / 团队 / 所在小组），路由只要求
    `library:read`；组长身份本身不再给审批权。"""
    return ApiResponse.ok(await service.decide(request_id, payload, actor, approve=True))


@router.post(
    "/share-requests/{request_id}/reject",
    summary="驳回申请",
    dependencies=[Depends(rate_limit("library.share", limit=60))],
)
async def reject_share_request(
    request_id: str,
    payload: ShareDecisionIn,
    actor: LibraryReadDep,
    service: ShareServiceDep,
) -> ApiResponse[ShareRequestOut]:
    return ApiResponse.ok(await service.decide(request_id, payload, actor, approve=False))


@router.post(
    "/share-requests/{request_id}/cancel",
    summary="撤回自己提交的申请",
    dependencies=[Depends(rate_limit("library.share", limit=60))],
)
async def cancel_share_request(
    request_id: str, actor: LibraryShareDep, service: ShareServiceDep
) -> ApiResponse[ShareRequestOut]:
    return ApiResponse.ok(await service.cancel(request_id, actor))


# --------------------------------------------------------------------------- #
# 小组与标签
# --------------------------------------------------------------------------- #
@router.get("/groups", summary="我的小组（含是否负责人）")
async def my_groups(
    actor: LibraryReadDep, service: ShareServiceDep
) -> ApiResponse[list[LibraryGroupOut]]:
    """前端用它渲染「小组素材库」的分组筛选与审批入口。

    不直接调用 `/identity/groups`：那个接口要 `identity:group:read`，
    普通成员没有；而且它返回全站小组，会泄露别人的组织结构。
    """
    return ApiResponse.ok(await service.my_groups(actor))


@router.put(
    "/groups/{group_id}/leaders",
    summary="设置小组负责人（整体替换）",
    dependencies=[Depends(rate_limit("library.leaders", limit=30))],
)
async def set_group_leaders(
    group_id: str,
    payload: GroupLeadersIn,
    actor: LibraryShareApproveDep,
    service: ShareServiceDep,
) -> ApiResponse[list[str]]:
    """负责人表现在只服务删除窄口与 `isLeader` 展示；审批资格改由三个审批权限点
    按申请目标范围判定（`_can_approve_target`），本接口仍由全站审批码守护。"""
    return ApiResponse.ok(await service.set_leaders(group_id, payload.user_ids, actor))


@router.get("/tags", summary="标签列表（两级平铺，含当前入口的素材数，按添加时间排序）")
async def list_tags(
    actor: LibraryReadDep,
    service: TagServiceDep,
    library: LibraryTab = LibraryTab.ALL,
    state: LibraryAssetState = LibraryAssetState.ACTIVE,
    kind: LibraryAssetKind | None = None,
) -> ApiResponse[list[LibraryTagStatOut]]:
    """计数按 `state` + `library` + `kind` 与列表同口径。

    `state=draft|trash` 时忽略 `library`；`kind` 与列表的类别筛选同口径
    （悬浮栏按类别看库时，胶囊上的数字必须也是「这一类里有几条」）。
    """
    return ApiResponse.ok(
        await service.list_tags(
            actor,
            state=state.value,
            library=library,
            kind=kind.value if kind else "",
        )
    )


@router.post(
    "/tags",
    status_code=status.HTTP_201_CREATED,
    summary="新建标签（同名返回已有那条）",
    dependencies=[Depends(rate_limit("library.tag", limit=60))],
)
async def create_tag(
    payload: TagIn, actor: LibraryCreateDep, service: TagServiceDep
) -> ApiResponse[LibraryTagOut]:
    """`parentId` 给一级标签的 id 就建成二级；留空建成一级。"""
    return ApiResponse.ok(await service.create(payload, actor))


@router.patch(
    "/tags/{tag_id}",
    summary="重命名标签（不做隐式合并）",
    dependencies=[Depends(rate_limit("library.tag", limit=60))],
)
async def rename_tag(
    tag_id: str, payload: TagIn, actor: LibraryTagManageDep, service: TagServiceDep
) -> ApiResponse[LibraryTagOut]:
    return ApiResponse.ok(await service.rename(tag_id, payload))


@router.patch(
    "/tags/{tag_id}/parent",
    summary="移动标签层级（parentId 为空则提升为一级）",
    dependencies=[Depends(rate_limit("library.tag", limit=60))],
)
async def move_tag(
    tag_id: str, payload: TagParentIn, actor: LibraryTagManageDep, service: TagServiceDep
) -> ApiResponse[LibraryTagOut]:
    """固定两级：父必须是一级、自己不能带子标签、不能指向自己。"""
    return ApiResponse.ok(await service.move(tag_id, payload))


@router.delete(
    "/tags/{tag_id}",
    summary="删除标签（旗下二级提升为一级，资产不受影响）",
    dependencies=[Depends(rate_limit("library.tag", limit=60))],
)
async def delete_tag(
    tag_id: str, actor: LibraryTagManageDep, service: TagServiceDep
) -> ApiResponse[None]:
    await service.delete(tag_id)
    return ApiResponse.ok(None)
