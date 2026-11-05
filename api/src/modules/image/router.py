"""生图路由：历史记录与生成入口。"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, status

from api.deps import ImageProviderDep, SessionDep, require
from core.context import CurrentUser
from core.response import ApiResponse
from modules.image.schema import GenerateImageIn, ImageJobListOut, ImageJobOut
from modules.image.service import ImageService

router = APIRouter(tags=["生图"])


def get_image_service(session: SessionDep) -> ImageService:
    return ImageService(session)


ImageServiceDep = Annotated[ImageService, Depends(get_image_service)]

# ---------- 权限依赖 ----------
ImageReadDep = Annotated[CurrentUser, Depends(require("image:read"))]
ImageCreateDep = Annotated[CurrentUser, Depends(require("image:create"))]


@router.get("/images", summary="生图历史")
async def list_images(
    service: ImageServiceDep, actor: ImageReadDep
) -> ApiResponse[ImageJobListOut]:
    return ApiResponse.ok(await service.list_jobs(actor))


@router.post(
    "/generations/image",
    status_code=status.HTTP_201_CREATED,
    summary="提交生图（待实现）",
)
async def generate_image(
    service: ImageServiceDep,
    actor: ImageCreateDep,
    provider: ImageProviderDep,
    payload: GenerateImageIn,
) -> ApiResponse[ImageJobOut]:
    job = await service.generate(actor, payload, provider)
    return ApiResponse.ok(ImageJobOut.model_validate(job), message="已提交")
