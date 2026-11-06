"""替换模板的 HTTP 依赖装配。"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends

from api.deps import require
from core.context import CurrentUser
from modules.template.replacement_service import ReplacementService


def get_replacement_service() -> ReplacementService:
    return ReplacementService()


ReplacementServiceDep = Annotated[ReplacementService, Depends(get_replacement_service)]
ReplacementReadDep = Annotated[CurrentUser, Depends(require("template:read"))]
ReplacementWriteDep = Annotated[CurrentUser, Depends(require("template:use"))]
ReplacementManageDep = Annotated[CurrentUser, Depends(require("template:read", "template:use"))]
ReplacementReviewDep = Annotated[
    CurrentUser, Depends(require("template:read", "template:review"))
]
