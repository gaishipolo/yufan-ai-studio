"""封装模板路由：只做协议转换。

权限点在本模块 `__init__.py` 里登记，核心不感知。

## 路径为什么是 `/template-packs` 而不是 `/template_pack`

REST 惯例是复数短横线（`/replacement-packs`、`/video-templates` 都在用）。
脚手架默认给的是 `/template_pack`（下划线），这里改掉——**路径是契约的一部分**，
改的时候 `api/docs/openapi.json` 与前端 `schema.ts` 要一起重新生成。
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
    TemplatePackUpdateDep,
    TemplatePackUseDep,
)
from modules.template_pack.schema import (
    TemplatePackCandidateListOut,
    TemplatePackDraftIn,
    TemplatePackListOut,
    TemplatePackOut,
    TemplatePackPromptIn,
    TemplatePackVerifyIn,
    TemplatePackVerifyOut,
)
from modules.template_pack.service import (
    candidates,
    disable,
    list_out,
    publish,
    save,
    update_prompt,
    verify_picks,
)

router = APIRouter(prefix="/template-packs", tags=["封装模板"])


@router.get("", summary="封装模板列表")
async def list_packs(
    session: SessionDep,
    actor: TemplatePackReadDep,
    owner_id: str = Query(default="", alias="ownerId", description="只看某人的模板"),
    status_filter: str = Query(default="", alias="status", description="按状态筛选"),
) -> ApiResponse[TemplatePackListOut]:
    """列表。

    **不传 `ownerId` 时只返回已发布的**：别人的草稿是私有工作区。
    传 `ownerId` 看自己的全部（含草稿）。
    """
    return ApiResponse.ok(
        await list_out(
            session, actor=actor, owner_id=owner_id, status=status_filter
        )
    )


@router.get("/{pack_id}", summary="封装模板详情")
async def get_pack(
    pack_id: str, session: SessionDep, actor: TemplatePackReadDep
) -> ApiResponse[TemplatePackOut]:
    from modules.template_pack.repository import TemplatePackRepository
    from modules.template_pack.service import to_out

    repo = TemplatePackRepository(session)
    row = await repo.get_or_404(pack_id)
    if row.status != "published" and row.owner_id != actor.id:
        from core.exceptions import NotFound

        raise NotFound("封装模板不存在")
    return ApiResponse.ok(await to_out(session, row))


# 写接口默认挂限流：漏挂等于完全不限（限流是依赖级，没有全局兜底）。
# 额度用配置里的默认值，不要写死数字——写死之后线上调额度就得改代码。
@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    summary="保存封装模板草稿",
    dependencies=[
        Depends(rate_limit("template_pack.create", limit=settings.rate_limit_default_per_min))
    ],
)
async def save_pack(
    payload: TemplatePackDraftIn,
    session: SessionDep,
    actor: TemplatePackCreateDep,
) -> ApiResponse[TemplatePackOut]:
    """保存草稿。幂等：同 `templateKey` 同配置返回原模板，异配置 409。"""
    return ApiResponse.ok(await save(payload, session, actor=actor))


@router.post(
    "/{pack_id}/publish",
    summary="发布封装模板",
    dependencies=[
        Depends(rate_limit("template_pack.publish", limit=settings.rate_limit_default_per_min))
    ],
)
async def publish_pack(
    pack_id: str, session: SessionDep, actor: TemplatePackPublishDep
) -> ApiResponse[TemplatePackOut]:
    """发布：进入模板库，别人可以取用。"""
    return ApiResponse.ok(await publish(pack_id, session, actor=actor))


@router.post(
    "/{pack_id}/disable",
    summary="停用封装模板",
    dependencies=[
        Depends(rate_limit("template_pack.disable", limit=settings.rate_limit_default_per_min))
    ],
)
async def disable_pack(
    pack_id: str, session: SessionDep, actor: TemplatePackPublishDep
) -> ApiResponse[TemplatePackOut]:
    """停用：保留历史，但不再对外。"""
    return ApiResponse.ok(await disable(pack_id, session, actor=actor))


@router.patch(
    "/{pack_id}/prompt",
    summary="改固定提示词",
    dependencies=[
        Depends(rate_limit("template_pack.update", limit=settings.rate_limit_default_per_min))
    ],
)
async def patch_prompt(
    pack_id: str,
    payload: TemplatePackPromptIn,
    session: SessionDep,
    actor: TemplatePackUpdateDep,
) -> ApiResponse[TemplatePackOut]:
    """只改提示词原文。仅限未发布的模板。"""
    return ApiResponse.ok(
        await update_prompt(pack_id, payload.prompt, session, actor=actor)
    )


@router.get(
    "/{pack_id}/candidates",
    summary="取筛选槽位的合规素材池",
)
async def list_candidates(
    pack_id: str,
    session: SessionDep,
    actor: TemplatePackUseDep,
    token: str = Query(description="槽位标识，如 {{Mixed 2}}"),
    limit: int = Query(default=100, ge=1, le=500),
) -> ApiResponse[TemplatePackCandidateListOut]:
    """运行时选素材用。

    **候选池由服务端算**：筛选条件（标签 + 类别）与可见性都有各自的判据，
    前端自己筛一遍必然漂移。
    """
    return ApiResponse.ok(
        await candidates(pack_id, token, session, actor=actor, limit=limit)
    )


@router.post(
    "/{pack_id}/verify",
    summary="复核运行时选材是否满足筛选条件",
    dependencies=[
        # 额度放宽：每次提交生成任务前都会调一次，纯读操作但调用频率高。
        # 限流在这里防的不是滥用（真正的闸门是筛选条件本身），而是
        # 「拿它当枚举接口扫全库」——扫一遍要一页 500 条，额度够用。
        Depends(rate_limit("template_pack.verify", limit=settings.rate_limit_default_per_min * 2))
    ],
)
async def verify_pack_picks(
    pack_id: str,
    payload: TemplatePackVerifyIn,
    session: SessionDep,
    actor: TemplatePackUseDep,
) -> ApiResponse[TemplatePackVerifyOut]:
    """提交生成任务前的最后一道闸。

    **前端过滤只是 UI 便利，接口可以被直接调用。** 筛选条件是模板的业务
    契约 —— 用户在模板库看到的「只能选产品标签的图」如果只在界面上生效，
    拿个接口一调就能绕过。所以服务端必须再判一次。

    不满足直接 400，不给「差一点点」的余地：筛选要么满足，要么不满足。
    """
    checked = await verify_picks(pack_id, payload.picks, session, actor=actor)
    return ApiResponse.ok(TemplatePackVerifyOut(picks=checked))
