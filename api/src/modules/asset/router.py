"""素材模块路由聚合。

`/assets`、`/teams`、`/asset-categories`、`/asset-tags`、`/asset-views`、`/subjects`
是六条独立的资源路径，前缀都由各自的路由器带好，这里只做 include。这样
`api.py` 里不必知道团队、分类树、主体与标签的存在，它们也不必挂在素材前缀下面。
"""

from __future__ import annotations

from fastapi import APIRouter

from modules.asset.api import router as assets_router
from modules.asset.category_api import router as category_router
from modules.asset.subject_api import router as subject_router
from modules.asset.tag_api import router as tag_router
from modules.asset.tag_api import views_router as view_router
from modules.asset.team_api import router as team_router

# tags 只在这里声明一次：`build_v1_router` 的判据是「父 router 有没有 tags」，
# 父与子都写会被 FastAPI 追加成 ["素材", "素材"]，文档里出现两个同名分组。
# 团队与主体接口也归「素材」分组——它们属于素材域的库边界与装配单位，
# 不是独立的业务模块。
router = APIRouter(tags=["素材"])
router.include_router(assets_router)
router.include_router(category_router)
router.include_router(subject_router)
router.include_router(team_router)
router.include_router(tag_router)
router.include_router(view_router)
