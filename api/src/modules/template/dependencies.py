"""模板库依赖装配：服务实例 + 五个权限点。

权限点**与重建前完全一致**（`template:read` / `use` / `review` / `pack:manage` /
`pack:publish`），不是照抄，是因为它们已经写进了三个内置角色
（`identity/permissions.py` 的 editor / viewer / asset_specialist）。换名字等于
让所有非超管账号在一次升级里静默失去权限，而超管自测发现不了。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends

from api.deps import SessionDep, require
from core.context import CurrentUser
from modules.template.pack_service import PackService
from modules.template.replacement_service import ReplacementService


def get_pack_service(session: SessionDep) -> PackService:
    return PackService(session)


def get_replacement_service(session: SessionDep) -> ReplacementService:
    return ReplacementService(session)


PackServiceDep = Annotated[PackService, Depends(get_pack_service)]
ReplacementServiceDep = Annotated[ReplacementService, Depends(get_replacement_service)]

# ---------- 权限依赖 ----------
# 五个码的分工刻意写得这么细（见 EXTENDING §2.1）：
#   read   —— 能看模板与封装（只读成员也要有，否则模板库页面一片空白）
#   use    —— 能提交生成（会花钱/占队列，只读成员不能有）
#   review —— 能评价成片（与 use 分开：评价不该要求能提交）
#   pack:manage  —— 能改本人封装的草稿
#   pack:publish —— 能发布/停用（对外可见性变更，比编辑更重）
TemplateReadDep = Annotated[CurrentUser, Depends(require("template:read"))]
TemplateUseDep = Annotated[CurrentUser, Depends(require("template:use"))]
TemplateReviewDep = Annotated[CurrentUser, Depends(require("template:review"))]
PackManageDep = Annotated[CurrentUser, Depends(require("template:pack:manage"))]
PackPublishDep = Annotated[CurrentUser, Depends(require("template:pack:publish"))]
