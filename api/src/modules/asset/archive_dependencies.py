"""归档 HTTP 依赖装配。"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends

from api.deps import SessionDep, require
from core.context import CurrentUser
from modules.asset.archive_service import ArchiveAssetService


def get_archive_service(session: SessionDep) -> ArchiveAssetService:
    return ArchiveAssetService(session)


ArchiveServiceDep = Annotated[ArchiveAssetService, Depends(get_archive_service)]
ArchiveReadDep = Annotated[CurrentUser, Depends(require("asset:read"))]
ArchiveWriteDep = Annotated[CurrentUser, Depends(require("asset:create"))]
