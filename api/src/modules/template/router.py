"""模板路由。"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from api.deps import SessionDep, require
from core.context import CurrentUser
from core.response import ApiResponse
from modules.template.schema import TemplateListOut
from modules.template.service import TemplateService

router = APIRouter(prefix="/templates", tags=["模板"])


def get_template_service(session: SessionDep) -> TemplateService:
    return TemplateService(session)


TemplateServiceDep = Annotated[TemplateService, Depends(get_template_service)]

# ---------- 权限依赖 ----------
TemplateReadDep = Annotated[CurrentUser, Depends(require("template:read"))]


@router.get("", summary="模板列表")
async def list_templates(
    service: TemplateServiceDep,
    actor: TemplateReadDep,
    industry: Annotated[str | None, Query(max_length=64)] = None,
) -> ApiResponse[TemplateListOut]:
    return ApiResponse.ok(await service.list_templates(industry=industry))
