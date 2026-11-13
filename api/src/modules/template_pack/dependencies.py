"""封装模板依赖装配。

权限依赖写在模块内部：新增权限点不需要修改 `api/deps.py`。

**不注入 `Repository` 依赖**：本模块有两张表（模板 + 槽位），槽位的操作
（整体替换、按 position 取）不是通用 CRUD 能表达的，硬套 `BaseRepository`
会让路由知道多一层没用的模型。service 自己用 `TemplatePackRepository`
更短、更直白。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends

from api.deps import require
from core.context import CurrentUser

# ---------- 权限依赖 ----------
TemplatePackReadDep = Annotated[CurrentUser, Depends(require("template_pack:read"))]
TemplatePackCreateDep = Annotated[CurrentUser, Depends(require("template_pack:create"))]
TemplatePackUpdateDep = Annotated[CurrentUser, Depends(require("template_pack:update"))]
TemplatePackPublishDep = Annotated[
    CurrentUser, Depends(require("template_pack:publish"))
]
TemplatePackUseDep = Annotated[CurrentUser, Depends(require("template_pack:use"))]
