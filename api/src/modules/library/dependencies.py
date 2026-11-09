"""资产库 v2 的依赖装配。

权限依赖写在模块内部，`api/deps.py` 不认识 `library:*`。

M2 起有两处刻意**不用** `require` 直接卡住：

- `POST /share-requests/{id}/approve|reject`：小组负责人也能审批，而组长不一定
  持有 `library:share:approve`（那是个管理员级权限点）。所以路由只要求
  `library:read`，真正的判定在 `ShareService._can_decide()`；
- `POST /assets/bulk`：权限按 action 判（`library:update` / `delete` / `create` /
  `share`），一个动作一个码，写在 `BulkService.PERMISSION_BY_ACTION` 里。

这两处如果照抄 `require("...")`，症状是「组长点了没反应」（403），而权限表里
看起来一切正常。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends

from api.deps import SessionDep, StorageDep, require
from core.context import CurrentUser
from modules.library.bulk import BulkService
from modules.library.lifecycle import LifecycleService
from modules.library.service import LibraryService
from modules.library.sharing import ShareService
from modules.library.tags import TagService

__all__ = [
    "BulkServiceDep",
    "LibraryCreateDep",
    "LibraryDeleteDep",
    "LibraryDraftDep",
    "LibraryReadDep",
    "LibraryServiceDep",
    "LibraryShareApproveDep",
    "LibraryShareDep",
    "LibraryTagManageDep",
    "LibraryUpdateDep",
    "LifecycleServiceDep",
    "ShareServiceDep",
    "TagServiceDep",
]


LibraryReadDep = Annotated[CurrentUser, Depends(require("library:read"))]
LibraryCreateDep = Annotated[CurrentUser, Depends(require("library:create"))]
LibraryUpdateDep = Annotated[CurrentUser, Depends(require("library:update"))]
LibraryDeleteDep = Annotated[CurrentUser, Depends(require("library:delete"))]
LibraryShareDep = Annotated[CurrentUser, Depends(require("library:share"))]
LibraryShareApproveDep = Annotated[
    CurrentUser, Depends(require("library:share:approve"))
]
LibraryTagManageDep = Annotated[CurrentUser, Depends(require("library:tag:manage"))]
#: 草稿采纳/驳回：本人（`create`）或代审（`draft:review`）
LibraryDraftDep = Annotated[
    CurrentUser, Depends(require("library:create", "library:draft:review", mode="any"))
]
#: 批量入口：只要在四种写权限里有任意一个就放行，具体动作在服务层判
BulkDep = Annotated[
    CurrentUser,
    Depends(
        require(
            "library:update",
            "library:delete",
            "library:create",
            "library:share",
            mode="any",
        )
    ),
]


def get_library_service(session: SessionDep, storage: StorageDep) -> LibraryService:
    return LibraryService(session=session, storage=storage)


def get_lifecycle_service(
    session: SessionDep, storage: StorageDep
) -> LifecycleService:
    return LifecycleService(session=session, storage=storage)


def get_share_service(session: SessionDep) -> ShareService:
    return ShareService(session=session)


def get_tag_service(session: SessionDep) -> TagService:
    return TagService(session=session)


def get_bulk_service(session: SessionDep, storage: StorageDep) -> BulkService:
    return BulkService(session=session, storage=storage)


LibraryServiceDep = Annotated[LibraryService, Depends(get_library_service)]
LifecycleServiceDep = Annotated[LifecycleService, Depends(get_lifecycle_service)]
ShareServiceDep = Annotated[ShareService, Depends(get_share_service)]
TagServiceDep = Annotated[TagService, Depends(get_tag_service)]
BulkServiceDep = Annotated[BulkService, Depends(get_bulk_service)]
