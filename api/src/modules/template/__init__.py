"""模板模块：行业模板与分镜结构复用。"""

from api.module import ModuleSpec
from core.permissions import PermissionSpec
from modules.template.router import router

module = ModuleSpec(
    name="template",
    router=router,
    tags=["模板"],
    permissions=(
        PermissionSpec("template:read", "查看行业模板", group="内容生产"),
    ),
)
