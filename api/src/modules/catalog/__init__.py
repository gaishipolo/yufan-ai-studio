"""模型目录模块：把「有哪些模型可用」变成接口，而不是前端常量。

价值不在这个接口本身，而在**解耦**：新增一个供应商模型时，
后端注册表多一条即可，前端选择器自动出现，不需要发版。
"""

from api.module import ModuleSpec
from core.permissions import PermissionSpec
from modules.catalog.router import router

module = ModuleSpec(
    name="catalog",
    router=router,
    tags=["模型"],
    description="模型目录：可用模型、所属厂商、参数 schema",
    permissions=(
        PermissionSpec("catalog:read", "查看模型目录", group="内容生产"),
    ),
)
