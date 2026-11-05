"""镜头库模块：可复用的运镜指令词条。"""

from api.module import ModuleSpec
from core.permissions import PermissionSpec
from modules.shot.router import router

module = ModuleSpec(
    name="shot",
    router=router,
    tags=["镜头库"],
    permissions=(
        PermissionSpec("shot:read", "查看镜头库", group="内容生产"),
    ),
)
