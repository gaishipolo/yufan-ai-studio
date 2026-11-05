"""系统模块：健康检查、版本信息。"""

from api.module import ModuleSpec
from modules.health.router import router

module = ModuleSpec(name="health", router=router, tags=["系统"], public=True)
