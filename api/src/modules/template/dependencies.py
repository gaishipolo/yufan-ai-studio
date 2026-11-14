"""替换链的依赖装配：服务实例 + 三个权限点。

权限点**与重建前完全一致**（`template:read` / `use` / `review`），不是照抄，是因为
它们已经写进了三个内置角色（`identity/permissions.py` 的 editor / viewer /
asset_specialist）。换名字等于让所有非超管账号在一次升级里静默失去权限，
而超管自测发现不了。

素材包的 `template:pack:manage` / `template:pack:publish` 两个码跟着封装接口
一起搬到了 `modules/template_pack/dependencies.py`（码本身没变）。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends

from api.deps import SessionDep, require
from core.context import CurrentUser
from modules.template.replacement_service import ReplacementService


def get_replacement_service(session: SessionDep) -> ReplacementService:
    return ReplacementService(session)


ReplacementServiceDep = Annotated[ReplacementService, Depends(get_replacement_service)]

# ---------- 权限依赖 ----------
# 三个码的分工刻意写得这么细（见 EXTENDING §2.1）：
#   read   —— 能看模板与替换档案（只读成员也要有，否则页面一片空白）
#   use    —— 能提交生成（会花钱/占队列，只读成员不能有）
#   review —— 能评价成片（与 use 分开：评价不该要求能提交）
TemplateReadDep = Annotated[CurrentUser, Depends(require("template:read"))]
TemplateUseDep = Annotated[CurrentUser, Depends(require("template:use"))]
TemplateReviewDep = Annotated[CurrentUser, Depends(require("template:review"))]
