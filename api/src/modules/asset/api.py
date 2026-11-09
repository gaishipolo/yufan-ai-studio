"""素材路由：全部挂在 `/assets` 下，只声明 HTTP 参数和响应。

**这里是素材唯一的入口**。上一版有两套：`/assets`（旧 `AssetService`，索引走
`MediaCatalog`）与 `/assets/archive`（归档盘 + `assets` 表）。两套路径长得几乎一样、
权限码却不同，前端因此养出两个客户端、两套页面，问题永远只在其中一套里被修。
索引搬进 `assets` 表之后，旧那套已经没有任何独有的能力，只剩 `cover` ——
而归档侧本来就有 `thumbnail`。所以路径统一到 `/assets`，`archive` 这一段去掉：
「归档」是数据来源，不是资源名。

三个 `PUT` 是「我的素材」的写入口（改名 / 分享 / 回收站），彼此用不同权限点：
改名只动自己的记录，分享会让素材进入公共原片池，删除影响可用性，不能共用一个码。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import Response

from api.deps import SessionDep, rate_limit, require
from core.config import settings
from core.context import CurrentUser
from core.media import MediaCategory
from core.response import ApiResponse
from modules.asset.archive_dependencies import ArchiveReadDep, ArchiveServiceDep, ArchiveWriteDep
from modules.asset.archive_schema import (
    ArchiveCategory,
    ArchiveChange,
    ArchivedAsset,
    ArchiveList,
    ArchiveScope,
    ArchiveState,
    ImportAsset,
)
from modules.asset.candidate_schema import (
    AdoptRequest,
    BatchAdoptRequest,
    BatchAdoptResult,
    CandidateList,
)
from modules.asset.candidate_service import CandidateService
from modules.asset.recipe_schema import DeriveIntent, DeriveRequest, LineageOut
from modules.asset.recipe_service import RecipeService
from modules.asset.tag_schema import AssetTagList, AssetTagSet, TagOut
from modules.asset.tag_service import TagService

# tags 由聚合层 `router.py` 统一声明：父 router 与子 router 都写会让 FastAPI
# 追加成 ["素材", "素材"]，OpenAPI 里出现两个同名分组（`build_v1_router` 的判据
# 是「父 router 有没有 tags」，这里不写就是交给上层补一次）。
router = APIRouter(prefix="/assets")


# 三个写权限别名提到最前面：下面的路由用它们做注解，而注解在 **def 时**求值。
# 放在文件末尾等于 ImportError（NameError），而错误信息指向的是 import 处，
# 不是缺失的定义——移动这次改动的顺序时踩过一次。
UpdateDep = Annotated[CurrentUser, Depends(require("asset:update"))]
ShareDep = Annotated[CurrentUser, Depends(require("asset:share"))]
DeleteDep = Annotated[CurrentUser, Depends(require("asset:delete"))]


@router.get("", summary="归档素材列表与空间占用")
async def list_archive(
    actor: ArchiveReadDep,
    service: ArchiveServiceDep,
    category: ArchiveCategory = "all",
    scope: ArchiveScope = "mine",
    keyword: str = "",
    state: ArchiveState = "all",
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=200, ge=1, le=200, alias="pageSize"),
    # 当前选中的团队库。为空表示「还没切库」或者用户在个人库——
    # 那时团队额度显示 0，而不是拿「所有团队的总和」糊弄。
    team_id: str = Query(default="", alias="teamId", max_length=32),
    # 标签筛选，**AND 语义**：`?tagIds=a&tagIds=b` 是「同时具备 a 和 b」。
    # 逗号分隔也接受（前端把多选拼成字符串时更省事），两种写法等价。
    tag_ids: Annotated[list[str] | None, Query(alias="tagIds")] = None,
    sort: str = Query(default="created_desc", max_length=24),
    # 新分类树按 id 过滤；`category`（旧枚举）保留为兼容参数，二者都给时以 id 为准
    category_id: str = Query(default="", alias="categoryId", max_length=32),
    # 二级「媒体类型」分面：`image` / `video` / `audio`，空表示不限。
    # 值由服务端校验（`MEDIA_TYPES` 白名单），不接受任意字符串——它进 SQL 比较。
    media_type: str = Query(default="", alias="mediaType", max_length=16),
) -> ApiResponse[ArchiveList]:
    # `?tagIds=a&tagIds=b` 与 `?tagIds=a,b` 等价：两种都是"多选"的自然写法，
    # 前者来自 `URLSearchParams.append`，后者来自手拼查询串。只认一种会让
    # 外部调用方（脚本、书签）里的一半写法静默退化成"只筛第一个"。
    flat = [part.strip() for raw in (tag_ids or []) for part in raw.split(",") if part.strip()]
    return ApiResponse.ok(
        await service.list_assets(
            actor.id,
            category,
            scope,
            keyword,
            state,
            page,
            page_size,
            team_id,
            flat,
            sort,
            # 管理团队的人能看到所有团队库（否则他管不了自己不所在的团队）；
            # 其余人只看自己所在的——跨团队不可见是默认行为，不是 M5 才生效。
            actor.has("asset:team:manage"),
            category_id,
            media_type,
        )
    )


@router.get("/candidates", summary="待确认的生成候选（按批分组）")
async def list_candidates(
    actor: ArchiveReadDep, service: ArchiveServiceDep, session: SessionDep
) -> ApiResponse[CandidateList]:
    """候选是**待办**，不是分类：它不进 `GET /assets`，只在这里按批出现。

    返回的是「几批」而不是「一页」——同一批（同一 `recipe_id`）必须挨着，
    否则"刚才那 4 张里选 2 张"这个动作在界面上无法完成。
    """
    return ApiResponse.ok(await CandidateService(session, service).groups(actor.id))


@router.post("/batch/adopt", summary="整组采纳候选（全成或全不成）")
async def batch_adopt(
    payload: BatchAdoptRequest,
    actor: UpdateDep,
    service: ArchiveServiceDep,
    session: SessionDep,
    # 采纳是逐条写的（每条要落分类、刷 blob 桶），一屏"全选"顶多几十条；
    # 30 次/分钟挡的是脚本，不是手速
    _: None = Depends(rate_limit("asset.candidates.adopt", limit=30)),
) -> ApiResponse[BatchAdoptResult]:
    return ApiResponse.ok(await CandidateService(session, service).adopt_many(actor.id, payload))


@router.post("/upload", status_code=201, summary="流式上传素材至归档盘")
async def upload(
    request: Request,
    actor: ArchiveWriteDep,
    service: ArchiveServiceDep,
    category: MediaCategory,
    filename: Annotated[str, Query(min_length=1, max_length=200)],
    # 一次上传按 3 次计入额度：写归档盘是重 IO，额度不该按「请求数」平等对待
    _: None = Depends(
        rate_limit(
            "asset.archive.upload", limit=settings.rate_limit_default_per_min, cost=3
        )
    ),
) -> ApiResponse[ArchivedAsset]:
    return ApiResponse.ok(await service.upload_for(request.stream(), filename, category, actor))


@router.post("/import", status_code=201, summary="从 Z 盘 inbox 登记现有素材")
async def import_asset(
    payload: ImportAsset,
    actor: ArchiveWriteDep,
    service: ArchiveServiceDep,
    # 导入同样要读文件 + 算校验和，按上传的同一档计
    _: None = Depends(
        rate_limit("asset.archive.import", limit=settings.rate_limit_default_per_min, cost=3)
    ),
) -> ApiResponse[ArchivedAsset]:
    return ApiResponse.ok(await service.import_for(payload, actor))


@router.get("/{asset_id}/file", summary="预览或下载已归档素材")
async def asset_file(
    asset_id: str, actor: ArchiveReadDep, service: ArchiveServiceDep
) -> Response:
    return (await service.file(asset_id, actor.id)).response()


@router.get("/{asset_id}/thumbnail", summary="读取归档素材缩略图")
async def asset_thumbnail(
    asset_id: str, actor: ArchiveReadDep, service: ArchiveServiceDep
) -> Response:
    return (await service.file(asset_id, actor.id, preview=True)).response()


@router.get("/{asset_id}/stream", summary="浏览器兼容视频预览（派生文件，不改原片）")
async def asset_stream(
    asset_id: str, actor: ArchiveReadDep, service: ArchiveServiceDep
) -> Response:
    # 远端驱动下这里返回 307：`Range` 由对象存储处理，首帧不用等整个文件
    return (await service.stream(asset_id, actor.id)).response()


@router.get("/{asset_id}", summary="读取本人或共享原片详情")
async def archive_detail(
    asset_id: str, actor: ArchiveReadDep, service: ArchiveServiceDep
) -> ApiResponse[ArchivedAsset]:
    return ApiResponse.ok(await service.detail(asset_id, actor.id))


@router.get("/{asset_id}/preview", summary="受控公共原片预览")
async def public_preview(
    asset_id: str, actor: ArchiveReadDep, service: ArchiveServiceDep
) -> Response:
    return (await service.preview(asset_id, actor.id)).response()


@router.put("/{asset_id}/name", summary="修改本人素材名称")
async def rename(
    asset_id: str, payload: ArchiveChange, actor: UpdateDep, service: ArchiveServiceDep
) -> ApiResponse[ArchivedAsset]:
    return ApiResponse.ok(await service.change_action(asset_id, actor.id, payload, "name"))


@router.put("/{asset_id}/sharing", summary="分享或取消分享本人原片")
async def sharing(
    asset_id: str, payload: ArchiveChange, actor: ShareDep, service: ArchiveServiceDep
) -> ApiResponse[ArchivedAsset]:
    return ApiResponse.ok(await service.change_action(asset_id, actor.id, payload, "shared"))


@router.put("/{asset_id}/deleted", summary="将本人素材移入回收站或恢复")
async def deleted(
    asset_id: str, payload: ArchiveChange, actor: DeleteDep, service: ArchiveServiceDep
) -> ApiResponse[ArchivedAsset]:
    return ApiResponse.ok(await service.change_action(asset_id, actor.id, payload, "deleted"))


@router.get("/{asset_id}/tags", summary="读取素材的标签")
async def get_tags(
    asset_id: str, actor: ArchiveReadDep, service: ArchiveServiceDep, session: SessionDep
) -> ApiResponse[AssetTagList]:
    """与 `PUT` 成对：只写不读的话，详情浮层里的标签只能靠列表接口顺带带回来，
    而列表一次 200 条、每条都带标签会让响应体大一个量级。"""
    await service.visible(asset_id, actor.id)  # 不存在的与别人的同一个错误
    tags = (await TagService(session).for_assets([asset_id])).get(asset_id, [])
    return ApiResponse.ok(
        AssetTagList(
            asset_id=asset_id,
            items=[
                TagOut(
                    id=tag.id,
                    dimension=tag.dimension,
                    name=tag.name,
                    status=tag.status,
                    source=tag.source,
                )
                for tag in tags
            ],
        )
    )


@router.put("/{asset_id}/tags", summary="整体替换本人素材的标签")
async def set_tags(
    asset_id: str,
    payload: AssetTagSet,
    actor: UpdateDep,
    service: ArchiveServiceDep,
    session: SessionDep,
    # 打标签是勾选面板的一次提交，一个用户手速再快也到不了每分钟 60 次；
    # 这个额度挡的是脚本刷（每次提交都会写一行关联并刷新 last_used_at）
    _: None = Depends(rate_limit("asset.tags.set", limit=60)),
) -> ApiResponse[AssetTagList]:
    """整体替换而不是逐条增删：前端是勾选面板，它天然知道勾了哪几个。

    「整体替换」的代价是并发下后写覆盖（两个人同时改同一条素材），
    但素材标签不是协同编辑的对象——它是一组个人描述，覆盖比冲突提示更符合预期。
    """
    await service.owned(asset_id, actor.id)  # 不存在的 id 与别人的 id 同一个错误
    tags = await TagService(session).set_asset_tags(asset_id, payload.tag_ids, actor.id)
    return ApiResponse.ok(
        AssetTagList(
            asset_id=asset_id,
            items=[
                TagOut(
                    id=tag.id,
                    dimension=tag.dimension,
                    name=tag.name,
                    status=tag.status,
                    source=tag.source,
                )
                for tag in tags
            ],
        )
    )


@router.delete("/{asset_id}", status_code=204, summary="彻底删除回收站中的归档素材")
async def purge(asset_id: str, actor: DeleteDep, service: ArchiveServiceDep) -> Response:
    """清空单条回收站记录。与 `PUT /deleted` 分成两步：软删可恢复，真删不可逆。"""
    await service.purge(asset_id, actor.id)
    return Response(status_code=204)


@router.post("/{asset_id}/adopt", summary="采纳生成候选（成为素材）")
async def adopt_candidate(
    asset_id: str,
    payload: AdoptRequest,
    actor: UpdateDep,
    service: ArchiveServiceDep,
    session: SessionDep,
    # 采纳会落分类、刷 blob 的桶，一条一次写；60 次/分钟够"逐张挑"用
    _: None = Depends(rate_limit("asset.candidates.adopt.one", limit=60)),
) -> ApiResponse[ArchivedAsset]:
    """补分类（可选）后把候选转正。

    幂等：已经采纳过的再点一次返回现状，而不是 409——"全选"与双击都会打到
    同一行，回冲突会让用户以为自己的操作没生效。
    """
    row = await CandidateService(session, service).adopt(asset_id, actor.id, payload)
    return ApiResponse.ok(service.to_out(row, actor.id))


@router.post("/{asset_id}/discard", summary="弃用生成候选")
async def discard_candidate(
    asset_id: str,
    actor: UpdateDep,
    service: ArchiveServiceDep,
    session: SessionDep,
    # 弃用与采纳共用一档额度（都是候选确认动作，手速上限相同）
    _: None = Depends(rate_limit("asset.candidates.adopt.one", limit=60)),
) -> ApiResponse[ArchivedAsset]:
    """弃用**不删字节**：字节等宽限期后由 `asset.gc` 回收，配方永久保留
    —— 衍生链可能从它经过，删了会让成品的血缘在中途断掉。"""
    row = await CandidateService(session, service).discard(asset_id, actor.id)
    return ApiResponse.ok(service.to_out(row, actor.id))


@router.get("/{asset_id}/lineage", summary="生成血缘：配方、父链与引用关系")
async def asset_lineage(
    asset_id: str, actor: ArchiveReadDep, service: ArchiveServiceDep, session: SessionDep
) -> ApiResponse[LineageOut]:
    # 血缘对**可读**素材开放（公共原片也能看它是怎么来的），不是只对本人
    return ApiResponse.ok(await RecipeService(session, service).lineage(asset_id, actor.id))


@router.post("/{asset_id}/derive", summary="一键衍生：回填生成页的输入位")
async def derive_from_asset(
    asset_id: str,
    payload: DeriveRequest,
    actor: ArchiveReadDep,
    service: ArchiveServiceDep,
    session: SessionDep,
    # 它不写库，但**会暴露配方全文**（提示词是有价值的资产），按读接口的宽松档限流
    _: None = Depends(rate_limit("asset.derive", limit=settings.rate_limit_default_per_min)),
) -> ApiResponse[DeriveIntent]:
    """只回填输入，**不建任务、不扣费**：生成有额度与模型选择，提交由生成页自己做。"""
    return ApiResponse.ok(
        await RecipeService(session, service).derive(asset_id, actor.id, payload.mode)
    )
