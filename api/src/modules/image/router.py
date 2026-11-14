"""生图路由：历史记录与生成入口。

注意：每个接口显式声明所需权限码，权限点在本模块 `permissions.py` 里登记，
核心不感知。路径刻意与视频任务分开（`/images`、`/generations/image`），
因为「生成」是一个动作、不是某个资源的子集合。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from api.deps import PageDep, rate_limit
from core.config import settings
from core.response import ApiResponse, Page
from modules.image.dependencies import (
    ImageCreateDep,
    ImageDeleteDep,
    ImageReadDep,
    ImageServiceDep,
)
from modules.image.schema import (
    DeleteImagesIn,
    DeleteImagesOut,
    GenerateImageIn,
    ImageJobOut,
    ImageRetryOut,
)

router = APIRouter(tags=["生图"])


@router.get("/images", summary="生图历史（分页）")
async def list_images(
    service: ImageServiceDep, actor: ImageReadDep, page: PageDep
) -> ApiResponse[Page[ImageJobOut]]:
    return ApiResponse.ok(await service.list_jobs(actor, page))


@router.post(
    "/generations/image",
    status_code=status.HTTP_201_CREATED,
    summary="提交生图",
    description=(
        "受理即返回 `queued`，实际生成由后台队列完成；"
        "客户端轮询 `GET /images` 或明细接口取 `status` / `outputUrl`。"
    ),
)
async def generate_image(
    service: ImageServiceDep,
    actor: ImageCreateDep,
    payload: GenerateImageIn,
    # 生图是真花钱的入口：每个请求对应一次供应商调用，按用户限速
    _: None = Depends(rate_limit("image.generate", limit=settings.rate_limit_default_per_min)),
) -> ApiResponse[ImageJobOut]:
    job = await service.generate(actor, payload)
    return ApiResponse.ok(await service.out(job), message="已提交")


@router.delete("/images", summary="批量删除生图记录")
async def delete_images(
    service: ImageServiceDep,
    actor: ImageDeleteDep,
    payload: DeleteImagesIn,
    # 批量写没有天然上限（一次 200 条），额度给得比生成宽，但要有
    _: None = Depends(rate_limit("image.delete", limit=settings.rate_limit_default_per_min)),
) -> ApiResponse[DeleteImagesOut]:
    """删除生成历史（含进行中的记录）。

    与 `DELETE /tasks` 同一套语义：能删本人的，有 `image:read:all` 才能删别人的；
    不存在或无权限的 id 回在 `skipped` 里，不整批 403。

    **删了就不能再重试**：失败记录上的 `externalId` 是核对"是否已扣费"的凭证。
    用户 2026-11-15 明确接受这个取舍，所以这里不拦进行中的任务、也不要求二次确认。
    """
    deleted, skipped = await service.delete_jobs(payload.ids, actor)
    message = (
        f"已删除 {deleted} 条" if not skipped else f"已删除 {deleted} 条，{len(skipped)} 条未删除"
    )
    return ApiResponse.ok(DeleteImagesOut(deleted=deleted, skipped=skipped), message=message)


@router.post("/images/{job_id}/retry", summary="重试失败的生图")
async def retry_image(
    service: ImageServiceDep,
    actor: ImageCreateDep,
    job_id: str,
    confirm_charged: Annotated[
        bool, Query(alias="confirmCharged", description="确认上一次可能已扣费，仍要重新提交")
    ] = False,
    # 重试同样会再扣一次费；额度比新建低一档（重试是补救动作，不该被刷）
    _: None = Depends(rate_limit("image.retry", limit=settings.rate_limit_default_per_min)),
) -> ApiResponse[ImageRetryOut]:
    """`SUBMIT_UNKNOWN_MESSAGE` 承诺的「手动重试」出口（R11-02 / 手册 §5.6②）。

    重试会再向供应商提交一次，因此要 `image:create`；替别人点还要 `image:retry:any`。
    没有这个出口时，`failed` 记录会被 `handle_submit` 的终态早退永久锁死：
    文案说「请核对后手动重试」，而用户根本无处可点。

    记录带 `externalId`（供应商任务号）时说明**上一次可能已经扣费**，默认返回
    409 要求先核对；只有带 `confirmCharged=true` 才允许重新提交。不带参数的
    旧调用行为不变（R12-01）。
    """
    job = await service.retry(actor, job_id, confirm_charged=confirm_charged)
    return ApiResponse.ok(ImageRetryOut(id=job.id, status=job.status), message="已重新排队")
