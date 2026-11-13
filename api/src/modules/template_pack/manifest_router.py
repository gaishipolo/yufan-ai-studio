"""manifest 配置路由：只做协议转换，判定全在 `manifest_service`。

## 路径为什么是 `/template-manifests`

`/template-packs` 已被旧的「带筛选约束的封装模板」占着（前端还有页面在用），
两套东西不能抢同一个前缀。阶段 C2 下线旧模块时再统一收敛。

## 权限点为什么复用 `template_pack:*`

不新增权限码：`tests/test_permission_coverage.py` 要求每个权限码都有判定点，
而 `identity/permissions.py` 的内置角色里没有新码，加了会让 editor / viewer
静默失权。读用 `:read`、存草稿用 `:create`、发布停用用 `:publish`，语义对得上。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, status

from api.deps import SessionDep, rate_limit
from core.config import settings
from core.response import ApiResponse
from modules.template_pack.dependencies import (
    TemplatePackCreateDep,
    TemplatePackPublishDep,
    TemplatePackReadDep,
)
from modules.template_pack.manifest_schema import (
    ManifestDraftIn,
    ManifestListOut,
    ManifestOut,
    ManifestPublishOut,
    ManifestValidateOut,
)
from modules.template_pack.manifest_service import (
    disable,
    get_out,
    list_out,
    publish,
    save,
    validate,
)

router = APIRouter(prefix="/template-manifests", tags=["模板封装配置"])

_WRITE_LIMIT = Depends(
    rate_limit("template_pack.create", limit=settings.rate_limit_default_per_min)
)


@router.post(
    "/validate",
    summary="预检 manifest 配置（不落库）",
    dependencies=[_WRITE_LIMIT],
)
async def validate_manifest(
    payload: ManifestDraftIn,
    session: SessionDep,
    actor: TemplatePackCreateDep,
) -> ApiResponse[ManifestValidateOut]:
    """结构、用途、Mixed 编号、素材可读性的全套预检；返回幂等指纹。"""
    return ApiResponse.ok(await validate(session, actor, payload))


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    summary="保存 manifest 草稿",
    dependencies=[_WRITE_LIMIT],
)
async def save_manifest(
    payload: ManifestDraftIn,
    session: SessionDep,
    actor: TemplatePackCreateDep,
) -> ApiResponse[ManifestOut]:
    """幂等：同 key 同配置返回原行；已发布配置改内容 409。"""
    return ApiResponse.ok(await save(session, actor, payload))


@router.get("", summary="manifest 配置列表")
async def list_manifests(
    session: SessionDep,
    actor: TemplatePackReadDep,
    mine_only: bool = Query(default=False, alias="mineOnly"),
    status_filter: str = Query(default="", alias="status"),
) -> ApiResponse[ManifestListOut]:
    """别人的草稿不进列表；传 `mineOnly` 只看自己的全部（含草稿）。"""
    return ApiResponse.ok(
        await list_out(
            session, actor, status_filter=status_filter, mine_only=mine_only
        )
    )


@router.get("/{key}", summary="manifest 配置详情")
async def get_manifest(
    key: str, session: SessionDep, actor: TemplatePackReadDep
) -> ApiResponse[ManifestOut]:
    return ApiResponse.ok(await get_out(session, actor, key))


@router.post(
    "/{key}/publish",
    summary="发布 manifest 配置",
    dependencies=[_WRITE_LIMIT],
)
async def publish_manifest(
    key: str, session: SessionDep, actor: TemplatePackPublishDep
) -> ApiResponse[ManifestPublishOut]:
    """状态流转 + 版本号 +1；返回每张工作台卡片的 `workbenchPath`。"""
    return ApiResponse.ok(await publish(session, actor, key))


@router.post(
    "/{key}/disable",
    summary="停用 manifest 配置",
    dependencies=[_WRITE_LIMIT],
)
async def disable_manifest(
    key: str, session: SessionDep, actor: TemplatePackPublishDep
) -> ApiResponse[ManifestOut]:
    """停用不是删除：模板是资产，历史版本还要能回放。"""
    return ApiResponse.ok(await disable(session, actor, key))
