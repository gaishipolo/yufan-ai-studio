"""导出依赖装配。

权限依赖写在模块内部：新增权限点不需要修改 `api/deps.py`。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends

from api.deps import SessionDep, require
from core.context import CurrentUser
from db.repository import BaseRepository
from modules.export.models import Export
from modules.export.service import ExportService


def get_export_repository(session: SessionDep) -> BaseRepository[Export]:
    return BaseRepository(session, Export)


ExportRepoDep = Annotated[BaseRepository[Export], Depends(get_export_repository)]


def get_export_service(
    session: SessionDep, repo: ExportRepoDep
) -> ExportService:
    return ExportService(session=session, repo=repo)


ExportServiceDep = Annotated[ExportService, Depends(get_export_service)]

# ---------- 权限依赖 ----------
# 没有 update：导出没有「改」，见 __init__.py 的说明
ExportReadDep = Annotated[CurrentUser, Depends(require("export:read"))]
ExportCreateDep = Annotated[CurrentUser, Depends(require("export:create"))]
ExportDeleteDep = Annotated[CurrentUser, Depends(require("export:delete"))]
