"""资产库 v2 的依赖装配。

权限依赖写在模块内部，`api/deps.py` 不认识 `library:*`。

M2 起有两处刻意**不用** `require` 直接卡住：

- `POST /share-requests/{id}/approve|reject`：审批资格按**申请的目标范围**判
  （全站码 / 团队码 / 小组码 + 我是不是该组成员），还要查组织目录，
  `require()` 表达不了。所以路由只要求 `library:read`，真正的判定在
  `ShareService._can_decide()`；
- `POST /assets/bulk`：权限按 action 判（`library:update` / `delete` / `create` /
  `share`），一个动作一个码，写在 `BulkService.PERMISSION_BY_ACTION` 里。

这两处如果照抄 `require("...")`，症状是「有细分审批权的人点了没反应」（403），
而权限表里看起来一切正常。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends

from api.deps import SessionDep, StorageDep, require
from core.context import CurrentUser
from modules.library.bulk import BulkService
from modules.library.downloads import DownloadService
from modules.library.ingest import GenerationIngest
from modules.library.lifecycle import LifecycleService
from modules.library.packs import PackService
from modules.library.service import LibraryService
from modules.library.sharing import ShareService
from modules.library.tags import TagService
from modules.library.text_assets import TextAssetService

__all__ = [
    "TextServiceDep",
    "BulkServiceDep",
    "DownloadServiceDep",
    "GenerationIngestDep",
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
    "PackServiceDep",
    "ShareServiceDep",
    "TagServiceDep",
]


LibraryReadDep = Annotated[CurrentUser, Depends(require("library:read"))]
LibraryCreateDep = Annotated[CurrentUser, Depends(require("library:create"))]
LibraryUpdateDep = Annotated[CurrentUser, Depends(require("library:update"))]
LibraryDeleteDep = Annotated[CurrentUser, Depends(require("library:delete"))]
LibraryShareDep = Annotated[CurrentUser, Depends(require("library:share"))]
#: 只守护 `PUT /groups/{id}/leaders`：负责人表现在只服务删除窄口，是全站级配置，
#: 沿用旧的全站审批码；审批申请本身不看这个依赖（见文件头注释）。
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
#: 生成入库：写的是**别人**（生成模块）的结果，所以单列一个码，
#: 不与上传的 `library:create` 共用——否则任何能上传的人都能伪造来源。
LibraryIngestDep = Annotated[
    CurrentUser, Depends(require("library:generation:ingest"))
]


def get_library_service(session: SessionDep, storage: StorageDep) -> LibraryService:
    return LibraryService(session=session, storage=storage)


def get_lifecycle_service(
    session: SessionDep, storage: StorageDep
) -> LifecycleService:
    return LifecycleService(session=session, storage=storage)


def get_pack_service(session: SessionDep, storage: StorageDep) -> PackService:
    return PackService(session=session, storage=storage)


def get_download_service(session: SessionDep, storage: StorageDep) -> DownloadService:
    return DownloadService(session=session, storage=storage)


def get_ingest_service(session: SessionDep, storage: StorageDep) -> GenerationIngest:
    return GenerationIngest(session=session, storage=storage)


def get_share_service(session: SessionDep) -> ShareService:
    return ShareService(session=session)


def get_tag_service(session: SessionDep) -> TagService:
    return TagService(session=session)


def get_bulk_service(session: SessionDep, storage: StorageDep) -> BulkService:
    return BulkService(session=session, storage=storage)


def get_text_asset_service(
    session: SessionDep, storage: StorageDep
) -> TextAssetService:
    return TextAssetService(session=session, storage=storage)


LibraryServiceDep = Annotated[LibraryService, Depends(get_library_service)]
LifecycleServiceDep = Annotated[LifecycleService, Depends(get_lifecycle_service)]
PackServiceDep = Annotated[PackService, Depends(get_pack_service)]
DownloadServiceDep = Annotated[DownloadService, Depends(get_download_service)]
GenerationIngestDep = Annotated[GenerationIngest, Depends(get_ingest_service)]
ShareServiceDep = Annotated[ShareService, Depends(get_share_service)]
TagServiceDep = Annotated[TagService, Depends(get_tag_service)]
BulkServiceDep = Annotated[BulkService, Depends(get_bulk_service)]
TextServiceDep = Annotated[TextAssetService, Depends(get_text_asset_service)]
