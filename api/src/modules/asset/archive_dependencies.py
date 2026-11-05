"""归档 HTTP 依赖装配。"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends

from api.deps import require
from core.context import CurrentUser
from modules.asset.archive_service import ArchiveAssetService


def get_archive_service() -> ArchiveAssetService:
    return ArchiveAssetService()


ArchiveServiceDep = Annotated[ArchiveAssetService, Depends(get_archive_service)]
ArchiveReadDep = Annotated[CurrentUser, Depends(require("asset:read"))]
ArchiveWriteDep = Annotated[CurrentUser, Depends(require("asset:create"))]
