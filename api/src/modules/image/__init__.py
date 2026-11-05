"""生图模块：文生图作业的提交与历史。"""

from api.module import ModuleSpec
from modules.image.permissions import SPECS
from modules.image.router import router

module = ModuleSpec(
    name="image",
    router=router,
    tags=["生图"],
    permissions=SPECS,
)
