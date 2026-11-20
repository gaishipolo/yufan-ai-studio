"""资产库 v2 路由，统一前缀 `/library`。

**与现役 `/assets` 完全隔离**：表前缀、权限码、注入口、前端路径全部独立，
切换时只改前端模块的 `load()` 一行，后端两套可以同时在线。

路径顺序有讲究：`/assets/bulk`、`/assets/upload` 必须声明在
`/assets/{asset_id}` **之前**。FastAPI 按声明顺序匹配，写在后面会被
`{asset_id}` 吃掉，然后返回「资产不存在」——这种错在 OpenAPI 里看不出来，
只有真的发一次请求才会发现。

M2 交付：三层可见集、共享与撤销审批、删除矩阵（含 §3.4.1 窄口）、草稿与回收站、
标签、批量写、选择器、批量下载。
"""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Request, status
from fastapi.responses import Response

from api.deps import PageDep, rate_limit
from core.config import settings
from core.exceptions import BadRequest, Forbidden
from core.ports.library_ingest import IngestRequest
from core.ports.library_lookup import LibraryAssetKind, LibraryAssetState
from core.response import ApiResponse
from modules.library import access
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
    PackOut,
    PackPatchIn,
    PackSummaryOut,
    SetPurposeIn,
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


def _case_purpose(actor: object, purpose: str | None) -> str:
    """把 `purpose` 查询参数翻成服务层要的值，并在这里把关权限。

    只认空串与 `case`：拼错一个词（`cases` / `demo`）如果被静默忽略，用户上传完
    发现库里没有、案例池里也没有，只能靠猜。写权限与读权限分开——
    能浏览全部案例不代表能往池子里放东西。
    """
    target = (purpose or "").strip().lower()
    if not target:
        return ""
    if target != access.PURPOSE_CASE:
        raise BadRequest(f"不支持的用途：{purpose}")
    if not actor.has("library:case:write"):  # type: ignore[attr-defined]
        raise Forbidden("没有上传案例的权限")
    return target


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
    purpose: Annotated[str | None, Query(max_length=16)] = None,
) -> ApiResponse[LibraryAssetPage]:
    """`state=draft|trash` 时忽略 `library`：草稿与回收站不在任何可见范围内。

    代审 / 管理员看全量靠权限点（`library:draft:review` / `library:delete:any`），
    不靠一个 `mine=false` 参数——参数能被任何人传，权限点不能。

    `purpose=case` 是**案例池**入口：只有持 `library:case:read` 的人能浏览全部
    案例。默认反过来——不传就从普通列表里排除案例，否则每个人打开「全部」都会
    看到一堆模板的展示素材，还会把它们算进自己的数量。
    """
    # 写错的用途一律报错：静默忽略的词让人以为自己在案例池里，实际看的是个人库。
    target = (purpose or "").strip().lower()
    if target and target != access.PURPOSE_CASE:
        raise BadRequest(f"不支持的用途：{purpose}")
    case_view = target == access.PURPOSE_CASE
    if case_view and not actor.has("library:case:read"):
        raise Forbidden("没有浏览案例池的权限")
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
            purpose=access.PURPOSE_CASE if case_view else None,
            # 案例池里只看案例；普通入口一律排除案例（含作者自己的）。
            exclude_purposes=() if case_view else (access.PURPOSE_CASE,),
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
    origin: Annotated[list[LibraryAssetOrigin] | None, Query()] = None,
    library: LibraryTab = LibraryTab.ALL,
    purpose: Annotated[str | None, Query(max_length=16)] = None,
) -> ApiResponse[LibrarySelectionPage]:
    """生成页 / 工作台挑素材用。

    **硬过滤 `state=active`**：草稿与回收站永远不出现在选择器里。这不是一个
    可选的筛选参数——调用方忘了传就会把用户删掉的东西塞进生成任务。

    `library` 与列表同一个枚举、同一份判定（`access.tab_clause`）：模板工作台
    的「我的原片 / 公共原片」就是 `personal` / `team`。**不新造一个
    `scope=mine|public` 参数**——那等于给可见性写第二套定义，两套一漂移就是
    「列表里没有、选择器里有」。默认 `all` 与切换前的行为完全一致。

    `purpose=case` 是**展示案例**入口（模板封装页的「从资产库选图片/视频」）：
    与列表那边**同一条规则** —— 要 `library:case:read`，这时只看案例；不传则
    一律排除案例。少了这一档，作者上传过的案例在第二次使用时根本挑不到 ——
    案例不进普通列表，选择器又把它排掉，等于只能用一次。
    """
    # 写错的用途一律报错：静默忽略的词让人以为自己在挑案例，实际看的是个人库。
    target = (purpose or "").strip().lower()
    if target and target != access.PURPOSE_CASE:
        raise BadRequest(f"不支持的用途：{purpose}")
    case_view = target == access.PURPOSE_CASE
    if case_view and not actor.has("library:case:read"):
        raise Forbidden("没有浏览案例池的权限")
    result = await service.list_assets(
        actor,
        page,
        library=library,
        state="active",
        kind=kind.value if kind else None,
        tag_ids=tag_ids,
        keyword=keyword,
        # 模板库切换后靠 `origin` 只挑原片视频；与列表用同一组枚举值，
        # 免得两处各写一份字符串字面量、改一处漏一处。
        origins=[item.value for item in origin] if origin else [],
        # 案例池里只看案例；普通入口一律排除案例（含作者自己的）。
        purpose=access.PURPOSE_CASE if case_view else None,
        exclude_purposes=() if case_view else (access.PURPOSE_CASE,),
        # **不像列表那样藏起封装成员**：这里是「挑素材去用」，藏掉就等于
        # 那几张图选不到了（槽位默认还是按素材挑）。列表藏是为了别重复，
        # 选择器藏是让人找不到东西 —— 同一件事在两个场景里结论相反。
        exclude_packed_members=False,
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
    summary="上传图片 / 视频 / 音频 / 文本（流式，按内容秒传）",
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
    purpose: Annotated[str | None, Query(max_length=16)] = None,
) -> ApiResponse[LibraryAssetOut]:
    """使用者自己挑文件上传：图片 / 视频（.mp4）/ 音频 / 文本，一律 `origin=upload`。

    视频为什么**还**有一个 `/source-videos`：分开的理由不是「类型不同」，而是
    **来源语义不同**。模板库的参考原片需要 `origin=template_source` 才能在切换后
    继续被模板封装挑中；而这里的视频是普通素材，来源由本接口写死为 `upload`，
    不会混进「原片」那一档。两条路径都写死来源，前端没有传 origin 的入口。
    """
    return ApiResponse.ok(
        await service.upload(
            request.stream(),
            filename,
            kind=kind,
            actor=actor,
            origin="upload",
            source_key=source_key,
            purpose=_case_purpose(actor, purpose),
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
    purpose: Annotated[str | None, Query(max_length=16)] = None,
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
            purpose=_case_purpose(actor, purpose),
            origin="template_source",
            source_key=source_key,
        )
    )


# --------------------------------------------------------------------------- #
# 封装（pack）
# --------------------------------------------------------------------------- #
@router.post(
    "/packs",
    status_code=status.HTTP_201_CREATED,
    summary="建封装：把一组素材打成一个包",
    dependencies=[
        # 与上传同档：都是重写操作，但只写元数据，按上传的一半计。
        Depends(rate_limit("library.pack", limit=settings.rate_limit_default_per_min, cost=2))
    ],
)
async def create_pack(
    payload: PackIn, actor: LibraryCreateDep, service: LibraryServiceDep
) -> ApiResponse[PackOut]:
    """成员的选择顺序就是包内顺序；包自动继承成员的**共同标签**。

    包本身是一行 `kind=pack` 的资产（没有自己的字节），所以列表/标签/删除
    都走既有路径，不为它单开分支。
    """
    return ApiResponse.ok(
        await service.create_pack(actor, name=payload.name, asset_ids=payload.asset_ids)
    )


@router.get("/packs", summary="封装列表（模板库选择器用）")
async def list_packs(
    actor: LibraryReadDep,
    service: LibraryServiceDep,
    tag_ids: Annotated[list[str] | None, Query(alias="tagIds")] = None,
    keyword: str = Query(default="", max_length=100),
    mode: Literal["any", "all"] = Query(default="any"),
) -> ApiResponse[list[PackSummaryOut]]:
    """`tagIds` 传**槽位的候选标签**。

    - `mode=any`（默认）：与包内成员标签有交集就返回 —— 「这个包能不能填这一格」；
    - `mode=all`：成员标签的并集要覆盖全部 `tagIds` —— 「能不能把这一组格子都填上」。
      封装容器用它：容器里有几格，包就得有几张对得上，否则点一次只填一半。

    判据就是「这个包能不能填那一格 / 那一组」，所以不需要给包再加一个「类型」字段。
    """
    return ApiResponse.ok(
        await service.list_packs(actor, tag_ids=tag_ids, keyword=keyword, mode=mode)
    )


@router.patch(
    "/packs/{pack_id}",
    summary="改封装（名字 / 成员）",
    dependencies=[
        Depends(rate_limit("library.pack", limit=settings.rate_limit_default_per_min, cost=2))
    ],
)
async def update_pack(
    pack_id: str,
    payload: PackPatchIn,
    actor: LibraryCreateDep,
    service: LibraryServiceDep,
) -> ApiResponse[PackOut]:
    """换成员后，包身上的标签会**重算成新成员的共同标签**。

    那是派生值：模板库靠它判断「这个包能不能填那一格」，增量维护会留下一堆
    已经不再共同的旧标签，选择器就会把包放进填不上的槽位里。
    """
    return ApiResponse.ok(
        await service.update_pack(
            actor, pack_id, name=payload.name, asset_ids=payload.asset_ids
        )
    )


@router.get("/packs/{pack_id}", summary="封装详情（成员与预览）")
async def get_pack(
    pack_id: str, actor: LibraryReadDep, service: LibraryServiceDep
) -> ApiResponse[PackOut]:
    return ApiResponse.ok(await service.get_pack(actor, pack_id))


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


@router.post(
    "/assets/{asset_id}/purpose",
    summary="设置用途：普通素材 / 模板案例",
    dependencies=[Depends(rate_limit("library.asset_write", limit=60))],
)
async def set_asset_purpose(
    asset_id: str,
    payload: SetPurposeIn,
    actor: LibraryUpdateDep,
    service: LibraryServiceDep,
) -> ApiResponse[LibraryAssetOut]:
    """把素材移入案例池（`purpose=case`）或移回普通素材（空串）。

    「选已有素材当案例」走这个接口：字节不复制，模板引用的仍是同一个 id。
    移入案例池要 `library:case:write`——它会改变这条素材的可见范围，
    不是一次普通的改名。
    """
    target = payload.purpose.strip().lower()
    if target == access.PURPOSE_CASE and not actor.has("library:case:write"):
        raise Forbidden("没有设置案例的权限")
    return ApiResponse.ok(
        await service.set_purpose(asset_id, target, actor, payload.expected_version)
    )


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


@router.get("/assets/{asset_id}/large", summary="读取悬浮大图（长边 1280 的派生图）")
async def asset_large(
    asset_id: str, actor: LibraryReadDep, service: LibraryServiceDep
) -> Response:
    """网格里悬停时看细节用的那一张。

    封面（`/thumbnail`）只有 360×240，几十像素的方块看不清产品图；
    原图（`/file`）平均 2.6MB、悬停一次拉一次。这一档是折中：
    长边 1280 的派生 JPEG，几百 KB，按内容摘要缓存 —— 同一份原图只生成一次。
    """
    return (await service.large(asset_id, actor)).response()


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
    """被下载任务引用时返回 409 `TRASH_ENTRY_REFERENCED` 并列出引用方。

    不做批量彻底删除：不可逆的动作用「勾选十条点一下」来触发太危险。
    """
    await service.purge(asset_id, actor)
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


@router.get("/tags", summary="标签列表（三级平铺，含当前入口的素材数，按添加时间排序）")
async def list_tags(
    actor: LibraryReadDep,
    service: TagServiceDep,
    library: LibraryTab = LibraryTab.ALL,
    state: LibraryAssetState = LibraryAssetState.ACTIVE,
    kind: LibraryAssetKind | None = None,
    purpose: Annotated[str | None, Query(max_length=16)] = None,
    tag_ids: Annotated[list[str] | None, Query(alias="tagIds")] = None,
) -> ApiResponse[list[LibraryTagStatOut]]:
    """计数按 `state` + `library` + `kind` + `purpose` 与列表同口径。

    `state=draft|trash` 时忽略 `library`；`kind` 与列表的类别筛选同口径
    （悬浮栏按类别看库时，胶囊上的数字必须也是「这一类里有几条」）。

    `purpose=case` 是案例池入口：只有持 `library:case:read` 的人能看案例池，
    且计数只算案例。不传时一律排除案例——与列表同一口径。

    `tagIds` 是**候选范围**（模板库用）：只统计符合这组标签的素材。
    模板作者圈了候选集之后，模板库要显示的是「这批素材身上实际有的二级标签」
    —— 按全库算会列出一堆这批素材一个都没挂的标签，用户点了只得到空列表。
    """
    target = (purpose or "").strip().lower()
    if target and target != access.PURPOSE_CASE:
        raise BadRequest(f"不支持的用途：{purpose}")
    case_view = target == access.PURPOSE_CASE
    if case_view and not actor.has("library:case:read"):
        raise Forbidden("没有浏览案例池的权限")
    return ApiResponse.ok(
        await service.list_tags(
            actor,
            state=state.value,
            library=library,
            kind=kind.value if kind else "",
            purpose=access.PURPOSE_CASE if case_view else "",
            tag_ids=list(tag_ids or []),
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
    """`parentId` 给父标签的 id 就建成它的下一级（最多三级）；留空建成一级。"""
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
    summary="移动标签层级（parentId 为空则提升为一级，最多三级）",
    dependencies=[Depends(rate_limit("library.tag", limit=60))],
)
async def move_tag(
    tag_id: str, payload: TagParentIn, actor: LibraryTagManageDep, service: TagServiceDep
) -> ApiResponse[LibraryTagOut]:
    """最多三级：父不能是三级标签、自己不能带子标签、不能指向自己或自己的下级。"""
    return ApiResponse.ok(await service.move(tag_id, payload))


@router.delete(
    "/tags/{tag_id}",
    summary="删除标签（旗下子级提升一级，资产不受影响）",
    dependencies=[Depends(rate_limit("library.tag", limit=60))],
)
async def delete_tag(
    tag_id: str, actor: LibraryTagManageDep, service: TagServiceDep
) -> ApiResponse[None]:
    await service.delete(tag_id)
    return ApiResponse.ok(None)
