"""模板模块：行业模板与分镜结构复用。"""

from api.module import ModuleSpec
from core.permissions import PermissionSpec
from modules.template.replacement_pipeline import KIND_REPLACEMENT
from modules.template.replacement_worker import resume
from modules.template.router import router

module = ModuleSpec(
    name="template",
    router=router,
    tags=["模板"],
    permissions=(
        PermissionSpec("template:read", "查看行业模板", group="内容生产"),
        PermissionSpec("template:use", "使用替换模板并提交零积分生成", group="内容生产"),
    ),
    startup=resume,
    # 已有自己的恢复器；声明负责的 kind 供架构守卫对账。
    recovery_kinds=(KIND_REPLACEMENT,),
)
