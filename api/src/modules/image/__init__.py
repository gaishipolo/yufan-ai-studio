"""生图模块：文生图作业的提交与历史。"""

from api.module import ModuleSpec
from core.permissions import PermissionSpec
from modules.image.router import router

module = ModuleSpec(
    name="image",
    router=router,
    tags=["生图"],
    permissions=(
        PermissionSpec("image:read", "查看生图历史", group="内容生产"),
        PermissionSpec("image:create", "提交生图任务", group="内容生产"),
    ),
)
