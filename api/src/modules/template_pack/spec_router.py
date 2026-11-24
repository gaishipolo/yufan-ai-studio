"""模板规格路由：只做协议转换，判定全在 `spec_service`。

## 权限码不新增

沿用 `template_pack:read / create / publish`。新增码会让内置角色静默失权
（`identity` 的内置角色里没有新码），而且语义本来就对得上：读=看模板、
create=存草稿、publish=发布/下架。

## 顺序有意义

`/published` 必须声明在 `/{key}` 之前，否则 `published` 会被当成一个 key。
"""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, status

from api.deps import SessionDep, rate_limit
from core.config import settings
from core.response import ApiResponse
from modules.template_pack.dependencies import (
    TemplatePackCreateDep,
    TemplatePackPublishDep,
    TemplatePackReadDep,
)
from modules.template_pack.spec_schema import (
    SpecCompileIn,
    SpecCompileOut,
    SpecLimitsOut,
    SpecPublishOut,
    SpecValidateOut,
    TemplateSpecIn,
    TemplateSpecListOut,
    TemplateSpecOut,
)
from modules.template_pack.spec_service import (
    compile_for,
    delete,
    disable,
    get_out,
    list_out,
    publish,
    restore,
    save,
    validate_draft,
)

router = APIRouter(prefix="/template-specs", tags=["模板规格"])

_WRITE_LIMIT = Depends(
    rate_limit("template_pack.create", limit=settings.rate_limit_default_per_min)
)


@router.post(
    "/validate", summary="预检模板规格（不落库）", dependencies=[_WRITE_LIMIT]
)
async def validate_spec_draft(
    payload: TemplateSpecIn,
    session: SessionDep,
    actor: TemplatePackCreateDep,
) -> ApiResponse[SpecValidateOut]:
    return ApiResponse.ok(await validate_draft(session, actor, payload))


@router.post(
    "", summary="保存草稿（同内容幂等，revision 冲突 409）", dependencies=[_WRITE_LIMIT]
)
async def save_spec(
    payload: TemplateSpecIn,
    session: SessionDep,
    actor: TemplatePackCreateDep,
) -> ApiResponse[TemplateSpecOut]:
    return ApiResponse.ok(await save(session, actor, payload))


@router.get("", summary="本人可见的规格列表")
async def list_specs(
    session: SessionDep,
    actor: TemplatePackReadDep,
    status_filter: str = Query(default="", alias="status", max_length=16),
    keyword: str = Query(default="", max_length=80),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100, alias="pageSize"),
) -> ApiResponse[TemplateSpecListOut]:
    return ApiResponse.ok(
        await list_out(
            session,
            actor,
            status=status_filter,
            keyword=keyword,
            page=page,
            page_size=page_size,
        )
    )


@router.get("/published", summary="模板库：已发布的规格卡片")
async def list_published_specs(
    session: SessionDep,
    actor: TemplatePackReadDep,
    keyword: str = Query(default="", max_length=80),
    target: Annotated[
        Literal["all", "replacement", "generation"],
        Query(description="按模型声明的替换能力筛：replacement / generation / all"),
    ] = "all",
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100, alias="pageSize"),
) -> ApiResponse[TemplateSpecListOut]:
    return ApiResponse.ok(
        await list_out(
            session,
            actor,
            published_only=True,
            keyword=keyword,
            target=target,
            page=page,
            page_size=page_size,
        )
    )


@router.get("/trash", summary="回收站：已软删的规格")
async def list_trashed_specs(
    session: SessionDep,
    actor: TemplatePackReadDep,
    keyword: str = Query(default="", max_length=80),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100, alias="pageSize"),
) -> ApiResponse[TemplateSpecListOut]:
    """必须声明在 `/{key}` 之前，否则 `trash` 会被当成一个 key。"""
    return ApiResponse.ok(
        await list_out(
            session,
            actor,
            keyword=keyword,
            trashed=True,
            page=page,
            page_size=page_size,
        )
    )


@router.get("/limits", summary="编辑页要显示的上限（存草稿 / 可发布）")
async def get_spec_limits(actor: TemplatePackReadDep) -> ApiResponse[SpecLimitsOut]:
    """上限只在这里给一次，前端不再抄一份数字。

    必须声明在 `/{key}` 之前，否则 `limits` 会被当成一个规格 key。
    """
    return ApiResponse.ok(SpecLimitsOut())


@router.get("/{key}", summary="规格详情（含草稿与最近发布）")
async def get_spec(
    key: str, session: SessionDep, actor: TemplatePackReadDep
) -> ApiResponse[TemplateSpecOut]:
    return ApiResponse.ok(await get_out(session, actor, key))


@router.post(
    "/{key}/publish",
    summary="发布：把当前草稿冻结成新版本",
    dependencies=[_WRITE_LIMIT],
)
async def publish_spec(
    key: str, session: SessionDep, actor: TemplatePackPublishDep
) -> ApiResponse[SpecPublishOut]:
    """发布是**对外公开**的那一刻：从这里起，固定槽位的素材与展示案例必须
    **所有登录用户都读得到**（`purpose=case` 或 `visibility=team`），否则 400。

    草稿阶段不这么判 —— 作者在自己的工作区里引用自己的私有素材完全正常。
    收紧的理由见 `spec_service._check_fixed_refs`：复用者填表时是用**复用者的
    身份**再判一次可见性的，而固定槽位在编译期必须等于作者钉的那一条
    （复用者没法自己换一张），所以作者的私有素材一发布，这个模板对所有人
    就是坏的 —— 错误还要延迟到别人点「生成」才炸出来。

    草稿里已经钉了私有素材的作者有两条出路：把素材移进案例池，或改成团队可见。
    """
    return ApiResponse.ok(await publish(session, actor, key))


@router.post(
    "/{key}/disable", summary="下架（历史版本保留）", dependencies=[_WRITE_LIMIT]
)
async def disable_spec(
    key: str, session: SessionDep, actor: TemplatePackPublishDep
) -> ApiResponse[TemplateSpecOut]:
    return ApiResponse.ok(await disable(session, actor, key))


@router.delete("/{key}", summary="移进回收站（软删，可恢复）", dependencies=[_WRITE_LIMIT])
async def delete_spec(
    key: str, session: SessionDep, actor: TemplatePackPublishDep
) -> ApiResponse[TemplateSpecOut]:
    return ApiResponse.ok(await delete(session, actor, key))


@router.post(
    "/{key}/restore", summary="从回收站恢复", dependencies=[_WRITE_LIMIT]
)
async def restore_spec(
    key: str, session: SessionDep, actor: TemplatePackPublishDep
) -> ApiResponse[TemplateSpecOut]:
    return ApiResponse.ok(await restore(session, actor, key))


@router.post(
    "/{key}/compile",
    summary="填表结果 → 执行请求体（纯映射，不落库）",
    dependencies=[_WRITE_LIMIT],
    status_code=status.HTTP_200_OK,
)
async def compile_spec_request(
    key: str,
    payload: SpecCompileIn,
    session: SessionDep,
    actor: TemplatePackReadDep,
) -> ApiResponse[SpecCompileOut]:
    """给模板库用：拿到出参后由前端直接 `POST /tasks`。

    挂 `rate_limit` 是因为路由策略按 HTTP 方法扫写路由（POST 一律要限流），
    而这条虽然无副作用，形状上是 POST。
    """
    return ApiResponse.ok(await compile_for(session, actor, key, payload))
