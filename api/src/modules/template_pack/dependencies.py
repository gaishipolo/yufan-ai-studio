"""模板封装模块的依赖装配：服务实例 + 权限依赖。

## 两套权限码，各自管一段

- `template_pack:*`：C1 新增的 manifest 配置接口（预检 / 保存 / 发布）。
- `template:pack:manage` / `template:pack:publish`：**搬过来的**素材包与视频模板
  接口沿用重建前的码，一个都没改名。改名等于让所有非超管账号在一次升级里静默
  失去权限，而超管自测发现不了（见 `modules/template/dependencies.py` 的说明）。

## 为什么服务依赖写在这里

新增权限点、换服务构造方式都不该动 `api/deps.py`（那是装配层）。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends

from api.deps import SessionDep, require
from core.context import CurrentUser
from modules.template_pack.pack_service import PackService


# ---------- 服务 ----------
def get_pack_service(session: SessionDep) -> PackService:
    return PackService(session)


PackServiceDep = Annotated[PackService, Depends(get_pack_service)]

# ---------- 权限依赖：manifest 配置 ----------
TemplatePackReadDep = Annotated[CurrentUser, Depends(require("template_pack:read"))]
TemplatePackCreateDep = Annotated[CurrentUser, Depends(require("template_pack:create"))]
TemplatePackPublishDep = Annotated[
    CurrentUser, Depends(require("template_pack:publish"))
]

# ---------- 权限依赖：素材包 / 视频模板（沿用重建前的码） ----------
PackManageDep = Annotated[CurrentUser, Depends(require("template:pack:manage"))]
PackPublishDep = Annotated[CurrentUser, Depends(require("template:pack:publish"))]
