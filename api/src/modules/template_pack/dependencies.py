"""模板封装模块的依赖装配：权限依赖。

## 一套权限码

`template_pack:read / create / publish` 管全部规格接口：读=看模板、create=存草稿、
publish=发布/下架。新增码会让内置角色静默失权（`identity` 的内置角色里没有新码）。

素材包与视频模板那套 `template:pack:manage` / `template:pack:publish` 随旧接口一起
下线了（2026-11-14 P3），不再有第二套码。

## 为什么权限依赖写在这里

新增权限点、换服务构造方式都不该动 `api/deps.py`（那是装配层）。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends

from api.deps import require
from core.context import CurrentUser

TemplatePackReadDep = Annotated[CurrentUser, Depends(require("template_pack:read"))]
TemplatePackCreateDep = Annotated[CurrentUser, Depends(require("template_pack:create"))]
TemplatePackPublishDep = Annotated[
    CurrentUser, Depends(require("template_pack:publish"))
]

# 案例（可复用模板快照）有自己的一对码：读案例库/复用案例 与 发布/编辑/下架/删除
# 案例是两种不同性质的动作 —— 看别人的案例去复用，和把自己的模板发出去给别人用，
# 不该是同一个权限。合用一个码就分不出「谁可以发布案例」。
#
# 新增码会让内置角色**静默失权**（identity 的内置角色里没有新码就等于没这个
# 权限），所以两个新码在 `identity/permissions.py` 里同步给了对应角色。
TemplateCaseReadDep = Annotated[
    CurrentUser, Depends(require("template_pack:case:read"))
]
TemplateCaseWriteDep = Annotated[
    CurrentUser, Depends(require("template_pack:case:write"))
]
