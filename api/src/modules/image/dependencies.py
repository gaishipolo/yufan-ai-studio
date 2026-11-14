"""生图依赖装配。

权限依赖写在模块内部：新增权限点不需要修改 `api/deps.py`。
`Service` 只注入 session——它内部自己建 Repository，因为「查哪张表」是模块私事，
把它提到依赖层会让路由文件多知道一个模型（`dependencies.py` 曾生成 repo 注入版，
对只有一张主表的模块是纯噪音）。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends

from api.deps import SessionDep, require
from core.context import CurrentUser
from modules.image.permissions import PERM_CREATE, PERM_DELETE, PERM_READ
from modules.image.service import ImageService


def get_image_service(session: SessionDep) -> ImageService:
    return ImageService(session)


ImageServiceDep = Annotated[ImageService, Depends(get_image_service)]

# ---------- 权限依赖 ----------
# 权限码只写在 `permissions.py` 一处，这里全部引常量：字符串写两遍迟早会出现
# 「常量改了、路由还挂着旧码」的分叉（task 模块就是这么做的）
ImageReadDep = Annotated[CurrentUser, Depends(require(PERM_READ))]
ImageCreateDep = Annotated[CurrentUser, Depends(require(PERM_CREATE))]
ImageDeleteDep = Annotated[CurrentUser, Depends(require(PERM_DELETE))]
