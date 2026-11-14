"""模板封装路由：`/templates` 下的素材包与视频模板。

从 `modules/template/router.py` 拆出来（2026-11-14 C2）。路径前缀与子路径
（`/replacement-packs`、`/video-templates`）与搬之前完全一致：前端按这些路径
对接，改一个字母就是「换存储」变成「换产品」。
"""

from __future__ import annotations

from fastapi import APIRouter

from modules.template_pack.pack_router import router as pack_router
from modules.template_pack.video_template_router import router as video_template_router

router = APIRouter(prefix="/templates", tags=["模板"])
router.include_router(pack_router)
router.include_router(video_template_router)
