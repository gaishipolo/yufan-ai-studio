"""模板案例路由：只做协议转换，判定全在 `case_service`。

## 与「素材案例池」的关系

素材库那边也有「案例」（`library_assets.purpose = case`）—— 那是**一条素材**
被标出来当模板卡片的封面。本模块的「案例」是**一份模板配置快照**，可被复制成
新模板。两者路由前缀不同（`/template-cases` vs `/library/...`），权限码也不同
（`template_pack:case:*` vs `library:case:*`）。
"""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Path, Query, status

from api.deps import SessionDep, rate_limit
from core.config import settings
from core.response import ApiResponse
from modules.template_pack.case_schema import (
    TemplateCaseCreateIn,
    TemplateCaseListOut,
    TemplateCaseOut,
    TemplateCaseUpdateIn,
)
from modules.template_pack.case_service import (
    delete_case,
    get_case,
    list_cases,
    offline_case,
    online_case,
    publish_case,
    restore_case,
    update_case,
)
from modules.template_pack.dependencies import (
    TemplateCaseReadDep,
    TemplateCaseWriteDep,
    TemplatePackPublishDep,
)

router = APIRouter(prefix="/template-cases", tags=["模板案例"])

_WRITE_LIMIT = Depends(
    rate_limit("template_pack.case_write", limit=settings.rate_limit_default_per_min)
)

_CaseId = Annotated[str, Path(min_length=1, max_length=64)]


@router.get("", summary="案例列表（library=公共案例库 / mine=我发布的 / trash=回收站）")
async def list_template_cases(
    session: SessionDep,
    actor: TemplateCaseReadDep,
    scope: Literal["library", "mine", "trash"] = Query(default="library"),
    status_filter: str = Query(default="", alias="status", max_length=16),
    category: str = Query(default="", max_length=64),
    keyword: str = Query(default="", max_length=80),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200, alias="pageSize"),
) -> ApiResponse[TemplateCaseListOut]:
    return ApiResponse.ok(
        await list_cases(
            session,
            actor,
            scope=scope,
            status=status_filter,
            category=category,
            keyword=keyword,
            page=page,
            page_size=page_size,
        )
    )


@router.post(
    "",
    summary="把模板发布为案例（快照当前草稿）",
    status_code=status.HTTP_201_CREATED,
    dependencies=[_WRITE_LIMIT],
)
async def create_template_case(
    payload: TemplateCaseCreateIn,
    session: SessionDep,
    actor: TemplateCaseWriteDep,
    # 发布案例 = 把模板配置公开给所有人复用，与「发布模板」是同一类对外动作。
    # 因此除了案例的写码，还要有模板的发布码 —— 否则一个只有 `case:write`
    # 的角色就能借案例绕过「模板要先发布才能用」的闸口。
    _publisher: TemplatePackPublishDep,
) -> ApiResponse[TemplateCaseOut]:
    return ApiResponse.ok(await publish_case(session, actor, payload))


@router.get("/{case_id}", summary="案例详情（含可复用的配置快照）")
async def get_template_case(
    case_id: _CaseId,
    session: SessionDep,
    actor: TemplateCaseReadDep,
) -> ApiResponse[TemplateCaseOut]:
    return ApiResponse.ok(await get_case(session, actor, case_id))


@router.patch(
    "/{case_id}",
    summary="编辑案例（名称/描述/分类）",
    dependencies=[_WRITE_LIMIT],
)
async def update_template_case(
    case_id: _CaseId,
    payload: TemplateCaseUpdateIn,
    session: SessionDep,
    actor: TemplateCaseWriteDep,
) -> ApiResponse[TemplateCaseOut]:
    return ApiResponse.ok(await update_case(session, actor, case_id, payload))


@router.post(
    "/{case_id}/offline",
    summary="下架案例（不可复用，可重新上架）",
    dependencies=[_WRITE_LIMIT],
)
async def offline_template_case(
    case_id: _CaseId,
    session: SessionDep,
    actor: TemplateCaseWriteDep,
) -> ApiResponse[TemplateCaseOut]:
    return ApiResponse.ok(await offline_case(session, actor, case_id))


@router.post(
    "/{case_id}/online",
    summary="重新上架案例",
    dependencies=[_WRITE_LIMIT],
)
async def online_template_case(
    case_id: _CaseId,
    session: SessionDep,
    actor: TemplateCaseWriteDep,
) -> ApiResponse[TemplateCaseOut]:
    return ApiResponse.ok(await online_case(session, actor, case_id))


@router.delete(
    "/{case_id}",
    summary="删除案例（软删，进回收站）",
    dependencies=[_WRITE_LIMIT],
)
async def delete_template_case(
    case_id: _CaseId,
    session: SessionDep,
    actor: TemplateCaseWriteDep,
) -> ApiResponse[TemplateCaseOut]:
    return ApiResponse.ok(await delete_case(session, actor, case_id))


@router.post(
    "/{case_id}/restore",
    summary="从回收站恢复案例",
    dependencies=[_WRITE_LIMIT],
)
async def restore_template_case(
    case_id: _CaseId,
    session: SessionDep,
    actor: TemplateCaseWriteDep,
) -> ApiResponse[TemplateCaseOut]:
    return ApiResponse.ok(await restore_case(session, actor, case_id))
