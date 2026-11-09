"""资产库 v2 的依赖装配。

权限依赖写在模块内部，`api/deps.py` 不认识 `library:*`。
M1 只有读 + 上传两个依赖：共享、审核、删除的依赖随 M2 的实现一起加，
不提前挂一个永远 501 的写路由。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends

from api.deps import SessionDep, StorageDep, require
from core.context import CurrentUser
from modules.library.service import LibraryService

__all__ = [
    "LibraryCreateDep",
    "LibraryReadDep",
    "LibraryServiceDep",
]


LibraryReadDep = Annotated[CurrentUser, Depends(require("library:read"))]
LibraryCreateDep = Annotated[CurrentUser, Depends(require("library:create"))]


def get_library_service(session: SessionDep, storage: StorageDep) -> LibraryService:
    return LibraryService(session=session, storage=storage)


LibraryServiceDep = Annotated[LibraryService, Depends(get_library_service)]
