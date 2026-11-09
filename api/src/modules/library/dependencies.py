"""资产库 v2 的权限依赖。

权限码只在模块内声明与引用，`api/deps.py` 不认识 `library:*`。
M0 只有读依赖：写接口（上传 / 共享 / 删除 / 审核）在 M2/M3 随实现一起加，
不提前挂一个永远 501 的写路由——那会让前端以为接口已经能调。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends

from api.deps import require
from core.context import CurrentUser

__all__ = ["LibraryReadDep"]

LibraryReadDep = Annotated[CurrentUser, Depends(require("library:read"))]
