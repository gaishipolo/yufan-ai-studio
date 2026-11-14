"""替换链路由：前缀 `/templates`，只挂替换相关子路由。

素材包与视频模板的子路由已经搬到 `modules/template_pack/router.py`（C2）。
两边的路径前缀相同、子路径不重叠，所以在 `app.routes` 上看到的是同一个
`/templates/*` 命名空间——前端 41 个文件按这些路径对接，一条都没变。

**没有 `GET /templates`**：重建前它返回一张 `video_templates` 演示表，前端
`useTemplates()` 全仓库零调用，而且形状还对不上（声明 `Paginated`、实际返回
`{items,total}`）。这条路径连同那张表一起删掉了，别再加回来。
"""

from __future__ import annotations

from fastapi import APIRouter

from modules.template.replacement_router import router as replacement_router

router = APIRouter(prefix="/templates", tags=["模板"])
router.include_router(replacement_router)
