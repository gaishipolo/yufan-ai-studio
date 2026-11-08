"""模板库路由聚合。

前缀 `/templates` 与子路由 `/replacement-packs`、`/replacement-runs`、
`/replacement-catalog` 都保持重建前的形状——前端 41 个文件按这些路径对接。

**没有 `GET /templates`**：重建前它返回一张 `video_templates` 演示表，前端
`useTemplates()` 全仓库零调用，而且形状还对不上（声明 `Paginated`、实际返回
`{items,total}`）。本轮重建的范围是替换流水线，不带模板目录，所以这条路径
连同那张表一起删掉了。
"""

from __future__ import annotations

from fastapi import APIRouter

from modules.template.pack_router import router as pack_router
from modules.template.replacement_router import router as replacement_router

router = APIRouter(prefix="/templates", tags=["模板"])
router.include_router(replacement_router)
router.include_router(pack_router)
