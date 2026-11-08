"""分镜模板模块：整段分镜结构与时长/比例预设。"""

from api.module import ModuleSpec
from core.permissions import PermissionSpec
from modules.storyboard.router import router
from modules.storyboard.seed import seed as seed_demo

module = ModuleSpec(
    name="storyboard",
    router=router,
    tags=["分镜模板"],
    permissions=(
        PermissionSpec("storyboard:read", "查看分镜模板", group="内容生产"),
    ),
    seed=seed_demo,
    seed_order=40,
)
